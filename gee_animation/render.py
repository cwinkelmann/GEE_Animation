"""Render index frames to annotated PNGs and assemble MP4 + GIF."""
from __future__ import annotations

import io
import logging
import math
from pathlib import Path
from urllib.request import urlopen

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw

from .aoi import _load_geojson_geometry, _read_shapefile_geometry
from .imaging import colorize
from .products import INDICES

log = logging.getLogger(__name__)


NODATA_RGB = (240, 240, 240)
REGION_OUTLINE_RGB = (255, 235, 59)   # amber — high contrast over the index palette


def _index_meta(cfg):
    """Registry entry for cfg.index (bands/formula/composite), or None if unknown."""
    return INDICES.get(getattr(cfg, "index", None))


def _is_composite(cfg) -> bool:
    meta = _index_meta(cfg)
    return bool(meta and meta.composite)


def _thumb_params(cfg, geometry) -> dict:
    """Build getThumbURL parameters with single-int dimensions to preserve aspect ratio.

    EE fits the largest side to `dimensions` and scales the other proportionally,
    preserving the frame's aspect ratio.
    """
    # single-int `dimensions` -> EE fits the largest side and preserves aspect ratio
    return {
        "min": cfg.viz_min,
        "max": cfg.viz_max,
        "dimensions": cfg.dimensions,
        "region": geometry,
        "format": "png",
    }


def _fetch_thumbnail(image, cfg, geometry):
    """Download the frame via EE getThumbURL.

    For a normal index returns ``(index_arr, valid)`` — a 2-D array in INDEX units
    plus a validity mask. For a composite (rgb/cir) returns ``(rgb_arr, valid)``
    where ``rgb_arr`` is H×W×3 (0..255, already colour). ``valid`` is False where
    EE returned no data (masked/cloud pixels are transparent).
    """
    if _is_composite(cfg):
        url = image.select(["R", "G", "B"]).getThumbURL(_thumb_params(cfg, geometry))
        with urlopen(url) as resp:  # noqa: S310 (trusted EE URL)
            data = resp.read()
        arr = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"), dtype=float)
        return arr[..., :3], arr[..., 3] > 0

    url = image.select("INDEX").getThumbURL(_thumb_params(cfg, geometry))
    with urlopen(url) as resp:  # noqa: S310 (trusted EE URL)
        data = resp.read()
    img = Image.open(io.BytesIO(data)).convert("LA")   # grayscale + alpha
    arr = np.asarray(img, dtype=float)
    gray = arr[..., 0] / 255.0
    valid = arr[..., 1] > 0                              # True where data present
    # EE scales min..max into 0..255; map back to INDEX units.
    index_arr = cfg.viz_min + gray * (cfg.viz_max - cfg.viz_min)
    return index_arr, valid


def apply_nodata(rgb: np.ndarray, valid: np.ndarray, color=NODATA_RGB) -> np.ndarray:
    out = rgb.copy()
    out[~valid] = color
    return out


def add_colorbar(rgb: np.ndarray, cfg, y_offset: int = 4) -> np.ndarray:
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = 4, y_offset
    ramp = colorize(
        np.linspace(cfg.viz_min, cfg.viz_max, bar_w)[None, :],
        cfg.viz_min, cfg.viz_max, cfg.palette,
    )[0]  # (bar_w, 3)
    for i in range(bar_w):
        c = tuple(int(v) for v in ramp[i])
        draw.line([(x0 + i, y0), (x0 + i, y0 + bar_h)], fill=c)
    draw.rectangle([x0, y0, x0 + bar_w, y0 + bar_h], outline=(255, 255, 255, 255))
    draw.text((x0, y0 + bar_h + 1), f"{cfg.index.upper()} {cfg.viz_min:g}..{cfg.viz_max:g}",
              fill=(255, 255, 255, 255))
    return np.asarray(img)


def _info_text(cfg) -> str:
    """One-line 'INDEX = formula   bands: …' describing how the frame was made."""
    meta = _index_meta(cfg)
    name = getattr(cfg, "index", "").upper()
    head = f"{name} = {meta.formula}" if (meta and meta.formula) else name
    bands = meta.bands if meta else ""
    return f"{head}   bands: {bands}" if bands else head


