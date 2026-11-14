from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import memory as memory_utils
from utils import triage
from utils.checks import owner_only
from utils.db import MEMORY_AUTO, MEMORY_PINNED
from utils.interactions import defer_ephemeral, respond
from utils.ollama import PRIORITY_BACKGROUND, OllamaError
from utils.prompting import (
    cap_reply,
    clean_reply,
    history_to_messages,
    is_action_only,
    is_meta,
    is_narration,
    speaks_as_other,
    split_message,
)

log = logging.getLogger(__name__)

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")

# Small local models become incoherent above this.
MAX_TEMPERATURE = 1.2
MAX_PINNED_MEMORIES = 25
# Messages starting with this are out-of-character: not stored, no reply.
OOC_PREFIX = "//"
# A typing event older than this no longer counts as "still writing".
TYPING_GRACE_SECONDS = 8.0

REPLY_SAMPLING = {"top_p": 0.9, "top_k": 40, "repeat_penalty": 1.1}

INSTALLS = app_commands.AppInstallationType(guild=False, user=True)
CONTEXTS = app_commands.AppCommandContext(
    guild=True, dm_channel=False, private_channel=False
)


def _user_install_owner_command():
    """Owner-only command that is only exposed to user-installed apps."""

    def decorator(func):
        func = app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)(func)
        func = app_commands.allowed_installs(guilds=False, users=True)(func)
        func = owner_only()(func)
        return func

    return decorator


async def model_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    try:
        models = await interaction.client.ollama.list_models()
    except Exception:
        return []

    current = current.lower()
    choices = []
    for model in models:
        if len(model) > 100 or current not in model.lower():
            continue
        choices.append(app_commands.Choice(name=model, value=model))
        if len(choices) == 25:
            break
    return choices


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _base_chat_kwargs() -> dict:
    # num_ctx / num_thread / num_batch must match between reply and memory
    # requests, otherwise Ollama reloads the model for every switch.
    return {
        "num_ctx": _env_int("OLLAMA_NUM_CTX", 4096),
        "think": _env_bool("OLLAMA_THINK", False),
        "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "2h").strip() or "2h",
        "num_thread": _env_int("OLLAMA_NUM_THREAD", 6),
        "num_batch": _env_int("OLLAMA_NUM_BATCH", 128),
    }


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _max_actions() -> int:
    return max(0, min(3, _env_int("ROLEPLAY_MAX_ACTIONS", 1)))


def _timestamp(value: Optional[float]) -> str:
    if not value:
        return "unknown"
    return discord.utils.format_dt(datetime.fromtimestamp(value), style="R")


