"""Footprint-wide monthly means of temperature departure spread and the indices —
the control view for a site WITHOUT a stem map (Grumsin), and context for the others.

    WT_SITE=WNE python index_means.py <geotiff root> <lst delta glob> <out png>
"""
import glob, os, re, sys, warnings
import numpy as np, rasterio, geopandas as gpd
from rasterio.features import geometry_mask
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
ROOT, LST_GLOB, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
SITE = os.environ.get("WT_SITE", "WNE")
FOOTPRINT = os.environ.get("WT_FOOTPRINT", {"R12": "docs/aoi/r12/r12_footprint.geojson", "R13": "docs/aoi/r13/r13_footprint.geojson"}.get(SITE, "docs/aoi/wne/wne.shp"))
PREFIX = os.environ.get("WT_PREFIX", f"{SITE.lower()}_data_")
ROWS = [("lst_rf Δ: spatial sd inside footprint", LST_GLOB, "K", "#c44", "sd"),
        ("ndvi, footprint mean", f"{ROOT}/{PREFIX}ndvi/*.tif", "", "#2a8", "mean"),
        ("ndmi, footprint mean", f"{ROOT}/{PREFIX}ndmi/*.tif", "", "#27a", "mean"),
        ("ndre, footprint mean", f"{ROOT}/{PREFIX}ndre/*.tif", "", "#a5a", "mean"),
        ("evi, footprint mean", f"{ROOT}/{PREFIX}evi/*.tif", "", "#c93", "mean")]
def series(pattern, stat):
    files = sorted(glob.glob(pattern))
    if not files: return [], []
    with rasterio.open(files[0]) as t:
        fp = gpd.read_file(FOOTPRINT).to_crs(t.crs)
        inside = geometry_mask(list(fp.geometry), out_shape=(t.height, t.width), transform=t.transform, invert=True)
    labs, vals = [], []
    for f in files:
        lab = re.search(r"(\d{4}-\d{2})\.tif$", f).group(1)
        with rasterio.open(f) as ds:
            d = ds.read(1).astype("float32"); d[d == ds.nodata] = np.nan; d[~np.isfinite(d)] = np.nan
        ok = np.isfinite(d) & inside
        if ok.sum() < 0.5 * inside.sum(): continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            vals.append(float(np.nanstd(d[inside]) if stat == "sd" else np.nanmean(d[inside])))
        labs.append(lab)
    return labs, vals
fig, axes = plt.subplots(len(ROWS), 1, figsize=(13, 2.3 * len(ROWS)), sharex=True, facecolor="white")
for ax, (name, pat, unit, col, stat) in zip(axes, ROWS):
    labs, vals = series(pat, stat)
    if not labs:
        ax.text(0.01, 0.5, f"{name}: no data", transform=ax.transAxes); continue
    x = np.array([int(l[:4]) + (int(l[5:7]) - 0.5) / 12 for l in labs]); y = np.array(vals)
    summer = np.array([int(l[5:7]) in (5, 6, 7, 8, 9) for l in labs])
    pre = y[summer & (x < 2025.5)]; post = y[summer & (x >= 2025.5)]
    ax.axvline(2025.5, color="#888", lw=1, ls="--"); ax.plot(x, y, color=col, lw=1.2)
    ax.scatter(x[summer], y[summer], s=22, color=col, zorder=3); ax.scatter(x[~summer], y[~summer], s=22, facecolor="white", edgecolor=col, zorder=3)
    ax.set_ylabel(f"{name.split(',')[0].split(':')[0]}\n{unit}".strip()); ax.grid(alpha=0.25)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    ax.text(0.995, 0.92, f"{name} · summer mean before {pre.mean():+.3f} → after {post.mean():+.3f} (n {len(pre)} / {len(post)})",
            transform=ax.transAxes, ha="right", va="top", fontsize=9, color="#333")
    print(f"{name:42s} months {len(labs):3d} | summer before {pre.mean():+.3f} after {post.mean():+.3f} | change {post.mean()-pre.mean():+.3f} | pre-storm summer sd {pre.std():.3f}")
axes[0].set_title(f"{SITE}: footprint-wide monthly values (no stem map → no windthrow split) · filled = May–Sep", loc="left", fontsize=11)
axes[-1].set_xlabel("year"); fig.tight_layout(); fig.savefig(OUT, dpi=110); print("wrote", OUT)
