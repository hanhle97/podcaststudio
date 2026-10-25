"""Subtitles step: subtitles.srt, subtitles.ass, karaoke.json from words.json."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.episodes import write_json
from app.jobs import StepError
from app.subtitles.build import build_cues, karaoke_groups, karaoke_json, to_ass, to_srt, \
    words_from_json

if TYPE_CHECKING:
    from app.pipeline import StepEnv, StepProgress


def run_subtitles(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    root = env.paths.root
    words_file = root / "words.json"
    if not words_file.exists():
        raise StepError("subtitles", "Chưa có words.json. Hãy chạy bước align trước.")
    words = words_from_json(json.loads(words_file.read_text(encoding="utf-8")))
    cfg = env.brand.subtitles

    cues = build_cues(words, cfg.max_chars_per_line, cfg.max_lines)
    (root / "subtitles.srt").write_text(to_srt(cues), encoding="utf-8", newline="\n")
    progress(0.5, f"SRT: {len(cues)} cue.")

    groups = karaoke_groups(words, cfg.karaoke_min_words, cfg.karaoke_max_words)
    write_json(root / "karaoke.json", karaoke_json(groups))
    (root / "subtitles.ass").write_text(to_ass(groups, env.brand), encoding="utf-8",
                                        newline="\n")
    return {"srt_cues": len(cues), "karaoke_groups": len(groups)}
