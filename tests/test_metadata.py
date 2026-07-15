import sqlite3
import types

from gee_animation import metadata
from gee_animation.compositing import Frame


def _fake_ee():
    return types.SimpleNamespace(
        Reducer=types.SimpleNamespace(min=lambda: "MIN", mean=lambda: "MEAN"))


def _rr(value):
    """Fakes reduceRegion(...).values().get(0).getInfo() -> value."""
    return types.SimpleNamespace(values=lambda: types.SimpleNamespace(
        get=lambda i: types.SimpleNamespace(getInfo=lambda: value)))


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


def test_frame_stats_omits_mean_when_no_band():
    # composites (rgb/cir) pass mean_band=None -> aoi_mean is null
    rows = metadata.frame_stats([Frame("2022-07", _Img(0.9, 22.5), 3)],
                                "REGION", 30, mean_band=None, ee_module=_fake_ee())
    assert rows == [("2022-07", 3, 0.1, None)]


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
