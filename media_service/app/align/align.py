"""Map whisper word timings onto the exact words of the script (pure, unit tested).

The displayed words are always the script's own words. Pronunciation entries may
turn one display word into several spoken tokens ("n8n" -> "en ây en"); timings
are aligned on spoken tokens and merged back onto display words.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.align.whisper import WhisperWord
from app.tts.text import apply_pronunciation


@dataclass
class TimedWord:
    word: str
    start: float
    end: float
    matched: bool


def normalize_token(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in ("L", "N"))


def spoken_tokens(display: list[str], pronunciation: dict[str, str]) -> list[tuple[str, int]]:
    """(normalized spoken token, index of the display word it belongs to)."""
    tokens: list[tuple[str, int]] = []
    for i, word in enumerate(display):
        for part in apply_pronunciation(word, pronunciation).split():
            norm = normalize_token(part)
            if norm:
                tokens.append((norm, i))
    return tokens


def _spread(weights: list[int], start: float, end: float) -> list[tuple[float, float]]:
    """Split [start, end] proportionally to `weights` (character counts)."""
    total = sum(max(1, w) for w in weights)
    spans, t = [], start
    for w in weights:
        d = (end - start) * max(1, w) / total
        spans.append((t, t + d))
        t += d
    return spans


def align_tokens(tokens: list[str], whisper: list[WhisperWord],
                 duration: float) -> list[tuple[float, float, bool]]:
    """Timing (start, end, matched) for every script token within [0, duration]."""
    heard = [(normalize_token(w.word), w) for w in whisper]
    heard = [(n, w) for n, w in heard if n]
    times: list[tuple[float, float, bool] | None] = [None] * len(tokens)
    matcher = SequenceMatcher(None, tokens, [n for n, _ in heard], autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                w = heard[j1 + k][1]
                times[i1 + k] = (w.start, w.end, True)
        elif op == "replace":
            start, end = heard[j1][1].start, heard[j2 - 1][1].end
            for k, (s, e) in enumerate(_spread([len(t) for t in tokens[i1:i2]], start, end)):
                times[i1 + k] = (s, e, False)

    # fill tokens whisper did not hear by interpolating in the surrounding gap
    i = 0
    while i < len(tokens):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(tokens) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else 0.0
        hi = times[j][0] if j < len(tokens) else duration
        hi = max(hi, lo)
        for k, (s, e) in enumerate(_spread([len(t) for t in tokens[i:j]], lo, hi)):
            times[i + k] = (s, e, False)
        i = j

    # monotonic and inside the clip
    out: list[tuple[float, float, bool]] = []
    prev_start = 0.0
    for s, e, m in times:  # type: ignore[misc]
        s = min(max(s, prev_start, 0.0), duration)
        e = min(max(e, s), duration)
        out.append((s, e, m))
        prev_start = s
    return out


def align_sentence(text: str, whisper: list[WhisperWord], duration: float,
                   pronunciation: dict[str, str]) -> list[TimedWord]:
    """Timed display words of one sentence; times relative to the sentence clip."""
    display = text.split()
    tokens = spoken_tokens(display, pronunciation)
    timed = align_tokens([t for t, _ in tokens], whisper, duration)

    result: list[TimedWord] = []
    for i, word in enumerate(display):
        mine = [timed[k] for k, (_, d) in enumerate(tokens) if d == i]
        if mine:
            result.append(TimedWord(word, mine[0][0], mine[-1][1], all(m for *_, m in mine)))
        else:  # punctuation-only word: sticks to its neighbour
            t = result[-1].end if result else 0.0
            result.append(TimedWord(word, t, t, False))
    return result
