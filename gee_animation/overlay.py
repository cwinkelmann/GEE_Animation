"""Raster overlay: iso-lines of an external GeoTIFF drawn over every frame.

Built for the windthrow work — the density of predicted fallen stems (metres of
stem per 20 m cell, rasterised from the drone stem map) outlined on the thermal,
NDVI and radar animations at the levels that defined "windthrow cells" in the
analysis (20 m) and the dense core (60 m). The mechanism is generic: any
single-band raster in any CRS, any ascending list of levels, one colour each.

Config::

    overlay:
      raster: docs/aoi/r12/r12_stem_density_20m.tif
      levels: [20, 60]                 # a line around every cell >= level
      colors: ["#ffb000", "#ff2a2a"]   # one per level (default ramp if omitted)
      line_px: 2                       # at 1080p; scaled with the output height
      alpha: 0.9

The lines are georeferenced like the region outline: the raster is reprojected
onto the output pixel grid of the frame (nearest neighbour, so a 20 m cell stays a
block and its outline follows the cell edges), the cells at or above a level are
outlined, and the outline is composited before the region outline. It is drawn
locally over pixels already fetched, so it is a cache hit (`cache.CLIENT_SIDE_FIELDS`).
Needs the `ml` extra (rasterio).
"""
from __future__ import annotations

import os

import numpy as np

from .config import ConfigError

DEFAULT_COLORS = ["#ffb000", "#ff2a2a", "#8b0000", "#4a0000"]
DEFAULT_LINE_PX = 2          # at 1080 px output height
DEFAULT_ALPHA = 0.9


def parse(raw) -> dict | None:
    """Validate the `overlay:` mapping from a config (or None) into a plain spec dict."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("overlay must be a mapping with raster: and levels:")
    raster = raw.get("raster")
    if not raster or not isinstance(raster, str):
        raise ConfigError("overlay.raster is required: path to a single-band GeoTIFF")
    if not os.path.exists(raster):
        raise ConfigError(f"overlay.raster not found: {raster}")
    levels = raw.get("levels")
    if not isinstance(levels, (list, tuple)) or not levels:
        raise ConfigError("overlay.levels must be a non-empty list of numbers")
    try:
        levels = [float(v) for v in levels]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"overlay.levels must be numbers: {exc}") from exc
    if any(b <= a for a, b in zip(levels, levels[1:])):
        raise ConfigError(f"overlay.levels must be strictly ascending, got {levels}")
    colors = raw.get("colors")
    if colors is None:
        if len(levels) > len(DEFAULT_COLORS):
            raise ConfigError(f"overlay: give colors: for more than {len(DEFAULT_COLORS)} levels")
        colors = DEFAULT_COLORS[:len(levels)]
    if not isinstance(colors, (list, tuple)) or len(colors) != len(levels):
        raise ConfigError("overlay.colors needs one colour per level")
    colors = [str(c) for c in colors]
    for c in colors:
        _hex_to_rgb(c)       # validates
    line_px = int(raw.get("line_px", DEFAULT_LINE_PX) or DEFAULT_LINE_PX)
    if line_px < 1:
        raise ConfigError("overlay.line_px must be >= 1")
    alpha = float(raw.get("alpha", DEFAULT_ALPHA))
    if not 0 < alpha <= 1:
        raise ConfigError("overlay.alpha must be in (0, 1]")
    return {"raster": raster, "levels": levels, "colors": colors, "line_px": line_px, "alpha": alpha}


def _hex_to_rgb(color: str) -> tuple:
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        raise ConfigError(f"overlay colour must be #rgb or #rrggbb, got {color!r}")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError as exc:
        raise ConfigError(f"overlay colour must be hex, got {color!r}") from exc


def _on_output_grid(spec: dict, bounds: tuple, crs, out_hw: tuple) -> np.ndarray:
    """The raster resampled (nearest) onto the frame's output pixel grid."""
    try:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.transform import from_bounds
        from rasterio.warp import reproject
    except ImportError as exc:        # pragma: no cover - exercised via monkeypatch
        raise RuntimeError("overlay needs rasterio: pip install -e '.[ml]'") from exc
    h, w = out_hw
    minx, miny, maxx, maxy = bounds
    dst_transform = from_bounds(minx, miny, maxx, maxy, w, h)
    dst_crs = crs or "EPSG:4326"
    out = np.full((h, w), np.nan, "float32")
    with rasterio.open(spec["raster"]) as ds:
        src = ds.read(1).astype("float32")
        if ds.nodata is not None:
            src[src == ds.nodata] = np.nan
        reproject(src, out, src_transform=ds.transform, src_crs=ds.crs,
                  dst_transform=dst_transform, dst_crs=dst_crs,
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.nearest)
    return out


def level_masks(spec: dict, bounds: tuple, crs, out_hw: tuple) -> list:
    """[(mask, rgb)] per level: a float32 0/1 mask (`out_hw`) outlining the cells at
    or above the level, and the level's colour. Built once per run by `render`."""
    grid = _on_output_grid(spec, bounds, crs, out_hw)
    px = max(1, int(round(spec["line_px"] * out_hw[0] / 1080)))
    masks = []
    for level, color in zip(spec["levels"], spec["colors"]):
        above = np.isfinite(grid) & (grid >= level)
        edge = above & ~_erode(above)          # inner boundary: cells above whose 4-neighbourhood is not
        for _ in range(px - 1):
            edge = _dilate(edge)
        masks.append((edge.astype(np.float32), _hex_to_rgb(color)))
    return masks


def _shifted(a: np.ndarray, dy: int, dx: int, fill: bool) -> np.ndarray:
    """`a` shifted by (dy, dx) with `fill` outside — plain numpy, no scipy dependency."""
    out = np.full_like(a, fill)
    h, w = a.shape
    ys, yd = (slice(dy, h), slice(0, h - dy)) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
    xs, xd = (slice(dx, w), slice(0, w - dx)) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
    out[ys, xs] = a[yd, xd]
    return out


def _erode(a: np.ndarray) -> np.ndarray:
    """4-neighbour binary erosion with False outside the array."""
    return a & _shifted(a, 1, 0, False) & _shifted(a, -1, 0, False) & _shifted(a, 0, 1, False) & _shifted(a, 0, -1, False)


def _dilate(a: np.ndarray) -> np.ndarray:
    """4-neighbour binary dilation."""
    return a | _shifted(a, 1, 0, False) | _shifted(a, -1, 0, False) | _shifted(a, 0, 1, False) | _shifted(a, 0, -1, False)
