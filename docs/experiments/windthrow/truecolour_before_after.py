"""Sentinel-2 true colour before and after the 2025 storm, windthrow-dense cells outlined.
    python docs/experiments/windthrow/truecolour_before_after.py <out png>
"""
import glob, io, os, sys, urllib.request, zipfile
import numpy as np, rasterio, geopandas as gpd
from rasterio.features import shapes
from shapely.geometry import shape
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1]; CACHE = "out/truecolour"; os.makedirs(CACHE, exist_ok=True)
FRAMES = {"R12": [13.2162, 52.5680, 13.2862, 52.6102], "R13": [13.1330, 52.5582, 13.2168, 52.6024]}
WINDOWS = {"before": ("2024-06-01", "2024-09-01"), "after": ("2025-07-15", "2025-10-01")}
FP = {"R12": "docs/aoi/r12/r12_footprint.geojson", "R13": "docs/aoi/r13/r13_footprint.geojson"}

def fetch(site, key):
    path = f"{CACHE}/{site}_{key}.tif"
    if os.path.exists(path): return path
    import ee
    from gee_animation import auth
    auth.init("hnee-331218"); ee.data.setDeadline(120_000)
    region = ee.Geometry.Rectangle(FRAMES[site]); s, e = WINDOWS[key]
    def mask(img):
        scl = img.select("SCL"); ok = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        return img.updateMask(ok)
    coll = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region).filterDate(s, e)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 60)).map(mask).select(["B4", "B3", "B2"]))
    img = coll.median().unmask(0).toFloat()
    url = img.getDownloadURL({"region": region, "crs": "EPSG:32633", "scale": 10, "format": "GEO_TIFF"})
    open(path, "wb").write(urllib.request.urlopen(url, timeout=300).read()); print("fetched", path, flush=True); return path

def stretch(a, lo=200, hi=2200):
    return np.clip((a - lo) / (hi - lo), 0, 1) ** 0.8

def wt_polys(site, tmpl_path):
    os.environ["WT_SITE"] = site; sys.modules.pop("windthrow_vs_delta", None)
    from windthrow_vs_delta import stem_density, WT_MIN_M
    with rasterio.open(tmpl_path) as t:
        dens, _ = stem_density(t)                # 10 m template: 20 m threshold scales to 5 m per 10 m cell
        wt = (dens >= WT_MIN_M / 4).astype("uint8")
        geoms = [shape(g) for g, v in shapes(wt, mask=wt.astype(bool), transform=t.transform)]
    return gpd.GeoSeries(geoms, crs=t.crs)

fig, axes = plt.subplots(2, 2, figsize=(15, 14), facecolor="white")
for i, site in enumerate(FRAMES):
    paths = {k: fetch(site, k) for k in WINDOWS}
    with rasterio.open(paths["before"]) as t:
        fp = gpd.read_file(FP[site]).to_crs(t.crs); bb = t.bounds; ext = (bb.left, bb.right, bb.bottom, bb.top)
    polys = wt_polys(site, paths["before"])
    for j, key in enumerate(WINDOWS):
        with rasterio.open(paths[key]) as ds: rgb = np.dstack([stretch(ds.read(b).astype("float32")) for b in (1, 2, 3)])
        ax = axes[i, j]; ax.imshow(rgb, extent=ext); fp.boundary.plot(ax=ax, color="#ffd700", lw=1.2)
        polys.boundary.plot(ax=ax, color="#ff2a2a", lw=0.7)
        ax.set_title(f"{site} · {key}: Sentinel-2 median {WINDOWS[key][0]} to {WINDOWS[key][1]}", loc="left", fontsize=10)
        ax.set_xticks([]); ax.set_yticks([])
axes[0, 0].text(0.01, 0.02, "yellow: drone footprint · red: windthrow-dense cells (≥ 20 m predicted stem per 20 m)", transform=axes[0, 0].transAxes,
                fontsize=9, color="white", bbox=dict(facecolor="black", alpha=0.5, pad=3))
fig.suptitle("True colour before and after the storm, 10 m", x=0.01, ha="left", fontsize=13)
fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(OUT, dpi=110); print("wrote", OUT)
