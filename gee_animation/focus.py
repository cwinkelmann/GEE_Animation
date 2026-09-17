"""Focus a run on its region: mask everything outside it, and/or express every
frame relative to its own region mean.

`region_only: true`   — imagery outside `aoi.region` is masked (renders as the
                        no-data grey), so the colour range can be spent on the
                        subject alone.
`relative: region_mean` — each frame becomes value − mean(value inside the
                        region) for THAT frame, in the index's units (K for a
                        thermal index). Absolute levels differ between years and
                        seasons; the within-region contrast (street vs. trees) is
                        what stays comparable, and this is what a fixed symmetric
                        colour range then shows across the whole series.

Runs last in the pipeline (after anomaly and smoothing), on the final field.
The mean is taken before the clip, over the region, at `cfg.scale`.
"""
from __future__ import annotations

import ee

from .products import INDEX_BAND

RELATIVE_MODES = ("region_mean",)


def apply(frames, cfg, region_geom, ee_module=ee):
    region_only = bool(getattr(cfg, "region_only", False))
    relative = getattr(cfg, "relative", None)
    if not region_only and not relative:
        return frames
    if relative and relative not in RELATIVE_MODES:
        raise ValueError(f"unknown relative mode {relative!r}")
    scale = float(getattr(cfg, "scale", 30) or 30)
    out = []
    for frame in frames:
        image = frame.image
        if relative == "region_mean":
            mean = image.select(INDEX_BAND).reduceRegion(
                ee_module.Reducer.mean(), geometry=region_geom, scale=scale,
                bestEffort=True, maxPixels=int(1e9)).get(INDEX_BAND)
            image = image.subtract(ee_module.Image.constant(ee_module.Number(mean)))
        if region_only:
            image = image.clip(region_geom)
        out.append(frame._replace(image=image))
    return out
