"""Assemble per-sentence TTS clips into one voice track and segments.json.

segments.json is the primary timing source for everything downstream
(alignment, subtitles, chapters, clip selection, video).
"""

from __future__ import annotations

import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from app.config import AudioCfg
from app.script import MusicUp, Pause, Script, Sentence


@dataclass
class Segment:
    id: str
    text: str
    speech_text: str
    chapter: int
    chapter_title: str | None
    speaker: str | None
    paragraph: int
    start: float
    end: float


@dataclass
class Break:
    kind: str  # "pause" | "music_up" | "chapter"
    start: float
    end: float


@dataclass
class ChapterSpan:
    index: int
    title: str | None
    start: float
    end: float


@dataclass
class Timeline:
    sample_rate: int
    duration: float
    segments: list[Segment] = field(default_factory=list)
    chapters: list[ChapterSpan] = field(default_factory=list)
    breaks: list[Break] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    def shifted(self, offset: float, total: float) -> Timeline:
        """Same timeline moved by `offset` (e.g. an intro), ending at `total` seconds."""
        def mv(x: float) -> float:
            return round(x + offset, 3)

        out = Timeline(sample_rate=self.sample_rate, duration=round(total, 3))
        out.segments = [Segment(**{**asdict(s), "start": mv(s.start), "end": mv(s.end)})
                        for s in self.segments]
        out.breaks = [Break(b.kind, mv(b.start), mv(b.end)) for b in self.breaks]
        out.chapters = [ChapterSpan(c.index, c.title, mv(c.start), mv(c.end))
                        for c in self.chapters]
        if out.chapters:
            out.chapters[0].start = 0.0 if offset > 0 else out.chapters[0].start
            out.chapters[-1].end = out.duration
        return out


def compute_timeline(script: Script, durations: dict[str, float], cfg: AudioCfg,
                     speech_texts: dict[str, str] | None = None,
                     glued: set[str] | None = None) -> Timeline:
    """Place every sentence on the timeline.

    Gaps: sentence gap within a paragraph, paragraph gap across paragraphs, chapter
    gap between chapters; [pause]/[music up] add their own duration on top.
    Sentences in `glued` were read in the same take as the previous sentence and cut
    out of it, so they follow it with no added gap (the voice's own pause is kept).
    """
    glued = glued or set()
    gaps = cfg.gaps_ms
    t = cfg.lead_in_ms / 1000
    tl = Timeline(sample_rate=cfg.sample_rate, duration=0.0)
    speech_texts = speech_texts or {}

    for ci, chapter in enumerate(script.chapters):
        if ci > 0:
            tl.breaks.append(Break("chapter", t, t + gaps.chapter / 1000))
            t += gaps.chapter / 1000
            tl.chapters[-1].end = t
        tl.chapters.append(ChapterSpan(chapter.index, chapter.title, t, t))
        prev: Sentence | None = None
        for item in chapter.items:
            if isinstance(item, (Pause, MusicUp)):
                kind = "pause" if isinstance(item, Pause) else "music_up"
                tl.breaks.append(Break(kind, t, t + item.seconds))
                t += item.seconds
                continue
            assert isinstance(item, Sentence)
            if prev is not None and item.id not in glued:
                same = prev.paragraph == item.paragraph
                t += (gaps.sentence if same else gaps.paragraph) / 1000
            dur = durations[item.id]
            tl.segments.append(Segment(
                id=item.id, text=item.text, speech_text=speech_texts.get(item.id, item.text),
                chapter=chapter.index, chapter_title=chapter.title, speaker=item.speaker,
                paragraph=item.paragraph, start=round(t, 3), end=round(t + dur, 3),
            ))
            t += dur
            prev = item
    t += cfg.tail_ms / 1000
    tl.duration = round(t, 3)
    if tl.chapters:
        tl.chapters[-1].end = tl.duration
    for c in tl.chapters:
        c.start, c.end = round(c.start, 3), round(c.end, 3)
    for b in tl.breaks:
        b.start, b.end = round(b.start, 3), round(b.end, 3)
    return tl


def read_wav_mono16(path: Path, expected_rate: int) -> np.ndarray:
    with wave.open(str(path)) as w:
        if w.getframerate() != expected_rate or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: cần WAV {expected_rate} Hz mono 16-bit, nhận "
                             f"{w.getframerate()} Hz {w.getnchannels()} kênh "
                             f"{w.getsampwidth() * 8}-bit")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def write_wav_mono16(path: Path, samples: np.ndarray, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())


def render_voice_track(tl: Timeline, clip_paths: dict[str, Path], out: Path) -> None:
    sr = tl.sample_rate
    track = np.zeros(int(round(tl.duration * sr)) + 1, dtype=np.int16)
    for seg in tl.segments:
        clip = read_wav_mono16(clip_paths[seg.id], sr)
        start = int(round(seg.start * sr))
        end = min(start + len(clip), len(track))
        track[start:end] = clip[: end - start]
    write_wav_mono16(out, track, sr)
