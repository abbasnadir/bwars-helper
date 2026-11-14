from __future__ import annotations

import re
from typing import Iterable, Optional

DISCORD_MESSAGE_LIMIT = 2000

# A single-asterisk *action* (not **bold**), on one line.
_ACTION_RE = re.compile(r"(?<!\*)\*(?![*\s])([^*\n]{1,600}?)(?<!\s)\*(?!\*)")
_STRAY_ASTERISK_RE = re.compile(r"(?<!\*)\*(?!\*)")
_SENTENCE_END_RE = re.compile(r"[.!?…](?:[\"'”’)\]]+)?(?=[\s*]|$)")
_COMPLETE_ENDINGS = tuple(".!?…\"'”’)]*~>")
# Out-of-character commentary that small models sometimes add.
_META_RE = re.compile(
    r"\b(?:the user'?s?|in[- ]character|persona|role-?play(?:ing)?|language model|as an ai|"
    r"usernames?|chat guest|responds?|responding|maintaining)\b",
    re.IGNORECASE,
)


def build_system_prompt(
    config: dict,
    memories: Optional[list[dict]] = None,
    *,
    max_actions: int = 1,
    memory_chars: int = 1500,
) -> str:
    name = config["character_name"].strip()
    persona = config["persona"].strip()
    scenario = (config.get("scenario") or "").strip()
    style = (config.get("style") or "").strip()

    if max_actions <= 0:
        action_rule = "- Do not use *asterisk* actions or narration; reply with speech only."
    else:
        action_rule = (
            "- Use at most one brief action in *asterisks*, and only sometimes. Most replies need none."
        )

    parts = [
        f"You are {name}, chatting with people in a Discord roleplay channel. "
        f"Stay in character as {name} at all times.",
        f"About {name}:\n{persona}",
    ]
    if scenario:
        parts.append(f"Setting:\n{scenario}")
    if style:
        parts.append(f"Style notes:\n{style}")

    memory_block = format_memories(memories or [], memory_chars)
    if memory_block:
        parts.append(f"Things {name} remembers:\n{memory_block}")

    parts.append(
        "How to reply:\n"
        f"- Speak as {name} in first person, mostly in plain spoken dialogue.\n"
        "- Keep it short: 1-3 sentences.\n"
        f"{action_rule}\n"
        "- Respond directly to the latest message. Use the speaker's name when it helps.\n"
        f"- Only speak for {name}. Never write lines, actions, or thoughts for anyone else.\n"
        "- Never narrate the chat or talk about AI, models, or bots. If asked, brush it off in character.\n"
        "- Never start with your own name. Never mass-mention @everyone or @here."
    )
    return "\n\n".join(parts)


def format_memories(memories: list[dict], limit: int = 1500) -> str:
    """Render pinned memories first, then automatic ones, within a character budget."""
    ordered = sorted(memories, key=lambda row: (row.get("kind") != "pinned", row.get("id", 0)))
    lines: list[str] = []
    used = 0
    for row in ordered:
        content = (row.get("content") or "").strip()
        if not content:
            continue
        about = (row.get("about") or "").strip()
        line = f"- {about}: {content}" if about else f"- {content}"
        if lines and used + len(line) > limit:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def history_to_messages(
    config: dict,
    memories: Optional[list[dict]],
    history: list[dict],
    *,
    max_chars: int = 4000,
    max_actions: int = 1,
    memory_chars: int = 1500,
    reply_to: Optional[list[str]] = None,
) -> list[dict]:
    messages = [
        {
            "role": "system",
            "content": build_system_prompt(
                config, memories, max_actions=max_actions, memory_chars=memory_chars
            ),
        }
    ]

    # Keep the prompt small so local CPU models do not spend seconds processing
    # context. Older messages are dropped first; long-term memory keeps their gist.
    trimmed: list[dict] = []
    chars = 0
    for row in reversed(history):
        content = (row.get("content") or "").strip()
        if not content:
            continue
        length = len(content)
        if trimmed and chars + length > max_chars:
            break

        if row.get("role") == "assistant":
            item = {"role": "assistant", "content": content}
        else:
            author = (row.get("author_name") or "Participant").strip()
            item = {"role": "user", "content": f"{author}: {content}"}

        trimmed.append(item)
        chars += length

    messages.extend(reversed(trimmed))

    # A short, explicit note right next to the newest message stops small models
    # from mixing up who they are talking to. It is never stored.
    if reply_to and len(messages) > 1 and messages[-1]["role"] == "user":
        if len(reply_to) == 1:
            note = f"({reply_to[0]} is talking to you.)"
        else:
            note = f"({', '.join(reply_to[:-1])} and {reply_to[-1]} are talking to you.)"
        messages[-1] = {"role": "user", "content": f"{messages[-1]['content']}\n\n{note}"}
    return messages


def limit_actions(text: str, max_actions: int = 1, max_action_chars: int = 120) -> str:
    """Keep at most ``max_actions`` short *actions* and drop the rest."""
    kept: list[str] = []

    def keep_or_drop(match: re.Match) -> str:
        inner = match.group(1).strip()
        if inner and len(kept) < max_actions and len(inner) <= max_action_chars:
            kept.append(f"*{inner}*")
            return f"\x00{len(kept) - 1}\x00"
        return " "

    result = _ACTION_RE.sub(keep_or_drop, text)
    # Unpaired asterisks left over from malformed actions.
    result = _STRAY_ASTERISK_RE.sub("", result)
    result = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], result)
    result = _tidy(result)

    if result:
        return result
    # The whole reply was narration. Keep its words rather than sending nothing.
    return _tidy(_STRAY_ASTERISK_RE.sub("", text))


