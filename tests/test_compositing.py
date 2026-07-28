import types
from datetime import date, datetime, timezone

import pytest

from gee_animation.compositing import (
    composite, month_starts, monthly_median, period_starts, pool_span,
    pooled_composite, Frame)


def test_month_starts_spans_range():
    assert month_starts("2022-11-01", "2023-02-01") == [
        "2022-11-01", "2022-12-01", "2023-01-01",
    ]


def test_month_starts_excludes_end_month():
    # end is exclusive; Feb 1 -> only Jan
    assert month_starts("2023-01-01", "2023-02-01") == ["2023-01-01"]


def test_month_starts_wrapper_still_returns_start_strings():
    # back-compat: month_starts is period_starts(..., "monthly") stripped to just
    # the start strings — tests/test_compositing.py (above) and gui.py:18 both use it.
    starts = month_starts("2022-01-01", "2022-03-01")
    assert starts == ["2022-01-01", "2022-02-01"]
    assert all(isinstance(s, str) for s in starts)


def test_period_starts_semimonthly_splits_at_1st_and_16th():
    assert period_starts("2022-01-01", "2022-02-01", "semimonthly") == [
        ("2022-01-01", "2022-01-01", "2022-01-16"),
        ("2022-01-16", "2022-01-16", "2022-02-01"),
    ]


def test_period_starts_10day_splits_at_1_11_21():
    assert period_starts("2022-01-01", "2022-02-01", "10day") == [
        ("2022-01-01", "2022-01-01", "2022-01-11"),
        ("2022-01-11", "2022-01-11", "2022-01-21"),
        ("2022-01-21", "2022-01-21", "2022-02-01"),
    ]


class FakeImage:
    def __init__(self, tag):
        self.tag = tag


class FakeFiltered:
    def __init__(self, count):
        self._count = count
    def median(self):
        return FakeImage("median")


def _millis(iso_date: str) -> int:
    d = date.fromisoformat(iso_date)
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


class FakeCollection:
    """Fakes the single call composite() makes: aggregate_array("system:time_start")
    to get every scene's timestamp in one round trip, then filterDate(period).median().
    `counts_by_start` keys are period-start dates (month starts for monthly cadence,
    finer period starts for sub-monthly)."""
    def __init__(self, counts_by_start):
        self.counts = counts_by_start
    def aggregate_array(self, prop):
        assert prop == "system:time_start"
        vals = [_millis(start) for start, n in self.counts.items() for _ in range(n)]
        return types.SimpleNamespace(getInfo=lambda: vals)
    def filterDate(self, start, end):
        return FakeFiltered(self.counts.get(start, 0))


def test_monthly_median_skips_empty_months_and_reports_scene_counts():
    cfg = types.SimpleNamespace(start="2022-01-01", end="2022-04-01")
    coll = FakeCollection({"2022-01-01": 5, "2022-02-01": 0, "2022-03-01": 3})
    frames = monthly_median(coll, cfg)
    assert [(f.label, f.n_scenes) for f in frames] == [("2022-01", 5), ("2022-03", 3)]
    assert all(isinstance(f, Frame) for f in frames)   # Feb skipped (0 images)


def test_monthly_median_respects_min_scenes():
    # min_scenes=4 -> the 3-scene March median is dropped (median-of-few, not trusted)
    cfg = types.SimpleNamespace(start="2022-01-01", end="2022-04-01", min_scenes=4)
    coll = FakeCollection({"2022-01-01": 5, "2022-02-01": 0, "2022-03-01": 3})
    frames = monthly_median(coll, cfg)
    assert [(f.label, f.n_scenes) for f in frames] == [("2022-01", 5)]


def test_composite_labels_sub_monthly_periods_with_dates():
    cfg = types.SimpleNamespace(start="2022-01-01", end="2022-02-01", cadence="semimonthly")
    coll = FakeCollection({"2022-01-01": 3, "2022-01-16": 2})
    frames = composite(coll, cfg)
    assert [(f.label, f.n_scenes) for f in frames] == [
        ("2022-01-01", 3), ("2022-01-16", 2),
    ]


def test_composite_issues_a_single_getInfo_round_trip():
    # Global constraint: exactly one getInfo() call, whatever the cadence — no
    # per-period round trip.
    calls = []

    class CountingCollection(FakeCollection):
        def aggregate_array(self, prop):
            calls.append(prop)
            return super().aggregate_array(prop)

    cfg = types.SimpleNamespace(start="2022-01-01", end="2022-04-01")
    coll = CountingCollection({"2022-01-01": 5, "2022-02-01": 0, "2022-03-01": 3})
    composite(coll, cfg)
    assert calls == ["system:time_start"]


# --- cross-year "best month" pooling ------------------------------------------------

