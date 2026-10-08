"""Do the vegetation indices show the windthrow the temperature showed?

Per observed month: mean of each index in windthrow cells (>= 20 m predicted
stem per 20 m cell) minus stem-free canopy, inside the footprint. Inputs are the
data-only runs (config/r12_data_<index>.yaml, no gap-filling, GeoTIFF per month)
plus the per-frame LST delta run for the temperature row.

    WT_SITE=R12 python index_drops.py <geotiff root> <lst delta glob> <out png>
"""
import glob, os, re, sys, warnings
import numpy as np, rasterio, geopandas as gpd
from rasterio.features import geometry_mask
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from windthrow_vs_delta import stem_density, WT_MIN_M
ROOT, LST_GLOB, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
SITE = os.environ.get("WT_SITE", "R12")
FOOTPRINT = os.environ.get("WT_FOOTPRINT", {"R12": "docs/aoi/r12/r12_footprint.geojson", "R13": "docs/aoi/r13/r13_footprint.geojson"}.get(SITE, "docs/aoi/wne/wne.shp"))
PREFIX = os.environ.get("WT_PREFIX", f"{SITE.lower()}_data_")
INDICES = [("lst_rf Δ", LST_GLOB, "K", "#c44"), ("ndvi", f"{ROOT}/{PREFIX}ndvi/*.tif", "", "#2a8"),
           ("ndmi", f"{ROOT}/{PREFIX}ndmi/*.tif", "", "#27a"), ("ndre", f"{ROOT}/{PREFIX}ndre/*.tif", "", "#a5a"),
           ("evi", f"{ROOT}/{PREFIX}evi/*.tif", "", "#c93")]
def series(pattern):
    files = sorted(glob.glob(pattern))
    if not files: return [], [], []
    with rasterio.open(files[0]) as t:
        dens, street = stem_density(t)
        fp = gpd.read_file(FOOTPRINT).to_crs(t.crs)
        inside = geometry_mask([g for g in fp.geometry], out_shape=(t.height, t.width), transform=t.transform, invert=True)
    core = inside & ~street
    wt, ctl = core & (dens >= WT_MIN_M), core & (dens == 0)
    labs, diff, n = [], [], []
    for f in files:
        lab = re.search(r"(\d{4}-\d{2})\.tif$", f).group(1)
        with rasterio.open(f) as ds:
            d = ds.read(1).astype("float32"); d[d == ds.nodata] = np.nan; d[~np.isfinite(d)] = np.nan
        ok = np.isfinite(d)
        if (ok & wt).sum() < 0.5 * wt.sum(): continue          # mostly masked month
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            diff.append(float(np.nanmean(d[wt]) - np.nanmean(d[ctl])))
        labs.append(lab); n.append(int((ok & wt).sum()))
    return labs, diff, n
fig, axes = plt.subplots(len(INDICES), 1, figsize=(13, 2.3 * len(INDICES)), sharex=True, facecolor="white")
for ax, (name, pat, unit, col) in zip(axes, INDICES):
    labs, diff, n = series(pat)
    if not labs:
        ax.text(0.01, 0.5, f"{name}: no data", transform=ax.transAxes); continue
    x = np.array([int(l[:4]) + (int(l[5:7]) - 0.5) / 12 for l in labs]); y = np.array(diff)
    summer = np.array([int(l[5:7]) in (5, 6, 7, 8, 9) for l in labs])
    pre = y[summer & (x < 2025.5)]; post = y[summer & (x >= 2025.5)]
    ax.axhline(0, color="#888", lw=1); ax.axvline(2025.5, color="#888", lw=1, ls="--")
    ax.plot(x, y, color=col, lw=1.2); ax.scatter(x[summer], y[summer], s=22, color=col, zorder=3)
    ax.scatter(x[~summer], y[~summer], s=22, facecolor="white", edgecolor=col, zorder=3)
    ax.set_ylabel(f"{name}\n{unit}".strip()); ax.grid(alpha=0.25)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    ax.text(0.995, 0.92, f"summer mean before {pre.mean():+.3f} → after {post.mean():+.3f}   (n months {len(pre)} / {len(post)})",
            transform=ax.transAxes, ha="right", va="top", fontsize=9, color="#333")
    print(f"{name:8s} months {len(labs):3d} | summer windthrow−canopy before {pre.mean():+.3f} after {post.mean():+.3f} | change {post.mean()-pre.mean():+.3f} | spread of pre-storm summers sd {pre.std():.3f}")
axes[0].set_title(f"{SITE}: windthrow cells (≥ 20 m predicted stem) minus stem-free canopy, per observed month · filled = May–Sep", loc="left", fontsize=11)
axes[-1].set_xlabel("year")
fig.tight_layout(); fig.savefig(OUT, dpi=110); print("wrote", OUT)
