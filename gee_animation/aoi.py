"""Turn an AOI config (bbox or GeoJSON file) into an ee.Geometry."""
from __future__ import annotations

import json
from pathlib import Path

import ee


def _load_geojson_geometry(src) -> dict:
    obj = src if isinstance(src, dict) else json.loads(Path(src).read_text())
    t = obj.get("type")
    if t == "FeatureCollection":
        return obj["features"][0]["geometry"]
    if t == "Feature":
        return obj["geometry"]
    return obj  # already a bare geometry


def parse(aoi_cfg: dict, ee_module=ee):
    if aoi_cfg.get("geojson"):
        geom = _load_geojson_geometry(aoi_cfg["geojson"])
        return ee_module.Geometry(geom)
    if aoi_cfg.get("bbox"):
        return ee_module.Geometry.Rectangle(list(aoi_cfg["bbox"]))
    raise ValueError("aoi must define either 'bbox' or 'geojson'")