class PooledCollection:
    """Fakes what pooled_composite touches: aggregate_array for timestamps and
    region_cloud_fraction, filterDate (both ISO strings and epoch-millis instants),
    .mosaic() for the single-instant pick and .filter()/.median() for the median pick.

    `scenes` is [(iso_date, region_cloud_fraction[, tile])] in collection order; the
    optional tile name lets a test put two granules on the SAME instant, which is what
    an AOI spanning a tile boundary looks like.
    """
    def __init__(self, scenes, filters=None):
        self.scenes = [(s + ("t",))[:3] if len(s) < 3 else tuple(s) for s in scenes]
        self.filters = [] if filters is None else filters

    def aggregate_array(self, prop):
        key = {"system:time_start": lambda d, c, t: _millis(d),
               "region_cloud_fraction": lambda d, c, t: c}[prop]
        vals = [key(*row) for row in self.scenes]
        return types.SimpleNamespace(getInfo=lambda: vals)

    def filterDate(self, start, end):
        if isinstance(start, str):
            keep = [r for r in self.scenes if start <= r[0] < end]
        else:   # epoch millis instant
            keep = [r for r in self.scenes if start <= _millis(r[0]) < end]
        return type(self)(keep, self.filters)      # subclass overrides survive

    def filter(self, f):
        # Applies calendarRange the way EE does (inclusive both ends, year-agnostic),
        # so the median test really exercises the bucketing and not just bookkeeping.
        _kind, lo, hi, unit = f
        field = {"month": lambda d: d.month, "day_of_month": lambda d: d.day}[unit]
        keep = [r for r in self.scenes if lo <= field(date.fromisoformat(r[0])) <= hi]
        return type(self)(keep, self.filters + [f])

    def _image(self, tag):
        img = FakeImage(tag)
        img.filters = list(self.filters)     # so tests can assert how it was bucketed
        return img

    def mosaic(self):
        return self._image("mosaic:" + "+".join(t for _d, _c, t in self.scenes))

    def median(self):
        return self._image("median:" + ",".join(d for d, _c, _t in self.scenes))


class FakeEE:
    """The two ee entry points pooled_composite uses: Filter.calendarRange, and
    Dictionary(...).getInfo() batching every per-scene array into ONE round trip."""
    class Filter:
        @staticmethod
        def calendarRange(start, end, unit):
            return ("calendarRange", start, end, unit)

    @staticmethod
    def Dictionary(props):
        return types.SimpleNamespace(
            getInfo=lambda: {k: v.getInfo() for k, v in props.items()})


def _pool_cfg(**kw):
    base = dict(start="2022-05-01", end="2022-06-01", cadence="monthly",
                pool_years=[2021, 2022], pool_strategy="least_cloudy")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _pooled(coll, cfg):
    """pooled_composite with EE faked at the ee_module seam (never live)."""
    return pooled_composite(coll, cfg, ee_module=FakeEE)


def test_pool_years_picks_least_cloudy_across_years():
    # May 2021 is 10% cloudy, May 2022 is 80% -> the 2021 scene wins, even though the
    # animation's nominal calendar is 2022.
    coll = PooledCollection([("2021-05-14", 0.10, "MAY21"), ("2022-05-11", 0.80, "MAY22")])
    frames = _pooled(coll, _pool_cfg())
    assert len(frames) == 1
    assert frames[0].image.tag == "mosaic:MAY21"
    assert frames[0].n_scenes == 1          # one acquisition, nothing averaged in time


def test_pooled_least_cloudy_mosaics_every_tile_of_the_winning_instant():
    # S2/Landsat scenes are per-tile: an AOI spanning a tile boundary must receive
    # BOTH granules of the chosen acquisition, or the frame comes back part no-data
    # grey. Tiles A and B share one instant; the cloudier 2022 scene must lose.
    coll = PooledCollection([("2021-05-14", 0.10, "A"), ("2021-05-14", 0.12, "B"),
                             ("2022-05-11", 0.80, "C")])
    frames = _pooled(coll, _pool_cfg())
    assert frames[0].image.tag == "mosaic:A+B"       # both tiles contribute
    assert frames[0].label == "2022-05 ← 2021"


def test_pooled_frame_label_names_source_year():
    # Mandatory provenance: a 2022-05 frame actually showing May 2021 must say so.
    coll = PooledCollection([("2021-05-14", 0.10), ("2022-05-11", 0.80)])
    frames = _pooled(coll, _pool_cfg())
    assert frames[0].label == "2022-05 ← 2021"
    assert "2021" in frames[0].label


def test_pooled_label_always_carries_a_source_even_when_the_nominal_year_wins():
    # The label is never bare, not even when the borrowed year IS the nominal one.
    coll = PooledCollection([("2021-05-14", 0.90), ("2022-05-11", 0.05)])
    frames = _pooled(coll, _pool_cfg())
    assert frames[0].label == "2022-05 ← 2022"


def test_pooled_least_cloudy_ignores_scenes_outside_the_pool_years():
    # 2019 is outside pool_years even if the widened collection still carries it.
    coll = PooledCollection([("2019-05-02", 0.01, "OLD"), ("2021-05-14", 0.30, "IN")])
    frames = _pooled(coll, _pool_cfg())
    assert frames[0].image.tag == "mosaic:IN"
    assert frames[0].label == "2022-05 ← 2021"


