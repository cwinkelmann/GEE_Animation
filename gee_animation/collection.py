"""Build a cloud-masked Sentinel-2 NDVI ImageCollection."""
from __future__ import annotations

import ee

S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
# SCL classes to drop: 3 shadow, 8/9/10 cloud (med/high/cirrus), 11 snow.
_SCL_MASK_CLASSES = [3, 8, 9, 10, 11]


def mask_s2_clouds(image, ee_module=ee):
    scl = image.select("SCL")
    mask = ee_module.Image.constant(1)
    for cls in _SCL_MASK_CLASSES:
        mask = mask.And(scl.neq(cls))
    return image.updateMask(mask)


def add_ndvi(image, ee_module=ee):
    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
    return image.addBands(ndvi)


def add_region_cloud_fraction(image, region, scale, ee_module=ee):
    scl = image.select("SCL")
    cloud = scl.remap(_SCL_MASK_CLASSES, [1] * len(_SCL_MASK_CLASSES), 0).rename("cloud")
    frac = cloud.reduceRegion(
        reducer=ee_module.Reducer.mean(),
        geometry=region,
        scale=scale,
        bestEffort=True,
        maxPixels=int(1e9),
    ).get("cloud")
    return image.set("region_cloud_fraction", frac)


def build(cfg, frame_geom, region_geom, ee_module=ee):
    coll = (
        ee_module.ImageCollection(S2_COLLECTION)
        .filterDate(cfg.start, cfg.end)
        .filterBounds(frame_geom)
        .filter(ee_module.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", cfg.max_cloud_percent))
        .map(lambda img: add_region_cloud_fraction(img, region_geom, cfg.scale, ee_module))
        .filter(ee_module.Filter.lt("region_cloud_fraction", cfg.region_max_cloud_percent / 100.0))
        .map(lambda img: mask_s2_clouds(img, ee_module))
        .map(lambda img: add_ndvi(img, ee_module))
    )
    return coll
