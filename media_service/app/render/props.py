"""Build the JSON props for the Remotion compositions (pure, unit tested).

Contract: remotion/src/types.ts.
"""

from __future__ import annotations

from typing import Any

from app.config import BrandConfig
from app.subtitles.build import build_cues, words_from_json


def chapter_titles(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"index": c["index"], "title": c.get("title") or f"Phần {c['index'] + 1}",
             "start": c["start"], "end": c["end"]} for c in chapters]


def cues_with_words(words: list[dict[str, Any]], brand: BrandConfig) -> list[dict[str, Any]]:
    """SRT cues, each line carrying its timed words (for word highlighting)."""
    out = []
    for cue in build_cues(words_from_json(words), brand.subtitles.max_chars_per_line,
                          brand.subtitles.max_lines):
        remaining = list(cue.words)
        lines = []
        for line in cue.lines:
            n = len(line.split())
            lines.append([{"word": w.word, "start": w.start, "end": w.end}
                          for w in remaining[:n]])
            remaining = remaining[n:]
        out.append({"start": cue.start, "end": cue.end, "lines": lines})
    return out


def background_spec(kind: str, images: list[str], video: str | None,
                    video_duration: float) -> dict[str, Any]:
    """Fall back to the animated gradient when the chosen kind has no assets."""
    if kind == "kenburns" and not images:
        kind = "gradient"
    if kind == "loop" and not video:
        kind = "gradient"
    return {"kind": kind, "images": images if kind == "kenburns" else [],
            "video": video if kind == "loop" else None,
            "videoDurationSec": video_duration if kind == "loop" else 0}


def build_props(*, segments: dict[str, Any], words: list[dict[str, Any]],
                karaoke: list[dict[str, Any]], brand: BrandConfig, title: str,
                background: dict[str, Any], logo: str | None) -> dict[str, Any]:
    c = brand.colors
    return {
        "fps": brand.video.fps,
        "durationSec": segments["duration"],
        "audio": "audio.wav",
        "viz": "viz.wav",
        "title": title,
        "brand": {
            "channelName": brand.channel.name,
            "logo": logo,
            "colors": {"primary": c.primary, "background": c.background, "text": c.text,
                       "muted": c.muted, "gradient": c.gradient},
        },
        "background": background,
        "chapters": chapter_titles(segments["chapters"]),
        "cues": cues_with_words(words, brand),
        "karaoke": [{"start": g["start"], "end": g["end"], "text": g["text"],
                     "words": g["words"]} for g in karaoke],
    }


def heuristic_clips(segments: dict[str, Any], min_s: float, max_s: float,
                    max_clips: int) -> list[dict[str, Any]]:
    """Fallback Shorts selection until the LLM picks clips: sentence-aligned windows.

    Windows start at chapter starts first, then at any later sentence, never overlap,
    and always begin and end on sentence boundaries.
    """
    segs = segments["segments"]
    chapter_first = {s["id"] for i, s in enumerate(segs)
                     if i == 0 or s["chapter"] != segs[i - 1]["chapter"]}
    order = [i for i, s in enumerate(segs) if s["id"] in chapter_first] + \
            [i for i, s in enumerate(segs) if s["id"] not in chapter_first]
    taken: list[tuple[float, float]] = []
    clips: list[dict[str, Any]] = []
    for i in order:
        start = segs[i]["start"]
        end_idx = None
        for j in range(i, len(segs)):
            length = segs[j]["end"] - start
            if length > max_s:
                break
            if length >= min_s:
                end_idx = j
                if segs[j]["text"].rstrip().endswith((".", "!", "?", "…")):
                    break
        if end_idx is None:
            continue
        end = segs[end_idx]["end"]
        if any(start < b and end > a for a, b in taken):
            continue
        taken.append((start, end))
        clips.append({"start": round(max(0.0, start - 0.2), 3), "end": round(end + 0.5, 3),
                      "hook": segs[i]["text"][:80], "reason": "heuristic"})
        if len(clips) >= max_clips:
            break
    return sorted(clips, key=lambda c: c["start"])
