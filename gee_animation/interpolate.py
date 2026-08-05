"""Generate frames between observations so playback reads as motion.

Pure array maths — no PIL, no I/O, no Earth Engine — so the blending rules (in
particular the no-data table) can be tested directly on small arrays.
"""
from __future__ import annotations

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


def _slot(label: str) -> int:
    """Ordinal position of a period label, in slots since year 0.

    Monthly labels ("2022-05") count whole months. Sub-monthly labels
    ("2022-05-11") count the 1st/11th/21st slots `compositing.period_starts`
    produces, three per month — so a 10-day cadence and a monthly one both give
    sensible distances without `interpolate` needing to know the cadence.
    """
    parts = label.split("-")
    year, month = int(parts[0]), int(parts[1])
    months = year * 12 + month
    if len(parts) < 3:
        return months * 3
    day = int(parts[2])
    return months * 3 + (0 if day < 11 else 1 if day < 21 else 2)


def period_gap(label_a: str, label_b: str) -> int:
    """Whole periods between two frame labels, at least 1.

    `_slot` puts both label styles on one scale (3 slots per month) so the
    diff is always meaningful, but the *unit* callers expect differs by
    cadence: monthly labels count whole months, so a pair of monthly labels
    divides the slot diff back down by 3; sub-monthly labels (or a mixed
    pair, which does not occur within a single run — see module docs) count
    10-day slots directly, at the raw scale `_slot` already produces.
    """
    diff = _slot(label_b) - _slot(label_a)
    if len(label_a.split("-")) < 3 and len(label_b.split("-")) < 3:
        diff //= 3
    return max(1, diff)
