"""Decide which incoming messages deserve a reply.

Every message is still kept as context (except pure noise that gets no reply),
but the character only answers when there is something to answer.
"""

from __future__ import annotations

import re

NOISE = "noise"  # "uhh", "lol", emoji, "???", keyboard mashing
SHORT = "short"  # "yes", "ok", "nah": meaningful only as an answer to a question
CONTENT = "content"

_NOISE_WORDS = {
    "uh", "um", "hm", "mm", "erm", "eh", "ah", "oh", "ooh", "huh", "meh", "welp",
    "lol", "lmao", "lmfao", "rofl", "kek", "xd", "bruh", "bro", "wow", "omg", "smh",
    "brb", "gg", "rip", "f", "w", "l", "lmk", "ikr", "fr", "ngl", "tbh",
}
_SHORT_WORDS = {
    "yes", "yeah", "yea", "ya", "ye", "yep", "yup", "sure", "ok", "okay", "k", "kk",
    "no", "nah", "nope", "fine", "deal", "maybe", "idk", "thanks", "thank", "you", "ty",
    "thx", "np", "alright", "aight", "right", "true", "agreed", "please", "pls",
}
_LAUGH_RE = re.compile(r"^(?:a?h+a+)+h*$|^(?:h+e+)+h*$|^(?:ja)+$|^l+o+l+$|^lmf?a+o+$|^x+d+$")
_CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _collapse(token: str) -> str:
    # "uhhhh" -> "uh", "okkk" -> "ok", "lmaooo" -> "lmao"
    return re.sub(r"(.)\1+", r"\1", token)


def _is_noise_token(token: str) -> bool:
    collapsed = _collapse(token)
    if collapsed in _NOISE_WORDS or _LAUGH_RE.match(token) or _LAUGH_RE.match(collapsed):
        return True
    if len(collapsed) <= 1 and collapsed not in _SHORT_WORDS:
        return True  # "aaa", "?", "x"
    # Keyboard mashing such as "ahhahhsaha" or "ewewew": long, but only 1-3 distinct letters.
    return len(token) >= 5 and len(set(token)) <= 3


def classify(text: str) -> str:
    text = _CUSTOM_EMOJI_RE.sub(" ", text.lower())
    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return NOISE  # only emoji, punctuation, or symbols
    if all(_is_noise_token(token) for token in tokens):
        return NOISE
    if all(_collapse(token) in _SHORT_WORDS or _is_noise_token(token) for token in tokens):
        return SHORT
    return CONTENT
