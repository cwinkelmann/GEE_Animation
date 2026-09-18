import math
import sqlite3
import types

import ee
import pytest

from gee_animation import metadata
from gee_animation.compositing import Frame


class _FakeDict:
    """Fakes `ee.Dictionary(mapping).getInfo()`: resolving the dictionary resolves
    every server-side value inside it, in ONE round trip. Appends its keys to
    `calls` so a test can count the round trips.

    `max_keys` fakes Earth Engine's fixed per-request memory ceiling: a request
    carrying more than that many reducers raises the same "User memory limit
    exceeded" EEException a real 60-frame batch does.
    """
    def __init__(self, mapping, calls, max_keys=None):
        self.mapping, self.calls, self.max_keys = mapping, calls, max_keys

    def getInfo(self):
        self.calls.append(sorted(self.mapping))
        if self.max_keys is not None and len(self.mapping) > self.max_keys:
            raise ee.EEException("User memory limit exceeded.")
        return {k: v.getInfo() for k, v in self.mapping.items()}


class _FakeReducer(str):
    """A reducer token that can be combined, mirroring ee.Reducer's fluent API."""
    def combine(self, other, sharedInputs=False):
        return _FakeReducer(f"{self}+{other}")


def _fake_ee(calls=None, max_keys=None):
    calls = [] if calls is None else calls
    return types.SimpleNamespace(
        Reducer=types.SimpleNamespace(
            min=lambda: _FakeReducer("MIN"),
            mean=lambda: _FakeReducer("MEAN"),
            percentile=lambda ps: _FakeReducer(f"P{list(ps)}")),
        Dictionary=lambda mapping: _FakeDict(mapping, calls, max_keys))


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
    """image.select(band): reduceRegion with the combined reducer -> {mean,p10,p90}.

    Returns the reduceRegion object itself (a deferred DICT), matching
    `metadata._stats_expr`, rather than the single deferred value the
    clear-fraction path takes through `_reduce_expr`.
    """
    def __init__(self, mean, p10=None, p90=None):
        self.mean, self.p10, self.p90 = mean, p10, p90

    def reduceRegion(self, reducer, **kw):
        stats = (None if self.mean is None else
                 {"INDEX_mean": self.mean, "INDEX_p10": self.p10,
                  "INDEX_p90": self.p90})
        # Serves both callers: .getInfo() -> the stats dict (metadata._stats_expr,
        # the current path) and .values().get(0) -> the bare mean (the legacy
        # single-value path the parity reference still exercises).
        return types.SimpleNamespace(
            getInfo=lambda: stats,
            values=lambda: types.SimpleNamespace(
                get=lambda i: types.SimpleNamespace(getInfo=lambda: self.mean)))


class _Img:
    """mask().reduce(min).reduceRegion(mean) -> clear; select(b) -> a _Band."""
    def __init__(self, clear, mean=None, p10=None, p90=None):
        self.clear, self.mean, self.p10, self.p90 = clear, mean, p10, p90
    def mask(self): return self
    def reduce(self, r): return self
    def select(self, band): return _Band(self.mean, self.p10, self.p90)
    def reduceRegion(self, reducer, **kw): return _rr(self.clear)


def test_frame_stats_reports_cloud_and_mean_over_aoi():
    frames = [Frame("2022-07", _Img(0.9, 22.5), 3), Frame("2022-08", _Img(1.0, 25.0), 4),
              Frame("2022-09", _Img(None, None), 1)]
    rows = metadata.frame_stats(frames, "REGION", 30, mean_band="INDEX", ee_module=_fake_ee())
    # cloud fraction = 1 - clear over the AOI; aoi_mean = mean index value over the AOI
    assert rows == [("2022-07", 3, 0.1, 22.5, None, None, None, None, None), ("2022-08", 4, 0.0, 25.0, None, None, None, None, None),
                    ("2022-09", 1, 1.0, None, None, None, None, None, None)]


def test_frame_stats_uses_the_clean_period_key_for_a_pooled_frame():
    # A pooled Frame carries its source year separately (Frame.source); frame_stats
    # must key its row on the plain "YYYY-MM" period (frame.label), not a string with
    # the provenance arrow baked in — that string is half of the DB's PRIMARY KEY
    # (name, month), so an arrow-bearing month would desync pooled vs. non-pooled runs.
    frames = [Frame("2022-05", _Img(0.9, 22.5), 1, 2021)]
    rows = metadata.frame_stats(frames, "REGION", 30, mean_band="INDEX", ee_module=_fake_ee())
    assert rows == [("2022-05", 1, 0.1, 22.5, None, None, None, None, None)]
    assert "←" not in rows[0][0]


