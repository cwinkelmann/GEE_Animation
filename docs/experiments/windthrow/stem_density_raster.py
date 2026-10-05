"""Rasterise the tegel-unet stem predictions into a density GeoTIFF for the render
overlay: metres of predicted fallen stem per 20 m cell, on the grid of a template
raster (any monthly GeoTIFF of the site's zoom frame), streets masked where a
street mask exists.

    WT_SITE=R12 python docs/experiments/windthrow/stem_density_raster.py <template tif> <out tif>
"""
import os, sys
import numpy as np, rasterio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from windthrow_vs_delta import stem_density

template, out = sys.argv[1], sys.argv[2]
with rasterio.open(template) as t:
    dens, street = stem_density(t)
    dens = np.where(street, 0.0, dens).astype("float32")
    profile = dict(driver="GTiff", height=t.height, width=t.width, count=1, dtype="float32",
                   crs=t.crs, transform=t.transform, nodata=-9999, compress="deflate")
with rasterio.open(out, "w", **profile) as d:
    d.write(dens, 1)
    d.update_tags(units="m of predicted fallen stem per cell", source="WINMOL tegel-unet 2026-08", site=os.environ.get("WT_SITE", "R12"))
cells = int((dens >= 20).sum())
print(f"wrote {out}: {dens.shape[1]}x{dens.shape[0]} cells, max {dens.max():.0f} m/cell, {cells} cells >= 20 m ({cells * 0.04:.0f} ha)")
