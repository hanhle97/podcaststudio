"""End-to-end test: script.md -> every output, checked automatically.

Runs inside the media-service container against the live API:

    docker compose exec media-service-gpu python scripts/e2e.py            # or -cpu
    docker compose exec media-service-gpu python scripts/e2e.py --clear-tts-cache

--clear-tts-cache drops this episode's cached TTS clips so every sentence is
synthesised again by Voice Studio. Writes data/episodes/<id>/e2e/report.json and
e2e/contact_sheet.png (frames to inspect: diacritics, subtitles, waveform, chapters).
Exit code 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings, load_brand  # noqa: E402
from app.llm.timing import required_clip_count  # noqa: E402

API = "http://localhost:8000"
CLIP_LEAD_IN, CLIP_TAIL = 0.2, 0.5


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


checks: list[Check] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    checks.append(Check(name, bool(ok), detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""), flush=True)
    return bool(ok)


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of",
                          "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def video_info(path: Path) -> tuple[int, int, float, float, bool]:
    info = probe(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    return v["width"], v["height"], num / den, float(info["format"]["duration"]), has_audio


def ebur128(path: Path) -> dict[str, float]:
    log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                          "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    summary = log[log.rfind("Summary:"):]
    grab = lambda k: float(re.search(rf"{k}:\s*(-?[\d.]+)", summary).group(1))  # noqa: E731
    return {"I": grab("I"), "LRA": grab("LRA"), "Peak": grab("Peak")}


def frame(video: Path, t: float, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}",
                    "-i", str(video), "-frames:v", "1", str(out)], check=True)
    return out


def contact_sheet(frames: list[tuple[str, Path]], out: Path, font_path: Path) -> None:
    thumbs = []
    for label, path in frames:
        img = Image.open(path).convert("RGB")
        img.thumbnail((640, 640))
        thumbs.append((label, img))
    cols = 3
    cell_w, cell_h = 660, max(t.height for _, t in thumbs) + 50
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(font_path), 22) if font_path.exists() else None
    for i, (label, img) in enumerate(thumbs):
        x, y = (i % cols) * cell_w + 10, (i // cols) * cell_h + 10
        draw.text((x, y), label, fill=(240, 240, 240), font=font)
        sheet.paste(img, (x, y + 34))
    sheet.save(out)


def parse_srt(text: str) -> list[dict[str, Any]]:
    cues = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        cues.append({"time": lines[1], "lines": lines[2:]})
    return cues


def run_pipeline(client: httpx.Client, ep: str) -> dict[str, Any]:
    job_id = client.post(f"/episodes/{ep}/pipeline/run", params={"force": "true"}).json()["job_id"]
    last = None
    started = time.time()
    while True:
        job = client.get(f"/jobs/{job_id}").json()
        msg = f"[{job['step']}] {job['progress']:.0%} {job.get('message') or ''}"
        if msg != last:
            print(f"    {time.time() - started:6.1f}s {msg}", flush=True)
            last = msg
        if job["status"] in ("succeeded", "failed"):
            job["elapsed"] = round(time.time() - started, 1)
            return job
        time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--script", default="examples/script_demo.md", type=Path)
    parser.add_argument("--clear-tts-cache", action="store_true")
    args = parser.parse_args()

    settings, brand = get_settings(), load_brand()
    client = httpx.Client(base_url=API, timeout=60)
    print(f"E2E: {args.script}")

    # 1) create episode
    r = client.post("/episodes", files={"file": (args.script.name, args.script.read_bytes(),
                                                 "text/markdown")})
    if not check("tạo episode từ script.md", r.status_code == 201, r.text[:200]
                 if r.status_code != 201 else ""):
        return finish(None)
    created = r.json()
    ep = created["episode"]["id"]
    root = settings.episodes_dir / ep
    print(f"  episode: {ep}")
    health = client.get("/tts/health").json()
    if not check("Voice Studio sẵn sàng", health.get("ok"), health.get("message", "")):
        return finish(root)

    if args.clear_tts_cache and (root / "tts" / "manifest.json").exists():
        manifest = json.loads((root / "tts" / "manifest.json").read_text(encoding="utf-8"))
        removed = 0
        for info in manifest["sentences"].values():
            for key in info["keys"]:
                for f in (settings.cache_dir / "tts").glob(f"{key}.*"):
                    f.unlink()
                    removed += 1
        print(f"  đã xóa {removed} file cache TTS của tập này")

    # 2) full pipeline, forced
    print("  chạy pipeline (force)…")
    job = run_pipeline(client, ep)
    steps = (job.get("result") or {}).get("steps", [])
    if not check("pipeline chạy hết", job["status"] == "succeeded",
                 f"{job['elapsed']}s" if job["status"] == "succeeded" else
                 f"lỗi ở {job.get('step_label')}: {(job.get('error') or {}).get('message')}"):
        return finish(root)
    tts = next((s for s in steps if s["step"] == "tts"), {})
    check("giọng đọc qua Voice Studio", tts.get("status") == "ran",
          f"{tts.get('sentences')} câu, {tts.get('api_calls')} lần gọi API")

    script = json.loads((root / "script.json").read_text(encoding="utf-8"))
    segments = json.loads((root / "segments.json").read_text(encoding="utf-8"))
    duration = segments["duration"]

    # 3) files
    required = ["episode_16x9.mp4", "final_audio.mp3", "final_audio.wav", "subtitles.srt",
                "subtitles.ass", "words.json", "karaoke.json", "metadata.json",
                "thumbnail.png", "segments.json", "clips.json"]
    missing = [f for f in required if not (root / f).exists()]
    check("đủ file đầu ra", not missing, f"thiếu {missing}" if missing else f"{len(required)} file")

    # 4) landscape video
    w, h, fps, dur, audio = video_info(root / "episode_16x9.mp4")
    check("episode_16x9.mp4 1920x1080 30fps có tiếng", (w, h) == (1920, 1080)
          and abs(fps - brand.video.fps) < 0.01 and audio, f"{w}x{h} {fps:g}fps audio={audio}")
    check("thời lượng video khớp audio", abs(dur - duration) < 1.0,
          f"video {dur:.2f}s / audio {duration:.2f}s")

    # 5) shorts
    shorts = sorted((root / "shorts").glob("clip_*_9x16.mp4"))
    need = required_clip_count(duration, brand.shorts.min_clips, brand.shorts.min_seconds)
    check("số Shorts", need <= len(shorts) <= brand.shorts.max_clips,
          f"{len(shorts)} clip (tập {duration:.0f}s cần ≥ {need}, tối đa {brand.shorts.max_clips})")
    for s in shorts:
        sw, sh, _, sdur, saudio = video_info(s)
        check(f"{s.name} 1080x1920, 30–60s", (sw, sh) == (1080, 1920) and saudio and
              brand.shorts.min_seconds - 1 <= sdur <= brand.shorts.max_seconds + 2,
              f"{sw}x{sh} {sdur:.1f}s")
    clips = json.loads((root / "clips.json").read_text(encoding="utf-8"))["clips"]
    starts = {round(x["start"], 3) for x in segments["segments"]}
    ends = {round(x["end"], 3) for x in segments["segments"]}
    aligned = all((c["start"] == 0 or round(c["start"] + CLIP_LEAD_IN, 3) in starts) and
                  (round(c["end"] - CLIP_TAIL, 3) in ends or c["end"] >= duration - 0.01)
                  for c in clips)
    check("clip khớp ranh giới câu", aligned, ", ".join(
        f"{c['start_sentence_id']}..{c['end_sentence_id']}" for c in clips))

    # 6) loudness
    target, tp = brand.audio.loudness.target_lufs, brand.audio.loudness.true_peak_db
    for name in ("final_audio.wav", "final_audio.mp3"):
        lu = ebur128(root / name)
        check(f"loudness {name}", abs(lu["I"] - target) <= 1.0 and lu["Peak"] <= tp + 0.5,
              f"I={lu['I']} LUFS (đích {target:g}), LRA={lu['LRA']} LU, Peak={lu['Peak']} dBFS")

    # 7) subtitles
    cues = parse_srt((root / "subtitles.srt").read_text(encoding="utf-8"))
    cfg = brand.subtitles
    too_long = [c["lines"] for c in cues if len(c["lines"]) > cfg.max_lines
                or any(len(x) > cfg.max_chars_per_line for x in c["lines"])]
    check(f"SRT ≤ {cfg.max_chars_per_line} ký tự/dòng, ≤ {cfg.max_lines} dòng", not too_long,
          f"{len(cues)} cue" if not too_long else f"vi phạm: {too_long[:2]}")
    script_text = " ".join(s["text"] for c in script["chapters"] for s in c["items"]
                           if s["kind"] == "sentence")
    srt_text = " ".join(" ".join(c["lines"]) for c in cues)
    check("phụ đề khớp từng chữ với kịch bản", srt_text == script_text)
    check("phụ đề không còn tag điều khiển", not re.search(r"\[(pause|music|nghỉ)", srt_text))
    words = json.loads((root / "words.json").read_text(encoding="utf-8"))
    ordered = all(a["start"] <= b["start"] and a["start"] <= a["end"]
                  for a, b in zip(words, words[1:]))
    keys_ok = all({"word", "start", "end", "sentence_id", "chapter"} <= set(x) for x in words)
    report = json.loads((root / "align_report.json").read_text(encoding="utf-8"))
    check("words.json đúng schema, thời gian tăng dần", ordered and keys_ok,
          f"{len(words)} từ, khớp whisper {report['matched_ratio']:.1%} trên {report['device']}")
    check("độ khớp timing ≥ 90%", report["matched_ratio"] >= 0.9)
    karaoke = json.loads((root / "karaoke.json").read_text(encoding="utf-8"))
    sizes = {len(g["words"]) for g in karaoke}
    check("karaoke cụm ≤ 4 từ", max(sizes) <= cfg.karaoke_max_words, f"cỡ cụm {sorted(sizes)}")
    check("subtitles.ass có karaoke", "\\k" in (root / "subtitles.ass").read_text(encoding="utf-8"))

    # 8) metadata
    meta = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    fm_title = script["meta"].get("title")
    check("tiêu đề", meta["title"] == fm_title if fm_title else 0 < len(meta["title"]) <= 70,
          f"\"{meta['title']}\"" + (" (từ frontmatter)" if fm_title else ""))
    check("10–15 tags", 10 <= len(meta["tags"]) <= 15, f"{len(meta['tags'])} tags")
    check("3 phương án text thumbnail", len(meta["thumbnail_texts"]) == 3,
          " | ".join(meta["thumbnail_texts"]))
    toc = meta["chapters_text"].splitlines()
    expected_chapters = len(script["chapters"]) if script["has_headings"] else len(toc)
    check("chapters YouTube từ 00:00", toc and toc[0].startswith("00:00 ")
          and len(toc) == expected_chapters, " / ".join(toc))
    check("mô tả chứa mục lục", meta["chapters_text"] in meta["description"])
    tw, th = Image.open(root / "thumbnail.png").size
    check("thumbnail.png 1280x720", (tw, th) == (1280, 720), f"{tw}x{th}")

    # 9) frames for visual inspection
    out_dir = root / "e2e"
    out_dir.mkdir(exist_ok=True)
    picks: list[tuple[str, Path]] = []
    for c in segments["chapters"]:
        first = next(s for s in segments["segments"] if s["chapter"] == c["index"])
        t = first["start"] + min(1.5, (first["end"] - first["start"]) / 2)
        picks.append((f"16:9 {t:.1f}s – {c['title']}",
                      frame(root / "episode_16x9.mp4", t, out_dir / f"land_{c['index']}.png")))
    music_up = next((b for b in segments["breaks"] if b["kind"] == "music_up"), None)
    if music_up:
        t = (music_up["start"] + music_up["end"]) / 2
        picks.append((f"16:9 {t:.1f}s – [music up]",
                      frame(root / "episode_16x9.mp4", t, out_dir / "land_music_up.png")))
    for s in shorts[:2]:
        sdur = video_info(s)[3]
        for t in (1.0, sdur / 2):
            picks.append((f"{s.stem} {t:.1f}s", frame(s, t, out_dir / f"{s.stem}_{t:.0f}.png")))
    blank = [label for label, p in picks if max(ImageStat.Stat(Image.open(p)).stddev) < 5]
    check("frame không trống", not blank, f"{len(picks)} frame" if not blank else str(blank))
    sheet = out_dir / "contact_sheet.png"
    contact_sheet(picks + [("thumbnail.png", root / "thumbnail.png")], sheet,
                  settings.assets_dir / brand.fonts.bold)
    print(f"  ảnh để soi: {sheet}")
    return finish(root, {"episode_id": ep, "pipeline_seconds": job["elapsed"], "steps": steps})


def finish(root: Path | None, extra: dict | None = None) -> int:
    failed = [c for c in checks if not c.ok]
    print(f"\n{'ĐẠT' if not failed else 'KHÔNG ĐẠT'}: {len(checks) - len(failed)}/{len(checks)} "
          "kiểm tra")
    if root:
        (root / "e2e").mkdir(exist_ok=True)
        (root / "e2e" / "report.json").write_text(json.dumps({
            "passed": not failed, **(extra or {}),
            "checks": [c.__dict__ for c in checks]}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
