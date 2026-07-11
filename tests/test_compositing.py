import types
from gee_animation.compositing import month_starts, monthly_median, Frame


def test_month_starts_spans_range():
    assert month_starts("2022-11-01", "2023-02-01") == [
        "2022-11-01", "2022-12-01", "2023-01-01",
    ]


def test_month_starts_excludes_end_month():
    # end is exclusive; Feb 1 -> only Jan
    assert month_starts("2023-01-01", "2023-02-01") == ["2023-01-01"]


class FakeImage:
    def __init__(self, tag):
        self.tag = tag


class FakeFiltered:
    def __init__(self, count):
        self._count = count
    def median(self):
        return FakeImage("median")


class FakeCollection:
    """Fakes the two calls monthly_median makes: aggregate the present months in
    one shot (via map + aggregate_array), then filterDate(month).median()."""
    def __init__(self, counts_by_start):
        self.counts = counts_by_start
    def map(self, fn):
        yms = [start[:7] for start, n in self.counts.items() for _ in range(n)]
        return types.SimpleNamespace(
            aggregate_array=lambda prop: types.SimpleNamespace(getInfo=lambda: yms))
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
