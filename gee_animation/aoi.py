"""Turn an AOI config (bbox, or GeoJSON as an inline dict or file path) into an ee.Geometry."""
from __future__ import annotations

import json
import math
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


def frame_bbox_from_region(region_geom, buffer_m, ee_module=ee):
    """[minLon, minLat, maxLon, maxLat] of `region_geom` buffered by `buffer_m` metres."""
    ring = region_geom.buffer(buffer_m).bounds().coordinates().getInfo()[0]
    xs = [pt[0] for pt in ring]
    ys = [pt[1] for pt in ring]
    return [min(xs), min(ys), max(xs), max(ys)]


def fit_bbox_to_aspect(bbox, aspect: float):
    """Grow `bbox` to a target width/height ratio measured in METRES.

    A degree of longitude is cos(latitude) shorter than a degree of latitude, so a
    bbox that looks wide in degrees can be square on the ground — at 53 deg N the
    factor is 0.6. Reshaping in degrees would therefore aim at the wrong shape.

    Only the short side grows; the subject is never cropped out of frame. Returns
    a new ``[minLon, minLat, maxLon, maxLat]``.
    """
    minlon, minlat, maxlon, maxlat = (float(v) for v in bbox)
    clat, clon = (minlat + maxlat) / 2.0, (minlon + maxlon) / 2.0
    m_per_lon = 111320.0 * math.cos(math.radians(clat))
    if m_per_lon <= 0:                       # a pole-adjacent AOI: nothing sane to do
        return [minlon, minlat, maxlon, maxlat]

    w_m = (maxlon - minlon) * m_per_lon
    h_m = (maxlat - minlat) * 110540.0
    if w_m <= 0 or h_m <= 0 or aspect <= 0:
        return [minlon, minlat, maxlon, maxlat]

    if w_m / h_m < aspect:
        w_m = h_m * aspect
    else:
        h_m = w_m / aspect
    dlon = (w_m / 2.0) / m_per_lon
    dlat = (h_m / 2.0) / 110540.0
    return [clon - dlon, clat - dlat, clon + dlon, clat + dlat]
