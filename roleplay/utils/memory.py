"""Long-term memory: short, important facts extracted from old messages.

Small local models are bad at free-form summaries (they tend to continue the
transcript instead), so extraction uses Ollama's JSON-schema structured output
and every fact is validated before it is stored.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from typing import Optional

from utils.ollama import OllamaClient, OllamaError

log = logging.getLogger(__name__)

Fact = tuple[str, str]  # (about, content)

MAX_ABOUT_CHARS = 60
MAX_FACT_CHARS = 200
MIN_FACT_CHARS = 8

FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "fact": {"type": "string"},
                },
                "required": ["subject", "fact"],
            },
        }
    },
    "required": ["facts"],
}

# Facts about the bot itself ("it has no context", "it's a Qwen model") are noise.
_META_RE = re.compile(
    r"\b(a\.?i\.?|bots?|chat ?bots?|language models?|llms?|qwen\w*|ollama|prompts?|tokens?|"
    r"context window|discord bot|transcript|the character|is mentioned|was mentioned)\b",
    re.IGNORECASE,
)


def transcript(rows: list[dict], character_name: str) -> str:
    lines = []
    for row in rows:
        content = " ".join((row.get("content") or "").split())
        if not content:
            continue
        speaker = character_name if row.get("role") == "assistant" else (row.get("author_name") or "Someone")
        lines.append(f"[{speaker}] {content[:500]}")
    return "\n".join(lines)


def _format_facts(facts: list[Fact]) -> str:
    if not facts:
        return "(none)"
    return "\n".join(f"- {about}: {content}" if about else f"- {content}" for about, content in facts)


def build_extract_messages(character_name: str, known: list[Fact], rows: list[dict]) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                f"You are a careful note-taker for a roleplay. The character you take notes for is "
                f"{character_name}. Each transcript line starts with the speaker's name in [brackets]; "
                "the text after it was said or done BY that speaker. Only record things clearly stated "
                "in the transcript. Never guess."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Notes so far:\n{_format_facts(known)}\n\n"
                f"Transcript:\n{transcript(rows, character_name)}\n\n"
                "Write NEW important facts from this transcript that are not already in the notes: "
                "who people are, names and nicknames, relationships, promises or debts, goals, "
                "important items or places, and major story events. Ignore jokes, insults, spam, "
                "small talk, and talk about the bot itself.\n"
                "- subject: just the name of the person or thing, 1-3 words (for example \"Ram\").\n"
                "- fact: one short, plain sentence stating the fact (for example \"Ram owes Quint "
                "ten gold.\").\n"
                "Return an empty list if nothing important happened. At most 8 facts."
            ),
        },
    ]


def build_consolidate_messages(character_name: str, facts: list[Fact], limit: int) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                f"You maintain long-term memory notes for a roleplay featuring {character_name}. "
                "Only use information already in the notes."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Notes:\n{_format_facts(facts)}\n\n"
                f"Rewrite these as at most {limit} notes. Merge duplicates and notes about the same "
                "thing, keep newer details when notes conflict, and drop the least important ones.\n"
                "- subject: just the name of the person or thing, 1-3 words.\n"
                "- fact: one short, plain sentence."
            ),
        },
    ]


def parse_facts(raw: str) -> Optional[list[Fact]]:
    """Return validated facts, or None if the output is not usable at all."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    items = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    facts: list[Fact] = []
    for item in items:
        if isinstance(item, dict):
            about = item.get("subject", item.get("about", ""))
            content = item.get("fact", "")
        elif isinstance(item, str):
            about, content = "", item
        else:
            continue
        fact = clean_fact(str(about), str(content))
        if fact:
            facts.append(fact)
    return dedupe(facts)


def clean_fact(about: str, content: str) -> Optional[Fact]:
    about = " ".join(about.replace("*", "").split()).strip(" -:.")
    content = " ".join(content.replace("*", "").split()).strip(" -")
    if len(about) > 40 or len(about.split()) > 4:
        # The model put a sentence in the subject; it is usually the real fact.
        if len(about) >= MIN_FACT_CHARS and (not content or _META_RE.search(content)):
            content = about
        about = ""
    about = about[:MAX_ABOUT_CHARS]
    if about and content.lower().startswith(about.lower() + ":"):
        content = content[len(about) + 1 :].strip()
    if not (MIN_FACT_CHARS <= len(content) <= MAX_FACT_CHARS):
        return None
    if _META_RE.search(content) or _META_RE.search(about):
        return None
    if content[-1] not in ".!?":
        content += "."
    return about, content