def test_frame_stats_omits_mean_when_no_band():
    # composites (rgb/cir) pass mean_band=None -> aoi_mean is null
    rows = metadata.frame_stats([Frame("2022-07", _Img(0.9, 22.5), 3)],
                                "REGION", 30, mean_band=None, ee_module=_fake_ee())
    assert rows == [("2022-07", 3, 0.1, None, None, None, None, None, None)]


def _frames(n, clear=0.9, mean=22.5):
    return [Frame(f"{2020 + i // 12}-{i % 12 + 1:02d}", _Img(clear, mean), 3)
            for i in range(n)]


def test_frame_stats_round_trips_do_not_scale_with_frame_count():
    # THE INVARIANT THAT MATTERS: round trips must not grow with the frame count.
    #
    # An earlier version of this test asserted `len(calls) == 1` for any n. That was
    # the wrong invariant, and it was actively harmful: one request has a FIXED
    # server-side memory ceiling, and each entry in the batch is not a cheap lookup —
    # it makes Earth Engine build that period's median composite over all its
    # cloud-masked scenes and reduce it over the AOI. 22 frames fit; 60 frames
    # (a 5-year monthly run) blew the ceiling with "User memory limit exceeded" and
    # produced ZERO output. Pinning "exactly 1" therefore pinned the bug.
    #
    # What the original optimisation was actually for was killing the one-round-trip-
    # per-frame loop (22 frames = 110 s of serial round trips). So: batch in chunks,
    # and assert ceil(n / chunk) — bounded, and far below n.
    calls = []
    frames = _frames(60)
    rows = metadata.frame_stats(frames, "REGION", 30, mean_band="INDEX",
                                ee_module=_fake_ee(calls))
    chunk = metadata.FRAME_STATS_CHUNK
    assert chunk <= 20, "a chunk this large is not comfortably under the known-good 22"
    assert len(calls) == math.ceil(len(frames) / chunk)
    assert len(calls) < len(frames) / 3        # nowhere near one per frame
    # every chunk carries a full batch of reducers (2 per frame: clear + mean) …
    assert [len(c) for c in calls[:-1]] == [2 * chunk] * (len(calls) - 1)
    # … and every frame is accounted for exactly once across the chunks
    assert sum(len(c) for c in calls) == 2 * len(frames)
    assert len({k for c in calls for k in c}) == 2 * len(frames)
    assert len(rows) == len(frames)


def test_frame_stats_batches_a_short_run_into_one_round_trip():
    # Below the chunk size the behaviour is unchanged from the batched version:
    # a 12-frame year is still a single request.
    calls = []
    rows = metadata.frame_stats(_frames(12), "REGION", 30, mean_band="INDEX",
                                ee_module=_fake_ee(calls))
    assert len(calls) == 1 and len(calls[0]) == 24 and len(rows) == 12


def test_frame_stats_halves_the_chunk_when_a_request_hits_the_memory_limit():
    # A very large AOI or a heavy index can exceed the ceiling at ANY fixed chunk
    # size. The run must then get slower, not fail: halve and retry, and still return
    # complete, correct rows in the original order.
    calls = []
    n = 40
    frames = [Frame(f"2022-{i:02d}", _Img(0.9, float(i)), i) for i in range(1, n + 1)]
    # only requests of <= 8 keys (4 frames) survive — forces two halvings from 16
    rows = metadata.frame_stats(frames, "REGION", 30, mean_band="INDEX",
                                ee_module=_fake_ee(calls, max_keys=8))
    assert rows == [(f"2022-{i:02d}", i, 0.1, float(i), None, None, None, None, None)
                    for i in range(1, n + 1)]
    # it backed off to a surviving chunk and stayed there rather than re-failing
    assert max(len(c) for c in calls[-3:]) <= 8
    assert len(calls) < n                       # still not one round trip per frame


def test_frame_stats_retry_bottoms_out_instead_of_looping_forever():
    # If even a single frame exceeds the limit there is nothing left to halve:
    # re-raise rather than spin. Bounded attempts, and the last one was one frame.
    calls = []
    with pytest.raises(ee.EEException, match="memory limit"):
        metadata.frame_stats(_frames(30), "REGION", 30, mean_band="INDEX",
                             ee_module=_fake_ee(calls, max_keys=0))
    # halving from C reaches 1 in floor(log2 C) steps, so floor(log2 C) + 1 attempts
    assert len(calls) == math.floor(math.log2(metadata.FRAME_STATS_CHUNK)) + 1
    assert len(calls[-1]) == 2                  # floor of one frame per request


