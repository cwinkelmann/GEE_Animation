"""Random-forest thermal sharpening (`lst_rf`): wiring tests with a permissive
fake Earth Engine. Numerical behaviour is the job of scripts/lst_rf_validate.py
(live EE, pre-registered gate in docs/superpowers/plans/2026-09-17-lst-rf-sharpening.md).
"""
import types

import pytest

from gee_animation import sharpen
from gee_animation.products import INDEX_BAND


class _P:
    """Chainable recorder: any attribute/call returns self and logs the call."""
    def __init__(self, log, tag="p"):
        self._log, self._tag = log, tag

    def __call__(self, *a, **k):
        return self

    def __getattr__(self, name):
        def method(*a, **k):
            self._log.append((name, a, k))
            return self
        return method


def _fake_ee(log):
    p = _P(log)

    def image_collection(arg=None, *a, **k):
        log.append(("ImageCollection", (arg,), {}))
        return p

    def classifier_rf(**k):
        log.append(("smileRandomForest", (), k))
        return p

    ee = types.SimpleNamespace(
        Image=p, ImageCollection=image_collection, Number=p, Date=p, List=p,
        Filter=p, Reducer=p, Projection=p, Geometry=p, Join=p,
        Classifier=types.SimpleNamespace(smileRandomForest=classifier_rf))
    return ee, p


def _calls(log, name):
    return [(a, k) for n, a, k in log if n == name]


def test_predictor_names_are_fixed_and_ordered():
    assert sharpen.PREDICTORS == ("ndvi", "nirv", "ndbi", "mndwi", "dem")


def test_s2_predictors_reads_s2_sr_and_the_dem_and_names_every_band():
    log = []
    ee, p = _fake_ee(log)
    out = sharpen.s2_predictors("2022-06-01", "2022-07-01", p, ee_module=ee)
    ids = [a[0] for a, k in _calls(log, "ImageCollection")]
    assert sharpen.S2_ID in ids and sharpen.DEM_ID in ids
    # the SCL/s2cloudless mask needs the probability band the sensor normally joins
    assert "COPERNICUS/S2_CLOUD_PROBABILITY" in ids
    renamed = {a[0] for a, k in _calls(log, "rename")}
    assert set(sharpen.PREDICTORS) <= renamed
    assert out is p


def test_rf_sharpen_trains_a_regression_forest_on_lst_and_adds_the_coarse_residual():
    log = []
    ee, p = _fake_ee(log)
    out = sharpen.rf_sharpen(p, p, proj=p, coarse_m=100, fine_m=20, region=p,
                             lst_native_m=30, predictors_native_m=10, ee_module=ee)
    rf = _calls(log, "smileRandomForest")
    assert len(rf) == 1 and rf[0][1]["numberOfTrees"] == sharpen.N_TREES
    assert ("REGRESSION",) in [a for a, k in _calls(log, "setOutputMode")]
    trained = _calls(log, "train")
    assert trained and trained[0][0][1] == "lst" and trained[0][0][2] == list(sharpen.PREDICTORS)
    # ONE classify (the fine prediction); the residual is taken against that
    # prediction AGGREGATED to the coarse grid (a reduceResolution after classify),
    # not against a second classify at coarse predictors — a forest is nonlinear,
    # so only the aggregated form keeps the sharpened cell means equal to the
    # observed LST. Then subtract (residual) and add (back).
    assert len(_calls(log, "classify")) == 1
    names = [n for n, a, k in log]
    assert names.index("classify") < names.index("subtract")
    assert "reduceResolution" in names[names.index("classify"):names.index("subtract")]
    names = [n for n, a, k in log]
    assert names.index("subtract") < names.index("add")
    # aggregated to the coarse grid with a mean, at the requested scales
    scales = [a[0] for a, k in _calls(log, "atScale")]
    assert 100 in scales and 20 in scales and 30 in scales and 10 in scales
    assert (INDEX_BAND,) in [a for a, k in _calls(log, "rename")]
    assert _calls(log, "toFloat")
    assert out is p


def test_linear_sharpen_fits_one_predictor_and_adds_the_coarse_residual():
    log = []
    ee, p = _fake_ee(log)
    out = sharpen.linear_sharpen(p, p, proj=p, coarse_m=300, fine_m=100, region=p,
                                 lst_native_m=30, predictors_native_m=10, ee_module=ee)
    assert _calls(log, "linearFit")
    names = [n for n, a, k in log]
    assert "subtract" in names and names.index("subtract") < len(names) - 1
    assert (INDEX_BAND,) in [a for a, k in _calls(log, "rename")]
    assert out is p


def test_lst_rf_period_derives_the_s2_window_from_the_scenes_and_trains_on_a_buffered_frame():
    log = []
    ee, p = _fake_ee(log)
    cfg = types.SimpleNamespace(frame_aoi={"bbox": [13.1, 52.5, 13.4, 52.6]})
    out = sharpen.lst_rf_period(p, cfg, ee_module=ee)
    # window: min/max of system:time_start, padded by S2_PAD_DAYS either side
    assert ("system:time_start",) in [a for a, k in _calls(log, "aggregate_array")]
    advances = [a[0] for a, k in _calls(log, "advance")]
    assert -sharpen.S2_PAD_DAYS in advances and sharpen.S2_PAD_DAYS in advances
    # training region is the frame buffered by TRAIN_BUFFER_M
    assert (sharpen.TRAIN_BUFFER_M,) in [a for a, k in _calls(log, "buffer")]
    assert _calls(log, "smileRandomForest")
    assert out is p


def test_utm_epsg_picks_the_zone_for_berlin_and_the_southern_hemisphere():
    assert sharpen._utm_epsg(13.25, 52.59) == "EPSG:32633"
    assert sharpen._utm_epsg(-70.0, -33.0) == "EPSG:32719"
