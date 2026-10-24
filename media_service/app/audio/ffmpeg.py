"""Thin wrappers around the ffmpeg / ffprobe CLIs."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

from app.jobs import StepError


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise StepError("ffmpeg", "Không tìm thấy ffmpeg. Hãy chạy trong Docker hoặc cài ffmpeg.")
    return path


def run_ffmpeg(args: list[str], step: str, what: str) -> str:
    """Run ffmpeg with `args`; return stderr (ffmpeg logs there). Raise StepError on failure."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise StepError(step, f"ffmpeg lỗi khi {what}.", f"{' '.join(cmd)}\n{tail}")
    return proc.stderr


def probe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise StepError("ffprobe", f"Không đọc được file audio {path.name}.", proc.stderr)
    return json.loads(proc.stdout)


def wav_duration(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()
