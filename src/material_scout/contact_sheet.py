from __future__ import annotations

import html
import io
import math
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .models import AssetCandidate

Image.MAX_IMAGE_PIXELS = 40_000_000


def render_discovery_artifacts(
    candidates: list[AssetCandidate], output_dir: Path, page_size: int = 20
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cover_dir = output_dir / "covers"
    cover_dir.mkdir(exist_ok=True)
    downloaded = [_download_cover(candidate, cover_dir) for candidate in candidates]

    pages: list[Path] = []
    for page_number in range(max(1, math.ceil(len(candidates) / page_size))):
        start = page_number * page_size
        page_candidates = candidates[start : start + page_size]
        page_covers = downloaded[start : start + page_size]
        page_path = output_dir / f"contact-sheet-{page_number + 1:02d}.jpg"
        _render_page(page_candidates, page_covers, page_path, start)
        pages.append(page_path)

    _render_html(candidates, downloaded, output_dir / "results.html")
    return pages


def _download_cover(candidate: AssetCandidate, cover_dir: Path) -> Path | None:
    if not candidate.thumbnail_url:
        return None
    destination = cover_dir / f"{_safe_name(candidate.candidate_id)}.jpg"
    try:
        request = urllib.request.Request(
            candidate.thumbnail_url,
            headers={"User-Agent": "Mozilla/5.0 MaterialScout/0.1"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(8_000_000)
        with Image.open(io.BytesIO(data)) as source:
            source.convert("RGB").save(destination, "JPEG", quality=86)
        return destination
    except (OSError, ValueError):
        return None


def _render_page(
    candidates: list[AssetCandidate],
    covers: list[Path | None],
    destination: Path,
    index_offset: int,
) -> None:
    columns = 4
    tile_width, tile_height = 320, 260
    cover_height = 180
    rows = max(1, math.ceil(len(candidates) / columns))
    canvas = Image.new("RGB", (columns * tile_width, rows * tile_height), "#0f172a")
    draw = ImageDraw.Draw(canvas)
    title_font = _font(17)
    meta_font = _font(15)

    for local_index, candidate in enumerate(candidates):
        column, row = local_index % columns, local_index // columns
        x, y = column * tile_width, row * tile_height
        cover = _cover_image(covers[local_index], tile_width - 12, cover_height - 10)
        canvas.paste(cover, (x + 6, y + 5))
        draw.rectangle((x + 6, y + 5, x + 72, y + 34), fill="#0f172a")
        draw.text(
            (x + 12, y + 8),
            f"#{index_offset + local_index + 1:03d}",
            font=meta_font,
            fill="#f8fafc",
        )
        source_label = candidate.source.upper()
        duration = _duration(candidate.duration_seconds)
        draw.text((x + 8, y + cover_height + 2), f"{source_label}  {duration}", font=meta_font, fill="#67e8f9")
        for line_index, line in enumerate(
            _wrap_text(draw, candidate.title, title_font, tile_width - 16, max_lines=2)
        ):
            draw.text(
                (x + 8, y + cover_height + 24 + line_index * 22),
                line,
                font=title_font,
                fill="#f8fafc",
            )
    canvas.save(destination, "JPEG", quality=88)


def _cover_image(path: Path | None, width: int, height: int) -> Image.Image:
    if path:
        try:
            with Image.open(path) as image:
                return ImageOps.fit(image.convert("RGB"), (width, height))
        except OSError:
            pass
    placeholder = Image.new("RGB", (width, height), "#334155")
    draw = ImageDraw.Draw(placeholder)
    draw.text((20, height // 2 - 10), "NO PREVIEW", font=_font(18), fill="#cbd5e1")
    return placeholder


def _render_html(candidates: list[AssetCandidate], covers: list[Path | None], destination: Path) -> None:
    cards = []
    for index, (candidate, cover) in enumerate(zip(candidates, covers), start=1):
        image_src = f"covers/{cover.name}" if cover else ""
        cards.append(
            f"""<article><div class="badge">#{index:03d} {html.escape(candidate.source)}</div>
            {f'<img src="{html.escape(image_src)}" alt="">' if image_src else '<div class="missing">NO PREVIEW</div>'}
            <h2>{html.escape(candidate.title)}</h2>
            <p>{html.escape(candidate.candidate_id)} · {_duration(candidate.duration_seconds)}</p>
            <a href="{html.escape(candidate.source_url)}">Open source</a></article>"""
        )
    document = """<!doctype html><meta charset="utf-8"><title>Material Scout</title>
    <style>body{font-family:system-ui;background:#0f172a;color:#f8fafc;margin:24px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:18px}article{background:#1e293b;padding:12px;border-radius:12px;position:relative}img,.missing{width:100%;aspect-ratio:16/9;object-fit:cover;background:#334155;display:grid;place-items:center}.badge{position:absolute;background:#020617dd;padding:5px 8px;border-radius:6px}h2{font-size:16px}p{color:#94a3b8}a{color:#67e8f9}</style>
    <h1>Material Scout candidates</h1><div class="grid">""" + "".join(cards) + "</div>"
    destination.write_text(document, encoding="utf-8")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    lines: list[str] = []
    remaining = value.strip()
    while remaining and len(lines) < max_lines:
        end = 1
        while end <= len(remaining) and draw.textlength(remaining[:end], font=font) <= max_width:
            end += 1
        end = max(1, end - 1)
        line = remaining[:end].rstrip()
        remaining = remaining[end:].lstrip()
        if len(lines) == max_lines - 1 and remaining:
            while line and draw.textlength(line + "…", font=font) > max_width:
                line = line[:-1]
            line += "…"
            remaining = ""
        lines.append(line)
    return lines or [""]


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
