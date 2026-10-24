"""Mix step: voice track -> light processing -> music bed with ducking -> final loudness."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.audio.assemble import compute_timeline, render_voice_track
from app.audio.ffmpeg import probe, run_ffmpeg
from app.audio.filters import (
    build_crossfade_graph, build_mix_graph, duck_ratio, loudnorm_apply, loudnorm_measure,
    music_gain_db, normalize_format, parse_loudnorm_json, voice_chain,
)
from app.episodes import AUDIO_EXTENSIONS, list_assets, write_json
from app.jobs import StepError

if TYPE_CHECKING:
    from app.pipeline import StepEnv, StepProgress


def measure_loudness(path: Path, step: str = "mix") -> dict[str, float]:
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


def duration_of(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def _asset(env: StepEnv, name: str, what: str) -> Path:
    path = env.settings.music_dir / name
    if not path.is_file():
        raise StepError("mix", f"Không tìm thấy {what} '{name}' trong assets/music/.")
    return path


def pick_music(env: StepEnv) -> Path | None:
    """Frontmatter music, else a random track that stays the same for this episode."""
    cfg = env.brand.audio.music
    if not cfg.enabled:
        return None
    script = env.script
    if script.meta.music:
        return _asset(env, script.meta.music, "bài nhạc")
    reserved = {cfg.intro, cfg.outro}
    tracks = [p for p in list_assets(env.settings.music_dir, AUDIO_EXTENSIONS)
              if p.name not in reserved]
    if not tracks:
        return None
    return random.Random(env.episode_id).choice(tracks)


def music_inputs(env: StepEnv) -> list[Path | str]:
    """Fingerprint inputs for the mix step's music choice."""
    cfg = env.brand.audio.music
    names = sorted(p.name for p in list_assets(env.settings.music_dir, AUDIO_EXTENSIONS))
    items: list[Path | str] = ["|".join(names)]
    track = None
    try:
        track = pick_music(env)
    except StepError:
        pass
    for p in (track, cfg.intro and env.settings.music_dir / cfg.intro,
              cfg.outro and env.settings.music_dir / cfg.outro):
        if p:
            items.append(p)
    return items


def _two_pass_loudnorm(env: StepEnv, src: Path, dst: Path, layout: str, pre: list[str],
                       what: str) -> dict[str, str]:
    lcfg = env.brand.audio.loudness
    chain = ",".join([*pre, loudnorm_measure(lcfg)])
    log = run_ffmpeg(["-i", str(src), "-af", chain, "-f", "null", "-"], "mix",
                     f"đo loudness {what} (pass 1)")
    try:
        measured = parse_loudnorm_json(log)
    except ValueError as e:
        raise StepError("mix", str(e), log[-2000:]) from e
    chain = ",".join([*pre, loudnorm_apply(lcfg, measured),
                      normalize_format(env.brand.audio.sample_rate, layout)])
    run_ffmpeg(["-i", str(src), "-af", chain, "-c:a", "pcm_s16le", str(dst)], "mix",
               f"chuẩn hóa loudness {what} (pass 2)")
    return measured