def limit_sentences(text: str, max_sentences: int = 3) -> str:
    """Keep the first ``max_sentences`` sentences, never cutting inside an *action*."""
    if max_sentences <= 0:
        return text
    actions = [match.span() for match in _ACTION_RE.finditer(text)]

    def inside_action(pos: int) -> bool:
        return any(start < pos < end for start, end in actions)

    count = 0
    for match in _SENTENCE_END_RE.finditer(text):
        end = match.end()
        # A closing action asterisk right after the punctuation belongs to this sentence.
        if end < len(text) and text[end] == "*" and any(e == end + 1 for _, e in actions):
            end += 1
        if inside_action(end):
            continue
        count += 1
        if count == max_sentences:
            return text[:end].rstrip()
    return text


def speaks_as_other(text: str, character_name: str, other_names: Iterable[str]) -> bool:
    """True when the reply opens with another participant's ``Name:`` label."""
    names = [n for n in other_names if n and n.strip().lower() != character_name.strip().lower()]
    pattern = _speaker_label(names)
    return bool(pattern and pattern.match(text))


def is_action_only(text: str) -> bool:
    """True when a reply is pure *narration* with no spoken words."""
    spoken = _ACTION_RE.sub("", text)
    return not re.search(r"[A-Za-z0-9]", spoken)


def is_meta(text: str) -> bool:
    """True when the reply talks about the roleplay instead of being part of it."""
    return bool(_META_RE.search(text))


def is_narration(text: str, character_name: str) -> bool:
    """True for third-person narration such as "Quint pats RAM on the shoulder."."""
    if is_action_only(text):
        return True
    spoken = _ACTION_RE.sub("", text).strip()
    name = re.escape(character_name.strip())
    # "Quint pats...", "Quint leaned..."
    if re.match(rf"^{name}\s+(?!here\b|is\b|was\b)[a-z]+(?:s|ed)\b", spoken, re.IGNORECASE):
        return True
    # "Quint's grip tightens..." but not "Quint's not gonna..." or "Quint's the name".
    return bool(
        re.match(
            rf"^{name}['’]s\s+(?!not\b|gonna\b|got\b|the\b|a\b|no\b|never\b|been\b|here\b|done\b)[a-z]+\b",
            spoken,
            re.IGNORECASE,
        )
    )


def trim_incomplete(text: str) -> str:
    """Drop a trailing sentence fragment left when generation hit the token cap."""
    text = text.rstrip()
    if not text or text.endswith(_COMPLETE_ENDINGS):
        return text
    ends = list(_SENTENCE_END_RE.finditer(text))
    if not ends:
        return text.rstrip(" ,;:-") + "…"
    return text[: ends[-1].end()].rstrip()


def clean_reply(
    reply: str,
    character_name: str,
    other_names: Iterable[str] = (),
    *,
    truncated: bool = False,
    max_actions: int = 1,
    max_sentences: int = 3,
) -> str:
    """Tidy a raw model reply into something that reads like a single chat message."""
    original = reply.strip()
    if not original:
        return original

    text = _strip_speaker_label(original, character_name)
    text = _cut_other_speakers(text, character_name, other_names)

    # Some models wrap the whole reply in quotes.
    if len(text) > 2 and text[0] == text[-1] == '"' and text.count('"') == 2:
        text = text[1:-1].strip()

    # Drop *actions* that are really commentary, e.g. "*Responding as Quint.*".
    without_meta = _ACTION_RE.sub(lambda m: " " if _META_RE.search(m.group(1)) else m.group(0), text)
    if re.search(r"[A-Za-z0-9]", without_meta):
        text = without_meta
    text = limit_actions(text, max_actions=max_actions)
    if truncated:
        text = trim_incomplete(text)
    text = limit_sentences(text, max_sentences)
    text = _tidy(text)
    return text or _tidy(original)


def cap_reply(text: str, limit: int) -> str:
    """Cut a reply down to ``limit`` characters at a natural boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text

    window = text[:limit]
    cut = max(
        window.rfind(". "),
        window.rfind("! "),
        window.rfind("? "),
        window.rfind("\n"),
    )
    if cut >= limit // 2:
        return window[: cut + 1].rstrip()

    cut = window.rfind(" ")
    if cut >= limit // 2:
        return window[:cut].rstrip() + "…"
    return window[: limit - 1].rstrip() + "…"


def split_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split text into Discord-safe chunks, preferring paragraph/word boundaries."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining.strip())
            break

        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at < limit // 2:
            split_at = window.rfind("\n")
        if split_at < limit // 2:
            split_at = window.rfind(" ")
        if split_at < limit // 2:
            split_at = limit

        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()

    return [chunk for chunk in chunks if chunk]


def _speaker_label(names: Iterable[str]) -> Optional[re.Pattern]:
    escaped = sorted({re.escape(n.strip()) for n in names if n and n.strip()}, key=len, reverse=True)
    if not escaped:
        return None
    return re.compile(
        rf"^\s*(?:\*\*|__|\*)?\s*(?:{'|'.join(escaped)})\s*(?:\*\*|__|\*)?\s*:\s*(?:\*\*|__)?\s*",
        re.IGNORECASE,
    )


def _strip_speaker_label(text: str, character_name: str) -> str:
    pattern = _speaker_label([character_name])
    if pattern is None:
        return text
    return pattern.sub("", text, count=1).strip() or text


def _cut_other_speakers(text: str, character_name: str, other_names: Iterable[str]) -> str:
    """Stop at the first line where the model starts writing for someone else."""
    names = [n for n in other_names if n and n.strip().lower() != character_name.strip().lower()]
    pattern = _speaker_label([*names, character_name])
    if pattern is None:
        return text
    lines = text.splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if pattern.match(line):
            return "\n".join(lines[:index]).strip()
    return text


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.!?;:])", r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