_COMMON_CAPITALIZED = {
    "the", "a", "an", "he", "she", "they", "it", "his", "her", "their", "its", "i", "we", "you",
    "this", "that", "these", "those", "there", "when", "after", "before", "while", "and", "but",
    "if", "in", "on", "at", "of", "to", "for", "with", "both", "each", "all", "some",
}
_CAPITALIZED_RE = re.compile(r"\b[A-Z][A-Za-z'’-]{1,}\b")


def grounded(fact: Fact, source: str) -> bool:
    """Reject facts that name people, places, or things never mentioned in the source."""
    haystack = source.lower()
    for word in _CAPITALIZED_RE.findall(f"{fact[0]} {fact[1]}"):
        word = re.sub(r"['’]s$", "", word)
        if word.lower() in _COMMON_CAPITALIZED:
            continue
        if word.lower() not in haystack:
            return False
    return True


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower())


def _similar(a: Fact, b: Fact) -> bool:
    ratio = difflib.SequenceMatcher(None, _normal(f"{a[0]} {a[1]}"), _normal(f"{b[0]} {b[1]}")).ratio()
    if ratio >= 0.75:
        return True
    return difflib.SequenceMatcher(None, _normal(a[1]), _normal(b[1])).ratio() >= 0.75


def dedupe(facts: list[Fact]) -> list[Fact]:
    result: list[Fact] = []
    for fact in facts:
        if not any(_similar(fact, existing) for existing in result):
            result.append(fact)
    return result


def merge_facts(existing: list[Fact], new: list[Fact]) -> list[Fact]:
    """Add new facts; a new fact replaces an older near-duplicate."""
    merged = list(existing)
    for fact in new:
        for index, old in enumerate(merged):
            if _similar(fact, old):
                merged[index] = fact
                break
        else:
            merged.append(fact)
    return merged


async def _ask(ollama: OllamaClient, messages: list[dict], chat_kwargs: dict) -> Optional[list[Fact]]:
    try:
        raw = await ollama.chat(messages=messages, format=FACTS_SCHEMA, **chat_kwargs)
    except OllamaError as exc:
        log.warning("Memory request failed: %s", exc)
        return None
    facts = parse_facts(raw)
    if facts is None:
        log.warning("Memory request returned unusable output: %.200r", raw)
    return facts


async def update_memory(
    ollama: OllamaClient,
    *,
    character_name: str,
    pinned: list[Fact],
    auto: list[Fact],
    rows: list[dict],
    max_facts: int,
    chat_kwargs: dict,
) -> Optional[list[Fact]]:
    """Return the new list of automatic facts, or None if extraction failed."""
    new = await _ask(ollama, build_extract_messages(character_name, pinned + auto, rows), chat_kwargs)
    if new is None:
        return None

    source = "\n".join(
        [character_name, transcript(rows, character_name), _format_facts(pinned + auto)]
    )
    rejected = [fact for fact in new if not grounded(fact, source)]
    if rejected:
        log.info("Dropped %d ungrounded memory fact(s): %s", len(rejected), rejected)
    # Don't store invented details or what the owner already pinned.
    new = [
        fact
        for fact in new
        if fact not in rejected and not any(_similar(fact, p) for p in pinned)
    ]
    merged = merge_facts(auto, new)
    if len(merged) <= max_facts:
        return merged

    consolidated = await _ask(
        ollama, build_consolidate_messages(character_name, merged, max_facts), chat_kwargs
    )
    if consolidated:
        merged_source = "\n".join([character_name, _format_facts(pinned + merged)])
        consolidated = [fact for fact in consolidated if grounded(fact, merged_source)]
    if consolidated and len(consolidated) >= min(3, max_facts):
        return consolidated[:max_facts]
    # Fall back to keeping the most recent facts.
    return merged[-max_facts:]
