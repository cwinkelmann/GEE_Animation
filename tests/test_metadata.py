import sqlite3
import types

import pytest

from gee_animation import metadata
from gee_animation.compositing import Frame


class _FakeDict:
    """Fakes `ee.Dictionary(mapping).getInfo()`: resolving the dictionary resolves
    every server-side value inside it, in ONE round trip — which is exactly the
    property `frame_stats` relies on. Appends its keys to `calls` so a test can
    count the round trips."""
    def __init__(self, mapping, calls): self.mapping, self.calls = mapping, calls

    def getInfo(self):
        self.calls.append(sorted(self.mapping))
        return {k: v.getInfo() for k, v in self.mapping.items()}


def _fake_ee(calls=None):
    calls = [] if calls is None else calls
    return types.SimpleNamespace(
        Reducer=types.SimpleNamespace(min=lambda: "MIN", mean=lambda: "MEAN"),
        Dictionary=lambda mapping: _FakeDict(mapping, calls))


def _rr(value):
    """Fakes reduceRegion(...).values().get(0) -> a deferred value whose .getInfo()
    yields `value` (resolved either directly, as the pre-batching code did, or via
    the enclosing ee.Dictionary, as frame_stats does now)."""
    return types.SimpleNamespace(values=lambda: types.SimpleNamespace(
        get=lambda i: types.SimpleNamespace(getInfo=lambda: value)))


def _legacy_frame_stats(frames, region_geom, scale, mean_band, ee_module):
    """The pre-batching implementation, verbatim — one/two getInfo() per frame.

    Kept as the reference the batched `frame_stats` must reproduce exactly: same
    order, same values, same rounding, same None handling.
    """
    def reduce_value(image, reducer):
        return image.reduceRegion(
            reducer, geometry=region_geom, scale=scale,
            bestEffort=True, maxPixels=int(1e9)).values().get(0).getInfo()

    rows = []
    for f in frames:
        valid = f.image.mask().reduce(ee_module.Reducer.min())
        clear = reduce_value(valid, ee_module.Reducer.mean())
        clear = 0.0 if clear is None else float(clear)
        mean = None
        if mean_band is not None:
            mv = reduce_value(f.image.select(mean_band), ee_module.Reducer.mean())
            mean = None if mv is None else round(float(mv), 4)
        rows.append((f.label, getattr(f, "n_scenes", None), round(1.0 - clear, 4), mean))
    return rows


class _Band:
    """image.select(band): its reduceRegion mean over the AOI -> `mean`."""
    def __init__(self, mean): self.mean = mean
    def reduceRegion(self, reducer, **kw): return _rr(self.mean)


class _Img:
    """Fakes mask().reduce(min).reduceRegion(mean) -> clear, and select(b).reduceRegion -> mean."""
    def __init__(self, clear, mean=None): self.clear, self.mean = clear, mean
    def mask(self): return self
    def reduce(self, r): return self
    def select(self, band): return _Band(self.mean)
    def reduceRegion(self, reducer, **kw): return _rr(self.clear)


def test_frame_stats_reports_cloud_and_mean_over_aoi():
    frames = [Frame("2022-07", _Img(0.9, 22.5), 3), Frame("2022-08", _Img(1.0, 25.0), 4),
              Frame("2022-09", _Img(None, None), 1)]
    rows = metadata.frame_stats(frames, "REGION", 30, mean_band="INDEX", ee_module=_fake_ee())
    # cloud fraction = 1 - clear over the AOI; aoi_mean = mean index value over the AOI
    assert rows == [("2022-07", 3, 0.1, 22.5), ("2022-08", 4, 0.0, 25.0),
                    ("2022-09", 1, 1.0, None)]


def test_frame_stats_uses_the_clean_period_key_for_a_pooled_frame():
    # A pooled Frame carries its source year separately (Frame.source); frame_stats
    # must key its row on the plain "YYYY-MM" period (frame.label), not a string with
    # the provenance arrow baked in — that string is half of the DB's PRIMARY KEY
    # (name, month), so an arrow-bearing month would desync pooled vs. non-pooled runs.
    frames = [Frame("2022-05", _Img(0.9, 22.5), 1, 2021)]
    rows = metadata.frame_stats(frames, "REGION", 30, mean_band="INDEX", ee_module=_fake_ee())
    assert rows == [("2022-05", 1, 0.1, 22.5)]
    assert "←" not in rows[0][0]