def run_mix(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    root = env.paths.root
    audio_dir = root / "audio"
    audio_dir.mkdir(exist_ok=True)
    manifest_file = root / "tts" / "manifest.json"
    if not manifest_file.exists():
        raise StepError("mix", "Chưa có giọng đọc. Hãy chạy bước tts trước.")
    sentences = json.loads(manifest_file.read_text(encoding="utf-8"))["sentences"]
    script = env.script
    missing = [s.id for s in script.sentences if s.id not in sentences]
    if missing:
        raise StepError("mix", f"Thiếu audio cho câu {', '.join(missing[:5])}. Chạy lại bước tts.")

    a = env.brand.audio
    mcfg = a.music
    sr = a.sample_rate

    # 1) voice track on its own timeline
    durations = {sid: info["duration"] for sid, info in sentences.items()}
    speech = {sid: info["speech_text"] for sid, info in sentences.items()}
    tl = compute_timeline(script, durations, a, speech)
    voice_raw = audio_dir / "voice_raw.wav"
    render_voice_track(tl, {sid: root / info["file"] for sid, info in sentences.items()},
                       voice_raw)
    progress(0.15, f"Đã ghép {len(tl.segments)} câu ({tl.duration:.1f}s).")

    # 2) light processing + loudness of the voice alone
    voice = audio_dir / "voice.wav"
    voice_measured = _two_pass_loudnorm(env, voice_raw, voice, "mono",
                                        voice_chain(a), "giọng")
    progress(0.35, f"Giọng: {voice_measured['input_i']} → {a.loudness.target_lufs:g} LUFS.")

    # 3) music bed with sidechain ducking
    track = pick_music(env)
    music_info: dict[str, Any] = {"track": None}
    body = voice
    if track is None:
        progress(0.5, "Không có nhạc nền (assets/music/ trống hoặc đã tắt), chỉ dùng giọng.")
    else:
        music_lufs = measure_loudness(track)["integrated_lufs"]
        gain = music_gain_db(music_lufs, a.loudness.target_lufs, mcfg)
        music_info = {"track": track.name, "track_lufs": music_lufs, "gain_db": gain,
                      "duck_ratio": duck_ratio(mcfg.duck_db), "level_db": mcfg.level_db,
                      "duck_db": mcfg.duck_db, "looped": duration_of(track) < tl.duration}
        body = audio_dir / "mix_body.wav"
        stem = audio_dir / "music_ducked.wav"
        for out, kind in ((body, "mix"), (stem, "music")):
            run_ffmpeg(["-i", str(voice), "-stream_loop", "-1", "-i", str(track),
                        "-filter_complex", build_mix_graph(tl.duration, gain, mcfg, sr, kind),
                        "-map", "[out]", "-c:a", "pcm_s16le", str(out)],
                       "mix", f"trộn nhạc nền ({kind})")
        progress(0.6, f"Nhạc '{track.name}' {gain:+.1f} dB, ducking {mcfg.duck_db:g} dB.")

    # 4) optional intro / outro with crossfades
    parts: list[Path] = []
    intro = _asset(env, mcfg.intro, "intro") if mcfg.intro else None
    outro = _asset(env, mcfg.outro, "outro") if mcfg.outro else None
    if intro:
        parts.append(intro)
    parts.append(body)
    if outro:
        parts.append(outro)
    full = body
    offset = 0.0
    if len(parts) > 1:
        full = audio_dir / "mix_full.wav"
        inputs = [arg for p in parts for arg in ("-i", str(p))]
        run_ffmpeg([*inputs, "-filter_complex",
                    build_crossfade_graph(len(parts), mcfg.crossfade_s, sr),
                    "-map", "[out]", "-c:a", "pcm_s16le", str(full)], "mix", "ghép intro/outro")
        if intro:
            offset = duration_of(intro) - mcfg.crossfade_s

    # 5) final loudness, exports
    final_wav, final_mp3 = root / "final_audio.wav", root / "final_audio.mp3"
    layout = "stereo" if track or len(parts) > 1 else "mono"
    _two_pass_loudnorm(env, full, final_wav, layout, [], "bản mix")
    run_ffmpeg(["-i", str(final_wav), "-c:a", "libmp3lame", "-b:a", a.mp3_bitrate,
                str(final_mp3)], "mix", "xuất MP3")
    progress(0.9, "Đang đo loudness đầu ra.")

    total = duration_of(final_wav)
    write_json(root / "segments.json", tl.shifted(offset, total).to_json())
    loudness = measure_loudness(final_wav)
    write_json(root / "loudness.json", {
        "voice_pass1": voice_measured, "final": loudness, "music": music_info,
        "target_lufs": a.loudness.target_lufs, "target_true_peak": a.loudness.true_peak_db,
    })
    return {"duration": round(total, 3), "music": music_info.get("track"), **loudness}
