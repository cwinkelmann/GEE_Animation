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
