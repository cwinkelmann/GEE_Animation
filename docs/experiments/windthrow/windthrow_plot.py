"""Plot the windthrow-vs-canopy temperature difference per month (from windthrow_vs_delta.py's CSV)."""
import sys, csv
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
rows = list(csv.DictReader(open(sys.argv[1])))
months = [r["month"] for r in rows]
x = np.array([int(m[:4]) + (int(m[5:7]) - 0.5) / 12 for m in months])
diff = np.array([float(r["diff_K"]) for r in rows]); n_wt = np.array([int(r["n_wt"]) for r in rows])
bins = ["bin_none", "bin_0-10 m", "bin_10-30 m", "bin_30-60 m", "bin_>60 m"]
fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 8), facecolor="white", height_ratios=[3, 2])
summer = np.array([int(m[5:7]) in (5, 6, 7, 8, 9) for m in months])
a1.axhline(0, color="#888", lw=1)
a1.plot(x, diff, color="#c44", lw=1.5, marker="o", ms=4, label="windthrow cells (≥20 m stem) minus stem-free canopy")
a1.scatter(x[summer], diff[summer], s=40, color="#c44", zorder=3)
a1.scatter(x[~summer], diff[~summer], s=40, facecolor="white", edgecolor="#c44", zorder=3, label="open marker = Oct–Apr")
a1.set_ylabel("Δ temperature, K"); import os; a1.set_title(f"Predicted windthrow cells vs stem-free canopy, {os.environ.get('WT_SITE', 'R12')}, RF-sharpened LST departure from footprint mean", loc="left", fontsize=11)
a1.legend(loc="upper left", frameon=False); a1.grid(alpha=0.25)
for k, y in enumerate(range(x.min().astype(int), int(x.max()) + 1)):
    a1.axvline(y, color="#ddd", lw=0.8, zorder=0)
vals = np.array([[float(r[b]) if r[b] not in ("", "nan") else np.nan for b in bins] for r in rows])
sm = np.nanmean(vals[summer], axis=0); wi = np.nanmean(vals[~summer], axis=0)
w = 0.38; xi = np.arange(len(bins))
a2.bar(xi - w/2, sm, w, color="#c44", label="May–Sep mean"); a2.bar(xi + w/2, wi, w, color="#69c", label="Oct–Apr mean")
a2.set_xticks(xi); a2.set_xticklabels([b.replace("bin_", "") for b in bins]); a2.axhline(0, color="#888", lw=1)
a2.set_ylabel("mean Δ, K"); a2.set_xlabel("predicted stem length per 20 m cell"); a2.legend(frameon=False); a2.grid(axis="y", alpha=0.25)
for ax in (a1, a2):
    for s in ("top", "right"): ax.spines[s].set_visible(False)
fig.tight_layout(); fig.savefig(sys.argv[2], dpi=120); print("wrote", sys.argv[2])
print(f"months {len(rows)} | summer mean diff {np.nanmean(diff[summer]):+.2f} K | winter mean diff {np.nanmean(diff[~summer]):+.2f} K | max {diff.max():+.2f} ({months[diff.argmax()]}) | min {diff.min():+.2f} ({months[diff.argmin()]})")
