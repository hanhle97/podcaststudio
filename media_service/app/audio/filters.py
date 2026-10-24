"""Builders for ffmpeg filter graphs (pure functions, unit tested)."""

from __future__ import annotations

import json
import re

from app.config import AudioCfg, LoudnessCfg, MusicCfg

# The ducking key is the voice pushed into a limiter, so it sits at a steady level
# (about KEY_OVER_DB above the sidechain threshold) whenever anyone speaks and is
# digital silence otherwise. That makes the gain reduction ~constant: duck_db.
KEY_BOOST_DB = 30
KEY_LIMIT = 0.5
DUCK_THRESHOLD = 0.01  # -40 dBFS
KEY_OVER_DB = 40  # measured with scripts/check_ducking.py


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


def normalize_format(sample_rate: int, layout: str = "mono") -> str:
    return f"aresample={sample_rate},aformat=sample_fmts=s16:channel_layouts={layout}"


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


# --- music bed + ducking -------------------------------------------------------------


def music_gain_db(music_lufs: float, voice_lufs: float, cfg: MusicCfg) -> float:
    """Gain that puts the un-ducked music at voice + level_db + duck_db.

    While the voice speaks the sidechain removes duck_db, leaving the music at
    voice + level_db (e.g. 22 dB under the voice).
    """
    return round(voice_lufs + cfg.level_db + cfg.duck_db - music_lufs, 2)


def duck_ratio(duck_db: float, key_over_db: float = KEY_OVER_DB) -> float:
    """Compressor ratio giving `duck_db` of reduction for a key `key_over_db` above threshold."""
    if duck_db <= 0:
        return 1.0
    if duck_db >= key_over_db:
        return 20.0
    return round(min(20.0, key_over_db / (key_over_db - duck_db)), 3)


def build_mix_graph(duration: float, gain_db: float, cfg: MusicCfg, sample_rate: int,
                    output: str = "mix") -> str:
    """filter_complex for input 0 = processed voice, input 1 = looped music.

    output="mix"   -> [out] = voice + ducked music
    output="music" -> [out] = ducked music only (diagnostics)
    """
    fmt = f"aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo"
    fade_out_start = max(0.0, duration - cfg.fade_out_s)
    bed = (f"[1:a]{fmt},volume={gain_db:g}dB,atrim=0:{duration:.3f},asetpts=N/SR/TB,"
           f"afade=t=in:d={cfg.fade_in_s:g},"
           f"afade=t=out:st={fade_out_start:.3f}:d={cfg.fade_out_s:g}[bed]")
    voice = f"[0:a]{fmt},asplit=2[voice][key0]"
    key = f"[key0]volume={KEY_BOOST_DB}dB,alimiter=limit={KEY_LIMIT}:attack=5:release=50[key]"
    duck = (f"[bed][key]sidechaincompress=threshold={DUCK_THRESHOLD}:"
            f"ratio={duck_ratio(cfg.duck_db)}:attack={cfg.duck_attack_ms:g}:"
            f"release={cfg.duck_release_ms:g}:makeup=1[ducked]")
    if output == "music":
        tail = "[voice]anullsink;[ducked]anull[out]"
    else:
        tail = "[voice][ducked]amix=inputs=2:duration=first:normalize=0[out]"
    return ";".join([bed, voice, key, duck, tail])


def build_crossfade_graph(n_inputs: int, crossfade_s: float, sample_rate: int) -> str:
    """Chain n stereo inputs with acrossfade (used for intro / body / outro)."""
    fmt = f"aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo"
    parts = [f"[{i}:a]{fmt}[a{i}]" for i in range(n_inputs)]
    prev = "a0"
    for i in range(1, n_inputs):
        label = "out" if i == n_inputs - 1 else f"x{i}"
        parts.append(f"[{prev}][a{i}]acrossfade=d={crossfade_s:g}:c1=tri:c2=tri[{label}]")
        prev = label
    if n_inputs == 1:
        parts[0] = parts[0].replace("[a0]", "[out]")
    return ";".join(parts)
