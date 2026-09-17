"""focus: mask imagery outside the region (`region_only`) and/or express each
frame relative to its own region mean (`relative: region_mean`)."""
import types

from gee_animation import focus
from gee_animation.compositing import Frame


class _P:
    def __init__(self, log):
        self._log = log

    def __call__(self, *a, **k):
        return self

    def __getattr__(self, name):
        def method(*a, **k):
            self._log.append((name, a, k))
            return self
        return method


def _fake_ee(log):
    p = _P(log)
    return types.SimpleNamespace(Image=p, Number=p, Reducer=p), p


def _names(log):
    return [n for n, a, k in log]


def test_apply_is_a_noop_without_either_option():
    log = []
    ee, p = _fake_ee(log)
    frames = [Frame("2022-07", "IMG")]
    cfg = types.SimpleNamespace(region_only=False, relative=None, scale=20)
    assert focus.apply(frames, cfg, "REGION", ee_module=ee) is frames
    assert log == []


def test_region_only_clips_every_frame_to_the_region_and_keeps_provenance():
    log = []
    ee, p = _fake_ee(log)
    frames = [Frame("2022-07", p, n_scenes=2, source=2021), Frame("2022-08", p, n_scenes=1)]
    cfg = types.SimpleNamespace(region_only=True, relative=None, scale=20)
    out = focus.apply(frames, cfg, "REGION", ee_module=ee)
    clips = [(a, k) for n, a, k in log if n == "clip"]
    assert len(clips) == 2 and all(a == ("REGION",) for a, k in clips)
    assert [(f.label, f.n_scenes, f.source) for f in out] == [("2022-07", 2, 2021), ("2022-08", 1, None)]


def test_relative_region_mean_subtracts_the_frames_own_region_mean():
    log = []
    ee, p = _fake_ee(log)
    cfg = types.SimpleNamespace(region_only=False, relative="region_mean", scale=20)
    focus.apply([Frame("2022-07", p)], cfg, "REGION", ee_module=ee)
    rr = [(a, k) for n, a, k in log if n == "reduceRegion"]
    assert len(rr) == 1 and rr[0][1]["geometry"] == "REGION" and rr[0][1]["scale"] == 20
    names = _names(log)
    assert "subtract" in names and names.index("reduceRegion") < names.index("subtract")
    assert "clip" not in names


def test_relative_then_region_only_computes_the_mean_before_clipping():
    log = []
    ee, p = _fake_ee(log)
    cfg = types.SimpleNamespace(region_only=True, relative="region_mean", scale=20)
    focus.apply([Frame("2022-07", p)], cfg, "REGION", ee_module=ee)
    names = _names(log)
    assert names.index("reduceRegion") < names.index("subtract") < names.index("clip")


def test_focus_handles_local_images_without_earth_engine():
    import numpy as np
    from gee_animation.local_image import LocalImage
    vals = np.ones((10, 10), "float32"); vals[:5] = 3.0
    img = LocalImage(vals, (0, 0, 200, 200), "EPSG:32633", 20)
    cfg = types.SimpleNamespace(region_only=True, relative="region_mean", scale=20,
                                region_aoi={"rings_projected": [[(0, 100), (200, 100), (200, 200), (0, 200), (0, 100)]]})
    out = focus.apply([Frame("2022-07", img)], cfg, region_geom=None,
                      ee_module=types.SimpleNamespace())
    v = out[0].image.values
    assert v[0, 0] == 0.0 and np.isnan(v[9, 9])       # top half minus its mean 3.0; bottom clipped
