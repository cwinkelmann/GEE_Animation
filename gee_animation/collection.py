"""Build a cloud-masked (sensor, index) ImageCollection with an INDEX band."""
from __future__ import annotations

import copy
import logging
from dataclasses import is_dataclass, replace

import ee

from .config import pool_span
from .products import THERMAL_INDICES, get_product

log = logging.getLogger(__name__)

# Thermal indices default to L8/L9: L7 SLC-off gaps and the 60/120 m TM/ETM+ thermal
# band stripe a few-scene median (see docs/REVIEW_AND_PLAN.md §1).
_LANDSAT8_START = "2013-04-11"   # first Landsat 8 acquisitions


def effective_missions(cfg):
    """The Landsat mission whitelist to enforce, or None (no filter / non-Landsat).

    Explicit `cfg.missions` wins; otherwise thermal indices default to L8/L9. Warns
    if a thermal default would exclude the pre-Landsat-8 part of the requested range.
    """
    if cfg.sensor != "landsat":
        return None
    if getattr(cfg, "missions", None):
        return list(cfg.missions)
    if cfg.index in THERMAL_INDICES:
        if str(cfg.start) < _LANDSAT8_START:
            log.warning(
                "thermal index %r defaults to L8/L9, but start %s predates Landsat 8 "
                "(%s); set `missions` explicitly to include TM/ETM+ (expect SLC-off / "
                "coarse-thermal striping).", cfg.index, cfg.start, _LANDSAT8_START)
        return ["L8", "L9"]
    return None


def add_region_cloud_fraction(image, region, scale, cloud_band, ee_module=ee):
    cloud = cloud_band(image, ee_module)
    frac = cloud.reduceRegion(
        reducer=ee_module.Reducer.mean(),
        geometry=region,
        scale=scale,
        bestEffort=True,
        maxPixels=int(1e9),
    ).get("cloud")
    return image.set("region_cloud_fraction", frac)


def _with_dates(cfg, start: str, end: str):
    """A copy of `cfg` with start/end replaced (dataclass or plain namespace)."""
    if is_dataclass(cfg):
        return replace(cfg, start=start, end=end)
    clone = copy.copy(cfg)
    clone.start, clone.end = start, end
    return clone


def build(cfg, frame_geom, region_geom, *, apply_cloud_filters: bool = True, ee_module=ee):
    """Build the (sensor, index) ImageCollection for `cfg`'s AOI/date range.

    `apply_cloud_filters=False` (default True) skips both the coarse scene-level
    cloud filter and the in-region cloud-fraction filter, while still computing
    `region_cloud_fraction` on every scene — this is what `inventory.py` needs to
    see the full unfiltered candidate set, with the same per-scene cloud metadata
    the filtered path would have judged them by.
    """
    sensor, index = get_product(cfg.sensor, cfg.index)
    span = pool_span(cfg)
    if span is not None:
        # Cross-year "best month" mode: the candidate scenes live outside
        # [cfg.start, cfg.end), so widen the date range to every pooled year here —
        # compositing.pooled_composite then buckets them by calendar period, ignoring
        # the year. Replacing start/end (rather than special-casing the filterDate
        # below) also reaches the index-supplied `build_collection` path.
        cfg = _with_dates(cfg, *span)
    if getattr(index, "build_collection", None) is not None:
        # Index supplies its own (already date/bounds-filtered) source collection,
        # e.g. lst_smw's satellite-aware Landsat+TOA join.
        coll = index.build_collection(cfg, frame_geom, ee_module)
    else:
        coll = (
            sensor.collection(ee_module)
            .filterDate(cfg.start, cfg.end)
            .filterBounds(frame_geom)
        )
    # Landsat mission selection (thermal defaults to L8/L9); applied to either path.
    missions = effective_missions(cfg)
    if missions is not None:
        coll = coll.filter(ee_module.Filter.inList("mission", missions))
    # Coarse scene-level cloud pre-filter — only sensors that carry a per-scene
    # cloud metadata property (S2, Landsat); MODIS has none, so skip it and rely
    # on the in-region QA cloud-fraction filter below.
    if apply_cloud_filters and sensor.scene_cloud_property is not None:
        coll = coll.filter(
            ee_module.Filter.lte(sensor.scene_cloud_property, cfg.max_cloud_percent))
    coll = coll.map(lambda img: add_region_cloud_fraction(
        img, region_geom, cfg.scale, sensor.cloud_band, ee_module))
    if apply_cloud_filters:
        coll = coll.filter(
            ee_module.Filter.lt("region_cloud_fraction", cfg.region_max_cloud_percent / 100.0))
    # index.compute derives a brand-new image (select/band-math/rename), which drops
    # every source property except the system:time_start each compute fn re-sets
    # explicitly (see products.py's NOTE). copyProperties restores the rest —
    # region_cloud_fraction, the sensor's scene cloud property, mission — so
    # inventory.scene_inventory and compositing.pooled_composite can still read them
    # back via aggregate_array() on the built collection.
    # Per-pixel QA cloud mask — separate from the scene-level filters above, so
    # `mask_clouds: false` (composites only; enforced by config.validate) keeps
    # real clouds in the imagery while the cloudiest scenes are still filtered out.
    if getattr(cfg, "mask_clouds", True):
        coll = coll.map(lambda img: sensor.mask_clouds(img, ee_module))
    coll = coll.map(lambda img: ee_module.Image(
        index.compute(sensor, img, ee_module).copyProperties(img, img.propertyNames())))
    return coll
