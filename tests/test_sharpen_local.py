"""Local (scikit-learn) thermal sharpening: Earth Engine serves composites as
GeoTIFFs; training, prediction and the conserving residual run on this machine.
Numerics are exercised on synthetic arrays; EE is a permissive fake."""
import io
import types

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from gee_animation import sharpen_local
from gee_animation.local_image import LocalImage
from gee_animation.compositing import Frame


def _tif_bytes(arr, x0=500000.0, y0=5830000.0, res=20.0, crs="EPSG:32633"):
    arr = np.asarray(arr, dtype="float32")
    if arr.ndim == 2:
        arr = arr[None]
    buf = io.BytesIO()
    with rasterio.MemoryFile() as mem:
        with mem.open(driver="GTiff", height=arr.shape[1], width=arr.shape[2], count=arr.shape[0],
                      dtype="float32", crs=crs, transform=from_origin(x0, y0, res, res)) as ds:
            ds.write(arr)
        buf.write(mem.read())
    return buf.getvalue()


# --- reading exports -----------------------------------------------------------

def test_read_geotiff_turns_the_export_fill_value_into_nan_and_keeps_georeferencing():
    a = np.full((10, 10), 25.0, "float32"); a[0, 0] = sharpen_local.FILL
    arr, bounds, crs, res = sharpen_local.read_geotiff(_tif_bytes(a))
    assert arr.shape == (1, 10, 10) and np.isnan(arr[0, 0, 0]) and arr[0, 5, 5] == 25.0
    assert bounds == (500000.0, 5830000.0 - 200.0, 500000.0 + 200.0, 5830000.0)
    assert crs == "EPSG:32633" and res == 20.0


# --- the numerics -----------------------------------------------------------------

def _synthetic(seed=0, h=50, w=60):
    rng = np.random.default_rng(seed)
    pred = rng.random((5, h, w)).astype("float32")
    lst = (20 + 10 * pred[2] - 4 * pred[0] + rng.normal(0, 0.3, (h, w))).astype("float32")
    return lst, pred


def test_block_mean_aggregates_5x5_cells_nan_aware():
    a = np.ones((1, 10, 10), "float32"); a[0, 0, 0] = np.nan; a[0, 0, 1] = 3.0
    m = sharpen_local.block_mean(a, 5)
    assert m.shape == (1, 2, 2)
    assert m[0, 0, 0] == pytest.approx((3.0 + 23 * 1.0) / 24)
    assert m[0, 1, 1] == 1.0


def test_fit_forest_then_sharpen_conserves_every_coarse_cell_exactly():
    lst, pred = _synthetic()
    model = sharpen_local.fit_forest([(lst, pred)], factor=5, n_samples=200, seed=1)
    sharp = sharpen_local.sharpen_frame(lst, pred, model, factor=5)
    assert sharp.shape == lst.shape and sharp.dtype == np.float32
    agg = sharpen_local.block_mean(sharp[None], 5)[0]
    coarse = sharpen_local.block_mean(lst[None], 5)[0]
    assert np.nanmax(np.abs(agg - coarse)) < 1e-3
    # and the fine field really varies inside a cell (it is not just the blocks)
    assert np.nanstd(sharp - np.repeat(np.repeat(coarse, 5, 0), 5, 1)) > 0.5


def test_sharpen_frame_leaves_cells_without_lst_masked():
    lst, pred = _synthetic()
    lst[:5, :5] = np.nan                          # one whole coarse cell missing
    model = sharpen_local.fit_forest([(lst, pred)], factor=5, n_samples=200, seed=1)
    sharp = sharpen_local.sharpen_frame(lst, pred, model, factor=5)
    assert np.all(np.isnan(sharp[:5, :5])) and np.isfinite(sharp[10, 10])


def test_models_are_trained_per_calendar_month_across_years_with_loyo_scores():
    inputs = {"2018-07": _synthetic(1), "2019-07": _synthetic(2), "2020-07": _synthetic(3),
              "2019-01": _synthetic(4)}
    models, scores = sharpen_local.train_monthly_models(inputs, factor=5, n_samples=200, seed=1)
    assert set(models) == {"07", "01"}
    assert {s["month"] for s in scores} == {"07", "01"}
    july = next(s for s in scores if s["month"] == "07")
    assert july["n_frames"] == 3 and july["loyo_rmse"] > 0     # 3 folds, one per year
    jan = next(s for s in scores if s["month"] == "01")
    assert jan["n_frames"] == 1 and jan["loyo_rmse"] is None   # nothing to hold out


# --- LocalImage -------------------------------------------------------------------

def test_local_image_thumbnail_fits_the_largest_side_and_masks_nan():
    vals = np.arange(20 * 40, dtype="float32").reshape(20, 40); vals[0, 0] = np.nan
    img = LocalImage(vals, bounds=(0, 0, 800, 400), crs="EPSG:32633", scale_m=20)
    arr, valid = img.thumbnail(dimensions=80)
    assert arr.shape == (40, 80) and valid.shape == (40, 80)
    assert not valid[0, 0] and valid[20, 40]
    assert arr[20, 40] == pytest.approx(vals[10, 20], abs=60)


def test_local_image_crop_to_bounds_uses_the_georeferencing():
    vals = np.arange(10 * 10, dtype="float32").reshape(10, 10)
    img = LocalImage(vals, bounds=(0, 0, 200, 200), crs="EPSG:32633", scale_m=20)
    sub = img.crop((40, 40, 120, 120))
    assert sub.values.shape == (4, 4) and sub.bounds == (40, 40, 120, 120)
    assert sub.values[0, 0] == vals[4, 2]        # rows count from the top (maxy)


def test_local_image_polygon_mask_and_region_mean():
    vals = np.ones((10, 10), "float32"); vals[:5] = 3.0
    img = LocalImage(vals, bounds=(0, 0, 200, 200), crs="EPSG:32633", scale_m=20)
    ring = [(0, 100), (200, 100), (200, 200), (0, 200), (0, 100)]     # the top half
    assert img.region_mean([ring]) == pytest.approx(3.0)
    clipped = img.clip([ring])
    assert np.isnan(clipped.values[9, 0]) and clipped.values[0, 0] == 3.0
    rel = img.subtract(2.0)
    assert rel.values[0, 0] == 1.0 and rel.values[9, 9] == -1.0


# --- the pipeline step (fake EE for the downloads) ----------------------------

def test_apply_is_a_noop_unless_sharpen_is_local():
    cfg = types.SimpleNamespace(sharpen=None)
    frames = [Frame("2022-07", "IMG")]
    assert sharpen_local.apply(frames, cfg, "FRAME", "REGION") is frames
