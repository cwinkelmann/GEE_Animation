"""Harmonic (Fourier) smoothing of an index time series.

Fits a seasonal model per pixel across the whole collection, then evaluates the
fitted curve at each frame's date. The result is a **model**, not a composite of
observations: it interpolates straight over cloud holes, removes the flicker of
thin one-pass composites, and eliminates the frozen-patch artefact that
`interpolate.blend` produces when a region is masked on one side of a
transition.

That smoothness is bought with a real trade, and the pipeline states it on every
frame (`render._caveats`) rather than in a comment: a harmonically smoothed
frame was never observed. It is the seasonal cycle plus a linear trend, sampled
at a date. Do not compute statistics from one — `config.validate` refuses to
combine `smooth: harmonic` with `metadata: true` for exactly that reason.

Model, for `k = 1..harmonics`::

    value(t) = c + m·t + Σ ( a_k·sin(2πkt) + b_k·cos(2πkt) )

`t` is time in fractional years, so k=1 is the annual cycle, k=2 the semiannual
(green-up and senescence asymmetry), and so on. Two harmonics is the usual
default for vegetation and temperature; more starts fitting noise.
"""
from __future__ import annotations

import math

import ee

from .products import INDEX_BAND

#: Origin for the time axis. Any fixed date works — it only shifts the phase —
#: but it must be identical for the fit and the evaluation, so it lives here
#: rather than being derived from a run's own start date.
EPOCH = "1970-01-01"


def predictor_names(harmonics: int) -> list[str]:
    """Band names of the design matrix, in the order the regression expects.

    Order matters twice over: `ee.Reducer.linearRegression` consumes the first
    `numX` bands as predictors and returns coefficients in the same order, so the
    fit and the evaluation must agree band for band.
    """
    names = ["constant", "t"]
    for k in range(1, int(harmonics) + 1):
        names += [f"sin{k}", f"cos{k}"]
    return names


def predictor_values(t: float, harmonics: int) -> list[float]:
    """The design-matrix row for time `t` (fractional years) — pure arithmetic.

    Kept free of Earth Engine so the model itself can be exercised on plain
    numbers: evaluating a known coefficient set at a known date is a test, not a
    round trip.
    """
    values = [1.0, float(t)]
    for k in range(1, int(harmonics) + 1):
        angle = 2.0 * math.pi * k * t
        values += [math.sin(angle), math.cos(angle)]
    return values


def fit(collection, harmonics: int = 2, ee_module=ee):
    """Per-pixel coefficient image for the harmonic model (UNEVALUATED)."""
    names = predictor_names(harmonics)
    epoch = ee_module.Date(EPOCH)

    def with_predictors(image):
        t = ee_module.Number(image.date().difference(epoch, "year"))
        bands = [ee_module.Image.constant(1).rename("constant"),
                 ee_module.Image.constant(t).float().rename("t")]
        for k in range(1, int(harmonics) + 1):
            angle = t.multiply(2.0 * math.pi * k)
            bands.append(ee_module.Image.constant(angle.sin()).float().rename(f"sin{k}"))
            bands.append(ee_module.Image.constant(angle.cos()).float().rename(f"cos{k}"))
        return image.addBands(ee_module.Image.cat(bands))

    prepared = collection.map(with_predictors).select(names + [INDEX_BAND])
    regression = prepared.reduce(
        ee_module.Reducer.linearRegression(numX=len(names), numY=1))
    # `coefficients` is an Nx1 array per pixel; project and flatten it back into
    # one named band per predictor so evaluation can select them by name.
    return regression.select("coefficients").arrayProject([0]).arrayFlatten([names])


def evaluate(coefficients, when: str, harmonics: int = 2, ee_module=ee):
    """The fitted value at date `when` (an ISO string), as an INDEX-band image."""
    t = ee_module.Date(when).difference(ee_module.Date(EPOCH), "year")
    names = predictor_names(harmonics)
    # getInfo-free: build the row server-side from the same trigonometry.
    total = None
    for name, k in zip(names, range(len(names))):
        term = coefficients.select(name)
        if name == "constant":
            piece = term
        elif name == "t":
            piece = term.multiply(ee_module.Image.constant(t))
        else:
            order = int(name[3:])
            angle = ee_module.Number(t).multiply(2.0 * math.pi * order)
            trig = angle.sin() if name.startswith("sin") else angle.cos()
            piece = term.multiply(ee_module.Image.constant(trig))
        total = piece if total is None else total.add(piece)
    return total.rename(INDEX_BAND)


def apply(frames, collection, cfg, ee_module=ee):
    """Replace each frame's image with the fitted curve at that frame's date.

    `frames` keep their labels, scene counts and pooling provenance — only the
    imagery changes — so the on-frame text still reports how much real data
    backs the period the model was fitted through.
    """
    mode = getattr(cfg, "smooth", None)
    if not mode:
        return frames
    if mode != "harmonic":
        raise ValueError(f"unknown smooth mode {mode!r}")
    harmonics = int(getattr(cfg, "harmonics", 2) or 2)
    coefficients = fit(collection, harmonics, ee_module)
    out = []
    for frame in frames:
        when = _frame_date(frame.label)
        image = evaluate(coefficients, when, harmonics, ee_module).set(
            "system:time_start", ee_module.Date(when).millis())
        out.append(frame._replace(image=image))
    return out


def _frame_date(label: str) -> str:
    """A period label -> the ISO date the model is evaluated at (period start).

    Quarterly labels ("2022-Q3") resolve to the quarter's first month; monthly
    ("2022-07") and sub-monthly ("2022-07-11") are already dates or become one.
    """
    parts = label.split("-")
    if len(parts) > 1 and parts[1][:1] in ("Q", "q"):
        month = 3 * int(parts[1][1:]) - 2
        return f"{parts[0]}-{month:02d}-01"
    if len(parts) == 2:
        return f"{label}-01"
    return label