def test_frame_stats_reraises_an_unrelated_ee_error_without_retrying():
    # Backoff is for memory/limit failures only — an ordinary EE error must surface
    # immediately, not be retried four times over.
    calls = []

    def boom(mapping):
        calls.append(sorted(mapping))
        raise ee.EEException("Image.select: Pattern 'INDEX' did not match any bands.")

    fake = types.SimpleNamespace(
        Reducer=types.SimpleNamespace(
            min=lambda: _FakeReducer("MIN"), mean=lambda: _FakeReducer("MEAN"),
            percentile=lambda ps: _FakeReducer(f"P{list(ps)}")),
        Dictionary=lambda mapping: types.SimpleNamespace(getInfo=lambda: boom(mapping)))
    with pytest.raises(ee.EEException, match="did not match any bands"):
        metadata.frame_stats(_frames(30), "REGION", 30, mean_band="INDEX", ee_module=fake)
    assert len(calls) == 1


def test_frame_stats_warns_once_about_the_reduced_chunk(caplog):
    # One warning naming the chunk it settled on — not one per retry.
    with caplog.at_level("WARNING", logger="gee_animation.metadata"):
        metadata.frame_stats(_frames(20), "REGION", 30, mean_band="INDEX",
                             ee_module=_fake_ee(max_keys=8))
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "4" in warnings[0].getMessage()


def test_frame_stats_does_not_warn_when_no_backoff_was_needed(caplog):
    with caplog.at_level("WARNING", logger="gee_animation.metadata"):
        metadata.frame_stats(_frames(20), "REGION", 30, mean_band="INDEX",
                             ee_module=_fake_ee())
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_frame_stats_makes_no_round_trip_for_no_frames():
    calls = []
    assert metadata.frame_stats([], "REGION", 30, mean_band="INDEX",
                                ee_module=_fake_ee(calls)) == []
    assert calls == []


@pytest.mark.parametrize("mean_band", ["INDEX", None])
@pytest.mark.parametrize("chunk_size", [1, 2, 5, 16, 100])
def test_frame_stats_matches_the_pre_batching_implementation(mean_band, chunk_size):
    # Chunked batching must not change a single row, at ANY chunk size: same order,
    # values, rounding and None handling — including an empty region (clear=None ->
    # cloud fraction 1.0) and a frame whose mean reduces to null. chunk_size is swept
    # across boundaries that do and do not divide the frame count, since a chunking
    # off-by-one would silently misattribute one frame's stats to another.
    frames = [Frame("2022-07", _Img(0.9, 22.5), 3),
              Frame("2022-08", _Img(1.0, 25.0), 4),
              Frame("2022-09", _Img(None, None), 1),        # empty region
              Frame("2022-10", _Img(0.83333, 1.0 / 3), 2),  # exercises rounding
              Frame("2022-11", _Img(0.5, None), 0),         # mean reduces to null
              Frame("2022-12", _Img(0.7, 19.0), 2, 2021)]   # pooled frame
    args = (frames, "REGION", 30, mean_band)
    rows = metadata.frame_stats(*args, ee_module=_fake_ee(), chunk_size=chunk_size)
    # The legacy reference predates the distribution columns, so it pins the four
    # fields it computed. p10/p90 and the outside area are covered separately by
    # test_frame_stats_collects_quantiles_inside_and_outside.
    assert [r[:4] for r in rows] == _legacy_frame_stats(*args, ee_module=_fake_ee())


def test_frame_stats_keeps_frames_distinct_across_chunk_boundaries(mean_band="INDEX"):
    # A distinct value per frame, spanning several chunks: catches an offset bug that
    # a uniform-value fixture would hide.
    n = 37
    frames = [Frame(f"f{i}", _Img(i / 100.0, float(i)), i) for i in range(n)]
    rows = metadata.frame_stats(frames, "REGION", 30, mean_band="INDEX",
                                ee_module=_fake_ee())
    assert rows == [(f"f{i}", i, round(1.0 - i / 100.0, 4), float(i),
                     None, None, None, None, None) for i in range(n)]


