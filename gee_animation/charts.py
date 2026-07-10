"""Region time-series of the computed INDEX band, for charting alongside the animation."""
from __future__ import annotations

import ee

from .products import INDEX_BAND

_REDUCERS = {"mean": lambda ee_module: ee_module.Reducer.mean(),
             "median": lambda ee_module: ee_module.Reducer.median()}


def region_timeseries(frames, region_geom, scale, reducer="mean", ee_module=ee):
    """Reduce each monthly frame's INDEX band over the region to one value.

    Returns a list of ``(label, value)`` in frame order; ``value`` is ``None``
    for a month with no valid pixels in the region.
    """
    red = _REDUCERS.get(reducer, _REDUCERS["mean"])(ee_module)
    series = []
    for frame in frames:
        value = (frame.image.select(INDEX_BAND)
                 .reduceRegion(reducer=red, geometry=region_geom, scale=scale,
                               bestEffort=True, maxPixels=int(1e9))
                 .get(INDEX_BAND)
                 .getInfo())
        series.append((frame.label, value))
    return series
