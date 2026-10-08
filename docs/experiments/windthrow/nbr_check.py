"""Does NBR (the standard disturbance index) see the 2025 windthrow as well as
NDVI / NDMI do? Same cells, windows and statistics as sensor_figures.py.

    python docs/experiments/windthrow/nbr_check.py <sar worktree> <out png>
"""
import glob, os, sys, warnings
import numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from sklearn.metrics import roc_auc_score, roc_curve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SAR_WT, OUT = sys.argv[1], sys.argv[2]
sys.argv = [sys.argv[0], SAR_WT, os.path.dirname(OUT) or "."]
import sensor_figures as sf
from sensor_figures import SITES, masks, change_map, on_grid, ym_of, xval, SUMMER

summer_pre = lambda ym: "2022-01" <= ym <= "2024-12" and int(ym[5:]) in SUMMER
summer_post = lambda ym: ym >= "2025-07" and int(ym[5:]) in SUMMER
INDEX_PATTERNS = {"R12": {"ndvi": "out/geotiffs/r12_data_ndvi/*.tif", "ndmi": "out/geotiffs/r12_data_ndmi/*.tif", "nbr": "out/geotiffs/r12_data_nbr/*.tif"},
                  "R13": {"ndvi": "out/geotiffs/r13_data_ndvi/*.tif", "ndmi": "out/geotiffs/r13_data_ndmi/*.tif", "nbr": "out/geotiffs/r13_data_nbr/*.tif"}}
COL = {"ndvi": "#2a7f4f", "ndmi": "#27a", "nbr": "#c8502a"}

def series(pattern, m):
    labs, d = [], []
    for f in sorted(glob.glob(pattern)):
        a = on_grid(f, 1, m["grid"]); ok = np.isfinite(a)
        if (ok & m["wt"]).sum() < 0.5 * m["wt"].sum(): continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore"); d.append(float(np.nanmean(a[m["wt"]]) - np.nanmean(a[m["ctl"]])))
        labs.append(ym_of(f))
    return np.array([xval(l) for l in labs]), np.array(d), np.array([int(l[5:7]) in SUMMER for l in labs])

fig, axes = plt.subplots(2, 2, figsize=(16, 9), facecolor="white")
print(f"{'site':5s} {'index':5s} {'summer before':>14s} {'after':>8s} {'drop':>7s} {'sigma':>6s} {'AUC':>6s}")
for i, site in enumerate(SITES):
    m = masks(site)
    for k, pat in INDEX_PATTERNS[site].items():
        if not glob.glob(pat): print(site, k, "no data"); continue
        x, y, summer = series(pat, m); ax = axes[i, 0]
        pre, post = y[summer & (x < 2025.5)], y[summer & (x > 2025.5)]
        ax.plot(x, y, color=COL[k], lw=1.1, label=f"{k.upper()}: {pre.mean():+.3f} → {post.mean():+.3f}")
        ax.scatter(x[summer], y[summer], s=12, color=COL[k]); ax.scatter(x[~summer], y[~summer], s=12, facecolor="white", edgecolor=COL[k])
        ch, _, _ = change_map(pat, 1, m["grid"], summer_pre, summer_post); ok = np.isfinite(ch)
        lab = np.r_[np.ones((ok & m["wt"]).sum()), np.zeros((ok & m["ctl"]).sum())]; sc = -np.r_[ch[ok & m["wt"]], ch[ok & m["ctl"]]]
        fpr, tpr, _ = roc_curve(lab, sc); auc = roc_auc_score(lab, sc)
        axes[i, 1].plot(fpr, tpr, color=COL[k], lw=2, label=f"{k.upper()} change · AUC {auc:.2f}")
        print(f"{site:5s} {k:5s} {pre.mean():+14.3f} {post.mean():+8.3f} {post.mean()-pre.mean():+7.3f} {pre.std():6.3f} {auc:6.2f}")
    ax = axes[i, 0]; ax.axhline(0, color="#888", lw=1); ax.axvline(2025.5, color="#c44", lw=1.2, ls="--"); ax.grid(alpha=.25)
    ax.set_title(f"{site}: windthrow cells minus canopy, monthly (filled = May–Sep); legend = summer mean before → after", loc="left", fontsize=10); ax.legend(frameon=False, fontsize=9)
    ax = axes[i, 1]; ax.plot([0, 1], [0, 1], color="#888", lw=1, ls="--"); ax.set_xlabel("false positive rate"); ax.set_ylabel("true positive rate"); ax.grid(alpha=.25)
    ax.set_title(f"{site}: per-cell summer change (post-storm minus 2022–24) as a windthrow detector", loc="left", fontsize=10); ax.legend(frameon=False, loc="lower right")
    for a in axes[i]: [a.spines[s].set_visible(False) for s in ("top", "right")]
fig.suptitle("NBR against NDVI and NDMI: the same windthrow test on the same 20 m cells", x=0.01, ha="left", fontsize=12)
fig.tight_layout(rect=(0, 0, 1, 0.96)); fig.savefig(OUT, dpi=110); print("wrote", OUT)
