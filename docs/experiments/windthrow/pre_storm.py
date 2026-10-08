"""Was there a thermal signal at future windthrow sites BEFORE the storm?"""
import glob, re, sys, numpy as np, rasterio
from scipy import ndimage
from scipy.stats import spearmanr, mannwhitneyu
from sklearn.metrics import roc_auc_score
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from windthrow_vs_delta import stem_density
PAT = sys.argv[1]
def months(lo, hi, mm=(5, 6, 7, 8, 9)):
    return [f for f in sorted(glob.glob(PAT)) if (m := re.search(r"(\d{4}-\d{2})\.tif$", f)) and lo <= m.group(1) <= hi and int(m.group(1)[5:]) in mm]
def mean_of(files):
    st = []
    for f in files:
        with rasterio.open(f) as ds:
            d = ds.read(1).astype("float32"); d[d == ds.nodata] = np.nan; st.append(d)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore"); return np.nanmean(np.stack(st), axis=0), len(st)
# only REAL pre-storm observations (donor-filled winter months excluded by taking summers; the observed summer list from the run)
observed = {"2017-05","2017-06","2017-07","2018-02","2018-03","2018-04","2018-05","2018-07","2018-08","2018-09","2018-10","2018-11","2019-02","2019-04","2019-05","2019-06","2019-07","2019-10","2020-01","2020-03","2020-04","2020-05","2020-09","2021-02","2021-03","2022-03","2022-05","2022-06","2022-08","2022-09","2022-10","2022-11","2023-05","2023-06","2023-08","2023-09","2023-11","2024-03","2024-05","2024-06","2024-07","2024-08","2024-09","2025-02","2025-03","2025-04","2025-05"}
pre_files = [f for f in months("2017-01", "2025-05") if re.search(r"(\d{4}-\d{2})\.tif$", f).group(1) in observed]
pre, n_pre = mean_of(pre_files)
post, n_post = mean_of(months("2025-07", "2026-08", (4,5,6,7,8,9)))
with rasterio.open(pre_files[0]) as t:
    dens, street = stem_density(t)
inside = np.isfinite(pre) & np.isfinite(post)
edge_dist = ndimage.distance_transform_edt(inside) * 20.0          # metres to the footprint edge
core = inside & ~ndimage.binary_dilation(street, iterations=2) & (edge_dist > 40)
wt = core & (dens >= 20); ctl = core & (dens == 0)
print(f"pre-storm summers: {n_pre} observed months (2017-2025-05); post: {n_post} months")
print(f"pre-storm Δ at future windthrow cells {pre[wt].mean():+.2f} K (n={wt.sum()}) vs stem-free {pre[ctl].mean():+.2f} K (n={ctl.sum()}) -> diff {pre[wt].mean()-pre[ctl].mean():+.2f} K, "
      f"Mann-Whitney p = {mannwhitneyu(pre[wt], pre[ctl]).pvalue:.3g}")
print(f"post-storm, same cells: {post[wt].mean():+.2f} vs {post[ctl].mean():+.2f} K -> diff {post[wt].mean()-post[ctl].mean():+.2f} K")
y = (dens[core] >= 20).astype(int)
for name, x in (("pre-storm Δ", pre[core]), ("post-storm Δ", post[core]), ("post − pre warming", (post - pre)[core]), ("distance to edge (inverse)", -edge_dist[core])):
    print(f"  AUC for 'is a windthrow-dense cell' from {name:28s}: {roc_auc_score(y, x):.3f}")
print(f"  Spearman(pre-storm Δ, stem density) = {spearmanr(pre[core], dens[core]).statistic:+.3f}")
# bins of pre-storm warmth -> windthrow rate, and edge control
q = np.quantile(pre[core], [0.2, 0.4, 0.6, 0.8])
b = np.digitize(pre[core], q)
print("  windthrow-dense rate by pre-storm warmth quintile (cold -> warm):", [f"{(y[b==i].mean()*100):.1f}%" for i in range(5)])
for lo, hi in ((40, 100), (100, 200), (200, 400), (400, 9999)):
    m = core & (edge_dist > lo) & (edge_dist <= hi)
    print(f"  edge distance {lo:>3}-{hi:<4} m: windthrow-dense rate {(dens[m] >= 20).mean()*100:4.1f}% | pre-storm Δ {pre[m].mean():+.2f} K | n={m.sum()}")
