"""Group an ImageCollection into monthly median NDVI frames."""
from __future__ import annotations

import logging
from collections import Counter, namedtuple
from datetime import date

import ee

log = logging.getLogger(__name__)

# n_scenes = how many scenes went into the month's median (None if unknown, e.g. a
# hand-built Frame in tests). A median-of-one is not softening anything, so this is
# surfaced on every frame and gated by cfg.min_scenes.
Frame = namedtuple("Frame", "label image n_scenes", defaults=(None,))


def _add_month(d: date) -> date:
    year = d.year + (d.month // 12)
    month = d.month % 12 + 1
    return date(year, month, 1)


def month_starts(start: str, end: str) -> list[str]:
    cur = date.fromisoformat(start).replace(day=1)
    stop = date.fromisoformat(end)
    out: list[str] = []
    while cur < stop:
        out.append(cur.isoformat())
        cur = _add_month(cur)
    return out


def monthly_median(collection, cfg, ee_module=ee) -> list[Frame]:
    # Find which months actually have imagery in ONE server-side call: tag each
    # image with its "YYYY-MM" and aggregate the distinct values. The naive
    # alternative — a `filterDate(month).size().getInfo()` per month — issues one
    # round-trip per month, each re-evaluating the whole (cloud-filtered)
    # collection, and does not scale to multi-year ranges.
    tagged = collection.map(
        lambda img: img.set(
            "ym", ee_module.Date(img.get("system:time_start")).format("YYYY-MM")))
    # Per-month scene counts (not just presence) from the same single getInfo.
    counts = Counter(tagged.aggregate_array("ym").getInfo())
    min_scenes = int(getattr(cfg, "min_scenes", 1) or 1)
    frames: list[Frame] = []
    for start in month_starts(cfg.start, cfg.end):
        label = start[:7]  # YYYY-MM
        n = counts.get(label, 0)
        if n < min_scenes:
            if n > 0:
                log.info("skipping %s: %d scene(s) below min_scenes=%d", label, n, min_scenes)
            continue
        nxt = _add_month(date.fromisoformat(start)).isoformat()
        frames.append(Frame(label=label, image=collection.filterDate(start, nxt).median(),
                            n_scenes=n))
    return frames
