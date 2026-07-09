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
    def size(self):
        return types.SimpleNamespace(getInfo=lambda: self._count)
    def median(self):
        return FakeImage("median")


class FakeCollection:
    """Records filterDate calls; returns configured counts per month."""
    def __init__(self, counts_by_start):
        self.counts = counts_by_start
    def filterDate(self, start, end):
        return FakeFiltered(self.counts.get(start, 0))


def _fake_ee():
    return types.SimpleNamespace()


def test_monthly_median_skips_empty_months():
    cfg = types.SimpleNamespace(start="2022-01-01", end="2022-04-01")
    coll = FakeCollection({"2022-01-01": 5, "2022-02-01": 0, "2022-03-01": 3})
    frames = monthly_median(coll, cfg, ee_module=_fake_ee())
    labels = [f.label for f in frames]
    assert labels == ["2022-01", "2022-03"]   # Feb skipped (0 images)
    assert all(isinstance(f, Frame) for f in frames)
