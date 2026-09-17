"""lst_rf with the forest on THIS machine (`sharpen: local`).

Earth Engine's part shrinks to two cached GeoTIFF exports per frame — the
Landsat LST composite and the Sentinel-2 predictor stack, both on the 20 m grid
over the frame buffered by TRAIN_BUFFER_M. Everything the spike timed at
sub-second runs here in numpy / scikit-learn: 100 m aggregation, one forest per
CALENDAR MONTH trained across all years of that month (leave-one-year-out
scored), prediction on the 20 m grid, and the conserving residual. Models are
persisted with joblib under <out_dir>/models/<name>/.

Why per calendar month: the index→temperature relation flips with season (a
July built-up pixel is the hottest thing in the frame; in January it is not),
and pooling the years of one month gives a consistent mapping across the
series — which is what makes years comparable in a delta view.
"""
from __future__ import annotations

import csv
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ee
import numpy as np

from . import cache, sharpen
from .local_image import LocalImage
from .products import INDEX_BAND

log = logging.getLogger(__name__)

FILL = -9999.0                      # export fill for masked pixels (a bare export writes 0)
FACTOR = sharpen.COARSE_M // sharpen.FINE_M    # 100 m / 20 m = 5 fine cells per coarse cell
N_TREES, N_SAMPLES_PER_FRAME, MIN_LEAF, BAG = 100, 5000, 5, 0.7
#: Deadline for every Earth Engine API call made here (getDownloadURL and the
#: lazy evaluations behind it). Without it a dropped network hangs the run forever.
EE_DEADLINE_MS = 120_000


def read_geotiff(data: bytes):
    """GeoTIFF bytes → (array (bands, H, W) float32 with NaN, bounds, crs, res)."""
    import rasterio
    with rasterio.MemoryFile(data) as mem, mem.open() as ds:
        arr = ds.read().astype("float32")
        if ds.nodata is not None:
            arr[arr == ds.nodata] = np.nan
        arr[(arr == FILL) | ~np.isfinite(arr)] = np.nan
        b = ds.bounds
        return arr, (b.left, b.bottom, b.right, b.top), str(ds.crs), float(ds.res[0])


