import types
from datetime import date, datetime, timezone

from gee_animation.compositing import composite, month_starts, monthly_median, period_starts, Frame


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


def _fake_ee():
    return types.SimpleNamespace()


def test_monthly_median_skips_empty_months_and_reports_scene_counts():
    cfg = types.SimpleNamespace(start="2022-01-01", end="2022-04-01")
    coll = FakeCollection({"2022-01-01": 5, "2022-02-01": 0, "2022-03-01": 3})
    frames = monthly_median(coll, cfg, ee_module=_fake_ee())
    assert [(f.label, f.n_scenes) for f in frames] == [("2022-01", 5), ("2022-03", 3)]
    assert all(isinstance(f, Frame) for f in frames)   # Feb skipped (0 images)


def test_monthly_median_respects_min_scenes():
    # min_scenes=4 -> the 3-scene March median is dropped (median-of-few, not trusted)
    cfg = types.SimpleNamespace(start="2022-01-01", end="2022-04-01", min_scenes=4)
    coll = FakeCollection({"2022-01-01": 5, "2022-02-01": 0, "2022-03-01": 3})
    frames = monthly_median(coll, cfg, ee_module=_fake_ee())
    assert [(f.label, f.n_scenes) for f in frames] == [("2022-01", 5)]


def test_composite_labels_sub_monthly_periods_with_dates():
    cfg = types.SimpleNamespace(start="2022-01-01", end="2022-02-01", cadence="semimonthly")
    coll = FakeCollection({"2022-01-01": 3, "2022-01-16": 2})
    frames = composite(coll, cfg, ee_module=_fake_ee())
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
    composite(coll, cfg, ee_module=_fake_ee())
    assert calls == ["system:time_start"]
