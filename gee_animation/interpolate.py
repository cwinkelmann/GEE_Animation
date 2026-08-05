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
