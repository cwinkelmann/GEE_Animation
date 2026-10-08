"""Where did most trees fall, and does the thermal profile show it?

Maps and statistics comparing predicted stem density with the mean summer
departure from the footprint mean, at 20 m (the sharpened field) and at 100 m
(where the field equals the observed Landsat temperature).

    python windthrow_maps.py "<glob of monthly delta tifs>" <first_year> <out png>
"""
import glob, re, sys
import numpy as np, rasterio, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import spearmanr, pearsonr
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from windthrow_vs_delta import stem_density

def block(a, k, fn=np.nanmean):
    h, w = a.shape[0] // k * k, a.shape[1] // k * k
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn(a[:h, :w].reshape(h // k, k, w // k, k), axis=(1, 3))

pattern, start, out = sys.argv[1], sys.argv[2], sys.argv[3]   # start = "YYYY-MM"
y0 = start
files = [f for f in sorted(glob.glob(pattern))
         if (m := re.search(r"(\d{4})-(\d{2})\.tif$", f)) and f"{m.group(1)}-{m.group(2)}" >= start and int(m.group(2)) in (4, 5, 6, 7, 8, 9)]
print(f"{len(files)} summer months from {y0}:", [re.search(r"(\d{4}-\d{2})\.tif$", f).group(1) for f in files])
with rasterio.open(files[0]) as t:
    dens, street = stem_density(t); nod = t.nodata
stack = []
for f in files:
    with rasterio.open(f) as ds:
        d = ds.read(1).astype("float32"); d[d == nod] = np.nan; stack.append(d)
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore"); mean20 = np.nanmean(np.stack(stack), axis=0)
mean20[street] = np.nan
inside = np.isfinite(mean20)
# 100 m view: 5x5 blocks — the field here is the observed Landsat temperature
mean100 = block(mean20, 5); dens100 = block(dens, 5, np.nansum); frac100 = block(inside.astype(float), 5)
ok100 = np.isfinite(mean100) & (frac100 >= 0.6)
def stats(d, v, ok, name):
    rho = spearmanr(d[ok], v[ok]).statistic; r = pearsonr(d[ok], v[ok]).statistic
    q = np.quantile(d[ok], [0.5, 0.9]); top = ok & (d >= q[1]); none = ok & (d == 0) if (d == 0).any() else ok & (d <= q[0])
    print(f"{name}: n={ok.sum()} cells | Spearman rho {rho:+.3f} | Pearson r {r:+.3f} | top-10% stem density cells mean {np.nanmean(v[top]):+.2f} K "
          f"vs {'stem-free' if (d == 0).any() else 'below-median'} cells {np.nanmean(v[none]):+.2f} K -> diff {np.nanmean(v[top]) - np.nanmean(v[none]):+.2f} K")
    return rho, r
rho20, _ = stats(dens, mean20, inside, "20 m (sharpened)")
rho100, _ = stats(dens100, mean100, ok100, "100 m (observed Landsat)")
fig, axes = plt.subplots(2, 2, figsize=(13, 12.5), facecolor="white")
sc = 3.0
panels = [(dens, "Predicted stem length per 20 m cell (tegel-unet, 2025 flight)", "Greys", 0, 60, "m / cell"),
          (mean20, f"Mean summer departure from footprint mean, 20 m sharpened, {y0}–", "RdBu_r", -sc, sc, "K"),
          (dens100, "Predicted stem length per 100 m cell", "Greys", 0, 60 * 25 / 4, "m / cell"),
          (mean100, f"Mean summer departure, 100 m = observed Landsat, {y0}–", "RdBu_r", -sc, sc, "K")]
for ax, (img, title, cmap, lo, hi, unit) in zip(axes.flat, panels):
    im = ax.imshow(img, cmap=cmap, vmin=lo, vmax=hi, interpolation="nearest")
    ax.set_title(title, fontsize=11, loc="left"); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02); cb.set_label(unit); cb.outline.set_visible(False)
fig.suptitle(f"R12 windthrow vs thermal profile · rho = {rho20:+.2f} at 20 m, {rho100:+.2f} at 100 m (streets excluded)", fontsize=12, x=0.01, ha="left")
fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out, dpi=110); print("wrote", out)