def draw_info_bar(rgb: np.ndarray, text: str) -> np.ndarray:
    """Draw a translucent top bar naming the bands used and the formula (if any)."""
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    bar_h = max(12, h // 12)
    draw.rectangle([0, 0, w, bar_h], fill=(0, 0, 0, 140))
    draw.text((4, 1), text, fill=(255, 255, 255, 255))
    return np.asarray(img)


def annotate(rgb: np.ndarray, label: str) -> np.ndarray:
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    bar_h = max(12, h // 12)
    draw.rectangle([0, h - bar_h, w, h], fill=(0, 0, 0, 140))
    draw.text((4, h - bar_h + 1), label, fill=(255, 255, 255, 255))
    return np.asarray(img)


def _geom_rings(geom: dict) -> list:
    """Rings (each a list of (lon, lat)) for a GeoJSON Polygon/MultiPolygon/LineString."""
    t = geom["type"]
    if t == "Polygon":
        return [[(x, y) for x, y in ring] for ring in geom["coordinates"]]
    if t == "MultiPolygon":
        return [[(x, y) for x, y in ring]
                for poly in geom["coordinates"] for ring in poly]
    if t == "LineString":
        return [[(x, y) for x, y in geom["coordinates"]]]
    raise ValueError(f"unsupported region geometry type for overlay: {t}")


def _aoi_geom_dict(aoi_cfg: dict) -> dict:
    """GeoJSON geometry (EPSG:4326) for a non-bbox AOI: shapefile or GeoJSON."""
    if aoi_cfg.get("shapefile"):
        return _read_shapefile_geometry(aoi_cfg["shapefile"])
    return _load_geojson_geometry(aoi_cfg["geojson"])


def _aoi_bounds(aoi_cfg: dict) -> tuple:
    """(minLon, minLat, maxLon, maxLat) for an AOI (bbox, GeoJSON, or shapefile)."""
    if aoi_cfg.get("bbox"):
        b = aoi_cfg["bbox"]
        return (b[0], b[1], b[2], b[3])
    rings = _geom_rings(_aoi_geom_dict(aoi_cfg))
    xs = [x for ring in rings for x, _ in ring]
    ys = [y for ring in rings for _, y in ring]
    return (min(xs), min(ys), max(xs), max(ys))


def _region_rings(aoi_cfg: dict) -> list:
    """Rings (list of (lon, lat)) for a region AOI (bbox rectangle, GeoJSON, or shapefile)."""
    if aoi_cfg.get("bbox"):
        mnx, mny, mxx, mxy = aoi_cfg["bbox"]
        return [[(mnx, mny), (mxx, mny), (mxx, mxy), (mnx, mxy), (mnx, mny)]]
    return _geom_rings(_aoi_geom_dict(aoi_cfg))


def draw_region(rgb: np.ndarray, bounds: tuple, rings: list,
                color=REGION_OUTLINE_RGB, width: int = 2) -> np.ndarray:
    """Draw region polygon outlines onto an RGB frame.

    `bounds` is the frame extent (minLon, minLat, maxLon, maxLat); the EE thumbnail
    is rendered in linear EPSG:4326 over this extent, so lon/lat map to pixels
    linearly (top row = maxLat).
    """
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img)
    h, w = rgb.shape[:2]
    minx, miny, maxx, maxy = bounds
    dx = (maxx - minx) or 1.0
    dy = (maxy - miny) or 1.0
    for ring in rings:
        pts = [((lon - minx) / dx * w, (maxy - lat) / dy * h) for lon, lat in ring]
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=width)
    return np.asarray(img)


def _nice_distance(meters: float) -> float:
    """Round a distance down to a cartographer-friendly 1/2/5 × 10ᵏ value."""
    exp = math.floor(math.log10(meters))
    base = 10 ** exp
    for mult in (5, 2, 1):
        if meters >= mult * base:
            return mult * base
    return base


