import hashlib
import json
import logging
import math
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from utils.checks import NotOwner
from utils.db import Database
from utils.interactions import defer_ephemeral, respond
from utils.ollama import OllamaClient
from utils.ratelimit import RateLimiter, deny_if_limited

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")

_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

if not OWNER_ID:
    log.warning(
        "OWNER_ID is not set; roleplay management commands will fall back to the Discord application owner."
    )

# Applies to every slash command, on top of each command's own cooldown.
COMMAND_LIMITER = RateLimiter(rate=5, per=15.0)


def _owner_id() -> int | None:
    if not OWNER_ID:
        return None
    try:
        return int(OWNER_ID)
    except ValueError:
        log.warning("OWNER_ID is not a valid integer; ignoring it.")
        return None


class BotCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Autocomplete fires on every keystroke; it must not use up the command limit.
        if interaction.type is discord.InteractionType.autocomplete:
            return True
        # Acknowledge before any other work so the 3-second deadline is never at
        # the mercy of checks, the database, or Ollama. Every command is ephemeral.
        if interaction.type is discord.InteractionType.application_command:
            if not await defer_ephemeral(interaction):
                return False
        return not await deny_if_limited(interaction, COMMAND_LIMITER, interaction.user.id)

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, NotOwner):
            message = "This command is only for the bot owner."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"This command is on cooldown. Try again in {math.ceil(error.retry_after)}s."
        elif isinstance(error, app_commands.CheckFailure):
            message = "You don't have permission to use this command."
        else:
            command_name = interaction.command.qualified_name if interaction.command else "unknown"
            log.error(
                "Error in /%s (guild %s, user %s)",
                command_name,
                interaction.guild_id,
                interaction.user.id,
                exc_info=error,
            )
            message = "Something went wrong while running this command. The error has been logged."

        await respond(interaction, message, ephemeral=True)


class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        # Typing events let the bot wait while someone is still writing a follow-up.
        intents.guild_typing = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=intents,
            tree_cls=BotCommandTree,
            owner_id=_owner_id(),
            # Server-installed commands are off by default. The roleplay commands
            # explicitly opt in to user installs so they are not exposed to
            # everyone in a server.
            allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
            allowed_contexts=app_commands.AppCommandContext(
                guild=True, dm_channel=False, private_channel=False
            ),
        )

        db_setting = Path(os.getenv("ROLEPLAY_DB", "data/roleplay.db"))
        if not db_setting.is_absolute():
            db_setting = BASE_DIR / db_setting
        self.db = Database(db_setting)
        # Remember the last synced command tree so restarts do not re-register
        # commands and hit Discord's global command rate limit.
        self._sync_state_path = self.db.path.parent / "command_tree.sha256"

        try:
            max_concurrency = int(os.getenv("OLLAMA_MAX_CONCURRENCY", "1"))
        except ValueError:
            max_concurrency = 1
        try:
            timeout = float(os.getenv("OLLAMA_TIMEOUT", "180"))
        except ValueError:
            timeout = 180.0

        self.ollama = OllamaClient(
            base_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
            timeout=timeout,
            max_concurrency=max_concurrency,
        )

    async def setup_hook(self):
        await self.db.init()
        await self.ollama.start()

        for filename in sorted(os.listdir(BASE_DIR / "cogs")):
            if filename.endswith(".py") and filename != "__init__.py":
                await self.load_extension(f"cogs.{filename[:-3]}")

        await self._sync_commands()

    async def _sync_commands(self) -> None:
        """Register slash commands only when the command tree actually changed."""
        mode = os.getenv("SYNC_COMMANDS", "auto").strip().lower()
        if mode in {"never", "off", "false", "0"}:
            log.info("Skipping slash command sync (SYNC_COMMANDS=%s)", mode)
            return
        if mode not in {"auto", "always", "force"}:
            log.warning("Unknown SYNC_COMMANDS=%r; falling back to auto", mode)
            mode = "auto"

        try:
            payload = [command.to_dict(self.tree) for command in self.tree.get_commands()]
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
            ).hexdigest()
        except Exception:
            log.exception("Could not hash the command tree; syncing anyway")
            digest = None

        if mode == "auto" and digest is not None:
            try:
                if self._sync_state_path.read_text(encoding="utf-8").strip() == digest:
                    log.info("Slash commands are unchanged; skipping sync")
                    return
            except OSError:
                pass

        try:
            synced = await self.tree.sync()
        except discord.HTTPException:
            log.exception("Failed to sync slash commands")
            return

        log.info("Synced %d global slash commands", len(synced))
        if digest is not None:
            try:
                self._sync_state_path.parent.mkdir(parents=True, exist_ok=True)
                self._sync_state_path.write_text(digest + "\n", encoding="utf-8")
            except OSError:
                log.warning("Could not save the command sync hash", exc_info=True)

    async def on_ready(self):
        log.info("Logged in as %s (%s)", self.user, self.user.id)

    async def close(self):
        try:
            await self.ollama.close()
        finally:
            await super().close()


bot = MyBot()

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN not found in environment variables.")
    else:
        # basicConfig above already installs the root handler. Letting discord.py
        # add its own would print every log line twice.
        bot.run(TOKEN)
