"""Group an ImageCollection into cadence-bucketed median-composite frames."""
from __future__ import annotations

import logging
from collections import Counter, namedtuple
from datetime import date, datetime, timezone

import ee

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


def pool_span(cfg) -> tuple[str, str] | None:
    """(start, end) covering every year in ``cfg.pool_years``, or None when
    cross-year pooling is off.

    ``collection.build`` widens its ``filterDate`` to this span so the pooled scenes
    exist at all; :func:`pooled_composite` then buckets them by calendar period.
    """
    years = getattr(cfg, "pool_years", None)
    if not years:
        return None
    y0, y1 = int(years[0]), int(years[-1])
    return f"{y0}-01-01", f"{y1 + 1}-01-01"


def _calendar_key(p_start_iso: str, p_end_iso: str) -> tuple[int, int, int]:
    """(month, first_day, last_day_inclusive) — a period's calendar signature with
    the year stripped, i.e. the slot it occupies in *every* year.

    Periods never span two calendar months (see :data:`_SPLIT_DAYS`), so a month plus
    a day-of-month range identifies the same slot in any year. The last day is 31 for
    a period that runs to the end of its month, so February's shorter months still
    match — the range is a filter, not a calendar.
    """
    p_start, p_end = date.fromisoformat(p_start_iso), date.fromisoformat(p_end_iso)
    last_day = 31 if p_end.month != p_start.month else p_end.day - 1
    return p_start.month, p_start.day, last_day


def _cloud_rank(clouds, i) -> float:
    """Sort key for the least-cloudy pick. A missing region_cloud_fraction (scene
    fully masked over the region, so reduceRegion came back empty) sorts last —
    mirroring `inventory._judge`, which treats it as unusable rather than perfect."""
    v = clouds[i] if clouds is not None and i < len(clouds) else None
    return float("inf") if v is None else float(v)


def pooled_composite(collection, cfg, ee_module=ee) -> list[Frame]:
    """Cross-year "best month" frames: each period of [cfg.start, cfg.end) is filled
    from the SAME calendar period in ANY year of ``cfg.pool_years``.

    **Cosmetic mode.** A frame labelled 2022-05 may actually show May 2021, so the
    result is not a time series and must not be read as one. That is why every frame
    label carries its source (``"2022-05 <- 2021"``) and `render._info_text` states
    the pooled year range — an unlabelled pooled render is a misleading artefact.

    ``cfg.pool_strategy``:

    * ``least_cloudy`` (default) — the single scene with the lowest region cloud
      fraction across all pooled years. Sharpest; nothing is averaged, and the label
      names the one year the frame came from.
    * ``median`` — median over every pooled year's scenes for that calendar period.
      Smoother and fills holes better, but blurs and mixes years, so the label names
      the whole pooled range.

    Round trips are constant, not per scene: one `aggregate_array` for the
    timestamps (which calendar slot and which year each scene sits in) and, for
    ``least_cloudy`` only, one for `region_cloud_fraction` (how to rank them). Both
    have to come back client-side because the *chosen* scene's year is what the frame
    label has to say.
    """
    cadence = getattr(cfg, "cadence", "monthly")
    periods = period_starts(cfg.start, cfg.end, cadence)
    strategy = getattr(cfg, "pool_strategy", None) or "least_cloudy"
    if strategy not in ("least_cloudy", "median"):
        raise ValueError(f"unknown pool_strategy {strategy!r}")
    span = pool_span(cfg)
    y0, y1 = int(cfg.pool_years[0]), int(cfg.pool_years[-1])
    # Re-apply the pooled span server-side so the scenes the median composites are
    # exactly the ones counted client-side below (a no-op when `collection.build`
    # already widened to the same range).
    pooled = collection.filterDate(*span)

    millis = pooled.aggregate_array("system:time_start").getInfo()
    clouds = (pooled.aggregate_array("region_cloud_fraction").getInfo()
              if strategy == "least_cloudy" else None)
    dates = [datetime.fromtimestamp(t / 1000, tz=timezone.utc).date() for t in millis]

    min_scenes = int(getattr(cfg, "min_scenes", 1) or 1)
    frames: list[Frame] = []
    for label, p_start, p_end in periods:
        month, first_day, last_day = _calendar_key(p_start, p_end)
        candidates = [i for i, d in enumerate(dates)
                      if y0 <= d.year <= y1 and d.month == month
                      and first_day <= d.day <= last_day]
        if len(candidates) < min_scenes:
            if candidates:
                log.info("skipping %s: %d pooled scene(s) below min_scenes=%d",
                         label, len(candidates), min_scenes)
            continue
        if strategy == "median":
            image = (pooled
                     .filter(ee_module.Filter.calendarRange(month, month, "month"))
                     .filter(ee_module.Filter.calendarRange(
                         first_day, last_day, "day_of_month"))
                     .median())
            source, n_scenes = f"{y0}–{y1}", len(candidates)
        else:
            best = min(candidates, key=lambda i: _cloud_rank(clouds, i))
            # Select that one scene by its exact acquisition instant (filterDate takes
            # epoch millis) — no extra round trip, and it is provably the scene whose
            # year the label names.
            image = pooled.filterDate(millis[best], millis[best] + 1).first()
            source, n_scenes = str(dates[best].year), 1
        # Mandatory provenance: the source year is part of the label, which render
        # draws on every frame and writes into every frame's filename.
        frames.append(Frame(label=f"{label} ← {source}", image=image,
                            n_scenes=n_scenes))
    return frames


def composite(collection, cfg) -> list[Frame]:
    if getattr(cfg, "pool_years", None):
        # Cross-year "best month" mode — opt-in; see pooled_composite.
        return pooled_composite(collection, cfg)
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
