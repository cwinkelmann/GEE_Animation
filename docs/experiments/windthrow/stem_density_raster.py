"""Rasterise the tegel-unet stem predictions into a density GeoTIFF for the render
overlay: metres of predicted fallen stem per 20 m cell, on the grid of a template
raster (any monthly GeoTIFF of the site's zoom frame), streets masked where a
street mask exists.

    WT_SITE=R12 python docs/experiments/windthrow/stem_density_raster.py <template tif> <out tif>
    WT_SITE=R12 python ... <template tif> <out tif> --kde 40 --res 5   # continuous density, m of stem per ha

--kde SIGMA_M: instead of the raw per-cell sum, a Gaussian kernel density of the stem
length (bandwidth sigma in metres) on a finer grid (--res metres, default 5), in
metres of predicted fallen stem per hectare — the surface for `overlay: {mode: fill}`.
"""
import os, sys
import numpy as np, rasterio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from windthrow_vs_delta import stem_density

import argparse
ap = argparse.ArgumentParser(); ap.add_argument("template"); ap.add_argument("out")
ap.add_argument("--kde", type=float, default=None, help="Gaussian bandwidth sigma in metres (continuous density)")
ap.add_argument("--res", type=float, default=5.0, help="grid resolution in metres for --kde")
a = ap.parse_args()
with rasterio.open(a.template) as t:
    if a.kde is None:
        dens, street = stem_density(t)
        dens = np.where(street, 0.0, dens).astype("float32")
        profile = dict(driver="GTiff", height=t.height, width=t.width, count=1, dtype="float32",
                       crs=t.crs, transform=t.transform, nodata=-9999, compress="deflate")
        tags = dict(units="m of predicted fallen stem per cell")
        msg = f"max {dens.max():.0f} m/cell, {int((dens >= 20).sum())} cells >= 20 m ({int((dens >= 20).sum()) * 0.04:.0f} ha)"
    else:
        # fine grid over the same extent, stem length summed per fine cell, then smoothed
        from rasterio.transform import from_origin
        from scipy.ndimage import gaussian_filter
        import geopandas as gpd
        from rasterio.features import rasterize
        from rasterio.enums import MergeAlg
        import windthrow_vs_delta as W
        b = t.bounds; k = int(round(t.res[0] / a.res)); h, w = t.height * k, t.width * k
        tr = from_origin(b.left, b.top, a.res, a.res)
        stems = gpd.read_file(W.STEMS, layer="stems").to_crs(t.crs)
        # length per fine cell: split each line into short segments and sum their lengths
        import shapely
        seg = stems.geometry.segmentize(a.res / 2)
        pieces = [(shapely.geometry.LineString(c[i:i + 2]), float(shapely.geometry.LineString(c[i:i + 2]).length))
                  for g in seg for c in ([list(g.coords)] if g.geom_type == "LineString" else [list(p.coords) for p in g.geoms])
                  for i in range(len(c) - 1)]
        length = rasterize(((p, v) for p, v in pieces), out_shape=(h, w), transform=tr, merge_alg=MergeAlg.add, dtype="float32", fill=0)
        if os.path.exists(W.STREETS):
            street = rasterize([(g, 1) for g in gpd.read_file(W.STREETS).to_crs(t.crs).geometry], out_shape=(h, w), transform=tr, dtype="uint8", fill=0).astype(bool)
            length[street] = 0
        dens = gaussian_filter(length, sigma=a.kde / a.res, mode="constant") / (a.res * a.res) * 1e4   # m per ha
        dens = dens.astype("float32")
        profile = dict(driver="GTiff", height=h, width=w, count=1, dtype="float32", crs=t.crs, transform=tr, nodata=-9999, compress="deflate")
        tags = dict(units="m of predicted fallen stem per ha, Gaussian KDE", bandwidth_m=str(a.kde))
        msg = f"KDE sigma {a.kde:.0f} m at {a.res:.0f} m: max {dens.max():.0f} m/ha, p99 {np.percentile(dens, 99):.0f}, area >= 500 m/ha {(dens >= 500).sum() * a.res**2 / 1e4:.0f} ha, >= 1500 {(dens >= 1500).sum() * a.res**2 / 1e4:.0f} ha"
with rasterio.open(a.out, "w", **profile) as d:
    d.write(dens, 1)
    d.update_tags(source="WINMOL tegel-unet 2026-08", site=os.environ.get("WT_SITE", "R12"), **tags)
print(f"wrote {a.out}: {dens.shape[1]}x{dens.shape[0]} cells, {msg}")
