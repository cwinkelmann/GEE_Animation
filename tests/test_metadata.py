import sqlite3
import types

from gee_animation import metadata
from gee_animation.compositing import Frame


def _fake_ee():
    return types.SimpleNamespace(
        Reducer=types.SimpleNamespace(min=lambda: "MIN", mean=lambda: "MEAN"))


class _Img:
    """Fakes image.mask().reduce(min).reduceRegion(mean).values().get(0).getInfo()."""
    def __init__(self, clear): self.clear = clear
    def mask(self): return self
    def reduce(self, r): return self
    def reduceRegion(self, reducer, **kw):
        return types.SimpleNamespace(values=lambda: types.SimpleNamespace(
            get=lambda i: types.SimpleNamespace(getInfo=lambda: self.clear)))


def test_frame_cloud_fractions_is_one_minus_clear_over_aoi():
    frames = [Frame("2022-07", _Img(0.9), 3), Frame("2022-08", _Img(1.0), 4),
              Frame("2022-09", _Img(None), 1)]
    rows = metadata.frame_cloud_fractions(frames, "REGION", 30, ee_module=_fake_ee())
    # cloud fraction inside the AOI = 1 - clear coverage
    assert rows == [("2022-07", 3, 0.1), ("2022-08", 4, 0.0), ("2022-09", 1, 1.0)]


def test_write_db_roundtrip_and_upsert(tmp_path):
    db = tmp_path / "metadata.db"
    run = {"name": "wne_lst", "sensor": "landsat", "index": "lst"}
    metadata.write_db(db, run, [("2022-07", 3, 0.1), ("2022-08", 4, 0.0)])
    con = sqlite3.connect(db)
    rows = con.execute("SELECT month, n_scenes, aoi_cloud_fraction, aoi_clear_fraction "
                       "FROM frame_clouds ORDER BY month").fetchall()
    con.close()
    assert rows == [("2022-07", 3, 0.1, 0.9), ("2022-08", 4, 0.0, 1.0)]
    # re-running the same (name, month) replaces the row rather than duplicating
    metadata.write_db(db, run, [("2022-07", 5, 0.25)])
    con = sqlite3.connect(db)
    got = con.execute("SELECT n_scenes, aoi_cloud_fraction FROM frame_clouds "
                      "WHERE month='2022-07'").fetchone()
    n = con.execute("SELECT COUNT(*) FROM frame_clouds").fetchone()[0]
    con.close()
    assert got == (5, 0.25) and n == 2
