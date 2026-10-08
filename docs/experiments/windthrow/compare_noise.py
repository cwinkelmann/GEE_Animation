"""Per-month noise of the pooled vs per-frame delta fields (both clipped to the footprint)."""
import glob, re, sys, numpy as np, rasterio
from scipy.ndimage import median_filter
A, B = sys.argv[1], sys.argv[2]
def rd(f):
    with rasterio.open(f) as ds:
        a = ds.read(1).astype("float32"); a[a == ds.nodata] = np.nan; return a
def noise(x):
    m = np.isfinite(x); med = median_filter(np.where(m, x, 0.0), 3); return float(np.nanstd((x - med)[m]))
rows = []
for f in sorted(glob.glob(f"{A}/*.tif")):
    lab = re.search(r"(\d{4}-\d{2})\.tif$", f).group(1); g = f"{B}/{f.split('/')[-1]}"
    try: a, b = rd(f), rd(g)
    except Exception: continue
    rows.append((lab, np.nanstd(a), noise(a), np.nanstd(b), noise(b)))
r = np.array([[x[1], x[2], x[3], x[4]] for x in rows])
print(f"{len(rows)} months | pooled: sd {r[:,0].mean():.2f} K, hi-freq noise {r[:,1].mean():.2f} K | per-frame: sd {r[:,2].mean():.2f} K, hi-freq noise {r[:,3].mean():.2f} K")
print("noise ratio per-frame/pooled: median %.2f" % np.median(r[:,3] / r[:,1]))
for lab, *v in rows:
    if lab[5:7] in ("07", "08") or lab in ("2026-05",): print(f"  {lab}: pooled sd {v[0]:.2f} noise {v[1]:.2f} | per-frame sd {v[2]:.2f} noise {v[3]:.2f}")
