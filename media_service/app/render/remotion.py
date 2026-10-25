"""Render step: prepare a public dir + props, bundle once, render with the Remotion CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from app.audio.ffmpeg import probe, run_ffmpeg
from app.episodes import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, list_assets, write_json
from app.jobs import StepError
from app.render.props import background_spec, build_props, heuristic_clips

if TYPE_CHECKING:
    from app.pipeline import StepEnv, StepProgress

FORMATS = ("landscape", "short", "all")
_PROGRESS = re.compile(r"(?:Rendered|Rendering frames|Encoded|Stitching)\D*(\d+)\s*/\s*(\d+)")


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def episode_title(env: StepEnv) -> str:
    meta_file = env.paths.root / "metadata.json"
    if meta_file.exists():
        title = json.loads(meta_file.read_text(encoding="utf-8")).get("title")
        if title:
            return title
    script = env.script
    return script.meta.title or script.sentences[0].text[:70]


def load_clips(env: StepEnv, segments: dict[str, Any]) -> list[dict[str, Any]]:
    """clips.json from the LLM step if present, else sentence-aligned fallback windows."""
    clips_file = env.paths.root / "clips.json"
    if clips_file.exists():
        data = json.loads(clips_file.read_text(encoding="utf-8"))
        return data.get("clips", data) if isinstance(data, dict) else data
    s = env.brand.shorts
    return heuristic_clips(segments, s.min_seconds, s.max_seconds, s.max_clips)


def prepare_public(env: StepEnv, public: Path) -> tuple[dict[str, Any], str | None]:
    """Copy/link everything the compositions load via staticFile()."""
    if public.exists():
        shutil.rmtree(public)
    public.mkdir(parents=True)
    root, assets = env.paths.root, env.settings.assets_dir
    _link_or_copy(root / "final_audio.wav", public / "audio.wav")
    run_ffmpeg(["-i", str(root / "final_audio.wav"), "-ac", "1", "-ar", "22050",
                "-c:a", "pcm_s16le", str(public / "viz.wav")], "render", "tạo audio cho waveform")

    fonts = env.brand.fonts
    for name, rel in (("regular", fonts.regular), ("bold", fonts.bold)):
        src = assets / rel
        if not src.is_file():
            raise StepError("render", f"Không tìm thấy font '{rel}' trong assets/. "
                                      "Cần font hỗ trợ tiếng Việt (mặc định Be Vietnam Pro).")
        _link_or_copy(src, public / "fonts" / f"{name}.ttf")

    logo = None
    if env.brand.channel.logo and (assets / env.brand.channel.logo).is_file():
        _link_or_copy(assets / env.brand.channel.logo, public / "logo.png")
        logo = "logo.png"

    kind = env.script.meta.background or env.brand.video.background
    images = [p for p in list_assets(env.settings.backgrounds_dir, IMAGE_EXTENSIONS)]
    videos = [p for p in list_assets(env.settings.backgrounds_dir, VIDEO_EXTENSIONS)]
    image_names, video_name, video_dur = [], None, 0.0
    if kind == "kenburns":
        for p in images:
            _link_or_copy(p, public / "bg" / p.name)
            image_names.append(f"bg/{p.name}")
    elif kind == "loop" and videos:
        _link_or_copy(videos[0], public / "bg" / videos[0].name)
        video_name = f"bg/{videos[0].name}"
        video_dur = float(probe(videos[0])["format"]["duration"])
    return background_spec(kind, image_names, video_name, video_dur), logo


def _remotion(args: list[str], cwd: Path, step_msg: str,
              on_progress: Callable[[float], None] | None = None) -> None:
    """Run `npx remotion ...`, streaming progress lines; raise StepError on failure."""
    proc = subprocess.Popen(["npx", "remotion", *args], cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1, env={**os.environ, "CI": "1"})
    tail: list[str] = []
    assert proc.stdout is not None
    buf = ""
    while True:
        chunk = proc.stdout.read(256)
        if not chunk:
            break
        buf += chunk
        *lines, buf = re.split(r"[\r\n]", buf)
        for line in lines:
            if not line.strip():
                continue
            tail = (tail + [line])[-40:]
            m = _PROGRESS.search(line)
            if m and on_progress and int(m.group(2)) > 0:
                on_progress(int(m.group(1)) / int(m.group(2)))
    if proc.wait() != 0:
        raise StepError("render", f"Remotion lỗi khi {step_msg}.", "\n".join(tail))


def run_render(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    fmt = env.options.get("format", "all")
    if fmt not in FORMATS:
        raise StepError("render", f"format phải là một trong {', '.join(FORMATS)}.")
    root = env.paths.root
    for name in ("final_audio.wav", "segments.json", "words.json", "karaoke.json"):
        if not (root / name).exists():
            raise StepError("render", f"Thiếu {name}. Hãy chạy các bước trước (mix, align, "
                                      "subtitles).")
    segments = json.loads((root / "segments.json").read_text(encoding="utf-8"))
    words = json.loads((root / "words.json").read_text(encoding="utf-8"))
    karaoke = json.loads((root / "karaoke.json").read_text(encoding="utf-8"))

    work = root / "render"
    public, bundle = work / "public", work / "bundle"
    background, logo = prepare_public(env, public)
    chapters_file = root / "chapters.json"  # LLM-named chapters when the script had none
    if chapters_file.exists():
        segments = {**segments, "chapters": json.loads(chapters_file.read_text(encoding="utf-8"))}
    props = build_props(segments=segments, words=words, karaoke=karaoke, brand=env.brand,
                        title=episode_title(env), background=background, logo=logo)
    remotion_dir = env.settings.remotion_dir
    progress(0.02, f"Nền: {background['kind']}. Đang đóng gói Remotion…")
    if bundle.exists():
        shutil.rmtree(bundle)
    _remotion(["bundle", "src/index.ts", "--public-dir", str(public), "--out-dir", str(bundle)],
              remotion_dir, "đóng gói composition")

    jobs: list[tuple[str, dict[str, Any], Path]] = []
    if fmt in ("landscape", "all"):
        jobs.append(("PodcastLandscape", props, root / "episode_16x9.mp4"))
    clips: list[dict[str, Any]] = []
    if fmt in ("short", "all"):
        clips = load_clips(env, segments)
        shorts_dir = root / "shorts"
        shorts_dir.mkdir(exist_ok=True)
        for old in shorts_dir.glob("clip_*_9x16.mp4"):
            old.unlink()
        for n, clip in enumerate(clips, 1):
            short_props = {**props, "startSec": clip["start"], "endSec": clip["end"],
                           "hook": clip.get("hook") or ""}
            jobs.append(("PodcastShort", short_props, shorts_dir / f"clip_{n:02d}_9x16.mp4"))
    if not jobs:
        raise StepError("render", "Không có gì để render (không chọn được clip nào cho Shorts).")

    # weight each render by its length so progress moves evenly
    weights = [p["durationSec"] if comp == "PodcastLandscape" else p["endSec"] - p["startSec"]
               for comp, p, _ in jobs]
    total, done = sum(weights), 0.0
    concurrency = os.environ.get("REMOTION_CONCURRENCY")
    outputs = []
    for (comp, comp_props, out), weight in zip(jobs, weights):
        props_file = work / f"props_{out.stem}.json"
        write_json(props_file, comp_props)
        base = 0.05 + 0.95 * done / total
        span = 0.95 * weight / total
        progress(base, f"Render {out.relative_to(root)}…")
        args = ["render", str(bundle), comp, str(out), "--props", str(props_file),
                "--codec", "h264", "--crf", "20", "--audio-codec", "aac",
                "--audio-bitrate", "192k", "--log", "info"]
        if concurrency:
            args += ["--concurrency", concurrency]
        _remotion(args, remotion_dir, f"render {out.name}",
                  lambda f, b=base, s=span: progress(b + s * f))
        done += weight
        outputs.append(str(out.relative_to(root)))
    write_json(work / "clips_rendered.json", clips)
    return {"format": fmt, "outputs": outputs, "background": background["kind"]}
