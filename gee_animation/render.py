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


def _fetch_thumbnail(image, cfg, geometry):
    """Download the NDVI band as a numpy array via EE getThumbURL."""
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
    img = Image.open(io.BytesIO(data)).convert("L")
    # EE scales min..max into 0..255; map back to NDVI units.
    arr = np.asarray(img, dtype=float) / 255.0
    return cfg.ndvi_min + arr * (cfg.ndvi_max - cfg.ndvi_min)


def annotate(rgb: np.ndarray, label: str) -> np.ndarray:
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    bar_h = max(12, h // 12)
    draw.rectangle([0, h - bar_h, w, h], fill=(0, 0, 0, 140))
    draw.text((4, h - bar_h + 1), label, fill=(255, 255, 255, 255))
    return np.asarray(img)


def _write_mp4(path: Path, frames: list[np.ndarray], fps: int) -> None:
    imageio.mimsave(path, frames, fps=fps, macro_block_size=None)


def _write_gif(path: Path, frames: list[np.ndarray], fps: int) -> None:
    imageio.mimsave(path, frames, duration=1.0 / fps)


def assemble(frames_rgb: list[np.ndarray], cfg, ee_module=None) -> list[Path]:
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


def render(frames, cfg, ee_module=None, fetch=_fetch_thumbnail, geometry=None) -> list[Path]:
    rgb_frames: list[np.ndarray] = []
    for frame in frames:
        ndvi_arr = fetch(frame.image, cfg, geometry)
        rgb = colorize(ndvi_arr, cfg.ndvi_min, cfg.ndvi_max, cfg.palette)
        rgb_frames.append(annotate(rgb, frame.label))
    return assemble(rgb_frames, cfg)
