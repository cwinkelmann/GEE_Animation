import types

from gee_animation.charts import inside_outside_timeseries


def _fake_ee():
    return types.SimpleNamespace(
        Reducer=types.SimpleNamespace(mean=lambda: "MEAN", median=lambda: "MED"))


def _frames(values_by_geom):
    """Frames whose INDEX band returns a value keyed by the reduce geometry."""
    calls = []

    class FakeReduced:
        def __init__(self, v): self.v = v
        def get(self, band):
            assert band == "INDEX"
            return types.SimpleNamespace(getInfo=lambda: self.v)

    class FakeBand:
        def reduceRegion(self, **kw):
            calls.append(kw)
            return FakeReduced(values_by_geom.get(kw["geometry"]))

    class FakeImg:
        def select(self, band): assert band == "INDEX"; return FakeBand()

    return FakeImg(), calls


def test_inside_outside_reduces_region_and_frame_minus_region():
    class FrameGeom:
        def difference(self, region): FrameGeom.diff = region; return "OUTSIDE"
    frame_geom = FrameGeom()
    img, calls = _frames({"REGION": 0.8, "OUTSIDE": 0.55})
    frames = [types.SimpleNamespace(label="2022-05", image=img)]
    out = inside_outside_timeseries(frames, "REGION", frame_geom, 30, ee_module=_fake_ee())
    assert out == [("2022-05", 0.8, 0.55)]              # inside > outside (forest vs surroundings)
    assert FrameGeom.diff == "REGION"                    # outside = frame.difference(region)
    assert calls[0]["reducer"] == "MEAN" and calls[0]["scale"] == 30
    geoms = [c["geometry"] for c in calls]
    assert geoms == ["REGION", "OUTSIDE"]                # inside then outside, per frame


def test_inside_outside_median_and_none_values():
    class FrameGeom:
        def difference(self, region): return "OUTSIDE"
    img, calls = _frames({"REGION": None, "OUTSIDE": 0.3})
    frames = [types.SimpleNamespace(label="2022-06", image=img)]
    out = inside_outside_timeseries(frames, "REGION", FrameGeom(), 30,
                                    reducer="median", ee_module=_fake_ee())
    assert out == [("2022-06", None, 0.3)]
    assert calls[0]["reducer"] == "MED"
