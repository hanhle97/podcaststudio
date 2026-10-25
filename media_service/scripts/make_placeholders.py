"""Generate placeholder assets so the pipeline runs before you add licensed ones.

Nothing is downloaded: everything is synthesised with ffmpeg / Pillow.
Replace the outputs with your own licensed music / images / logo.

Usage:  python scripts/make_placeholders.py --out /path/to/assets [--only backgrounds loop logo]
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


PALETTES = [
    ("#0F172A", "#1E3A8A", "#4C1D95"),
    ("#111827", "#065F46", "#0E7490"),
    ("#1F1235", "#7C2D12", "#B45309"),
]


def make_backgrounds(out_dir: Path, size: tuple[int, int] = (1920, 1080)) -> None:
    """Soft diagonal gradients with blurred light blobs (stand-ins for photos)."""
    from PIL import Image, ImageDraw, ImageFilter

    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = size
    for n, palette in enumerate(PALETTES, 1):
        cols = [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in palette]
        img = Image.new("RGB", size)
        px = img.load()
        for y in range(h):
            for x in range(0, w, 2):
                t = (x / w * 0.6 + y / h * 0.4)
                a, b, k = (cols[0], cols[1], t * 2) if t < 0.5 else (cols[1], cols[2], t * 2 - 1)
                c = tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))
                px[x, y] = c
                if x + 1 < w:
                    px[x + 1, y] = c
        blobs = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(blobs)
        for i in range(5):
            cx, cy, r = (w * (0.15 + 0.18 * i)), (h * (0.3 + 0.1 * ((i * 7) % 5))), h * 0.18
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 28))
        img = Image.alpha_composite(img.convert("RGBA"), blobs.filter(ImageFilter.GaussianBlur(80)))
        path = out_dir / f"placeholder_{n:02d}.jpg"
        img.convert("RGB").save(path, quality=90)
        print(f"✓ {path}")


def make_loop_video(out: Path, seconds: int = 10) -> None:
    """Slowly moving gradient, H.264, for the 'loop' background."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"gradients=s=1920x1080:c0=0x0F172A:c1=0x1E3A8A:c2=0x4C1D95:x0=0:y0=0:"
                    f"x1=1920:y1=1080:speed=0.008:d={seconds}:r=30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "28", str(out)], check=True)
    print(f"✓ {out}")


def make_logo(out: Path, font: Path | None) -> None:
    from PIL import Image, ImageDraw, ImageFont

    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([16, 16, size - 16, size - 16], radius=110, fill=(245, 183, 0, 255))
    f = ImageFont.truetype(str(font), 230) if font and font.exists() else ImageFont.load_default()
    d.text((size / 2, size / 2 + 8), "PS", font=f, fill=(15, 23, 42, 255), anchor="mm")
    img.save(out)
    print(f"✓ {out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="assets directory")
    parser.add_argument("--only", nargs="*", choices=["music", "backgrounds", "loop", "logo"])
    args = parser.parse_args()
    if not shutil.which("ffmpeg"):
        print("Cần ffmpeg (chạy trong container media-service).")
        return 1
    parser_targets = args.only or ["music", "backgrounds", "loop", "logo"]
    if "music" in parser_targets:
        make_music(args.out / "music" / "placeholder_ambient.mp3")
    if "backgrounds" in parser_targets:
        make_backgrounds(args.out / "backgrounds")
    if "loop" in parser_targets:
        make_loop_video(args.out / "backgrounds" / "placeholder_loop.mp4")
    if "logo" in parser_targets:
        make_logo(args.out / "logo.png", args.out / "fonts" / "BeVietnamPro-Bold.ttf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
