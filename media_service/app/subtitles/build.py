"""Subtitle builders (pure): SRT cues, karaoke word groups, ASS document.

Cues never span two sentences, so they never cross a chapter boundary either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby

from app.config import BrandConfig

CLAUSE_END = (",", ";", ":", "–", "—")
LINE_CLAUSE_BONUS = 10
SENTENCE_END = (".", "!", "?", "…")


@dataclass
class Word:
    word: str
    start: float
    end: float
    sentence_id: str
    chapter: int


@dataclass
class Cue:
    start: float
    end: float
    lines: list[str]
    words: list[Word] = field(default_factory=list)


def words_from_json(items: list[dict]) -> list[Word]:
    return [Word(w["word"], w["start"], w["end"], w["sentence_id"], w["chapter"]) for w in items]


def by_sentence(words: list[Word]) -> list[list[Word]]:
    return [list(g) for _, g in groupby(words, key=lambda w: w.sentence_id)]


def ends_clause(word: str) -> bool:
    return word.rstrip("\"'”’)»").endswith(CLAUSE_END + SENTENCE_END)


# --- SRT ---------------------------------------------------------------------------


def wrap(words: list[str], max_chars: int, max_lines: int) -> list[str] | None:
    """Lay words out in <= max_lines lines of <= max_chars; None if impossible."""
    text = " ".join(words)
    if len(text) <= max_chars or len(words) == 1:
        return [text]
    if max_lines < 2:
        return None
    best: tuple[tuple[int, int], list[str]] | None = None
    for i in range(1, len(words)):
        first, second = " ".join(words[:i]), " ".join(words[i:])
        if len(first) > max_chars:
            break
        if len(second) > max_chars:
            continue
        rest = wrap(words[i:], max_chars, max_lines - 1) if max_lines > 2 else [second]
        if rest is None:
            continue
        # balanced lines, but a break after punctuation is worth ~10 chars of imbalance
        longest = max(len(first), *(len(r) for r in rest))
        score = (longest + (0 if ends_clause(words[i - 1]) else LINE_CLAUSE_BONUS), longest)
        if best is None or score < best[0]:
            best = (score, [first, *rest])
    return best[1] if best else None


CLAUSE_PENALTY = 2500  # cost of a cue that does not end at punctuation (≈ 50 chars²)
CUE_COST = 2000  # fixed cost per cue: an extra cue must buy a clean clause break


def split_sentence(words: list[Word], max_chars: int, max_lines: int) -> list[list[Word]]:
    """Split one sentence into balanced cues, cut at clauses where possible.

    Dynamic programming over cut points: each cue must fit `wrap`; minimise
    sum(len²) + CUE_COST per cue + CLAUSE_PENALTY per cue ending mid-clause.
    A sentence that fits one cue stays one cue unless a split is clearly better.
    """
    n = len(words)
    texts = [w.word for w in words]
    fits = [[False] * (n + 1) for _ in range(n + 1)]
    for i in range(n):
        for j in range(i + 1, n + 1):
            if wrap(texts[i:j], max_chars, max_lines) is None:
                break
            fits[i][j] = True

    INF = float("inf")
    best = [INF] * (n + 1)  # best[j] = cost of words[:j]
    prev = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for i in range(j):
            if not fits[i][j] or best[i] == INF:
                continue
            length = len(" ".join(texts[i:j]))
            penalty = 0 if j == n or ends_clause(texts[j - 1]) else CLAUSE_PENALTY
            cand = best[i] + length ** 2 + CUE_COST + penalty
            if cand < best[j]:
                best[j], prev[j] = cand, i
    chunks: list[list[Word]] = []
    j = n
    while j > 0:
        chunks.append(words[prev[j]:j])
        j = prev[j]
    return chunks[::-1]


def build_cues(words: list[Word], max_chars: int = 42, max_lines: int = 2,
               hold: float = 0.4, min_duration: float = 0.8) -> list[Cue]:
    cues: list[Cue] = []
    for sentence in by_sentence(words):
        for chunk in split_sentence(sentence, max_chars, max_lines):
            lines = wrap([w.word for w in chunk], max_chars, max_lines) or \
                [" ".join(w.word for w in chunk)]
            cues.append(Cue(chunk[0].start, chunk[-1].end, lines, chunk))
    # let each cue linger a little, without overlapping the next one
    for i, cue in enumerate(cues):
        limit = cues[i + 1].start - 0.05 if i + 1 < len(cues) else cue.end + hold
        cue.end = round(max(cue.end, min(cue.end + hold, limit),
                            min(cue.start + min_duration, limit)), 3)
    return cues


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = [f"{i}\n{srt_time(c.start)} --> {srt_time(c.end)}\n" + "\n".join(c.lines)
              for i, c in enumerate(cues, 1)]
    return "\n\n".join(blocks) + "\n"


# --- karaoke -----------------------------------------------------------------------


def karaoke_groups(words: list[Word], min_words: int = 2, max_words: int = 4,
                   max_chars: int = 24) -> list[list[Word]]:
    """2-4 word groups per sentence, cut at punctuation, no orphan single words."""
    groups: list[list[Word]] = []
    for sentence in by_sentence(words):
        i, n = 0, len(sentence)
        while i < n:
            remaining = n - i
            size = min(max_words, remaining)
            # cut early after punctuation if both sides keep >= min_words
            for k in range(min_words, size):
                if ends_clause(sentence[i + k - 1].word) and remaining - k >= min_words:
                    size = k
                    break
            # keep the text short enough for a big centred caption
            while size > min_words and len(" ".join(w.word for w in sentence[i:i + size])) \
                    > max_chars:
                size -= 1
            # never leave a single orphan word at the end of the sentence
            if remaining - size == 1 and size > min_words:
                size -= 1
            elif 0 < remaining - size < min_words and size + (remaining - size) <= max_words:
                size = remaining
            groups.append(sentence[i:i + size])
            i += size
    return groups


def karaoke_json(groups: list[list[Word]]) -> list[dict]:
    return [{
        "start": g[0].start, "end": g[-1].end, "sentence_id": g[0].sentence_id,
        "chapter": g[0].chapter, "text": " ".join(w.word for w in g),
        "words": [{"word": w.word, "start": w.start, "end": w.end} for w in g],
    } for g in groups]


# --- ASS ---------------------------------------------------------------------------


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def to_ass(groups: list[list[Word]], brand: BrandConfig, width: int = 1080,
           height: int = 1920) -> str:
    """Karaoke ASS for vertical Shorts: word turns primary colour when spoken (\\k)."""
    c = brand.colors
    header = "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Karaoke,{brand.fonts.family},96,{ass_color(c.primary)},{ass_color(c.text)},"
        f"{ass_color(c.background, 0x20)},{ass_color('#000000', 0x80)},-1,0,0,0,100,100,0,0,"
        "1,6,3,5,80,80,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])
    events = []
    for g in groups:
        parts = []
        for k, w in enumerate(g):
            nxt = g[k + 1].start if k + 1 < len(g) else w.end
            dur_cs = max(1, int(round((nxt - w.start) * 100)))
            parts.append(f"{{\\k{dur_cs}}}{ass_escape(w.word)}")
        events.append(f"Dialogue: 0,{ass_time(g[0].start)},{ass_time(g[-1].end)},Karaoke,,"
                      f"0,0,0,,{' '.join(parts)}")
    return header + "\n" + "\n".join(events) + "\n"
