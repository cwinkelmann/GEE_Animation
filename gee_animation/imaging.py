"""Pure NDVI + colour-ramp helpers (no Earth Engine dependency)."""
from __future__ import annotations

import numpy as np


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    nir = np.asarray(nir, dtype=float)
    red = np.asarray(red, dtype=float)
    denom = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(denom == 0, 0.0, (nir - red) / denom)
    return np.clip(out, -1.0, 1.0)


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def colorize(
    ndvi_arr: np.ndarray, vmin: float, vmax: float, palette: list[str]
) -> np.ndarray:
    if vmax <= vmin:
        raise ValueError("vmax must be greater than vmin")
    stops = np.array([_hex_to_rgb(c) for c in palette], dtype=float)  # (K, 3)
    k = len(stops)
    arr = np.clip(np.asarray(ndvi_arr, dtype=float), vmin, vmax)
    t = (arr - vmin) / (vmax - vmin)          # 0..1
    pos = t * (k - 1)                          # 0..K-1
    lo = np.floor(pos).astype(int)
    lo = np.clip(lo, 0, k - 2) if k > 1 else np.zeros_like(lo)
    hi = np.clip(lo + 1, 0, k - 1)
    frac = (pos - lo)[..., None]
    rgb = stops[lo] * (1 - frac) + stops[hi] * frac
    return np.clip(np.round(rgb), 0, 255).astype(np.uint8)
