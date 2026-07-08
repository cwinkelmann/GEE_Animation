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


def build(cfg, geometry, ee_module=ee):
    coll = (
        ee_module.ImageCollection(S2_COLLECTION)
        .filterDate(cfg.start, cfg.end)
        .filterBounds(geometry)
        .filter(ee_module.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", cfg.max_cloud_percent))
        .map(lambda img: mask_s2_clouds(img, ee_module))
        .map(lambda img: add_ndvi(img, ee_module))
    )
    return coll
