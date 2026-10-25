"""thumbnail.png (1280x720) from a Pillow template: background, logo, big Vietnamese text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import BrandConfig
from app.episodes import IMAGE_EXTENSIONS, list_assets
from app.jobs import StepError

if TYPE_CHECKING:
    from app.pipeline import StepEnv, StepProgress

SIZE = (1280, 720)
MARGIN = 72


def hex_rgb(color: str) -> tuple[int, int, int]:
    h = color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def gradient(size: tuple[int, int], colors: list[str]) -> Image.Image:
    """Diagonal multi-stop gradient (vectorised through small image + resize)."""
    cols = [hex_rgb(c) for c in colors] or [(15, 23, 42)]
    small = Image.new("RGB", (64, 36))
    px = small.load()
    for y in range(36):
        for x in range(64):
            t = (x / 63) * 0.65 + (y / 35) * 0.35
            seg = min(int(t * (len(cols) - 1)), len(cols) - 2) if len(cols) > 1 else 0
            local = t * (len(cols) - 1) - seg if len(cols) > 1 else 0
            a, b = cols[seg], cols[min(seg + 1, len(cols) - 1)]
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * local) for i in range(3))
    return small.resize(size, Image.Resampling.BICUBIC)


def cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    scale = max(size[0] / img.width, size[1] / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)),
                     Image.Resampling.LANCZOS)
    left, top = (img.width - size[0]) // 2, (img.height - size[1]) // 2
    return img.crop((left, top, left + size[0], top + size[1]))


def fit_text(draw: ImageDraw.ImageDraw, text: str, font_path: Path, max_width: int,
             max_lines: int = 3, sizes: range = range(132, 55, -4)) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest font size whose greedy word-wrap fits in max_lines x max_width."""
    words = text.split()
    for size in sizes:
        font = ImageFont.truetype(str(font_path), size)
        lines: list[str] = []
        for w in words:
            candidate = f"{lines[-1]} {w}" if lines else w
            if lines and draw.textlength(candidate, font=font) <= max_width:
                lines[-1] = candidate
            else:
                lines.append(w)
        if len(lines) <= max_lines and all(draw.textlength(x, font=font) <= max_width
                                           for x in lines):
            return font, lines
    font = ImageFont.truetype(str(font_path), sizes[-1])
    return font, [text]


def render_thumbnail(brand: BrandConfig, text: str, font_bold: Path, font_regular: Path,
                     background: Path | None = None, logo: Path | None = None) -> Image.Image:
    c = brand.colors
    if background and background.exists():
        base = cover(Image.open(background).convert("RGB"), SIZE)
        base = base.filter(ImageFilter.GaussianBlur(2))
    else:
        base = gradient(SIZE, c.gradient)
    img = base.convert("RGBA")

    # darken the left side where the text sits
    shade = Image.new("L", (SIZE[0], 1))
    for x in range(SIZE[0]):
        shade.putpixel((x, 0), int(200 * max(0.0, 1 - x / (SIZE[0] * 0.95))))
    overlay = Image.new("RGBA", SIZE, hex_rgb(c.background) + (0,))
    overlay.putalpha(shade.resize(SIZE))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # header: logo + channel name
    x = MARGIN
    if logo and logo.exists():
        mark = Image.open(logo).convert("RGBA")
        mark.thumbnail((84, 84))
        img.alpha_composite(mark, (MARGIN, MARGIN - 8))
        x += mark.width + 22
    name_font = ImageFont.truetype(str(font_bold), 36)
    draw.text((x, MARGIN + 34), brand.channel.name, font=name_font, fill=c.text, anchor="lm")

    # big text, bottom-left, with an accent bar
    font, lines = fit_text(draw, text, font_bold, max_width=int(SIZE[0] * 0.78))
    line_h = int(font.size * 1.18)
    block_h = line_h * len(lines)
    top = SIZE[1] - MARGIN - 28 - block_h
    for i, line in enumerate(lines):
        y = top + i * line_h
        draw.text((MARGIN + 4, y + 5), line, font=font, fill=(0, 0, 0, 140))  # soft shadow
        draw.text((MARGIN, y), line, font=font, fill=c.primary if i == len(lines) - 1 else c.text)
    draw.rounded_rectangle([MARGIN, SIZE[1] - MARGIN - 12, MARGIN + 220, SIZE[1] - MARGIN],
                           radius=6, fill=c.primary)
    return img.convert("RGB")


def run_thumbnail(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    root, assets = env.paths.root, env.settings.assets_dir
    meta_file = root / "metadata.json"
    meta: dict[str, Any] = json.loads(meta_file.read_text(encoding="utf-8")) \
        if meta_file.exists() else {}
    texts = meta.get("thumbnail_texts") or []
    text = texts[0] if texts else (meta.get("title") or env.script.meta.title or "")
    if not text:
        raise StepError("thumbnail", "Chưa có text cho thumbnail. Hãy chạy bước metadata trước.")
    bold, regular = assets / env.brand.fonts.bold, assets / env.brand.fonts.regular
    if not bold.exists():
        raise StepError("thumbnail", f"Không tìm thấy font {env.brand.fonts.bold} trong assets/.")
    images = list_assets(env.settings.backgrounds_dir, IMAGE_EXTENSIONS)
    logo = assets / env.brand.channel.logo if env.brand.channel.logo else None
    img = render_thumbnail(env.brand, text, bold, regular, images[0] if images else None, logo)
    img.save(root / "thumbnail.png", optimize=True)
    return {"text": text, "background": images[0].name if images else "gradient"}
