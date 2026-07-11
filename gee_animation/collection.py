"""Build a cloud-masked (sensor, index) ImageCollection with an INDEX band."""
from __future__ import annotations

import logging

import ee

from .products import get_product

log = logging.getLogger(__name__)

# Thermal indices default to L8/L9: L7 SLC-off gaps and the 60/120 m TM/ETM+ thermal
# band stripe a few-scene median (see docs/REVIEW_AND_PLAN.md §1).
_THERMAL_INDICES = {"lst", "lst_smw", "lst_sharp"}
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
    if cfg.index in _THERMAL_INDICES:
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


def build(cfg, frame_geom, region_geom, ee_module=ee):
    sensor, index = get_product(cfg.sensor, cfg.index)
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
    if sensor.scene_cloud_property is not None:
        coll = coll.filter(
            ee_module.Filter.lte(sensor.scene_cloud_property, cfg.max_cloud_percent))
    coll = (
        coll
        .map(lambda img: add_region_cloud_fraction(
            img, region_geom, cfg.scale, sensor.cloud_band, ee_module))
        .filter(ee_module.Filter.lt("region_cloud_fraction", cfg.region_max_cloud_percent / 100.0))
        .map(lambda img: sensor.mask_clouds(img, ee_module))
        .map(lambda img: index.compute(sensor, img, ee_module))
    )
    return coll
