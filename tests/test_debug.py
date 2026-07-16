import types
from datetime import datetime, timezone

from gee_animation import debug
from gee_animation.config import RunConfig


def _ms(day: str) -> int:
    """Epoch milliseconds (UTC) for a YYYY-MM-DD date."""
    return int(datetime.strptime(day, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


class _Scene:
    """Fakes one scene image: system:time_start + mask().reduce().reduceRegion() chain."""
    def __init__(self, name, ts, clear):
        self.name, self.ts, self.clear = name, ts, clear

    def get(self, prop):
        assert prop == "system:time_start"
        return types.SimpleNamespace(getInfo=lambda: self.ts)

    def mask(self):
        return self

    def reduce(self, r):
        return self

    def reduceRegion(self, reducer, **kw):
        return types.SimpleNamespace(values=lambda: types.SimpleNamespace(
            get=lambda i: types.SimpleNamespace(getInfo=lambda: self.clear)))


class _Coll:
    """Fakes the EE ImageCollection surface month_scene_frames/export use."""
    def __init__(self, scenes):
        self._scenes = scenes

    def filterDate(self, a, b):
        return self

    def size(self):
        return types.SimpleNamespace(getInfo=lambda: len(self._scenes))

    def toList(self, n):
        return types.SimpleNamespace(get=lambda i: self._scenes[i])

    def median(self):
        return "MEDIAN_IMG"


def _fake_ee():
    return types.SimpleNamespace(
        Image=lambda x: x,
        Reducer=types.SimpleNamespace(min=lambda: "MIN", mean=lambda: "MEAN"))


def _cfg(out_dir):
    return RunConfig(
        name="dbg", project="p", frame_aoi={"bbox": [0, 0, 1, 1]},
        region_aoi={"bbox": [0, 0, 1, 1]}, start="2022-01-01", end="2022-12-01",
        sensor="sentinel2", index="ndvi", cadence="monthly", max_cloud_percent=60,
        region_max_cloud_percent=40, viz_min=0.0, viz_max=1.0, palette=["#000", "#0f0"],
        fps=6, scale=10, dimensions=256, out_dir=out_dir)


def test_month_scene_frames_labels_by_date_and_cloud():
    # clear = 1 - cloud; the second scene's clear is None (fully masked over AOI)
    scenes = [_Scene("a", _ms("2022-07-03"), 0.88), _Scene("b", _ms("2022-07-18"), None)]
    frames = debug.month_scene_frames(_Coll(scenes), "2022-07", "REGION", 10,
                                      ee_module=_fake_ee())
    # each scene labelled by its acquisition date + in-AOI cloud % (omitted if unknown)
    assert [f.label for f in frames] == ["2022-07-03_cloud12pct", "2022-07-18"]
    assert [f.image for f in frames] == scenes
    # n_scenes is None on an individual scene — it is not a composite
    assert all(f.n_scenes is None for f in frames)


def test_month_scene_frames_empty_month_returns_empty():
    assert debug.month_scene_frames(_Coll([]), "2022-01", "REGION", 10,
                                    ee_module=_fake_ee()) == []


def test_export_month_scenes_renders_inputs_then_median(tmp_path):
    coll = _Coll([_Scene("a", _ms("2022-07-03"), 0.9), _Scene("b", _ms("2022-07-18"), 0.8)])
    calls = []

    def fake_build(cfg, frame_geom, region_geom):
        return coll

    def fake_render(frames, cfg, geometry=None):
        calls.append((cfg.name, cfg.out_dir,
                      [f.label for f in frames], [f.n_scenes for f in frames]))
        return []

    base = debug.export_month_scenes(
        _cfg(str(tmp_path)), "FRAME", "REGION", "2022-07",
        build=fake_build, render=fake_render, ee_module=_fake_ee())

    # inputs rendered first, then the median they collapse into
    assert [c[0] for c in calls] == ["scene", "median"]
    # the scenes render receives the two input frames...
    assert calls[0][2] == ["2022-07-03_cloud10pct", "2022-07-18_cloud20pct"]
    assert calls[0][3] == [None, None]
    # ...and the median render receives one frame tagged with the scene count
    assert calls[1][2] == ["2022-07"] and calls[1][3] == [2]
    assert base == tmp_path / "debug" / "2022-07"


def test_export_month_scenes_raises_on_empty_month(tmp_path):
    def fake_build(cfg, frame_geom, region_geom):
        return _Coll([])

    try:
        debug.export_month_scenes(_cfg(str(tmp_path)), "F", "R", "2022-01",
                                  build=fake_build, render=lambda *a, **k: [],
                                  ee_module=_fake_ee())
    except RuntimeError as exc:
        assert "no scenes" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for an empty month")
