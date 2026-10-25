"""Pure helpers: transcript-with-timing prompt text, chapters, clip validation."""

from __future__ import annotations

from typing import Any

from app.llm.schemas import ChaptersLLM, ClipsLLM

CLIP_LEAD_IN = 0.2   # seconds before the first sentence (inside the sentence gap)
CLIP_TAIL = 0.5      # seconds after the last sentence


def fmt_ts(seconds: float) -> str:
    """YouTube timestamp: M:SS / MM:SS, or H:MM:SS past an hour."""
    s = int(max(0.0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def transcript_lines(segments: dict[str, Any], chapters: list[dict[str, Any]] | None = None) -> str:
    """Script text with sentence ids and timing, grouped by chapter, for the LLM."""
    chapters = chapters if chapters is not None else segments["chapters"]
    titles = {c["index"]: c.get("title") for c in chapters}
    lines: list[str] = []
    current = None
    for s in segments["segments"]:
        if s["chapter"] != current and chapters:
            current = s["chapter"]
            title = titles.get(current)
            if title:
                lines.append(f"\n## {title}")
        dur = s["end"] - s["start"]
        speaker = f" {s['speaker']}:" if s.get("speaker") else ""
        lines.append(f"[{s['id']}] ({fmt_ts(s['start'])}, {dur:.1f}s){speaker} {s['text']}")
    return "\n".join(lines).strip()


def youtube_chapters(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{time, title, start}] with the first chapter forced to 00:00 (YouTube rule)."""
    out = []
    for i, c in enumerate(chapters):
        start = 0.0 if i == 0 else c["start"]
        out.append({"time": fmt_ts(start), "title": c["title"], "start": round(start, 3)})
    return out


def chapters_text(chapters: list[dict[str, Any]]) -> str:
    return "\n".join(f"{c['time']} {c['title']}" for c in youtube_chapters(chapters))


def chapters_from_llm(result: ChaptersLLM, segments: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate LLM chapter starts and turn them into timed chapters. Raises ValueError."""
    segs = segments["segments"]
    index = {s["id"]: i for i, s in enumerate(segs)}
    errors: list[str] = []
    starts: list[int] = []
    for c in result.chapters:
        if c.start_sentence_id not in index:
            errors.append(f"không có câu '{c.start_sentence_id}'")
        else:
            starts.append(index[c.start_sentence_id])
    if errors:
        raise ValueError("; ".join(errors))
    if starts != sorted(set(starts)):
        raise ValueError("các chapter phải theo thứ tự tăng dần và không trùng câu bắt đầu")
    if starts[0] != 0:
        raise ValueError(f"chapter đầu tiên phải bắt đầu ở câu {segs[0]['id']}")
    duration = segments["duration"]
    out = []
    for k, (c, i) in enumerate(zip(result.chapters, starts)):
        start = segs[i]["start"]
        end = segs[starts[k + 1]]["start"] if k + 1 < len(starts) else duration
        if k + 1 < len(starts) and end - start < 10:
            raise ValueError(f"chapter '{c.title}' ngắn hơn 10 giây (YouTube yêu cầu ≥ 10s)")
        out.append({"index": k, "title": c.title.strip(), "start": start, "end": end})
    out[0]["start"] = 0.0
    return out


def required_clip_count(duration: float, min_clips: int, min_s: float) -> int:
    """How many clips we can demand: short episodes cannot fit min_clips windows."""
    return max(1, min(min_clips, int(duration // (min_s + 5))))


def clips_from_llm(result: ClipsLLM, segments: dict[str, Any], min_s: float, max_s: float,
                   min_count: int) -> list[dict[str, Any]]:
    """Map sentence ids to timestamps on sentence boundaries; raise ValueError listing problems."""
    segs = segments["segments"]
    index = {s["id"]: i for i, s in enumerate(segs)}
    errors: list[str] = []
    clips: list[dict[str, Any]] = []
    for n, c in enumerate(result.clips, 1):
        a, b = index.get(c.start_sentence_id), index.get(c.end_sentence_id)
        if a is None or b is None:
            errors.append(f"clip {n}: id câu không tồn tại ({c.start_sentence_id}, "
                          f"{c.end_sentence_id})")
            continue
        if b < a:
            errors.append(f"clip {n}: câu kết thúc đứng trước câu bắt đầu")
            continue
        length = segs[b]["end"] - segs[a]["start"]
        if not (min_s - 1 <= length <= max_s + 1):
            errors.append(f"clip {n}: dài {length:.1f}s, cần {min_s:g}-{max_s:g}s")
            continue
        clips.append({
            "start": round(max(0.0, segs[a]["start"] - CLIP_LEAD_IN), 3),
            "end": round(min(segments["duration"], segs[b]["end"] + CLIP_TAIL), 3),
            "start_sentence_id": c.start_sentence_id, "end_sentence_id": c.end_sentence_id,
            "title": c.title.strip(), "hook": c.hook.strip(), "reason": c.reason.strip(),
        })
    ordered = sorted(clips, key=lambda x: x["start"])
    for x, y in zip(ordered, ordered[1:]):
        if y["start"] < x["end"] - CLIP_TAIL - CLIP_LEAD_IN:
            errors.append(f"clip '{x['title']}' và '{y['title']}' bị chồng lên nhau")
    if not errors and len(clips) < min_count:
        errors.append(f"cần ít nhất {min_count} clip, mới có {len(clips)}")
    if errors:
        raise ValueError("; ".join(errors))
    return ordered
