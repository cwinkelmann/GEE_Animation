"""Generate frames between observations so playback reads as motion.

Pure array maths — no PIL, no I/O, no Earth Engine — so the blending rules (in
particular the no-data table) can be tested directly on small arrays.
"""
from __future__ import annotations

from bisect import bisect_right

import numpy as np


def blend(a_vals, a_valid, b_vals, b_valid, t: float):
    """Interpolate between two observations at fraction `t` in [0, 1].

    `a_vals`/`b_vals` are float arrays of identical shape — 2-D index units, or
    H×W×3 colour; this does not care which. `a_valid`/`b_valid` are boolean masks
    of shape ``values.shape[:2]``.

    Where both endpoints have data, the value is a linear blend. Where only one
    does, that endpoint's value is **held** rather than faded toward the no-data
    colour: a cloud hole present in one observation and absent in the next would
    otherwise pulse grey in and out on every transition, implying data appeared
    and vanished when in truth one observation simply had a hole. A pixel is
    no-data only when neither endpoint observed it.
    """
    both = a_valid & b_valid
    valid = a_valid | b_valid
    blended = a_vals * (1.0 - t) + b_vals * t
    # Broadcast the 2-D masks over a trailing colour axis when present.
    sel = both[..., None] if blended.ndim == 3 else both
    a_only = (a_valid & ~b_valid)[..., None] if blended.ndim == 3 else (a_valid & ~b_valid)
    values = np.where(sel, blended, np.where(a_only, a_vals, b_vals))
    return values, valid


def expand(items, steps: int, period_gap):
    """Yield ``(values, valid, label, is_real)`` with generated frames between items.

    `steps` is the number of generated frames per **one-period** step. Periods are
    not evenly spaced — bins below `min_scenes` are dropped — so a gap of k periods
    gets ``k * steps`` generated frames and playback speed tracks elapsed time. A
    fixed count per pair would play a three-month absence as fast as a one-month
    step, implying change happened faster than it did.
    """
    items = list(items)
    for i, (values, valid, label) in enumerate(items):
        yield values, valid, label, True
        if steps <= 0 or i + 1 >= len(items):
            continue
        b_vals, b_valid, b_label = items[i + 1]
        n = max(1, int(period_gap(label, b_label))) * steps
        for k in range(1, n + 1):
            t = k / (n + 1)
            gen_vals, gen_valid = blend(values, valid, b_vals, b_valid, t)
            yield gen_vals, gen_valid, f"{label} -> {b_label}  {round(100 * t)}%", False


# Day-of-month a sub-monthly period starts on, per cadence. Mirrors
# compositing.py's `_SPLIT_DAYS` (compositing.py:29) — that table is the source
# of truth for where a run's periods actually split, since it is what
# `period_starts` uses to build the labels this module only ever reads back.
# Duplicated rather than imported so `interpolate.py` stays free of any
# dependency on `compositing` (see module docstring); the two must be changed
# together if `compositing`'s split points ever move.
_SPLIT_DAYS = {
    "semimonthly": (1, 16),
    "10day": (1, 11, 21),
}


def _slot(label: str, cadence: str) -> int:
    """Ordinal position of a period label, in whole periods of `cadence`.

    Monthly labels ("2022-05") count whole months directly. Sub-monthly
    labels ("2022-05-16") count whichever periods `cadence` splits the month
    into (`_SPLIT_DAYS`) — a "YYYY-MM-01" label is period 0 under every
    sub-monthly cadence, but how many periods make up the *rest* of the month
    depends on `cadence`, which is why it has to be passed in rather than
    guessed from the label (see `gap_for`).
    """
    parts = label.split("-")
    year, month = int(parts[0]), int(parts[1])
    months = year * 12 + month
    if cadence == "monthly":
        return months
    days = _SPLIT_DAYS[cadence]
    day = int(parts[2])
    offset = bisect_right(days, day) - 1
    return months * len(days) + offset


def gap_for(cadence: str):
    """Build a `period_gap(label_a, label_b)` for one run's cadence.

    The label alone cannot always determine the grid: `compositing.period_starts`
    (compositing.py:55) picks the label *format* from the cadence, not the other
    way round, and a "YYYY-MM-01" label is the start of a period under both
    `semimonthly` and `10day` — they only disagree on how many periods fill the
    rest of the month. So the cadence has to come from the caller. Within one
    run, every label passed to the returned function shares that one cadence:
    `compositing.composite` picks exactly one strategy per run, and each
    strategy calls `period_starts` with the single `cfg.cadence` value
    (compositing.py:157, compositing.py:253) — so mixed-cadence pairs don't
    occur in practice.
    """
    def _gap(label_a: str, label_b: str) -> int:
        return max(1, _slot(label_b, cadence) - _slot(label_a, cadence))
    return _gap


# Convenience default for callers/tests that only ever deal with monthly
# labels. Sub-monthly runs must build their own gap function via
# `gap_for(cfg.cadence)` — this one silently ignores a "YYYY-MM-DD" label's
# day and counts whole months only, which is wrong for any sub-monthly cadence.
period_gap = gap_for("monthly")
