import types

import pytest

from gee_animation import anomaly
from gee_animation.compositing import Frame


class _Img:
    """Records the z-score / subtraction chain: (x - mean) / std, rename, set."""
    def __init__(self, rec, tag="x"):
        self.rec, self.tag = rec, tag
    def select(self, b): self.rec.setdefault("select", []).append(b); return self
    def subtract(self, o): self.rec["subtract"] = o; return self
    def divide(self, o): self.rec["divide"] = o; return self
    def mean(self): return "MEAN"
    def reduce(self, r): self.rec["reduce"] = r; return self
    def rename(self, n): self.rec["rename"] = n; return self
    def set(self, k, v): self.rec["set"] = (k, v); return self
    def get(self, k): return "TS"


def _fake_ee(rec):
    return types.SimpleNamespace(
        Filter=types.SimpleNamespace(
            calendarRange=lambda a, b, u: rec.setdefault("cal", []).append((a, b, u))),
        Reducer=types.SimpleNamespace(stdDev=lambda: "STD"))


def test_climatology_anomaly_is_zscore_against_that_month():
    rec = {}
    ee = _fake_ee(rec)

    class Baseline:
        def filter(self, f): rec.setdefault("filtered", True); return self
        def select(self, b): return self
        def mean(self): return "MEAN"
        def reduce(self, r): rec["reduce"] = r; return self
        def rename(self, n): rec["rename_std"] = n; return self
    img = _Img(rec)
    out = anomaly.climatology_anomaly([Frame("2022-07", img, 3)], Baseline(), ee_module=ee)
    assert rec["cal"] == [(7, 7, "month")]          # baseline filtered to calendar July
    assert rec["subtract"] == "MEAN" and rec["divide"] is not None   # (x - mean)/std
    assert rec["reduce"] == "STD"                    # std via stdDev reducer
    assert rec["rename"] == "INDEX" and rec["set"] == ("system:time_start", "TS")
    assert out[0].label == "2022-07" and out[0].n_scenes == 3   # frame metadata preserved


def test_reference_anomaly_subtracts_monthly_era5():
    rec = {}
    filt = {}

    class Era5Coll:
        def filterDate(self, s, e): filt["range"] = (s, e); return self
        def select(self, b): filt["band"] = b; return self
        def mean(self): return self
        def subtract(self, v): filt["k2c"] = v; return "ERA5C"
    ee = types.SimpleNamespace(ImageCollection=lambda cid: (filt.__setitem__("cid", cid) or Era5Coll()))
    img = _Img(rec)
    out = anomaly.reference_anomaly([Frame("2022-07", img, 2)], ee_module=ee)
    assert filt["cid"] == "ECMWF/ERA5_LAND/HOURLY"
    assert filt["range"] == ("2022-07-01", "2022-08-01")   # that month
    assert filt["band"] == "temperature_2m" and filt["k2c"] == 273.15
    assert rec["subtract"] == "ERA5C" and rec["rename"] == "INDEX"


def test_apply_is_noop_without_anomaly():
    frames = ["a", "b"]
    cfg = types.SimpleNamespace(anomaly=None)
    assert anomaly.apply(frames, cfg, "F", "R", build_fn=None) is frames


def test_apply_climatology_builds_baseline_over_years():
    from gee_animation.config import RunConfig
    captured = {}

    def fake_build(cfg, frame, region, ee_module=None):
        captured["baseline_range"] = (cfg.start, cfg.end)
        return "BASELINE_COLL"

    def fake_clim(frames, baseline, ee_module=None):
        captured["baseline"] = baseline
        return ["ANOM"]

    cfg = RunConfig(
        name="t", project="p", frame_aoi={"bbox": [0, 0, 1, 1]}, region_aoi={"bbox": [0, 0, 1, 1]},
        start="2022-05-01", end="2022-09-01", sensor="landsat", index="lst_smw",
        cadence="monthly", max_cloud_percent=60, region_max_cloud_percent=10,
        viz_min=-3, viz_max=3, palette=["#000000"], fps=4, scale=30, dimensions=64,
        anomaly="climatology", baseline_years=[2015, 2024])
    import gee_animation.anomaly as A
    A_clim = A.climatology_anomaly
    A.climatology_anomaly = fake_clim
    try:
        out = A.apply(["f"], cfg, "F", "R", fake_build)
    finally:
        A.climatology_anomaly = A_clim
    assert out == ["ANOM"]
    assert captured["baseline_range"] == ("2015-01-01", "2025-01-01")   # y0..y1+1 exclusive
    assert captured["baseline"] == "BASELINE_COLL"
