"""Mix step: voice track -> light processing -> loudness-normalised final audio."""

from __future__ import annotations

import json
import re
import shutil
from typing import TYPE_CHECKING, Any

from app.audio.assemble import compute_timeline, render_voice_track
from app.audio.ffmpeg import run_ffmpeg
from app.audio.filters import build_voice_filter, parse_loudnorm_json
from app.episodes import write_json
from app.jobs import StepError

if TYPE_CHECKING:
    from app.pipeline import StepEnv, StepProgress


def measure_loudness(path, step: str = "mix") -> dict[str, float]:
    """Integrated loudness / LRA / true peak via the ebur128 filter."""
    log = run_ffmpeg(["-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                     step, f"đo loudness {path.name}")
    summary = log[log.rfind("Summary:"):]

    def grab(label: str) -> float:
        m = re.search(rf"{label}:\s*(-?[\d.]+|-inf)", summary)
        if not m:
            raise StepError(step, f"Không đọc được '{label}' từ ebur128.", summary)
        return float(m.group(1)) if m.group(1) != "-inf" else float("-inf")

    return {"integrated_lufs": grab("I"), "lra": grab("LRA"), "true_peak_dbfs": grab("Peak")}


def run_mix(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    root = env.paths.root
    manifest_file = root / "tts" / "manifest.json"
    if not manifest_file.exists():
        raise StepError("mix", "Chưa có giọng đọc. Hãy chạy bước tts trước.")
    sentences = json.loads(manifest_file.read_text(encoding="utf-8"))["sentences"]
    script = env.script
    missing = [s.id for s in script.sentences if s.id not in sentences]
    if missing:
        raise StepError("mix", f"Thiếu audio cho câu {', '.join(missing[:5])}. Chạy lại bước tts.")

    a = env.brand.audio
    durations = {sid: info["duration"] for sid, info in sentences.items()}
    speech = {sid: info["speech_text"] for sid, info in sentences.items()}
    tl = compute_timeline(script, durations, a, speech)
    write_json(root / "segments.json", tl.to_json())
    progress(0.1, f"Timeline {tl.duration:.1f}s, {len(tl.segments)} câu.")

    voice_raw = root / "audio" / "voice_raw.wav"
    render_voice_track(tl, {sid: root / info["file"] for sid, info in sentences.items()},
                       voice_raw)
    progress(0.3, "Đã ghép giọng đọc, đang đo loudness (pass 1).")

    log = run_ffmpeg(["-i", str(voice_raw), "-af", build_voice_filter(a), "-f", "null", "-"],
                     "mix", "đo loudness (pass 1)")
    try:
        measured = parse_loudnorm_json(log)
    except ValueError as e:
        raise StepError("mix", str(e), log[-2000:]) from e
    progress(0.55, f"Pass 1: {measured['input_i']} LUFS, chuẩn hóa về "
                   f"{a.loudness.target_lufs:g} LUFS (pass 2).")

    voice = root / "audio" / "voice.wav"
    run_ffmpeg(["-i", str(voice_raw), "-af", build_voice_filter(a, measured),
                "-c:a", "pcm_s16le", str(voice)], "mix", "chuẩn hóa loudness (pass 2)")

    # Phase 2: no music yet, the processed voice is the final audio.
    final_wav, final_mp3 = root / "final_audio.wav", root / "final_audio.mp3"
    shutil.copyfile(voice, final_wav)
    run_ffmpeg(["-i", str(final_wav), "-c:a", "libmp3lame", "-b:a", a.mp3_bitrate,
                str(final_mp3)], "mix", "xuất MP3")
    progress(0.9, "Đang đo loudness đầu ra.")

    loudness = measure_loudness(final_wav)
    write_json(root / "loudness.json", {"pass1": measured, "final": loudness,
                                        "target_lufs": a.loudness.target_lufs,
                                        "target_true_peak": a.loudness.true_peak_db})
    return {"duration": tl.duration, **loudness}
