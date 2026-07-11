"""Group an ImageCollection into monthly median NDVI frames."""
from __future__ import annotations

from collections import namedtuple
from datetime import date

import ee

Frame = namedtuple("Frame", "label image")


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
    present = set(tagged.aggregate_array("ym").getInfo())
    frames: list[Frame] = []
    for start in month_starts(cfg.start, cfg.end):
        label = start[:7]  # YYYY-MM
        if label not in present:
            continue
        nxt = _add_month(date.fromisoformat(start)).isoformat()
        frames.append(Frame(label=label, image=collection.filterDate(start, nxt).median()))
    return frames
