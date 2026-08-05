"""Group an ImageCollection into cadence-bucketed median-composite frames."""
from __future__ import annotations

import logging
from collections import Counter, namedtuple
from datetime import date, datetime, timezone

import ee

from .config import pool_span

log = logging.getLogger(__name__)

# n_scenes = how many scenes went into the period's median (None if unknown, e.g. a
# hand-built Frame in tests). A median-of-one is not softening anything, so this is
# surfaced on every frame and gated by cfg.min_scenes.
#
# source = where a pooled frame's imagery actually came from (a year, e.g. 2021, for
# least_cloudy; a "y0–y1" range string for median), or None for a non-pooled frame.
# Kept separate from `label` (the period key) because `label` also serves as the PNG
# filename and the metadata `month` column — see pooled_composite.
Frame = namedtuple("Frame", "label image n_scenes source", defaults=(None, None))

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
    mirroring `inventory._judge`, which treats it as unusable rather than perfect.

    `clouds` is indexed positionally against the timestamps, so it MUST be the same
    length; that is guaranteed structurally by fetching both in one `ee.Dictionary`
    and checked explicitly in `pooled_composite`. There is deliberately no
    out-of-range fallback here: a short list would silently rank a scene by another
    scene's cloud value and then label the frame with the wrong source year.
    """
    v = clouds[i]
    return float("inf") if v is None else float(v)


def _min_scenes(cfg) -> int:
    """cfg.min_scenes, defaulting (and falling back on a falsy override) to 1."""
    return int(getattr(cfg, "min_scenes", 1) or 1)


def pooled_composite(collection, cfg, ee_module=ee) -> list[Frame]:
    """Cross-year "best month" frames: each period of [cfg.start, cfg.end) is filled
    from the SAME calendar period in ANY year of ``cfg.pool_years``.

    **Cosmetic mode.** A frame labelled 2022-05 may actually show May 2021, so the
    result is not a time series and must not be read as one. That is why every frame
    carries its source in `Frame.source` (e.g. ``2021``) — `render()` composes it into
    the drawn text (``"2022-05 ← 2021"``) and `render._info_text` states the pooled
    year range — an unlabelled pooled render is a misleading artefact.

    ``cfg.pool_strategy``:

    * ``least_cloudy`` (default) — the acquisition with the lowest region cloud
      fraction across all pooled years, mosaicked over its tiles. Sharpest; nothing is
      averaged in time, and the label names the one year the frame came from.
      Caveat: `region_cloud_fraction` is a mean over *unmasked* pixels, so a granule
      that only clips a corner of the region scores near zero and can outrank a
      genuinely clear full-coverage scene. Mosaicking the winning instant restores
      coverage, but the ranking itself is still coverage-blind; weighting it would
      need a per-scene valid-pixel fraction that `add_region_cloud_fraction` does not
      currently compute.
    * ``median`` — median over every pooled year's scenes for that calendar period.
      Smoother and fills holes better, but blurs and mixes years, so the label names
      the whole pooled range.
    * ``gap_fill`` — **true gap filling, and the only strategy that preserves the
      requested year.** The other two replace *every* frame, so a run over 2022 pooled
      across 2018–2024 can end up with only a couple of frames actually from 2022 — a
      synthetic "typical summer" rather than the year that was asked for. `gap_fill`
      instead keeps the nominal year (the year in each period's own start date, derived
      per period so a range spanning New Year stays honest) whenever that year has at
      least `cfg.min_scenes` scenes in the period, compositing them with the same
      `.median()` the non-pooled path uses. Only a period the nominal year cannot fill
      borrows, and it borrows the way `least_cloudy` does — the clearest single
      acquisition from the OTHER pooled years, mosaicked over its tiles.

      Provenance therefore has to say two different things, and the distinction is the
      point: a nominal-year frame is real data for its period, so `Frame.source` is
      ``None`` and `render` draws the bare period label — an arrow there would be a lie
      in the other direction. A borrowed frame keeps its source year (``2022-06 ←
      2019``). `render._info_text` still names the whole pooled range, which stays
      correct: it says what frames *may* have been drawn from, and some were.

      Two consequences of the single round trip, both deliberate: the nominal year must
      itself be inside `pool_years` to be seen at all (scenes outside the pooled span
      never reach the client), and `min_scenes` gates the borrowed set too — if neither
      the nominal year nor the other years clear it, the period is skipped, exactly as
      the other strategies skip.

    ONE round trip, whatever the scene or frame count: the per-scene arrays are
    batched into a single `ee.Dictionary(...).getInfo()`, the idiom
    `inventory.scene_inventory` uses. That is not only cheaper — it makes the
    timestamps and the cloud fractions come from one evaluation of one collection, so
    their positional alignment is structural rather than assumed. They have to come
    back client-side because the *chosen* scene's year is what the frame label says.
    """
    cadence = getattr(cfg, "cadence", "monthly")
    periods = period_starts(cfg.start, cfg.end, cadence)
    strategy = getattr(cfg, "pool_strategy", None) or "least_cloudy"
    if strategy not in ("least_cloudy", "median", "gap_fill"):
        raise ValueError(f"unknown pool_strategy {strategy!r}")
    span = pool_span(cfg)
    y0, y1 = int(cfg.pool_years[0]), int(cfg.pool_years[-1])
    # Re-apply the pooled span server-side so the scenes the median composites are
    # exactly the ones counted client-side below (a no-op when `collection.build`
    # already widened to the same range).
    pooled = collection.filterDate(*span)

    props = {"time": pooled.aggregate_array("system:time_start")}
    if strategy in ("least_cloudy", "gap_fill"):
        # gap_fill ranks clouds only for the periods it has to borrow, but the ranking
        # data still comes from the SAME single Dictionary — fetching it lazily per
        # gap would be one round trip per empty period.
        props["region_cloud"] = pooled.aggregate_array("region_cloud_fraction")
    data = ee_module.Dictionary(props).getInfo()
    millis = data["time"]
    clouds = data.get("region_cloud")
    if clouds is not None and len(clouds) != len(millis):
        # Fail loudly rather than rank scene i by scene j's cloud value: that would
        # put the wrong source year on the label, which is the one thing this mode
        # may never get wrong.
        raise RuntimeError(
            f"pooled scene metadata is misaligned: {len(millis)} timestamps but "
            f"{len(clouds)} region_cloud_fraction values")
    dates = [datetime.fromtimestamp(t / 1000, tz=timezone.utc).date() for t in millis]

    min_scenes = _min_scenes(cfg)
    frames: list[Frame] = []
    for label, p_start, p_end in periods:
        month, first_day, last_day = _calendar_key(p_start, p_end)
        candidates = [i for i, d in enumerate(dates)
                      if y0 <= d.year <= y1 and d.month == month
                      and first_day <= d.day <= last_day]
        if strategy == "gap_fill":
            # Nominal year first: this period is only a "gap" if its OWN year cannot
            # fill it. The year comes from the period's start date, not from cfg.start,
            # so a range crossing New Year keeps each period on its real year.
            nominal_year = date.fromisoformat(p_start).year
            nominal = [i for i in candidates if dates[i].year == nominal_year]
            if len(nominal) >= min_scenes:
                # Exactly the non-pooled path: a median of this period's own scenes.
                # source stays None — this is genuine data for the period, and marking
                # it borrowed would misreport it just as badly as hiding a borrow.
                frames.append(Frame(label=label,
                                    image=pooled.filterDate(p_start, p_end).median(),
                                    n_scenes=len(nominal), source=None))
                continue
            if nominal:
                log.info("%s: %d nominal-year scene(s) below min_scenes=%d, "
                         "looking for a donor year", label, len(nominal), min_scenes)
            # Genuine gap: borrow from the other pooled years only.
            candidates = [i for i in candidates if dates[i].year != nominal_year]
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
            # Select that one acquisition instant (filterDate takes epoch millis) — no
            # extra round trip, and it is provably the scene whose year the label
            # names. `.mosaic()`, not `.first()`: S2/Landsat scenes are per-tile, so an
            # AOI spanning a tile boundary would come back part no-data from a single
            # granule. Mosaicking that one instant's tiles restores full coverage and
            # still averages nothing across time.
            image = pooled.filterDate(millis[best], millis[best] + 1).mosaic()
            source, n_scenes = dates[best].year, 1
        # Mandatory provenance: the source is carried on the frame (not baked into
        # `label`, which stays a clean period key used as filename/DB key); `render`
        # composes it into the drawn text on every frame.
        frames.append(Frame(label=label, image=image, n_scenes=n_scenes, source=source))
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
    min_scenes = _min_scenes(cfg)
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