def draw_scale_bar(rgb: np.ndarray, bounds: tuple, target_frac: float = 0.25,
                   color=(255, 255, 255)) -> np.ndarray:
    """Draw a ground-distance scale bar (bottom-right) onto an RGB frame.

    `bounds` is the frame extent (minLon, minLat, maxLon, maxLat); the thumbnail
    is linear EPSG:4326 over it, so metres-per-pixel follows from the longitude
    span at the frame's mid-latitude. A "nice" round distance near `target_frac`
    of the frame width is chosen for the bar length and label.
    """
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    h, w = rgb.shape[:2]
    minx, miny, maxx, maxy = bounds
    m_per_deg_lon = 111320.0 * math.cos(math.radians((miny + maxy) / 2.0))
    frame_w_m = (maxx - minx) * m_per_deg_lon
    if w < 24 or frame_w_m <= 0:      # too small to annotate meaningfully
        return np.asarray(img)
    nice_m = _nice_distance(frame_w_m * target_frac)
    bar_px = int(round(nice_m / (frame_w_m / w)))
    if bar_px < 1:
        return np.asarray(img)
    label = f"{nice_m / 1000:g} km" if nice_m >= 1000 else f"{nice_m:g} m"

    draw = ImageDraw.Draw(img, "RGBA")
    bar_h = max(12, h // 12)          # bottom label bar height (see annotate)
    margin, tick = 6, 4
    x1 = w - margin
    x0 = x1 - bar_px
    y = h - bar_h - margin            # sit just above the bottom month-label bar
    tb = draw.textbbox((0, 0), label)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    panel_top = y - tick - th - 4
    draw.rectangle([x0 - 4, panel_top, x1 + 4, y + 4], fill=(0, 0, 0, 120))
    draw.line([(x0, y), (x1, y)], fill=color, width=2)
    draw.line([(x0, y - tick), (x0, y)], fill=color, width=2)   # end ticks
    draw.line([(x1, y - tick), (x1, y)], fill=color, width=2)
    draw.text(((x0 + x1) / 2 - tw / 2, panel_top + 2), label, fill=color)
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


def _write_frames(out_dir: Path, name: str, frames_rgb: list[np.ndarray],
                  labels: list[str]) -> list[Path]:
    """Save each annotated frame as its own PNG, named ``{name}_{label}.png``.

    Returns the frame paths in order so callers can offer the single images for
    download alongside the assembled MP4/GIF.
    """
    paths: list[Path] = []
    for rgb, label in zip(frames_rgb, labels):
        p = out_dir / f"{name}_{label}.png"
        Image.fromarray(rgb.astype(np.uint8), "RGB").save(p)
        paths.append(p)
    return paths


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
    frame_aoi = getattr(cfg, "frame_aoi", None)
    bounds = _aoi_bounds(frame_aoi) if frame_aoi else None
    draw_overlay = getattr(cfg, "draw_region", False) and getattr(cfg, "region_aoi", None)
    if draw_overlay:
        rings = _region_rings(cfg.region_aoi)
    composite = _is_composite(cfg)
    info_text = _info_text(cfg)
    rgb_frames: list[np.ndarray] = []
    for frame in frames:
        arr, valid = fetch(frame.image, cfg, geometry)
        # composite fetch already returns colour (H×W×3); an index returns 2-D.
        rgb = arr if arr.ndim == 3 else colorize(arr, cfg.viz_min, cfg.viz_max, cfg.palette)
        rgb = apply_nodata(rgb, valid)
        rgb = annotate(rgb, frame.label)
        top_h = max(12, rgb.shape[0] // 12)          # height of the top info bar
        if not composite:                            # colorbar needs a palette
            rgb = add_colorbar(rgb, cfg, y_offset=top_h + 4)
        rgb = draw_info_bar(rgb, info_text)
        if draw_overlay:
            rgb = draw_region(rgb, bounds, rings)
        if bounds is not None:
            rgb = draw_scale_bar(rgb, bounds)
        rgb_frames.append(rgb)
    paths = assemble(rgb_frames, cfg)
    paths += _write_frames(Path(cfg.out_dir), cfg.name, rgb_frames,
                           [f.label for f in frames])
    return paths