class RoleplayCog(commands.Cog):
    roleplay = app_commands.Group(
        name="roleplay",
        description="Set up and manage the roleplay character for a channel.",
        allowed_installs=INSTALLS,
        allowed_contexts=CONTEXTS,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._queues: dict[int, asyncio.Queue] = {}
        self._workers: dict[int, asyncio.Task] = {}
        self._last_compact_attempt: dict[int, float] = {}
        self._compact_tasks: dict[int, asyncio.Task] = {}
        # (channel_id, user_id) -> monotonic time of the last typing event / message.
        self._typing_at: dict[tuple[int, int], float] = {}
        self._message_at: dict[tuple[int, int], float] = {}

    def cog_unload(self):
        for task in [*self._workers.values(), *self._compact_tasks.values()]:
            task.cancel()
        self._workers.clear()
        self._queues.clear()
        self._compact_tasks.clear()

    # --------------------------------------------------------------- queueing

    def _enqueue(self, channel_id: int, message: discord.Message) -> None:
        queue = self._queues.setdefault(channel_id, asyncio.Queue())
        queue.put_nowait(message)

        task = self._workers.get(channel_id)
        if task is None or task.done():
            self._workers[channel_id] = asyncio.create_task(self._worker(channel_id))

    async def _worker(self, channel_id: int) -> None:
        queue = self._queues.get(channel_id)
        if queue is None:
            return
        try:
            while True:
                batch = [await queue.get()]
                try:
                    await self._gather(channel_id, queue, batch)
                    await self._handle_batch(batch)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Unhandled error while replying in channel %s", channel_id)
                finally:
                    for _ in batch:
                        queue.task_done()
        except asyncio.CancelledError:
            pass
        finally:
            if self._workers.get(channel_id) is asyncio.current_task():
                self._workers.pop(channel_id, None)

    async def _gather(self, channel_id: int, queue: asyncio.Queue, batch: list) -> None:
        """Collect follow-up messages so a burst gets one combined reply.

        Waits a short quiet period after the latest message, and keeps waiting
        (up to a cap) while someone in the channel is still typing.
        """
        quiet = max(0.0, _env_float("ROLEPLAY_GATHER_SECONDS", 1.2))
        cap = max(quiet, _env_float("ROLEPLAY_MAX_GATHER_SECONDS", 8.0))
        started = time.monotonic()
        while True:
            while not queue.empty():
                batch.append(queue.get_nowait())
            remaining = cap - (time.monotonic() - started)
            if remaining <= 0:
                return
            typing = self._someone_typing(channel_id)
            if not typing and quiet <= 0:
                return
            try:
                batch.append(await asyncio.wait_for(queue.get(), min(1.0 if typing else quiet, remaining)))
            except asyncio.TimeoutError:
                if not self._someone_typing(channel_id):
                    return

    def _someone_typing(self, channel_id: int) -> bool:
        now = time.monotonic()
        for (channel, user), typed_at in self._typing_at.items():
            if channel != channel_id or now - typed_at > TYPING_GRACE_SECONDS:
                continue
            # Typing that started after their last message means another one is coming.
            if typed_at > self._message_at.get((channel, user), 0.0):
                return True
        return False

    def _stop_worker(self, channel_id: int) -> None:
        self._queues.pop(channel_id, None)
        self._last_compact_attempt.pop(channel_id, None)
        for tasks in (self._workers, self._compact_tasks):
            task = tasks.pop(channel_id, None)
            if task is not None:
                task.cancel()

    # ------------------------------------------------------------- listener

    @commands.Cog.listener()
    async def on_typing(self, channel, user, when) -> None:
        if getattr(user, "bot", False) or channel.id not in self._queues:
            return
        now = time.monotonic()
        self._typing_at[(channel.id, user.id)] = now
        if len(self._typing_at) > 500:
            self._typing_at = {
                key: at for key, at in self._typing_at.items() if now - at <= TYPING_GRACE_SECONDS
            }

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if message.content.lstrip().startswith(OOC_PREFIX):
            return

        try:
            config = await self.bot.db.get_channel(message.channel.id)
        except Exception:
            log.exception("Could not read roleplay config for channel %s", message.channel.id)
            return

        if not config or not config["enabled"]:
            return

        key = (message.channel.id, message.author.id)
        self._message_at[key] = time.monotonic()
        if len(self._message_at) > 2000:
            self._message_at = dict(list(self._message_at.items())[-1000:])
        self._enqueue(message.channel.id, message)

    # --------------------------------------------------------------- replies

    def _is_direct(self, message: discord.Message, character_name: str) -> bool:
        """True when the message clearly addresses the character."""
        me = self.bot.user
        if me is not None and any(user.id == me.id for user in message.mentions):
            return True
        reference = message.reference
        resolved = getattr(reference, "resolved", None) if reference else None
        if me is not None and isinstance(resolved, discord.Message) and resolved.author.id == me.id:
            return True
        name = character_name.strip()
        return bool(name) and re.search(rf"\b{re.escape(name)}\b", message.content, re.IGNORECASE) is not None

    def _build_turns(self, messages: list[discord.Message], character_name: str) -> list[dict]:
        """Merge consecutive messages from the same person into one turn."""
        rank = {triage.NOISE: 0, triage.SHORT: 1, triage.CONTENT: 2}
        turns: list[dict] = []
        for message in messages:
            content = self._message_content(message)
            if not content:
                continue
            if message.content.strip():
                kind = triage.classify(message.content)
            else:
                kind = triage.SHORT if message.attachments else triage.NOISE
            direct = self._is_direct(message, character_name)

            if turns and turns[-1]["author_id"] == message.author.id:
                turn = turns[-1]
                turn["texts"].append(content)
                turn["message"] = message
                turn["direct"] = turn["direct"] or direct
                if rank[kind] > rank[turn["kind"]]:
                    turn["kind"] = kind
                continue

            turns.append(
                {
                    "author_id": message.author.id,
                    "display": (message.author.display_name or message.author.name)[:80],
                    "username": message.author.name,
                    "texts": [content],
                    "message": message,
                    "kind": kind,
                    "direct": direct,
                }
            )
        return turns

    async def _handle_batch(self, messages: list[discord.Message]) -> None:
        channel = messages[-1].channel
        config = await self.bot.db.get_channel(channel.id)
        if not config or not config["enabled"]:
            return

        turns = self._build_turns(messages, config["character_name"])
        if not turns:
            return

        # Short answers ("yes", "ok") only matter if the character's latest line,
        # with nothing else said since, was a question.
        recent = await self.bot.db.get_recent_messages(channel.id, 1)
        bot_asked = bool(recent) and recent[-1]["role"] == "assistant" and "?" in recent[-1]["content"]
        answer = [
            turn
            for turn in turns
            if turn["kind"] == triage.CONTENT
            or turn["direct"]
            or (turn["kind"] == triage.SHORT and bot_asked)
        ]

        # Keep everything except pure noise as context, even when not replying.
        for turn in turns:
            if turn["kind"] == triage.NOISE and not turn["direct"]:
                continue
            await self.bot.db.add_message(
                channel.id,
                "user",
                "\n".join(turn["texts"])[:4000],
                author_id=turn["author_id"],
                author_name=turn["display"],
            )

        if not answer:
            log.info(
                "Not replying to %d message(s) in channel %s: nothing to answer",
                len(messages),
                channel.id,
            )
            return

        await self._reply(channel, config, answer)

    async def _reply(self, channel: discord.TextChannel, config: dict, answer: list[dict]) -> None:
        reference = answer[-1]["message"]
        # Use the name everyone sees in chat. Add the unique username only when
        # two different people share a display name; otherwise it confuses the model.
        authors: dict[int, dict] = {}
        for turn in answer:
            authors.setdefault(turn["author_id"], turn)
        display_counts: dict[str, int] = {}
        for turn in authors.values():
            key = turn["display"].lower()
            display_counts[key] = display_counts.get(key, 0) + 1
        reply_to = [
            f"{turn['display']} (@{turn['username']})"
            if display_counts[turn["display"].lower()] > 1
            else turn["display"]
            for turn in authors.values()
        ]

        history = await self.bot.db.get_recent_messages(channel.id, int(config["max_history"]))
        memories = await self.bot.db.list_memories(channel.id)
        max_actions = _max_actions()
        messages = history_to_messages(
            config,
            memories,
            history,
            max_chars=max(1200, _env_int("ROLEPLAY_HISTORY_CHARS", 4000)),
            max_actions=max_actions,
            memory_chars=max(300, _env_int("ROLEPLAY_MEMORY_CHARS", 1500)),
            reply_to=reply_to,
        )
        participants = self._participant_names(history)

        started = time.monotonic()
        try:
            async with channel.typing():
                reply, done_reason = await self._generate_reply(
                    config, messages, participants, max_actions
                )
        except asyncio.CancelledError:
            raise
        except OllamaError as exc:
            log.warning(
                "Ollama failed in channel %s with model %s: %s",
                channel.id,
                config["model"],
                exc,
            )
            await self._safe_send(
                channel,
                "⚠️ I couldn't generate a reply from the local roleplay model. "
                "Check that Ollama is running and the configured model is available.",
                reference=reference,
            )
            return
        except Exception:
            log.exception("Unexpected model error in channel %s", channel.id)
            await self._safe_send(
                channel,
                "⚠️ Something went wrong while generating a reply.",
                reference=reference,
            )
            return

        if not reply:
            log.warning("Model produced an empty reply in channel %s", channel.id)
            return

        await self.bot.db.add_message(
            channel.id,
            "assistant",
            reply,
            author_id=self.bot.user.id,
            author_name=config["character_name"],
        )

        chunks = split_message(reply)
        for index, chunk in enumerate(chunks):
            await self._safe_send(channel, chunk, reference=reference if index == 0 else None)

        log.info(
            "Replied to %s in channel %s as %s in %.1fs (%d chars, %s)",
            ", ".join(reply_to),
            channel.id,
            config["character_name"],
            time.monotonic() - started,
            len(reply),
            done_reason or "?",
        )

        self._schedule_compaction(channel.id)

    async def _generate_reply(
        self,
        config: dict,
        messages: list[dict],
        participants: list[str],
        max_actions: int,
    ) -> tuple[str, str]:
        name = config["character_name"]
        temperature = min(MAX_TEMPERATURE, float(config["temperature"]))
        candidates: list[tuple[int, str, str]] = []
        for attempt in range(2):
            prompt = messages
            if attempt:
                # Small models sometimes narrate in the third person, reply with
                # only an action, or write someone else's line; nudge once, calmer.
                last = dict(messages[-1])
                last["content"] += "\n\n(Say something to them directly, in first person.)"
                prompt = [*messages[:-1], last]
                temperature = min(temperature, 0.6)

            result = await self.bot.ollama.chat_result(
                model=config["model"],
                messages=prompt,
                temperature=temperature,
                num_predict=_env_int("OLLAMA_NUM_PREDICT", 128),
                stop=self._stop_sequences(participants, name),
                options=REPLY_SAMPLING,
                **_base_chat_kwargs(),
            )
            reply = clean_reply(
                result.content,
                name,
                participants,
                truncated=result.done_reason == "length",
                max_actions=max_actions,
                max_sentences=max(0, _env_int("ROLEPLAY_MAX_SENTENCES", 3)),
            )
            reply = cap_reply(reply, min(1900, max(200, _env_int("ROLEPLAY_MAX_REPLY_CHARS", 1200))))

            if not reply or speaks_as_other(result.content, name, participants):
                score = 0
            elif is_action_only(reply) or is_meta(reply):
                score = 1
            elif is_narration(reply, name):
                score = 2
            else:
                score = 3
            candidates.append((score, reply, result.done_reason))
            if score == 3:
                break

        score, reply, done_reason = max(candidates, key=lambda c: c[0])
        if score == 0:
            log.info("Discarding unusable replies in favour of silence: %r", [c[1][:80] for c in candidates])
            return "", done_reason
        return reply, done_reason

    @staticmethod
    def _message_content(message: discord.Message) -> str:
        parts = []
        text = message.content.strip()
        if text:
            parts.append(text)

        if message.attachments:
            names = ", ".join(attachment.filename for attachment in message.attachments[:5])
            parts.append(f"[attachment: {names}]")

        if message.stickers:
            names = ", ".join(sticker.name for sticker in message.stickers[:5])
            parts.append(f"[sticker: {names}]")

        if message.embeds and not text:
            parts.append("[embed]")

        return "\n".join(parts).strip()[:4000]

    @staticmethod
    async def _safe_send(
        channel: discord.abc.Messageable,
        content: str,
        *,
        reference: Optional[discord.Message] = None,
    ) -> None:
        if not content:
            return
        allowed = discord.AllowedMentions.none()
        try:
            if reference is not None:
                await reference.reply(content, mention_author=False, allowed_mentions=allowed)
            else:
                await channel.send(content, allowed_mentions=allowed)
        except discord.HTTPException:
            if reference is None:
                log.exception("Could not send a roleplay message in channel %s", getattr(channel, "id", "?"))
                return
            # The referenced message may have been deleted; fall back to a normal send.
            try:
                await channel.send(content, allowed_mentions=allowed)
            except discord.HTTPException:
                log.exception(
                    "Could not send a roleplay message in channel %s",
                    getattr(channel, "id", "?"),
                )

    # -------------------------------------------------------------- context

    def _schedule_compaction(self, channel_id: int) -> None:
        """Update long-term memory in the background so replies are never delayed."""
        running = self._compact_tasks.get(channel_id)
        if running is not None and not running.done():
            return
        last_attempt = self._last_compact_attempt.get(channel_id, 0.0)
        if time.monotonic() - last_attempt < 60:
            return
        self._compact_tasks[channel_id] = asyncio.create_task(self._compact(channel_id))

    async def _compact(self, channel_id: int) -> None:
        try:
            await self._maybe_compact(channel_id)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Memory update failed in channel %s", channel_id)
        finally:
            if self._compact_tasks.get(channel_id) is asyncio.current_task():
                self._compact_tasks.pop(channel_id, None)

    async def _maybe_compact(self, channel_id: int) -> None:
        config = await self.bot.db.get_channel(channel_id)
        if not config:
            return

        max_history = int(config["max_history"])
        compact_after = max(4, _env_int("ROLEPLAY_COMPACT_AFTER", 10))
        count = await self.bot.db.count_messages(channel_id)
        if count <= max_history + compact_after:
            return

        self._last_compact_attempt[channel_id] = time.monotonic()

        rows = await self.bot.db.get_oldest_messages(channel_id, min(count - max_history, 40))
        selected = []
        chars = 0
        for row in rows:
            length = len(row["content"])
            if selected and chars + length > 6000:
                break
            selected.append(row)
            chars += length
        if not selected:
            return
        through_id = int(selected[-1]["id"])

        memories = await self.bot.db.list_memories(channel_id)
        pinned = [(m["about"], m["content"]) for m in memories if m["kind"] == MEMORY_PINNED]
        auto = [(m["about"], m["content"]) for m in memories if m["kind"] == MEMORY_AUTO]

        chat_kwargs = {
            **_base_chat_kwargs(),
            "model": os.getenv("OLLAMA_SUMMARY_MODEL", "").strip() or config["model"],
            "temperature": 0.1,
            "num_predict": _env_int("OLLAMA_SUMMARY_PREDICT", 400),
            "options": {"repeat_penalty": 1.15},
            "priority": PRIORITY_BACKGROUND,
        }
        facts = await memory_utils.update_memory(
            self.bot.ollama,
            character_name=config["character_name"],
            pinned=pinned,
            auto=auto,
            rows=selected,
            max_facts=max(3, min(40, _env_int("ROLEPLAY_MEMORY_FACTS", 15))),
            chat_kwargs=chat_kwargs,
        )

        if facts is None:
            # Keep the messages for a later retry, but never let them pile up forever.
            if count > max_history + compact_after + 60:
                deleted = await self.bot.db.delete_messages_through(channel_id, through_id)
                log.warning(
                    "Memory update kept failing in channel %s; dropped %d old messages",
                    channel_id,
                    deleted,
                )
            return

        await self.bot.db.replace_auto_memories(channel_id, facts, through_id)
        log.info(
            "Updated memory in channel %s from %d messages (%d facts)",
            channel_id,
            len(selected),
            len(facts),
        )

    # -------------------------------------------------------------- commands

    @roleplay.command(name="setup", description="Set up or replace the roleplay character in a channel.")
    @app_commands.describe(
        channel="The text channel the character will live in.",
        name="Character name.",
        persona="Personality, background, voice, and quirks (max 2000 characters).",
        scenario="Setting or current situation (max 1500 characters).",
        model="Local Ollama model to use. Leave blank to use the default.",
        temperature="Creativity from 0.0 (focused) to 1.2 (wild). Default 0.8.",
        history="Recent messages kept verbatim, from 4 to 40. Default 10.",
        fresh_start="Clear stored messages and automatic memories (pinned memories are kept).",
    )
    @app_commands.autocomplete(model=model_autocomplete)
    @_user_install_owner_command()
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        name: str,
        persona: str,
        scenario: str = "",
        model: str = "",
        temperature: app_commands.Range[float, 0.0, MAX_TEMPERATURE] = 0.8,
        history: app_commands.Range[int, 4, 40] = 10,
        fresh_start: bool = False,
    ):
        if not await self._defer(interaction):
            return

        if not (1 <= len(name.strip()) <= 80):
            await self._respond(interaction, "The character name must be 1-80 characters.", ephemeral=True)
            return
        if not (1 <= len(persona.strip()) <= 2000):
            await self._respond(interaction, "The persona must be 1-2000 characters.", ephemeral=True)
            return
        if len(scenario.strip()) > 1500:
            await self._respond(interaction, "The scenario can be at most 1500 characters.", ephemeral=True)
            return

        problem = self._channel_problem(channel)
        if problem:
            await self._respond(interaction, problem, ephemeral=True)
            return

        model = model.strip() or os.getenv("OLLAMA_MODEL", "").strip()
        if model and not MODEL_RE.match(model):
            await self._respond(interaction, "That model name contains invalid characters.", ephemeral=True)
            return
        if not model:
            try:
                model = await self.bot.ollama.resolve_model()
            except OllamaError as exc:
                await self._respond(interaction, str(exc), ephemeral=True)
                return

        temperature = max(0.0, min(MAX_TEMPERATURE, float(temperature)))
        history = max(4, min(40, int(history)))

        await self.bot.db.upsert_channel(
            channel_id=channel.id,
            guild_id=channel.guild.id,
            character_name=name.strip(),
            persona=persona.strip(),
            scenario=scenario.strip(),
            model=model,
            temperature=temperature,
            max_history=history,
            created_by=interaction.user.id,
        )

        if fresh_start:
            self._stop_worker(channel.id)
            await self.bot.db.clear_context(channel.id)

        embed = discord.Embed(
            title="Roleplay character ready",
            description=(
                f"**{discord.utils.escape_markdown(name.strip())}** will now reply to every message in "
                f"{channel.mention}."
            ),
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="Model", value=f"`{model}`", inline=True)
        embed.add_field(name="Temperature", value=f"{temperature:.2f}", inline=True)
        embed.add_field(name="Recent history", value=f"{history} messages", inline=True)
        embed.set_footer(
            text="Use /roleplay tune to adjust the personality, or /roleplay reset to clear the chat memory."
        )
        await self._respond(interaction, embed=embed, ephemeral=True)

    @roleplay.command(name="tune", description="Adjust a channel character's behaviour or creativity.")
    @app_commands.describe(
        channel="Channel to tune. Defaults to the current channel.",
        instruction="Extra instruction, for example: 'Be more sarcastic and use short replies.'",
        temperature="New creativity from 0.0 (focused) to 1.2 (wild).",
        clear="Remove all previous tuning instructions.",
    )
    @_user_install_owner_command()
    async def tune(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        instruction: str = "",
        temperature: Optional[app_commands.Range[float, 0.0, MAX_TEMPERATURE]] = None,
        clear: bool = False,
    ):
        if not await self._defer(interaction):
            return

        channel = await self._resolve_channel(interaction, channel)
        if channel is None:
            return

        config = await self.bot.db.get_channel(channel.id)
        if not config:
            await self._respond(interaction, "That channel has no roleplay setup yet.", ephemeral=True)
            return

        instruction = instruction.strip()
        if not instruction and temperature is None and not clear:
            await self._respond(
                interaction,
                "Provide an instruction, a temperature, or set clear:true.",
                ephemeral=True,
            )
            return
        if len(instruction) > 1000:
            await self._respond(interaction, "Keep the instruction under 1000 characters.", ephemeral=True)
            return

        changes = []
        if clear:
            await self.bot.db.clear_style(channel.id)
            changes.append("cleared previous tuning notes")
        if instruction:
            await self.bot.db.append_style(channel.id, instruction)
            changes.append("added a tuning note")
        if temperature is not None:
            temperature = max(0.0, min(MAX_TEMPERATURE, float(temperature)))
            await self.bot.db.set_temperature(channel.id, temperature)
            changes.append(f"set temperature to {temperature:.2f}")

        updated = await self.bot.db.get_channel(channel.id) or config
        style = (updated.get("style") or "").strip() or "(none)"
        await self._respond(
            interaction,
            f"Updated **{config['character_name']}**: {', '.join(changes)}.\n\n"
            f"Current tuning notes:\n{style[-1500:]}",
            ephemeral=True,
        )

    @roleplay.command(name="status", description="Show the roleplay setup and context stats for a channel.")
    @app_commands.describe(channel="Channel to inspect. Defaults to the current channel.")
    @_user_install_owner_command()
    async def status(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not await self._defer(interaction):
            return

        channel = await self._resolve_channel(interaction, channel)
        if channel is None:
            return

        config = await self.bot.db.get_channel(channel.id)
        if not config:
            await self._respond(interaction, "That channel has no roleplay setup yet.", ephemeral=True)
            return

        stats = await self.bot.db.context_stats(channel.id)
        style = (config.get("style") or "").strip() or "(none)"

        embed = discord.Embed(
            title=f"{config['character_name']} — roleplay status",
            description=f"Channel: {channel.mention}",
            colour=discord.Colour.green() if config["enabled"] else discord.Colour.dark_grey(),
        )
        embed.add_field(name="Enabled", value="yes" if config["enabled"] else "no", inline=True)
        embed.add_field(name="Model", value=f"`{config['model']}`", inline=True)
        temperature = float(config["temperature"])
        temperature_text = f"{temperature:.2f}"
        if temperature > MAX_TEMPERATURE:
            temperature_text += f" (used as {MAX_TEMPERATURE:.1f})"
        embed.add_field(name="Temperature", value=temperature_text, inline=True)
        embed.add_field(name="Recent history", value=f"{config['max_history']} messages", inline=True)
        embed.add_field(name="Stored messages", value=str(stats["messages"]), inline=True)
        embed.add_field(
            name="Long-term memory",
            value=f"{stats['pinned_memories']} pinned, {stats['auto_memories']} automatic",
            inline=True,
        )
        embed.add_field(name="Tuning notes", value=style[:1024], inline=False)
        embed.add_field(
            name="Updated",
            value=f"{_timestamp(config.get('created_at'))} / {_timestamp(config.get('updated_at'))}",
            inline=False,
        )
        await self._respond(interaction, embed=embed, ephemeral=True)

    @roleplay.command(name="reset", description="Clear a channel's roleplay conversation memory.")
    @app_commands.describe(
        channel="Channel to reset. Defaults to the current channel.",
        confirm="Set to true to confirm the reset.",
    )
    @_user_install_owner_command()
    async def reset(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        confirm: bool = False,
    ):
        if not await self._defer(interaction):
            return

        channel = await self._resolve_channel(interaction, channel)
        if channel is None:
            return

        config = await self.bot.db.get_channel(channel.id)
        if not config:
            await self._respond(interaction, "That channel has no roleplay setup yet.", ephemeral=True)
            return
        if not confirm:
            await self._respond(
                interaction,
                "This will delete the stored messages and automatic memories. Pinned memories "
                "are kept. Run the command again with `confirm:true`.",
                ephemeral=True,
            )
            return

        self._stop_worker(channel.id)
        messages, memories = await self.bot.db.clear_context(channel.id)
        await self._respond(
            interaction,
            f"Cleared {messages} stored message(s) and {memories} automatic memory note(s) "
            f"for **{config['character_name']}**. Pinned memories were kept.",
            ephemeral=True,
        )

    @roleplay.command(name="toggle", description="Pause or resume replies in a roleplay channel.")
    @app_commands.describe(
        enabled="True to make the character reply, false to pause.",
        channel="Channel to change. Defaults to the current channel.",
    )
    @_user_install_owner_command()
    async def toggle(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not await self._defer(interaction):
            return

        channel = await self._resolve_channel(interaction, channel)
        if channel is None:
            return

        updated = await self.bot.db.set_enabled(channel.id, enabled)
        if not updated:
            await self._respond(interaction, "That channel has no roleplay setup yet.", ephemeral=True)
            return

        if not enabled:
            self._stop_worker(channel.id)
        await self._respond(
            interaction,
            f"Roleplay replies are now **{'enabled' if enabled else 'paused'}** in {channel.mention}.",
            ephemeral=True,
        )

    @roleplay.command(name="remove", description="Delete a channel's roleplay setup and all stored memory.")
    @app_commands.describe(
        channel="Channel to remove. Defaults to the current channel.",
        confirm="Set to true to confirm deletion.",
    )
    @_user_install_owner_command()
    async def remove(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        confirm: bool = False,
    ):
        if not await self._defer(interaction):
            return

        channel = await self._resolve_channel(interaction, channel)
        if channel is None:
            return

        config = await self.bot.db.get_channel(channel.id)
        if not config:
            await self._respond(interaction, "That channel has no roleplay setup yet.", ephemeral=True)
            return
        if not confirm:
            await self._respond(
                interaction,
                "This will delete the setup, stored messages, and long-term memory. "
                "Run the command again with `confirm:true`.",
                ephemeral=True,
            )
            return

        self._stop_worker(channel.id)
        await self.bot.db.delete_channel(channel.id)
        await self._respond(
            interaction,
            f"Removed the roleplay setup for **{config['character_name']}** from {channel.mention}.",
            ephemeral=True,
        )

    @roleplay.command(name="memory", description="Show what a channel's character remembers long-term.")
    @app_commands.describe(channel="Channel to inspect. Defaults to the current channel.")
    @_user_install_owner_command()
    async def memory(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not await self._defer(interaction):
            return

        channel = await self._resolve_channel(interaction, channel)
        if channel is None:
            return

        config = await self.bot.db.get_channel(channel.id)
        if not config:
            await self._respond(interaction, "That channel has no roleplay setup yet.", ephemeral=True)
            return

        memories = await self.bot.db.list_memories(channel.id)
        embed = discord.Embed(
            title=f"{config['character_name']} — long-term memory",
            colour=discord.Colour.blurple(),
        )
        for kind, title in ((MEMORY_PINNED, "Pinned"), (MEMORY_AUTO, "Learned from chat")):
            lines = []
            for row in memories:
                if row["kind"] != kind:
                    continue
                about = f"**{discord.utils.escape_markdown(row['about'])}:** " if row["about"] else ""
                lines.append(f"`#{row['id']}` {about}{discord.utils.escape_markdown(row['content'])}")
            value = "\n".join(lines) or "(none)"
            if len(value) > 1024:
                value = value[:1000].rsplit("\n", 1)[0] + "\n…"
            embed.add_field(name=title, value=value, inline=False)
        embed.set_footer(text="Use /roleplay remember to pin a fact, or /roleplay forget with an id to remove one.")
        await self._respond(interaction, embed=embed, ephemeral=True)

    @roleplay.command(name="remember", description="Pin an important fact the character should never forget.")
    @app_commands.describe(
        fact="The fact, for example: 'Ram owes Quint ten gold.'",
        about="Who or what the fact is about (optional).",
        channel="Channel to change. Defaults to the current channel.",
    )
    @_user_install_owner_command()
    async def remember(
        self,
        interaction: discord.Interaction,
        fact: app_commands.Range[str, 3, memory_utils.MAX_FACT_CHARS],
        about: Optional[app_commands.Range[str, 1, memory_utils.MAX_ABOUT_CHARS]] = None,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not await self._defer(interaction):
            return

        channel = await self._resolve_channel(interaction, channel)
        if channel is None:
            return

        config = await self.bot.db.get_channel(channel.id)
        if not config:
            await self._respond(interaction, "That channel has no roleplay setup yet.", ephemeral=True)
            return

        stats = await self.bot.db.context_stats(channel.id)
        if stats["pinned_memories"] >= MAX_PINNED_MEMORIES:
            await self._respond(
                interaction,
                f"There are already {MAX_PINNED_MEMORIES} pinned memories. Remove one with "
                "`/roleplay forget` first.",
                ephemeral=True,
            )
            return

        about_text = " ".join((about or "").split())
        fact_text = " ".join(fact.split())
        memory_id = await self.bot.db.add_memory(channel.id, MEMORY_PINNED, fact_text, about_text)
        label = f"**{discord.utils.escape_markdown(about_text)}:** " if about_text else ""
        await self._respond(
            interaction,
            f"Pinned memory `#{memory_id}` for **{config['character_name']}**: "
            f"{label}{discord.utils.escape_markdown(fact_text)}",
            ephemeral=True,
        )

    @roleplay.command(name="forget", description="Remove one long-term memory by its id.")
    @app_commands.describe(
        memory_id="The memory id shown by /roleplay memory.",
        channel="Channel to change. Defaults to the current channel.",
    )
    @_user_install_owner_command()
    async def forget(
        self,
        interaction: discord.Interaction,
        memory_id: app_commands.Range[int, 1],
        channel: Optional[discord.TextChannel] = None,
    ):
        if not await self._defer(interaction):
            return

        channel = await self._resolve_channel(interaction, channel)
        if channel is None:
            return

        removed = await self.bot.db.delete_memory(channel.id, int(memory_id))
        if not removed:
            await self._respond(
                interaction,
                f"No memory `#{memory_id}` in {channel.mention}. Check `/roleplay memory`.",
                ephemeral=True,
            )
            return
        await self._respond(
            interaction,
            f"Forgot memory `#{memory_id}`: {discord.utils.escape_markdown(removed['content'])}",
            ephemeral=True,
        )

    @roleplay.command(name="models", description="List the local Ollama models available to the bot.")
    @_user_install_owner_command()
    async def models(self, interaction: discord.Interaction):
        if not await self._defer(interaction):
            return

        try:
            models = await self.bot.ollama.list_models(force=True)
        except OllamaError as exc:
            await self._respond(interaction, str(exc), ephemeral=True)
            return

        if not models:
            await self._respond(
                interaction,
                "No local Ollama models found. Pull one with `ollama pull llama3.2`, then try again.",
                ephemeral=True,
            )
            return

        shown = models[:40]
        description = "\n".join(f"`{model}`" for model in shown)
        if len(models) > len(shown):
            description += f"\n…and {len(models) - len(shown)} more."
        embed = discord.Embed(
            title="Local Ollama models",
            description=description[:4000],
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(text=f"Ollama: {self.bot.ollama.base_url}")
        await self._respond(interaction, embed=embed, ephemeral=True)

    @roleplay.command(name="list", description="List every channel with a roleplay setup.")
    @_user_install_owner_command()
    async def list_setups(self, interaction: discord.Interaction):
        if not await self._defer(interaction):
            return

        configs = await self.bot.db.list_channels()
        if not configs:
            await self._respond(interaction, "No roleplay channels have been set up yet.", ephemeral=True)
            return

        lines = []
        for config in configs[:20]:
            guild = self.bot.get_guild(config["guild_id"])
            guild_name = guild.name if guild else f"server {config['guild_id']}"
            state = "on" if config["enabled"] else "paused"
            lines.append(
                f"<#{config['channel_id']}> — **{discord.utils.escape_markdown(config['character_name'])}** "
                f"({state}, `{config['model']}`) in {discord.utils.escape_markdown(guild_name)}"
            )
        if len(configs) > 20:
            lines.append(f"…and {len(configs) - 20} more.")

        embed = discord.Embed(
            title="Roleplay channels",
            description="\n".join(lines)[:4000],
            colour=discord.Colour.blurple(),
        )
        await self._respond(interaction, embed=embed, ephemeral=True)

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _participant_names(history: list[dict]) -> list[str]:
        names = {
            (row.get("author_name") or "").strip()
            for row in history
            if row.get("role") != "assistant"
        }
        return sorted(name for name in names if name)

    @staticmethod
    def _stop_sequences(participants: list[str], character_name: str) -> list[str]:
        """Stop generation at a paragraph break or before another speaker's line."""
        names = {character_name.strip(), *participants}
        return ["\n\n", *(f"\n{name}:" for name in sorted(names) if name)]

    @staticmethod
    async def _defer(interaction: discord.Interaction) -> bool:
        # The command tree normally acknowledges first; this is a fallback.
        return await defer_ephemeral(interaction)

    @staticmethod
    async def _respond(interaction: discord.Interaction, content: str = "", **kwargs):
        await respond(interaction, content, **kwargs)

    @staticmethod
    async def _resolve_channel(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel],
    ) -> Optional[discord.TextChannel]:
        channel = channel or interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await RoleplayCog._respond(
                interaction,
                "Choose a normal server text channel.",
                ephemeral=True,
            )
            return None
        return channel

    @staticmethod
    def _channel_problem(channel: discord.TextChannel) -> Optional[str]:
        me = channel.guild.me
        if me is None:
            return (
                "I'm not a member of that server yet. Invite the bot to the server "
                "before setting up a roleplay channel."
            )
        permissions = channel.permissions_for(me)
        missing = []
        if not permissions.view_channel:
            missing.append("View Channel")
        if not permissions.send_messages:
            missing.append("Send Messages")
        if missing:
            return f"I need these permissions in {channel.mention}: {', '.join(missing)}."
        return None


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleplayCog(bot))
