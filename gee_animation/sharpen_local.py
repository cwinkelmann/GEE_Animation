"""lst_rf with the forest on THIS machine (`sharpen: local`).

Earth Engine's part shrinks to two cached GeoTIFF exports per frame — the
Landsat LST composite and the Sentinel-2 predictor stack, both on the 20 m grid
over the frame buffered by TRAIN_BUFFER_M. Everything the spike timed at
sub-second runs here in numpy / scikit-learn: 100 m aggregation, one forest per
FRAME (scored out-of-bag on its own coarse cells), prediction on the 20 m grid,
and the conserving residual. Models are persisted with joblib under
<out_dir>/models/<name>/.

Why per frame and not per calendar month across years (the first design): the
index→temperature relation does not transfer between dates — pooling ten Julys
tripled the 20 m noise and scored 2–5 K leave-one-year-out. See
train_frame_models.
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
                                  max_samples=BAG, n_jobs=-1, random_state=seed,
                                  oob_score=True)
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


def train_frame_models(inputs: dict, factor: int = FACTOR,
                       n_samples: int = N_SAMPLES_PER_FRAME, seed: int = 1):
    """{label: (lst, pred)} → ({label: model}, [score rows]): ONE forest per frame,
    scored out-of-bag on that frame's own coarse cells.

    Why not one model per calendar month across years (the first design): pooling
    ten Julys tripled the 20 m noise (high-frequency sd 1.79 K vs 0.60 K on
    2024-07, fields correlating at only 0.41) and leave-one-year-out errors were
    2–5 K. The index→temperature relation does not transfer between dates — every
    pass has its own sun, wind and soil moisture — so each frame gets its own fit.
    """
    models, scores = {}, []
    for label in sorted(inputs):
        lst, pred = inputs[label]
        model = fit_forest([(lst, pred)], factor, n_samples, seed)
        X, y = _coarse_samples(lst, pred, factor)
        oob_r2 = float(getattr(model, "oob_score_", float("nan")))
        oob_pred = getattr(model, "oob_prediction_", None)
        n_fit = len(model.oob_prediction_) if oob_pred is not None else 0
        # oob_prediction_ lines up with the (possibly subsampled) training rows;
        # RMSE from it is on the fitted subset, which is what the OOB score is too.
        y_fit = y if n_fit == len(y) else None
        oob_rmse = (float(np.sqrt(np.nanmean((oob_pred - y_fit) ** 2)))
                    if y_fit is not None and n_fit else float("nan"))
        if not np.isfinite(oob_rmse):
            oob_rmse = float(np.sqrt(max(0.0, (1 - oob_r2)) * np.var(y))) if len(y) else float("nan")
        models[label] = model
        scores.append({"label": label, "n_cells": int(len(y)), "oob_r2": oob_r2, "oob_rmse": oob_rmse})
        log.info("lst_rf local: %s — %d cells, OOB R² %.2f, OOB RMSE %.2f K",
                 label, len(y), oob_r2, oob_rmse)
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
    models, scores = train_frame_models(inputs)

    out_dir = Path(getattr(cfg, "out_dir", "out"))
    mdir = out_dir / "models" / cfg.name
    mdir.mkdir(parents=True, exist_ok=True)
    import joblib
    for label, model in models.items():
        joblib.dump(model, mdir / f"{label}.joblib")
    with (out_dir / f"{cfg.name}_models.csv").open("w", newline="") as fh:
        wtr = csv.DictWriter(fh, fieldnames=["label", "n_cells", "oob_r2", "oob_rmse"])
        wtr.writeheader(); wtr.writerows(scores)
    log.info("lst_rf local: %d model(s) written to %s", len(models), mdir)

    out = []
    for frame in frames:
        lst, pred = inputs[frame.label]
        (b, c, res) = geo[frame.label]
        sharp = sharpen_frame(lst, pred, models[frame.label])
        local = LocalImage(sharp, b, c, res).crop(pbounds)
        out.append(frame._replace(image=local))
    return out
