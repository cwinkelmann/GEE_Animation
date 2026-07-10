"""Region time-series of the computed INDEX band, for charting alongside the animation."""
from __future__ import annotations

import ee

from .products import INDEX_BAND

_REDUCERS = {"mean": lambda ee_module: ee_module.Reducer.mean(),
             "median": lambda ee_module: ee_module.Reducer.median()}


def inside_outside_timeseries(frames, region_geom, frame_geom, scale,
                              reducer="mean", ee_module=ee):
    """Reduce each monthly frame's INDEX band inside vs outside the AOI.

    Returns a list of ``(label, inside, outside)`` in frame order, where `inside`
    is the value over the AOI region and `outside` is over the frame minus the
    region. Either value is ``None`` when that area has no valid pixels.
    """
    red = _REDUCERS.get(reducer, _REDUCERS["mean"])(ee_module)
    outside_geom = frame_geom.difference(region_geom)

    def _reduce(band, geom):
        return (band.reduceRegion(reducer=red, geometry=geom, scale=scale,
                                  bestEffort=True, maxPixels=int(1e9))
                .get(INDEX_BAND).getInfo())

    rows = []
    for frame in frames:
        band = frame.image.select(INDEX_BAND)
        rows.append((frame.label, _reduce(band, region_geom), _reduce(band, outside_geom)))
    return rows
