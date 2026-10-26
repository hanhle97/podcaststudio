"""Text preparation before TTS: pronunciation mapping and request-sized chunks."""

from __future__ import annotations

import re


def apply_pronunciation(text: str, mapping: dict[str, str]) -> str:
    """Replace whole-word, case-sensitive dictionary entries (longest first)."""
    if not mapping:
        return text
    keys = sorted(mapping, key=len, reverse=True)
    pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(k) for k in keys) + r")(?!\w)")
    return pattern.sub(lambda m: mapping[m.group(1)], text)


QUOTES = "\"“”„«»"


def speakable(text: str, mapping: dict[str, str]) -> str:
    """What the voice is asked to say: pronunciation applied, quote marks removed.

    Quoted speech makes the model skip the words around it, and quotes are silent anyway.
    """
    text = apply_pronunciation(text, mapping)
    return " ".join(text.translate({ord(q): None for q in QUOTES}).split())


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split one sentence into pieces <= max_chars, preferring , ; : then spaces.

    Punctuation stays attached to the piece it ends so intonation is natural.
    """
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text]
    window = text[: max_chars + 1]
    cut = -1
    for pattern in (r"[,;:–—]\s", r"\s"):
        matches = [m.end() for m in re.finditer(pattern, window) if m.end() > max_chars // 3]
        if matches:
            cut = matches[-1]
            break
    if cut <= 0:
        cut = max_chars
    head, tail = text[:cut].strip(), text[cut:].strip()
    return [head] + (chunk_text(tail, max_chars) if tail else [])
