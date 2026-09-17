"""A frame whose pixels live on this machine, not in Earth Engine.

`LocalImage` stands in for an `ee.Image` in `compositing.Frame.image` once a
pipeline step has produced the field locally (sharpen_local). It carries the
values (float32, NaN = no data), the bounds in a projected CRS and the cell
size, and offers exactly the operations the rest of the pipeline needs:
a thumbnail for the renderer, a crop, a polygon mask, a region mean.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from .aoi import _load_geojson_geometry, _read_shapefile_geometry


@dataclass(frozen=True)
class LocalImage:
    values: np.ndarray            # (H, W) float32, NaN where no data
    bounds: tuple                 # (minx, miny, maxx, maxy) in `crs` units (metres)
    crs: str                      # e.g. "EPSG:32633"
    scale_m: float                # cell size

    @property
    def transform(self):
        from rasterio.transform import from_origin
        return from_origin(self.bounds[0], self.bounds[3], self.scale_m, self.scale_m)

    def thumbnail(self, dimensions: int):
        """(values, valid) resampled so the largest side is `dimensions` px — the
        contract of an EE thumbnail, minus the 8-bit quantisation."""
        h, w = self.values.shape
        if w >= h:
            tw, th = int(dimensions), max(1, int(round(h * dimensions / w)))
        else:
            th, tw = int(dimensions), max(1, int(round(w * dimensions / h)))
        valid = np.isfinite(self.values)
        filled = np.where(valid, self.values, 0.0).astype("float32")
        arr = np.asarray(Image.fromarray(filled, "F").resize((tw, th), Image.BILINEAR), dtype=float)
        mask = np.asarray(Image.fromarray(valid.astype("uint8") * 255, "L")
                          .resize((tw, th), Image.NEAREST)) > 127
        return arr, mask

    def crop(self, bounds: tuple) -> "LocalImage":
        minx, miny, maxx, maxy = bounds
        res = self.scale_m
        c0 = int(round((minx - self.bounds[0]) / res)); c1 = int(round((maxx - self.bounds[0]) / res))
        r0 = int(round((self.bounds[3] - maxy) / res)); r1 = int(round((self.bounds[3] - miny) / res))
        h, w = self.values.shape
        c0, c1, r0, r1 = max(0, c0), min(w, c1), max(0, r0), min(h, r1)
        vals = self.values[r0:r1, c0:c1]
        new_bounds = (self.bounds[0] + c0 * res, self.bounds[3] - r1 * res,
                      self.bounds[0] + c1 * res, self.bounds[3] - r0 * res)
        return LocalImage(vals, new_bounds, self.crs, self.scale_m)

    def _mask(self, rings) -> np.ndarray:
        from rasterio.features import geometry_mask
        polys = [{"type": "Polygon", "coordinates": [[list(p) for p in ring]]} for ring in rings]
        return geometry_mask(polys, out_shape=self.values.shape, transform=self.transform,
                             invert=True)

    def region_mean(self, rings) -> float:
        return float(np.nanmean(self.values[self._mask(rings)]))

    def clip(self, rings) -> "LocalImage":
        vals = np.where(self._mask(rings), self.values, np.nan).astype("float32")
        return LocalImage(vals, self.bounds, self.crs, self.scale_m)

    def subtract(self, constant: float) -> "LocalImage":
        return LocalImage((self.values - float(constant)).astype("float32"),
                          self.bounds, self.crs, self.scale_m)


def _geom_rings(geom: dict) -> list:
    t = geom["type"]
    if t == "Polygon":
        return [[(x, y) for x, y in ring] for ring in geom["coordinates"]]
    if t == "MultiPolygon":
        return [[(x, y) for x, y in ring] for poly in geom["coordinates"] for ring in poly]
    raise ValueError(f"unsupported region geometry type for a local mask: {t}")


def projected_region_rings(aoi_cfg: dict, crs: str) -> list:
    """The region AOI's rings in `crs` (metres). A `rings_projected` entry is used
    as-is (tests, or a caller that already projected)."""
    if aoi_cfg.get("rings_projected"):
        return aoi_cfg["rings_projected"]
    if aoi_cfg.get("bbox"):
        mnx, mny, mxx, mxy = aoi_cfg["bbox"]
        rings = [[(mnx, mny), (mxx, mny), (mxx, mxy), (mnx, mxy), (mnx, mny)]]
    elif aoi_cfg.get("shapefile"):
        rings = _geom_rings(_read_shapefile_geometry(aoi_cfg["shapefile"]))
    else:
        rings = _geom_rings(_load_geojson_geometry(aoi_cfg["geojson"]))
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    return [[t.transform(lon, lat) for lon, lat in ring] for ring in rings]