def test_write_db_roundtrip_and_upsert(tmp_path):
    db = tmp_path / "metadata.db"
    run = {"name": "wne_lst", "sensor": "landsat", "index": "lst"}
    metadata.write_db(db, run, [("2022-07", 3, 0.1, 22.5, None, None, None, None, None), ("2022-08", 4, 0.0, 25.0, None, None, None, None, None)])
    con = sqlite3.connect(db)
    rows = con.execute("SELECT month, n_scenes, aoi_cloud_fraction, aoi_clear_fraction, "
                       "aoi_mean FROM frame_clouds ORDER BY month").fetchall()
    con.close()
    assert rows == [("2022-07", 3, 0.1, 0.9, 22.5), ("2022-08", 4, 0.0, 1.0, 25.0)]
    # re-running the same (name, month) replaces the row rather than duplicating
    metadata.write_db(db, run, [("2022-07", 5, 0.25, 20.0, None, None, None, None, None)])
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
                      [("2022-06", 4, 0.1, 21.0, None, None, None, None, None)])
    con = sqlite3.connect(db)
    rows = con.execute("SELECT month, aoi_mean FROM frame_clouds ORDER BY month").fetchall()
    con.close()
    assert rows == [("2021-06", None), ("2022-06", 21.0)]


def test_frame_stats_collects_quantiles_inside_and_outside():
    # The point of the distribution columns: a forest's coolest tenth against the
    # surrounding fields' hottest tenth is the contrast the animation shows, and it
    # moves differently from the mean. Inside and outside are reduced separately.
    inside = _Img(0.9, mean=22.5, p10=18.0, p90=27.0)
    frames = [Frame("2022-07", inside, 3)]
    rows = metadata.frame_stats(frames, "REGION", 30, mean_band="INDEX",
                                ee_module=_fake_ee(), outside_geom="OUTSIDE")
    label, n, cloud, a_mean, a_p10, a_p90, o_mean, o_p10, o_p90 = rows[0]
    assert (label, n, cloud) == ("2022-07", 3, 0.1)
    assert (a_mean, a_p10, a_p90) == (22.5, 18.0, 27.0)
    # the same fake image backs both areas here, so outside mirrors inside —
    # what this pins is that the outside reducers RAN and were unpacked
    assert (o_mean, o_p10, o_p90) == (22.5, 18.0, 27.0)


def test_frame_stats_leaves_outside_null_when_no_outside_geometry():
    # A run without a frame geometry (or an api caller that omits it) must still
    # produce rows — with the outside columns null, not missing.
    frames = [Frame("2022-07", _Img(0.9, mean=22.5, p10=18.0, p90=27.0), 3)]
    rows = metadata.frame_stats(frames, "REGION", 30, mean_band="INDEX",
                                ee_module=_fake_ee())
    assert rows[0][3:6] == (22.5, 18.0, 27.0)
    assert rows[0][6:] == (None, None, None)


def test_stats_expr_combines_percentiles_into_one_reduction():
    # One reduceRegion per area per frame, not three: the percentiles share the
    # mean's pixel scan, so the batching in frame_stats is unaffected.
    seen = {}
    class _Probe:
        def reduceRegion(self, reducer, **kw):
            seen["reducer"] = str(reducer)
            seen["geometry"] = kw.get("geometry")
            return "EXPR"
    ee_fake = _fake_ee()
    assert metadata._stats_expr(_Probe(), "GEOM", 30, ee_fake) == "EXPR"
    assert seen["geometry"] == "GEOM"
    assert seen["reducer"] == "MEAN+P[10, 90]"      # combined, single reduction


def test_write_db_stores_and_migrates_the_distribution_columns(tmp_path):
    db = tmp_path / "metadata.db"
    run = {"name": "wne_lst", "sensor": "landsat", "index": "lst"}
    # an old DB written before the distribution columns existed
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE frame_clouds (name TEXT, sensor TEXT, index_name TEXT, "
                "month TEXT, n_scenes INTEGER, aoi_cloud_fraction REAL, "
                "aoi_clear_fraction REAL, aoi_mean REAL, PRIMARY KEY (name, month))")
    con.commit(); con.close()
    metadata.write_db(db, run, [("2022-07", 3, 0.1, 22.5, 18.0, 27.0, 25.1, 20.0, 31.0)])
    con = sqlite3.connect(db)
    got = con.execute("SELECT aoi_mean, aoi_p10, aoi_p90, outside_mean, outside_p10, "
                      "outside_p90 FROM frame_clouds WHERE month='2022-07'").fetchone()
    con.close()
    assert got == (22.5, 18.0, 27.0, 25.1, 20.0, 31.0)
