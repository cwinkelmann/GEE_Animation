"""Group an ImageCollection into cadence-bucketed median-composite frames."""
from __future__ import annotations

import logging
from collections import Counter, namedtuple
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

# n_scenes = how many scenes went into the period's median (None if unknown, e.g. a
# hand-built Frame in tests). A median-of-one is not softening anything, so this is
# surfaced on every frame and gated by cfg.min_scenes.
Frame = namedtuple("Frame", "label image n_scenes", defaults=(None,))

# Day-of-month a period starts on, per cadence. Sub-monthly periods always split
# within a calendar month (never span two), so every period nests inside exactly one
# month — that's what lets anomaly.py score a period against a *monthly* climatology.
_SPLIT_DAYS = {
    "monthly": (1,),
    "semimonthly": (1, 16),
    "10day": (1, 11, 21),
}


def _add_month(d: date) -> date:
    year = d.year + (d.month // 12)
    month = d.month % 12 + 1
    return date(year, month, 1)


def period_starts(start: str, end: str, cadence: str) -> list[tuple[str, str, str]]:
    """[(label, period_start_iso, period_end_iso)] for every period overlapping
    [start, end) (end exclusive), one calendar month at a time. Monthly labels are
    "YYYY-MM"; sub-monthly labels are the period's start date "YYYY-MM-DD".
    """
    days = _SPLIT_DAYS[cadence]
    month = date.fromisoformat(start).replace(day=1)
    stop = date.fromisoformat(end)
    out: list[tuple[str, str, str]] = []
    while month < stop:
        nxt_month = _add_month(month)
        bounds = [date(month.year, month.month, d) for d in days] + [nxt_month]
        for p_start, p_end in zip(bounds, bounds[1:]):
            if p_start >= stop:
                break
            label = p_start.isoformat()[:7] if cadence == "monthly" else p_start.isoformat()
            out.append((label, p_start.isoformat(), p_end.isoformat()))
        month = nxt_month
    return out


def month_starts(start: str, end: str) -> list[str]:
    """Back-compat thin wrapper over period_starts(..., "monthly"): just the start
    strings, used by tests/test_compositing.py and gui.py's progress estimate."""
    return [p_start for _, p_start, _ in period_starts(start, end, "monthly")]


def composite(collection, cfg) -> list[Frame]:
    # Find which periods actually have imagery in ONE server-side call: fetch every
    # scene's system:time_start and bucket it into cadence periods client-side. The
    # naive alternative — a `filterDate(period).size().getInfo()` per period —
    # issues one round-trip per period, each re-evaluating the whole (cloud-filtered)
    # collection, and does not scale to multi-year ranges or finer cadences.
    # (No `ee_module` param: unlike the old tag-then-aggregate implementation, nothing
    # here calls into the `ee` API — bucketing is pure Python over the fetched millis.)
    cadence = getattr(cfg, "cadence", "monthly")
    periods = period_starts(cfg.start, cfg.end, cadence)
    millis = collection.aggregate_array("system:time_start").getInfo()
    counts: Counter = Counter()
    for t in millis:
        d = datetime.fromtimestamp(t / 1000, tz=timezone.utc).date().isoformat()
        for label, p_start, p_end in periods:
            if p_start <= d < p_end:
                counts[label] += 1
                break
    min_scenes = int(getattr(cfg, "min_scenes", 1) or 1)
    frames: list[Frame] = []
    for label, p_start, p_end in periods:
        n = counts.get(label, 0)
        if n < min_scenes:
            if n > 0:
                log.info("skipping %s: %d scene(s) below min_scenes=%d", label, n, min_scenes)
            continue
        frames.append(Frame(label=label, image=collection.filterDate(p_start, p_end).median(),
                            n_scenes=n))
    return frames


# Back-compat alias: cli.py, api.py and gui.py all name this in their `deps` tables.
monthly_median = composite
