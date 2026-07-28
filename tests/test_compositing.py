import types
from datetime import date, datetime, timezone

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
    .first() for the single-scene pick and .filter()/.median() for the median pick.

    `scenes` is [(iso_date, region_cloud_fraction)] in collection order.
    """
    def __init__(self, scenes, filters=None):
        self.scenes = list(scenes)
        self.filters = [] if filters is None else filters

    def _rows(self):
        return self.scenes

    def aggregate_array(self, prop):
        key = {"system:time_start": lambda d, c: _millis(d),
               "region_cloud_fraction": lambda d, c: c}[prop]
        vals = [key(d, c) for d, c in self._rows()]
        return types.SimpleNamespace(getInfo=lambda: vals)

    def filterDate(self, start, end):
        if isinstance(start, str):
            keep = [(d, c) for d, c in self._rows() if start <= d < end]
        else:   # epoch millis instant
            keep = [(d, c) for d, c in self._rows() if start <= _millis(d) < end]
        return PooledCollection(keep, self.filters)

    def filter(self, f):
        # Applies calendarRange the way EE does (inclusive both ends, year-agnostic),
        # so the median test really exercises the bucketing and not just bookkeeping.
        _kind, lo, hi, unit = f
        field = {"month": lambda d: d.month, "day_of_month": lambda d: d.day}[unit]
        keep = [(d, c) for d, c in self._rows()
                if lo <= field(date.fromisoformat(d)) <= hi]
        return PooledCollection(keep, self.filters + [f])

    def _image(self, tag):
        img = FakeImage(tag)
        img.filters = list(self.filters)     # so tests can assert how it was bucketed
        return img

    def first(self):
        return self._image(f"scene:{self.scenes[0][0]}")

    def median(self):
        return self._image("median:" + ",".join(d for d, _ in self.scenes))


class FakeEE:
    """Only the Filter.calendarRange constructor pooled_composite uses."""
    class Filter:
        @staticmethod
        def calendarRange(start, end, unit):
            return ("calendarRange", start, end, unit)


def _pool_cfg(**kw):
    base = dict(start="2022-05-01", end="2022-06-01", cadence="monthly",
                pool_years=[2021, 2022], pool_strategy="least_cloudy")
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_pool_years_picks_least_cloudy_across_years():
    # May 2021 is 10% cloudy, May 2022 is 80% -> the 2021 scene wins, even though the
    # animation's nominal calendar is 2022.
    coll = PooledCollection([("2021-05-14", 0.10), ("2022-05-11", 0.80)])
    frames = composite(coll, _pool_cfg())
    assert len(frames) == 1
    assert frames[0].image.tag == "scene:2021-05-14"
    assert frames[0].n_scenes == 1          # one scene, nothing averaged


def test_pooled_frame_label_names_source_year():
    # Mandatory provenance: a 2022-05 frame actually showing May 2021 must say so.
    coll = PooledCollection([("2021-05-14", 0.10), ("2022-05-11", 0.80)])
    frames = composite(coll, _pool_cfg())
    assert frames[0].label == "2022-05 ← 2021"
    assert "2021" in frames[0].label


def test_pooled_label_always_carries_a_source_even_when_the_nominal_year_wins():
    # The label is never bare, not even when the borrowed year IS the nominal one.
    coll = PooledCollection([("2021-05-14", 0.90), ("2022-05-11", 0.05)])
    frames = composite(coll, _pool_cfg())
    assert frames[0].label == "2022-05 ← 2022"


def test_pooled_least_cloudy_ignores_scenes_outside_the_pool_years():
    # 2019 is outside pool_years even if the widened collection still carries it.
    coll = PooledCollection([("2019-05-02", 0.01), ("2021-05-14", 0.30)])
    frames = composite(coll, _pool_cfg())
    assert frames[0].image.tag == "scene:2021-05-14"
    assert frames[0].label == "2022-05 ← 2021"


def test_pooled_least_cloudy_skips_scenes_with_no_region_cloud_fraction():
    # A null fraction (scene fully masked over the region) must never win.
    coll = PooledCollection([("2021-05-14", None), ("2022-05-11", 0.40)])
    frames = composite(coll, _pool_cfg())
    assert frames[0].image.tag == "scene:2022-05-11"


def test_pool_strategy_median_composites_all_years():
    coll = PooledCollection([("2021-05-14", 0.10), ("2022-05-11", 0.80),
                             ("2022-07-01", 0.10)])          # July: wrong month
    frames = pooled_composite(coll, _pool_cfg(pool_strategy="median"), ee_module=FakeEE)
    assert len(frames) == 1
    assert frames[0].image.tag == "median:2021-05-14,2022-05-11"
    assert frames[0].n_scenes == 2
    assert frames[0].label == "2022-05 ← 2021–2022"          # whole range, not one year


def test_pool_strategy_median_filters_by_calendar_month_and_day_of_month():
    coll = PooledCollection([("2021-05-14", 0.10), ("2022-05-11", 0.80)])
    cfg = _pool_cfg(pool_strategy="median", cadence="semimonthly",
                    start="2022-05-01", end="2022-05-16")
    frames = pooled_composite(coll, cfg, ee_module=FakeEE)
    # 2022-05-01..2022-05-16 -> calendar slot "May, days 1..15", year-independent
    assert frames[0].image.filters == [("calendarRange", 5, 5, "month"),
                                       ("calendarRange", 1, 15, "day_of_month")]


def test_pooled_skips_periods_below_min_scenes():
    coll = PooledCollection([("2021-05-14", 0.10), ("2022-05-11", 0.80)])
    assert composite(coll, _pool_cfg(min_scenes=3)) == []
    assert len(composite(coll, _pool_cfg(min_scenes=2))) == 1


def test_pooled_issues_constant_round_trips_not_one_per_frame():
    # 12 frames, still two aggregate_array round trips (and none per scene).
    calls = []

    class Counting(PooledCollection):
        def aggregate_array(self, prop):
            calls.append(prop)
            return super().aggregate_array(prop)
        def filterDate(self, start, end):
            out = super().filterDate(start, end)
            return Counting(out.scenes, out.filters)

    scenes = [(f"2021-{m:02d}-05", 0.1) for m in range(1, 13)]
    frames = composite(Counting(scenes),
                       _pool_cfg(start="2022-01-01", end="2023-01-01"))
    assert len(frames) == 12
    assert calls == ["system:time_start", "region_cloud_fraction"]


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
