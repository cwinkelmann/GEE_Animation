"""Render NDVI frames to annotated PNGs and assemble MP4 + GIF."""
from __future__ import annotations

import io
import logging
from pathlib import Path
from urllib.request import urlopen

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw

from .imaging import colorize

log = logging.getLogger(__name__)


NODATA_RGB = (240, 240, 240)


def _fetch_thumbnail(image, cfg, geometry):
    """Download the NDVI band via EE getThumbURL.

    Returns ``(ndvi, valid)`` where ``valid`` is a boolean mask (True where
    EE returned data; masked/cloud pixels are transparent → False).
    """
    url = image.select("NDVI").getThumbURL(
        {
            "min": cfg.ndvi_min,
            "max": cfg.ndvi_max,
            "dimensions": cfg.dimensions,
            "region": geometry,
            "format": "png",
        }
    )
    with urlopen(url) as resp:  # noqa: S310 (trusted EE URL)
        data = resp.read()
    img = Image.open(io.BytesIO(data)).convert("LA")   # grayscale + alpha
    arr = np.asarray(img, dtype=float)
    gray = arr[..., 0] / 255.0
    valid = arr[..., 1] > 0                              # True where data present
    # EE scales min..max into 0..255; map back to NDVI units.
    ndvi = cfg.ndvi_min + gray * (cfg.ndvi_max - cfg.ndvi_min)
    return ndvi, valid


def apply_nodata(rgb: np.ndarray, valid: np.ndarray, color=NODATA_RGB) -> np.ndarray:
    out = rgb.copy()
    out[~valid] = color
    return out


def add_colorbar(rgb: np.ndarray, cfg) -> np.ndarray:
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = 4, 4
    ramp = colorize(
        np.linspace(cfg.ndvi_min, cfg.ndvi_max, bar_w)[None, :],
        cfg.ndvi_min, cfg.ndvi_max, cfg.palette,
    )[0]  # (bar_w, 3)
    for i in range(bar_w):
        c = tuple(int(v) for v in ramp[i])
        draw.line([(x0 + i, y0), (x0 + i, y0 + bar_h)], fill=c)
    draw.rectangle([x0, y0, x0 + bar_w, y0 + bar_h], outline=(255, 255, 255, 255))
    draw.text((x0, y0 + bar_h + 1), f"NDVI {cfg.ndvi_min:g}..{cfg.ndvi_max:g}",
              fill=(255, 255, 255, 255))
    return np.asarray(img)


def annotate(rgb: np.ndarray, label: str) -> np.ndarray:
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    bar_h = max(12, h // 12)
    draw.rectangle([0, h - bar_h, w, h], fill=(0, 0, 0, 140))
    draw.text((4, h - bar_h + 1), label, fill=(255, 255, 255, 255))
    return np.asarray(img)


def _pad_to_even(frame: np.ndarray) -> np.ndarray:
    """Pad a frame's width/height up to the next even number (edge-replicated).

    libx264 requires both dimensions to be even; AOIs frequently yield an odd
    width or height (e.g. 768x577). Padding by at most 1px avoids rescaling the
    whole frame (and imageio's resize warning).
    """
    h, w = frame.shape[:2]
    if h % 2 or w % 2:
        frame = np.pad(frame, ((0, h % 2), (0, w % 2), (0, 0)), mode="edge")
    return frame


def _write_mp4(path: Path, frames: list[np.ndarray], fps: int) -> None:
    even = [_pad_to_even(f) for f in frames]
    imageio.mimsave(path, even, fps=fps, macro_block_size=1)


def _write_gif(path: Path, frames: list[np.ndarray], fps: int) -> None:
    # imageio>=2.28: GIF per-frame duration is in milliseconds
    imageio.mimsave(path, frames, format="GIF", duration=1000.0 / fps, loop=0)


def assemble(frames_rgb: list[np.ndarray], cfg) -> list[Path]:
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gif_path = out_dir / f"{cfg.name}.gif"
    mp4_path = out_dir / f"{cfg.name}.mp4"
    paths: list[Path] = []
    try:
        _write_mp4(mp4_path, frames_rgb, cfg.fps)
        paths.append(mp4_path)
    except Exception as exc:  # ffmpeg missing / encode error
        log.warning("MP4 write failed (%s); producing GIF only", exc)
    _write_gif(gif_path, frames_rgb, cfg.fps)
    paths.append(gif_path)
    return paths


def render(frames, cfg, fetch=_fetch_thumbnail, geometry=None) -> list[Path]:
    rgb_frames: list[np.ndarray] = []
    for frame in frames:
        ndvi_arr, valid = fetch(frame.image, cfg, geometry)
        rgb = colorize(ndvi_arr, cfg.ndvi_min, cfg.ndvi_max, cfg.palette)
        rgb = apply_nodata(rgb, valid)
        rgb = annotate(rgb, frame.label)
        rgb = add_colorbar(rgb, cfg)
        rgb_frames.append(rgb)
    return assemble(rgb_frames, cfg)
