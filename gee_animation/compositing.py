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
    frames: list[Frame] = []
    for start in month_starts(cfg.start, cfg.end):
        nxt = _add_month(date.fromisoformat(start)).isoformat()
        monthly = collection.filterDate(start, nxt)
        if monthly.size().getInfo() == 0:
            continue
        label = start[:7]  # YYYY-MM
        frames.append(Frame(label=label, image=monthly.median()))
    return frames