def test_frame_stats_omits_mean_when_no_band():
    # composites (rgb/cir) pass mean_band=None -> aoi_mean is null
    rows = metadata.frame_stats([Frame("2022-07", _Img(0.9, 22.5), 3)],
                                "REGION", 30, mean_band=None, ee_module=_fake_ee())
    assert rows == [("2022-07", 3, 0.1, None)]


def test_frame_stats_issues_a_single_getInfo_round_trip():
    # Global constraint (as pinned for compositing.composite and inventory
    # .scene_inventory): exactly one getInfo(), whatever the frame count — never one
    # (or two) per frame. 22 frames used to cost 110 s in serial round trips alone.
    calls = []
    frames = [Frame(f"2022-{m:02d}", _Img(0.9, 22.5), 3) for m in range(1, 13)]
    rows = metadata.frame_stats(frames, "REGION", 30, mean_band="INDEX",
                                ee_module=_fake_ee(calls))
    assert len(calls) == 1
    # …and that one call really did carry every frame's reducers.
    assert len(calls[0]) == 2 * len(frames)
    assert len(rows) == len(frames)


def test_frame_stats_makes_no_round_trip_for_no_frames():
    calls = []
    assert metadata.frame_stats([], "REGION", 30, mean_band="INDEX",
                                ee_module=_fake_ee(calls)) == []
    assert calls == []


@pytest.mark.parametrize("mean_band", ["INDEX", None])
def test_frame_stats_matches_the_pre_batching_implementation(mean_band):
    # Batching must not change a single row: same order, values, rounding and None
    # handling — including an empty region (clear=None -> cloud fraction 1.0) and a
    # frame whose mean reduces to null.
    frames = [Frame("2022-07", _Img(0.9, 22.5), 3),
              Frame("2022-08", _Img(1.0, 25.0), 4),
              Frame("2022-09", _Img(None, None), 1),        # empty region
              Frame("2022-10", _Img(0.83333, 1.0 / 3), 2),  # exercises rounding
              Frame("2022-11", _Img(0.5, None), 0),         # mean reduces to null
              Frame("2022-12", _Img(0.7, 19.0), 2, 2021)]   # pooled frame
    args = (frames, "REGION", 30, mean_band)
    assert (metadata.frame_stats(*args, ee_module=_fake_ee())
            == _legacy_frame_stats(*args, ee_module=_fake_ee()))


def test_write_db_roundtrip_and_upsert(tmp_path):
    db = tmp_path / "metadata.db"
    run = {"name": "wne_lst", "sensor": "landsat", "index": "lst"}
    metadata.write_db(db, run, [("2022-07", 3, 0.1, 22.5), ("2022-08", 4, 0.0, 25.0)])
    con = sqlite3.connect(db)
    rows = con.execute("SELECT month, n_scenes, aoi_cloud_fraction, aoi_clear_fraction, "
                       "aoi_mean FROM frame_clouds ORDER BY month").fetchall()
    con.close()
    assert rows == [("2022-07", 3, 0.1, 0.9, 22.5), ("2022-08", 4, 0.0, 1.0, 25.0)]
    # re-running the same (name, month) replaces the row rather than duplicating
    metadata.write_db(db, run, [("2022-07", 5, 0.25, 20.0)])
    con = sqlite3.connect(db)
    got = con.execute("SELECT n_scenes, aoi_cloud_fraction, aoi_mean FROM frame_clouds "
                      "WHERE month='2022-07'").fetchone()
    n = con.execute("SELECT COUNT(*) FROM frame_clouds").fetchone()[0]
    con.close()
    assert got == (5, 0.25, 20.0) and n == 2


def test_write_db_migrates_pre_aoi_mean_schema(tmp_path):
    # a DB written before aoi_mean existed gets the column added, not an error
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE frame_clouds (name TEXT, sensor TEXT, index_name TEXT, "
                "month TEXT, n_scenes INTEGER, aoi_cloud_fraction REAL, "
                "aoi_clear_fraction REAL, PRIMARY KEY (name, month))")
    con.execute("INSERT INTO frame_clouds VALUES ('old','landsat','lst','2021-06',2,0.2,0.8)")
    con.commit()
    con.close()
    metadata.write_db(db, {"name": "new", "sensor": "landsat", "index": "lst"},
                      [("2022-06", 4, 0.1, 21.0)])
    con = sqlite3.connect(db)
    rows = con.execute("SELECT month, aoi_mean FROM frame_clouds ORDER BY month").fetchall()
    con.close()
    assert rows == [("2021-06", None), ("2022-06", 21.0)]