def test_pooled_least_cloudy_skips_scenes_with_no_region_cloud_fraction():
    # A null fraction (scene fully masked over the region) must never win.
    coll = PooledCollection([("2021-05-14", None, "MASKED"), ("2022-05-11", 0.40, "OK")])
    frames = _pooled(coll, _pool_cfg())
    assert frames[0].image.tag == "mosaic:OK"


def test_pooled_rejects_misaligned_scene_metadata():
    # Timestamps and cloud fractions are paired positionally; a short cloud list would
    # rank scene i by scene j's value and then put the WRONG source year on the label.
    # Detect that, never paper over it with an out-of-range fallback.
    class Misaligned(PooledCollection):
        def aggregate_array(self, prop):
            out = super().aggregate_array(prop)
            if prop == "region_cloud_fraction":
                short = out.getInfo()[:-1]
                return types.SimpleNamespace(getInfo=lambda: short)
            return out

    coll = Misaligned([("2021-05-14", 0.10), ("2022-05-11", 0.80)])
    with pytest.raises(RuntimeError, match="misaligned"):
        _pooled(coll, _pool_cfg())


def test_pool_strategy_median_composites_all_years():
    coll = PooledCollection([("2021-05-14", 0.10), ("2022-05-11", 0.80),
                             ("2022-07-01", 0.10)])          # July: wrong month
    frames = _pooled(coll, _pool_cfg(pool_strategy="median"))
    assert len(frames) == 1
    assert frames[0].image.tag == "median:2021-05-14,2022-05-11"
    assert frames[0].n_scenes == 2
    assert frames[0].label == "2022-05 ← 2021–2022"          # whole range, not one year


def test_pool_strategy_median_filters_by_calendar_month_and_day_of_month():
    coll = PooledCollection([("2021-05-14", 0.10), ("2022-05-11", 0.80)])
    cfg = _pool_cfg(pool_strategy="median", cadence="semimonthly",
                    start="2022-05-01", end="2022-05-16")
    frames = _pooled(coll, cfg)
    # 2022-05-01..2022-05-16 -> calendar slot "May, days 1..15", year-independent
    assert frames[0].image.filters == [("calendarRange", 5, 5, "month"),
                                       ("calendarRange", 1, 15, "day_of_month")]


def test_pooled_skips_periods_below_min_scenes():
    coll = PooledCollection([("2021-05-14", 0.10), ("2022-05-11", 0.80)])
    assert _pooled(coll, _pool_cfg(min_scenes=3)) == []
    assert len(_pooled(coll, _pool_cfg(min_scenes=2))) == 1


def test_pooled_issues_one_round_trip_not_one_per_frame():
    # 12 frames, still exactly ONE getInfo(): every per-scene array is batched into a
    # single ee.Dictionary, which is also what makes their alignment structural.
    round_trips = []

    class CountingEE(FakeEE):
        @staticmethod
        def Dictionary(props):
            inner = FakeEE.Dictionary(props)
            def getInfo():
                round_trips.append(sorted(props))
                return inner.getInfo()
            return types.SimpleNamespace(getInfo=getInfo)

    scenes = [(f"2021-{m:02d}-05", 0.1) for m in range(1, 13)]
    frames = pooled_composite(PooledCollection(scenes),
                              _pool_cfg(start="2022-01-01", end="2023-01-01"),
                              ee_module=CountingEE)
    assert len(frames) == 12
    assert round_trips == [["region_cloud", "time"]]


def test_composite_delegates_to_pooled_composite_when_pool_years_is_set(monkeypatch):
    # composite() keeps its two-argument signature; the pooled path is reached purely
    # off cfg.pool_years, with no live EE call from composite() itself.
    seen = []
    monkeypatch.setattr("gee_animation.compositing.pooled_composite",
                        lambda coll, cfg: (seen.append((coll, cfg)) or ["POOLED"]))
    coll = PooledCollection([("2021-05-14", 0.10)])
    cfg = _pool_cfg()
    assert composite(coll, cfg) == ["POOLED"]
    assert seen == [(coll, cfg)]


def test_pool_span_is_none_without_pool_years_and_spans_whole_years_with():
    assert pool_span(types.SimpleNamespace()) is None
    assert pool_span(types.SimpleNamespace(pool_years=None)) is None
    assert pool_span(types.SimpleNamespace(pool_years=[2019, 2024])) == (
        "2019-01-01", "2025-01-01")


def test_composite_without_pool_years_is_unchanged():
    # Regression guard for the opt-in contract: no pool_years -> the plain
    # median-per-period path, untouched labels, one round trip.
    cfg = types.SimpleNamespace(start="2022-01-01", end="2022-03-01", pool_years=None)
    coll = FakeCollection({"2022-01-01": 2, "2022-02-01": 1})
    frames = composite(coll, cfg)
    assert [(f.label, f.n_scenes) for f in frames] == [("2022-01", 2), ("2022-02", 1)]
    assert all("←" not in f.label for f in frames)
