import types

from gee_animation.charts import region_timeseries


def test_region_timeseries_reduces_index_band_per_frame():
    rec = []

    class FakeReduced:
        def __init__(self, v): self.v = v
        def get(self, band):
            assert band == "INDEX"
            return types.SimpleNamespace(getInfo=lambda: self.v)

    class FakeBand:
        def __init__(self, v): self.v = v
        def reduceRegion(self, **kw): rec.append(kw); return FakeReduced(self.v)

    class FakeImg:
        def __init__(self, v): self.v = v
        def select(self, band): assert band == "INDEX"; return FakeBand(self.v)

    frames = [types.SimpleNamespace(label="2022-05", image=FakeImg(0.5)),
              types.SimpleNamespace(label="2022-06", image=FakeImg(0.7))]
    ee = types.SimpleNamespace(
        Reducer=types.SimpleNamespace(mean=lambda: "MEAN", median=lambda: "MED"))

    out = region_timeseries(frames, "REGION", 30, reducer="mean", ee_module=ee)
    assert out == [("2022-05", 0.5), ("2022-06", 0.7)]
    assert rec[0]["reducer"] == "MEAN" and rec[0]["geometry"] == "REGION"
    assert rec[0]["scale"] == 30 and rec[0]["bestEffort"] is True


def test_region_timeseries_median_reducer_and_none_value():
    class FakeReduced:
        def get(self, band): return types.SimpleNamespace(getInfo=lambda: None)
    class FakeBand:
        def reduceRegion(self, **kw): FakeBand.kw = kw; return FakeReduced()
    class FakeImg:
        def select(self, band): return FakeBand()
    frames = [types.SimpleNamespace(label="2022-05", image=FakeImg())]
    ee = types.SimpleNamespace(
        Reducer=types.SimpleNamespace(mean=lambda: "MEAN", median=lambda: "MED"))
    out = region_timeseries(frames, "REGION", 30, reducer="median", ee_module=ee)
    assert out == [("2022-05", None)]          # empty region -> None value
    assert FakeBand.kw["reducer"] == "MED"
