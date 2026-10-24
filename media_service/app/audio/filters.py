"""Builders for ffmpeg filter graphs (pure functions, unit tested)."""

from __future__ import annotations

import json
import re

from app.config import AudioCfg, LoudnessCfg


def db_to_linear(db: float) -> float:
    return 10 ** (db / 20)


def trim_filter(threshold_db: float, keep_start_s: float = 0.05,
                keep_end_s: float = 0.08) -> str:
    """Remove leading/trailing silence of a TTS clip, keeping a short natural margin."""
    head = (f"silenceremove=start_periods=1:start_threshold={threshold_db:g}dB:"
            f"start_silence={keep_start_s:g}")
    tail = (f"silenceremove=start_periods=1:start_threshold={threshold_db:g}dB:"
            f"start_silence={keep_end_s:g}")
    return f"{head},areverse,{tail},areverse"


def normalize_format(sample_rate: int) -> str:
    return f"aresample={sample_rate},aformat=sample_fmts=s16:channel_layouts=mono"


def voice_chain(cfg: AudioCfg) -> list[str]:
    """Light processing for an already-clean TTS voice: highpass + optional gentle compressor."""
    filters = [f"highpass=f={cfg.highpass_hz:g}"]
    c = cfg.compressor
    if c.enabled:
        filters.append(
            f"acompressor=threshold={db_to_linear(c.threshold_db):.6f}:ratio={c.ratio:g}:"
            f"attack={c.attack_ms:g}:release={c.release_ms:g}"
        )
    return filters


def loudnorm_measure(cfg: LoudnessCfg) -> str:
    return (f"loudnorm=I={cfg.target_lufs:g}:TP={cfg.true_peak_db:g}:LRA={cfg.lra:g}:"
            "print_format=json")


def loudnorm_apply(cfg: LoudnessCfg, measured: dict[str, str]) -> str:
    """Second loudnorm pass using the first pass' measurements (linear mode)."""
    return (
        f"loudnorm=I={cfg.target_lufs:g}:TP={cfg.true_peak_db:g}:LRA={cfg.lra:g}:"
        f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true:print_format=summary"
    )


def build_voice_filter(cfg: AudioCfg, measured: dict[str, str] | None = None) -> str:
    """Full voice graph. Without `measured` it is the analysis pass (ends in loudnorm json)."""
    chain = voice_chain(cfg)
    if measured is None:
        chain.append(loudnorm_measure(cfg.loudness))
    else:
        chain.append(loudnorm_apply(cfg.loudness, measured))
        chain.append(normalize_format(cfg.sample_rate))
    return ",".join(chain)


_REQUIRED = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def parse_loudnorm_json(stderr: str) -> dict[str, str]:
    """Extract the JSON block loudnorm prints at the end of the analysis pass."""
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr)
    if not blocks:
        raise ValueError("Không tìm thấy kết quả đo loudnorm trong log ffmpeg.")
    data = json.loads(blocks[-1])
    missing = [k for k in _REQUIRED if k not in data]
    if missing:
        raise ValueError(f"Kết quả loudnorm thiếu trường: {', '.join(missing)}")
    for k in ("input_i", "input_tp", "input_lra", "input_thresh"):
        if data[k] in ("-inf", "inf", "nan"):
            raise ValueError("Audio im lặng hoàn toàn, không đo được độ to.")
    return {k: str(v) for k, v in data.items()}
