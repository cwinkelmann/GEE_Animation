"""Build a cloud-masked (sensor, index) ImageCollection with an INDEX band."""
from __future__ import annotations

import ee

from .products import get_product


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
    coll = (
        sensor.collection(ee_module)
        .filterDate(cfg.start, cfg.end)
        .filterBounds(frame_geom)
        .filter(ee_module.Filter.lte(sensor.scene_cloud_property, cfg.max_cloud_percent))
        .map(lambda img: add_region_cloud_fraction(
            img, region_geom, cfg.scale, sensor.cloud_band, ee_module))
        .filter(ee_module.Filter.lt("region_cloud_fraction", cfg.region_max_cloud_percent / 100.0))
        .map(lambda img: sensor.mask_clouds(img, ee_module))
        .map(lambda img: index.compute(sensor, img, ee_module))
    )
    return coll
