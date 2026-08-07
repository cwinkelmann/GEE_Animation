"""Plain-language display text for frame overlays.

Pure string formatting — no PIL, no I/O, no Earth Engine — mirroring how
`interpolate.py` is kept pure so its rules can be exercised on plain values
instead of real frames.

`compositing.py`/`render.py` produce compact, precise labels for people who
already know the pipeline: `"2022-05"`, `"n=3"`, `"2022-06 ← 2021"`. An
analyst-persona review of the audiences these animations now target found
that wording correct but jargon: a period key isn't a sentence, `n=3` reads
as a variable dump rather than "how much data backs this frame", and the
`←` borrow arrow assumes the reader already knows gap-filling exists. This
module rewords the same facts in plain language without dropping any of
them — in particular the interpolation percentage in `generated_text` stays
(review: "do not remove the interpolation percentage — add to it"), because
it is the one honesty signal that a generated frame is not an observation.

Period keys are the label formats `compositing.period_starts` produces:
`"YYYY-MM"` for monthly cadence, `"YYYY-MM-DD"` for sub-monthly. This module
does not import `compositing` — it only ever parses the strings it is
handed, the same seam `interpolate.py` uses for the same reason (see that
module's `_SPLIT_DAYS` comment).
"""
from __future__ import annotations

import calendar


def _parse(label: str) -> tuple[int, int, int | None]:
    """(year, month, day) from a period label; `day` is None for "YYYY-MM"."""
    parts = label.split("-")
    year, month = int(parts[0]), int(parts[1])
    day = int(parts[2]) if len(parts) > 2 else None
    return year, month, day


def _side(month: int, day: int | None) -> str:
    """Month name, or "day month" for a sub-monthly label — year omitted; the
    caller decides whether the year is shared (once) or must be shown per side."""
    name = calendar.month_name[month]
    return f"{day} {name}" if day is not None else name


def period_text(label: str) -> str:
    """Plain-language rendering of one period label: "May 2022" (monthly) or
    "11 May 2022" (sub-monthly), replacing the raw "YYYY-MM"/"YYYY-MM-DD" key.
    """
    year, month, day = _parse(label)
    return f"{_side(month, day)} {year}"


def observed_text(label: str, n_scenes: int | None, source) -> str:
    """Plain-language status line for a real (non-generated) frame.

    Segments are joined with " · " in a fixed order — period, then provenance,
    then scene count — and each is included only when known:

    - `source` is the year (or, for a multi-year pool, a "YYYY–YYYY" range)
      a gap-filled frame borrowed from; rendered as "image from <source>"
      rather than the `"YYYY-MM ← YYYY"` arrow notation, which reads as a
      diff to anyone not already familiar with this pipeline.
    - `n_scenes` is the number of satellite passes composited into the frame;
      rendered as "<n> pass"/"<n> passes" rather than `"n=<n>"`, which reads
      as a debug variable rather than a measure of how much data backs the
      image. `n_scenes=None` (e.g. `debug.py`'s per-scene frames, which are
      not composites) omits the segment entirely rather than showing "None
      passes" or leaving a dangling separator.
    """
    segments = [period_text(label)]
    if source is not None:
        segments.append(f"image from {source}")
    if n_scenes is not None:
        segments.append(f"{n_scenes} pass" if n_scenes == 1 else f"{n_scenes} passes")
    return " · ".join(segments)


def generated_text(label_a: str, label_b: str, pct: int) -> str:
    """Plain-language caption for an interpolated (non-observed) frame between
    two real periods, at `pct` percent of the way from `label_a` to `label_b`.

    Renders as "between <a> and <b> · <pct>%", keeping the percentage the
    interpolation math already reports (review: it must not be dropped, only
    reworded around) — it is what tells a viewer this frame was generated,
    not photographed. The year is shown once, after both sides, when the two
    periods fall in the same year ("between May and June 2022"); when they
    straddle a year boundary each side gets its own year, since dropping
    either would make one of them ambiguous ("between December 2021 and
    January 2022"). Sub-monthly labels use day-of-month forms on both sides
    the same way, including when the gap also crosses a month boundary
    ("between 21 May and 1 June 2022").
    """
    year_a, month_a, day_a = _parse(label_a)
    year_b, month_b, day_b = _parse(label_b)
    side_a, side_b = _side(month_a, day_a), _side(month_b, day_b)
    if year_a == year_b:
        span = f"between {side_a} and {side_b} {year_a}"
    else:
        span = f"between {side_a} {year_a} and {side_b} {year_b}"
    return f"{span} · {pct}%"
