"""Does PRE-storm Sentinel-1 backscatter predict where the storm hit? Per cell:
early level (2017-18), pre-storm level (2022-24), per-cell VH trend 2018-2024, and
the post-storm change; AUC of each for 'windthrow-dense cell' (>= 20 m stem)."""
import glob, os, re, sys, warnings
import numpy as np
sys.path.insert(0, "scripts"); sys.path.insert(0, "docs/experiments/windthrow")
from sar_windthrow_spike import SITE, CFG, OUT, FILL
from scipy import ndimage
from sklearn.metrics import roc_auc_score
import rasterio, geopandas as gpd
from rasterio.features import geometry_mask
from windthrow_vs_delta import stem_density, WT_MIN_M
files = sorted(glob.glob(f"{OUT}/*.tif")); labs = [re.search(r"(\d{4}-\d{2})\.tif$", f).group(1) for f in files]
with rasterio.open(files[0]) as t:
    dens, street = stem_density(t)
    fp = gpd.read_file(CFG["footprint"]).to_crs(t.crs)
    inside = geometry_mask(list(fp.geometry), out_shape=(t.height, t.width), transform=t.transform, invert=True)
edge = ndimage.distance_transform_edt(inside) * 20.0
core = inside & ~street & (edge > 40)
vh = []
for f in files:
    with rasterio.open(f) as ds:
        a = ds.read(2).astype("float32"); a[a == FILL] = np.nan; vh.append(a)
vh = np.stack(vh); x = np.array([int(l[:4]) + (int(l[5:7]) - 0.5) / 12 for l in labs])
summer = np.array([int(l[5:7]) in (5, 6, 7, 8, 9) for l in labs])
def mean_over(sel):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore"); return np.nanmean(vh[sel], axis=0)
early = mean_over((x >= 2017) & (x < 2019)); pre = mean_over((x >= 2022) & (x < 2025) & summer); post = mean_over((x >= 2025.5) & summer)
# per-cell linear trend 2018-2024 (all months; seasonal cycle averages out over 7 full years)
sel = (x >= 2018) & (x < 2025); xs = x[sel] - x[sel].mean(); Y = vh[sel]
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    Ym = Y - np.nanmean(Y, axis=0); slope = np.nansum(xs[:, None, None] * Ym, axis=0) / np.sum(xs ** 2)
y = (dens[core] >= WT_MIN_M).astype(int)
def auc(v):
    ok = np.isfinite(v[core]); a = roc_auc_score(y[ok], v[core][ok]); return a, max(a, 1 - a)
print(f"{SITE}: {core.sum()} cells, windthrow-dense {y.mean()*100:.1f}%")
for name, v in (("early level 2017-18 (VH dB)", early), ("pre-storm level 2022-24 summers", pre), ("per-cell VH trend 2018-2024 (dB/yr)", slope), ("early minus pre-storm (loss over the years)", early - pre), ("post-storm change (after minus 2022-24) — detection, not prediction", post - pre)):
    a, best = auc(v); print(f"  {name:64s} AUC {a:.3f}  (either direction {best:.3f})")
# windthrow rate by quintile of the pre-storm trend (most negative -> most positive)
ok = core & np.isfinite(slope); q = np.quantile(slope[ok], [0.2, 0.4, 0.6, 0.8]); b = np.digitize(slope[ok], q); yy = (dens[ok] >= WT_MIN_M)
print("  windthrow-dense rate by quintile of the 2018-24 VH trend (falling -> rising):", [f"{yy[b == i].mean()*100:.1f}%" for i in range(5)])
print(f"  mean trend: windthrow-dense cells {slope[core & (dens >= WT_MIN_M)].mean():+.3f} dB/yr vs stem-free {slope[core & (dens == 0)].mean():+.3f} dB/yr")
