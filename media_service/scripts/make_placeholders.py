"""Generate placeholder assets so the pipeline runs before you add licensed ones.

Nothing is downloaded: everything is synthesised with ffmpeg / Pillow.
Replace the outputs with your own licensed music / images.

Usage:  python scripts/make_placeholders.py --out /path/to/assets
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def make_music(out: Path, seconds: int = 40) -> None:
    """Soft A-minor pad (sines + slow tremolo), stereo MP3."""
    notes = [110.0, 220.0, 261.63, 329.63, 392.0]
    inputs: list[str] = []
    for f in notes:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=48000:duration={seconds}"]
    n = len(notes)
    graph = (
        "".join(f"[{i}:a]" for i in range(n))
        + f"amix=inputs={n}:normalize=1,"
        "tremolo=f=0.15:d=0.35,lowpass=f=1800,"
        "aecho=0.6:0.5:180|360:0.25|0.15,"
        "pan=stereo|c0=c0|c1=c0,"
        "afade=t=in:d=2,"
        f"afade=t=out:st={seconds - 2}:d=2,volume=-6dB"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
                    "-filter_complex", graph, "-c:a", "libmp3lame", "-b:a", "192k", str(out)],
                   check=True)
    print(f"✓ {out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="assets directory")
    args = parser.parse_args()
    if not shutil.which("ffmpeg"):
        print("Cần ffmpeg (chạy trong container media-service).")
        return 1
    make_music(args.out / "music" / "placeholder_ambient.mp3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
