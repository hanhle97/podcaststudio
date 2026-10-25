"""Align step: words.json with per-word timing for every script word."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.align.align import align_sentence
from app.align.whisper import WhisperTimer, WhisperWord
from app.config import load_pronunciation
from app.episodes import write_json
from app.jobs import StepError

if TYPE_CHECKING:
    from app.pipeline import StepEnv, StepProgress


def provider_words(env: StepEnv, info: dict[str, Any]) -> list[WhisperWord] | None:
    """Word timestamps returned by the TTS API itself, if it gave any (single-chunk only)."""
    keys = info.get("keys", [])
    if len(keys) != 1:
        return None
    path = env.settings.cache_dir / "tts" / f"{keys[0]}.words.json"
    if not path.exists():
        return None
    return [WhisperWord(w["word"], w["start"], w["end"])
            for w in json.loads(path.read_text(encoding="utf-8"))]


def run_align(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    root = env.paths.root
    seg_file, manifest_file = root / "segments.json", root / "tts" / "manifest.json"
    if not seg_file.exists() or not manifest_file.exists():
        raise StepError("align", "Chưa có segments.json. Hãy chạy bước tts và mix trước.")
    segments = json.loads(seg_file.read_text(encoding="utf-8"))["segments"]
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))["sentences"]
    pronunciation = load_pronunciation(env.settings.config_dir)

    timer = WhisperTimer(env.settings.whisper_model, env.settings.whisper_device)
    progress(0.0, f"faster-whisper {env.settings.whisper_model} trên {timer.device}.")

    words: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    for i, seg in enumerate(segments):
        info = manifest[seg["id"]]
        clip = root / info["file"]
        heard = provider_words(env, info)
        source = "tts"
        if heard is None:
            source = "whisper"
            heard = timer.words(clip, prompt=info["speech_text"], language=env.brand.tts.language)
        duration = seg["end"] - seg["start"]
        timed = align_sentence(seg["text"], heard, duration, pronunciation)
        for w in timed:
            words.append({
                "word": w.word, "start": round(seg["start"] + w.start, 3),
                "end": round(seg["start"] + w.end, 3), "sentence_id": seg["id"],
                "chapter": seg["chapter"], "matched": w.matched,
            })
        matched = sum(w.matched for w in timed)
        report.append({"sentence_id": seg["id"], "source": source, "words": len(timed),
                       "matched": matched, "heard": " ".join(w.word for w in heard)})
        progress((i + 1) / len(segments), f"Căn chỉnh câu {i + 1}/{len(segments)}: "
                                          f"{matched}/{len(timed)} từ khớp.")

    write_json(root / "words.json", words)
    total = len(words)
    matched_total = sum(w["matched"] for w in words)
    write_json(root / "align_report.json", {
        "device": timer.device, "model": env.settings.whisper_model,
        "fallback": timer.fallback_reason, "matched_ratio": round(matched_total / total, 3),
        "sentences": report,
    })
    return {"words": total, "matched_ratio": round(matched_total / total, 3),
            "device": timer.device}
