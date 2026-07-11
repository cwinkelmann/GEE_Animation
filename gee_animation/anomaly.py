"""Anomaly rendering — turn an index loop from decorative into diagnostic.

Instead of the raw value (which mostly animates the weather on the overpass day),
render how far each pixel departs from its own normal (docs/REVIEW_AND_PLAN.md P1-1):

  * ``climatology`` — per-pixel z-score ``(x - mean_m) / std_m`` against that pixel's
    long-term value **for the same calendar month** over ``baseline_years``. Works for
    any index; units are standard deviations.
  * ``reference`` (thermal only) — ``LST - ERA5-Land 2 m air temperature``, i.e. how
    much warmer/cooler the surface is than the air that month. Units are °C.

Both use a diverging, symmetric palette (see :data:`ANOMALY_VIZ`), so the interesting
structure — e.g. the Grumsin forest as a coherent cool unit — actually shows up.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import ee

from .products import INDEX_BAND

# Diverging blue -> white -> red; symmetric ranges. climatology in std-devs, reference
# in °C. Applied as the viz default when `anomaly` is set (subsumes P0-5).
_DIVERGING = ["#2166ac", "#67a9cf", "#d1e5f0", "#f7f7f7", "#fddbc7", "#ef8a62", "#b2182b"]
ANOMALY_VIZ = {
    "climatology": (-3.0, 3.0, _DIVERGING),   # z-score (standard deviations)
    "reference": (-10.0, 10.0, _DIVERGING),   # °C (surface minus air)
}
_ERA5 = "ECMWF/ERA5_LAND/HOURLY"


def _month(label: str) -> int:
    return int(label[5:7])   # "YYYY-MM" -> MM


def climatology_anomaly(frames, baseline_collection, ee_module=ee):
    """Replace each frame's INDEX with its z-score vs the baseline monthly climatology."""
    def _stats(m):
        monthly = (baseline_collection
                   .filter(ee_module.Filter.calendarRange(m, m, "month"))
                   .select(INDEX_BAND))
        return monthly.mean(), monthly.reduce(ee_module.Reducer.stdDev()).rename(INDEX_BAND)

    out = []
    for f in frames:
        mean, std = _stats(_month(f.label))
        z = (f.image.select(INDEX_BAND).subtract(mean).divide(std)
             .rename(INDEX_BAND).set("system:time_start", f.image.get("system:time_start")))
        out.append(f._replace(image=z))
    return out


def reference_anomaly(frames, ee_module=ee):
    """Replace each thermal frame's LST with LST minus that month's mean ERA5 air temp."""
    out = []
    for f in frames:
        y, m = int(f.label[:4]), _month(f.label)
        start = date(y, m, 1)
        end = date(y + m // 12, m % 12 + 1, 1)
        era5_c = (ee_module.ImageCollection(_ERA5)
                  .filterDate(start.isoformat(), end.isoformat())
                  .select("temperature_2m").mean().subtract(273.15))
        anom = (f.image.select(INDEX_BAND).subtract(era5_c)
                .rename(INDEX_BAND).set("system:time_start", f.image.get("system:time_start")))
        out.append(f._replace(image=anom))
    return out


def apply(frames, cfg, frame_geom, region_geom, build_fn, ee_module=ee):
    """Transform frames per ``cfg.anomaly`` (None -> unchanged).

    ``build_fn`` builds a collection (collection.build) — used to assemble the
    baseline collection over ``cfg.baseline_years`` for the climatology.
    """
    mode = getattr(cfg, "anomaly", None)
    if not mode:
        return frames
    if mode == "climatology":
        y0, y1 = cfg.baseline_years
        base_cfg = replace(cfg, start=f"{y0}-01-01", end=f"{y1 + 1}-01-01", anomaly=None)
        baseline = build_fn(base_cfg, frame_geom, region_geom, ee_module)
        return climatology_anomaly(frames, baseline, ee_module)
    if mode == "reference":
        return reference_anomaly(frames, ee_module)
    raise ValueError(f"unknown anomaly mode {mode!r}")