def block_mean(a: np.ndarray, k: int) -> np.ndarray:
    """(bands, H, W) → (bands, H//k, W//k), NaN-aware mean of each k×k block."""
    b, h, w = a.shape
    h2, w2 = h // k * k, w // k * k
    x = a[:, :h2, :w2].reshape(b, h2 // k, k, w2 // k, k)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN blocks are expected
        return np.nanmean(x, axis=(2, 4))


def _coarse_samples(lst: np.ndarray, pred: np.ndarray, factor: int):
    lst_c = block_mean(lst[None], factor)[0]
    pred_c = block_mean(pred, factor)
    ok = np.isfinite(lst_c) & np.all(np.isfinite(pred_c), axis=0)
    return pred_c[:, ok].T, lst_c[ok]


def fit_forest(pairs, factor: int = FACTOR, n_samples: int = N_SAMPLES_PER_FRAME, seed: int = 1):
    """Fit one regression forest on the pooled 100 m cells of every (lst, pred) pair."""
    from sklearn.ensemble import RandomForestRegressor
    rng = np.random.default_rng(seed)
    xs, ys = [], []
    for lst, pred in pairs:
        X, y = _coarse_samples(lst, pred, factor)
        if len(y) > n_samples:
            idx = rng.choice(len(y), size=n_samples, replace=False)
            X, y = X[idx], y[idx]
        xs.append(X); ys.append(y)
    X, y = np.concatenate(xs), np.concatenate(ys)
    model = RandomForestRegressor(n_estimators=N_TREES, min_samples_leaf=MIN_LEAF,
                                  max_samples=BAG, n_jobs=-1, random_state=seed)
    model.fit(X, y)
    return model


def sharpen_frame(lst: np.ndarray, pred: np.ndarray, model, factor: int = FACTOR) -> np.ndarray:
    """Predict on the fine grid, then add the coarse residual so every coarse cell
    of the result averages back to the observed LST. Cells without LST stay NaN."""
    h, w = lst.shape
    h2, w2 = h // factor * factor, w // factor * factor
    pf = pred[:, :h2, :w2]
    okf = np.all(np.isfinite(pf), axis=0)
    fit_f = np.full((h2, w2), np.nan, "float32")
    if okf.any():
        fit_f[okf] = model.predict(pf[:, okf].T)
    lst_c = block_mean(lst[None, :h2, :w2], factor)[0]
    fit_c = block_mean(fit_f[None], factor)[0]
    resid = lst_c - fit_c
    sharp = fit_f + np.repeat(np.repeat(resid, factor, 0), factor, 1)
    out = np.full((h, w), np.nan, "float32")
    out[:h2, :w2] = sharp
    return out


def _month_key(label: str) -> str:
    parts = label.split("-")
    return parts[1][:2] if len(parts) > 1 else label


def train_monthly_models(inputs: dict, factor: int = FACTOR,
                         n_samples: int = N_SAMPLES_PER_FRAME, seed: int = 1):
    """{label: (lst, pred)} → ({month: model}, [score rows]). One forest per calendar
    month across its frames; leave-one-frame-out RMSE on the held-out frame's
    coarse cells when there is more than one frame (one frame ≈ one year)."""
    by_month: dict[str, list] = {}
    for label, pair in inputs.items():
        by_month.setdefault(_month_key(label), []).append((label, pair))
    models, scores = {}, []
    for month, items in sorted(by_month.items()):
        pairs = [pair for _, pair in items]
        models[month] = fit_forest(pairs, factor, n_samples, seed)
        n_cells = sum(len(_coarse_samples(l, p, factor)[1]) for l, p in pairs)
        loyo = None
        if len(pairs) > 1:
            errs = []
            for i, (lst, pred) in enumerate(pairs):
                m = fit_forest(pairs[:i] + pairs[i + 1:], factor, n_samples, seed)
                X, y = _coarse_samples(lst, pred, factor)
                if len(y):
                    errs.append(float(np.sqrt(np.mean((m.predict(X) - y) ** 2))))
            loyo = float(np.mean(errs)) if errs else None
        scores.append({"month": month, "n_frames": len(pairs), "n_cells": int(n_cells),
                       "loyo_rmse": loyo})
        log.info("lst_rf local: month %s — %d frame(s), %d cells, LOYO RMSE %s",
                 month, len(pairs), n_cells, f"{loyo:.2f} K" if loyo else "n/a")
    return models, scores


def _download(cfg, frame, image, params, marker: str, fetch) -> bytes:
    key_params = {**params, "local_input": marker}
    with cache.frame_identity(getattr(frame, "label", None), getattr(frame, "source", None)):
        key = cache.thumb_key(cfg, key_params, False)
        data = cache.load(cfg, key)
        if data is None:
            data = fetch(image.getDownloadURL(params))
            cache.store(cfg, key, data)
    return data


def apply(frames, cfg, frame_geom, region_geom, ee_module=ee, fetch=None):
    """Replace every frame's EE image with a locally sharpened `LocalImage`."""
    if getattr(cfg, "sharpen", None) != "local":
        return frames
    from . import render as R
    if hasattr(getattr(ee_module, "data", None), "setDeadline"):
        ee_module.data.setDeadline(EE_DEADLINE_MS)
    if fetch is None:
        fetch = lambda url: R._fetch_url(url, timeout=300)   # noqa: E731
    bounds = R._aoi_bounds(cfg.frame_aoi)
    crs = R._resolve_crs(cfg, bounds) or sharpen._utm_epsg(
        (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
    pbounds, _ = R._project(bounds, [], crs)
    train_geom = frame_geom.buffer(sharpen.TRAIN_BUFFER_M).bounds()
    params = {"region": train_geom, "crs": crs, "scale": sharpen.FINE_M,
              "format": "GEO_TIFF", "filePerBand": False}

    def inputs_for(frame):
        img = frame.image
        lst_img = img.select(INDEX_BAND).unmask(FILL)
        preds = sharpen.s2_predictors(ee_module.Date(img.get("s2_start")),
                                      ee_module.Date(img.get("s2_end")),
                                      train_geom, ee_module).unmask(FILL)
        lst, b, c, res = read_geotiff(_download(cfg, frame, lst_img, params, "lst", fetch))
        pred, _, _, _ = read_geotiff(_download(cfg, frame, preds, params, "predictors", fetch))
        h = min(lst.shape[1], pred.shape[1]); w = min(lst.shape[2], pred.shape[2])
        return frame.label, (lst[0, :h, :w], pred[:, :h, :w]), (b, c, res)

    if not frames:
        return frames
    workers = max(1, int(getattr(cfg, "workers", 4) or 4))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = list(pool.map(inputs_for, frames))
    inputs = {label: pair for label, pair, _ in fetched}
    geo = {label: meta for label, _, meta in fetched}
    models, scores = train_monthly_models(inputs)

    out_dir = Path(getattr(cfg, "out_dir", "out"))
    mdir = out_dir / "models" / cfg.name
    mdir.mkdir(parents=True, exist_ok=True)
    import joblib
    for month, model in models.items():
        joblib.dump(model, mdir / f"{month}.joblib")
    with (out_dir / f"{cfg.name}_models.csv").open("w", newline="") as fh:
        wtr = csv.DictWriter(fh, fieldnames=["month", "n_frames", "n_cells", "loyo_rmse"])
        wtr.writeheader(); wtr.writerows(scores)
    log.info("lst_rf local: %d model(s) written to %s", len(models), mdir)

    out = []
    for frame in frames:
        lst, pred = inputs[frame.label]
        (b, c, res) = geo[frame.label]
        sharp = sharpen_frame(lst, pred, models[_month_key(frame.label)])
        local = LocalImage(sharp, b, c, res).crop(pbounds)
        out.append(frame._replace(image=local))
    return out
