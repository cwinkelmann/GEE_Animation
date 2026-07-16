"""Pure NDVI + colour-ramp + thermal-sharpening helpers (no Earth Engine)."""
from __future__ import annotations

import numpy as np


def _block_mean(arr: np.ndarray, factor: int) -> np.ndarray:
    """Mean over non-overlapping factor×factor blocks -> the coarse grid."""
    h, w = arr.shape
    hc, wc = h // factor, w // factor
    return arr[:hc * factor, :wc * factor].reshape(hc, factor, wc, factor).mean(axis=(1, 3))


def _linfit(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Least-squares (slope, offset) for y = a·x + b over finite pairs."""
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 2 or np.ptp(x) == 0:          # no usable gradient -> flat fit
        return 0.0, (float(y.mean()) if len(y) else 0.0)
    a, b = np.polyfit(x, y, 1)
    return float(a), float(b)


def distrad_sharpen(lst_coarse: np.ndarray, predictor_fine: np.ndarray,
                    factor: int) -> np.ndarray:
    """Thermal sharpening (TsHARP / DisTrad; Kustas 2003, Agam 2007).

    Fit ``LST = a·predictor + b`` at the coarse scale (per scene), apply it at the
    fine predictor scale, then add the coarse residual back (nearest / piecewise
    constant) so the sharpened field **aggregates back to ``lst_coarse`` exactly** —
    a temperature, not just a texture. This residual correction is the step the old
    hand-tuned slope lacked.

    ``lst_coarse`` is (H, W); ``predictor_fine`` is (H·factor, W·factor); returns the
    fine-grid sharpened LST.
    """
    lst_coarse = np.asarray(lst_coarse, dtype=float)
    predictor_fine = np.asarray(predictor_fine, dtype=float)
    pred_coarse = _block_mean(predictor_fine, factor)
    a, b = _linfit(pred_coarse.ravel(), lst_coarse.ravel())
    residual_coarse = lst_coarse - (a * pred_coarse + b)
    residual_fine = np.repeat(np.repeat(residual_coarse, factor, 0), factor, 1)
    fh, fw = residual_fine.shape
    return a * predictor_fine[:fh, :fw] + b + residual_fine


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
