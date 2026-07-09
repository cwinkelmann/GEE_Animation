"""Turn an AOI config (bbox, or GeoJSON as an inline dict or file path) into an ee.Geometry."""
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


def _read_shapefile_geometry(path: str) -> dict:
    """Read a shapefile, reproject to EPSG:4326, and return a GeoJSON geometry dict.

    Multiple features are dissolved into a single (Multi)Polygon. Requires the
    optional `geopandas` dependency (``pip install "gee_animation[shapefile]"``).
    """
    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "reading shapefiles requires geopandas; install with "
            '`pip install "gee_animation[shapefile]"`'
        ) from exc
    gdf = gpd.read_file(path)
    if gdf.crs is not None:
        gdf = gdf.to_crs("EPSG:4326")
    geom = gdf.geometry.union_all() if len(gdf) > 1 else gdf.geometry.iloc[0]
    return geom.__geo_interface__


def parse(aoi_cfg: dict, ee_module=ee):
    if aoi_cfg.get("shapefile"):
        return ee_module.Geometry(_read_shapefile_geometry(aoi_cfg["shapefile"]))
    if aoi_cfg.get("geojson"):
        geom = _load_geojson_geometry(aoi_cfg["geojson"])
        return ee_module.Geometry(geom)
    if aoi_cfg.get("bbox"):
        return ee_module.Geometry.Rectangle(list(aoi_cfg["bbox"]))
    raise ValueError("aoi must define 'bbox', 'geojson', or 'shapefile'")
