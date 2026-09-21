"""Grumsin (control) vs Tegel R12 / R13: site-neutral thermal metrics from the
footprint-only delta runs. Per summer month: heat-island fraction (share of the
footprint >= 1 K above its own mean), spatial sd of the field; and the warming
after 2025-07 relative to the 2022-24 summers, per site and per cell."""
import glob, re, sys, warnings
import numpy as np, rasterio, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
SITES = [("Grumsin (control)", sys.argv[1], "#4a7fb5"), ("Tegel R12", sys.argv[2], "#c44"), ("Tegel R13", sys.argv[3], "#e08a2e")]
OUT = sys.argv[4]
def load(pattern):
    out = {}
    for f in sorted(glob.glob(pattern)):
        m = re.search(r"(\d{4}-\d{2})\.tif$", f)
        with rasterio.open(f) as ds:
            d = ds.read(1).astype("float32"); d[d == ds.nodata] = np.nan
        out[m.group(1)] = d
    return out
def summer(lab): return int(lab[5:7]) in (5, 6, 7, 8, 9)
def mean_of(frames, keys):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore"); return np.nanmean(np.stack([frames[k] for k in keys]), axis=0)
fig = plt.figure(figsize=(16, 11), facecolor="white")
gs = fig.add_gridspec(2, 3, height_ratios=[1.15, 1])
ax1 = fig.add_subplot(gs[0, :])
print(f"{'site':18s} {'summers':>8s} {'island% pre':>12s} {'island% post':>13s} {'sd pre':>7s} {'sd post':>8s} {'p90 warming':>12s} {'cells>=1K':>10s}")
for i, (name, pat, col) in enumerate(SITES):
    fr = load(pat)
    labs = [k for k in sorted(fr) if summer(k) and np.isfinite(fr[k]).sum() > 500]
    x = np.array([int(k[:4]) + (int(k[5:7]) - 0.5) / 12 for k in labs])
    island = np.array([np.nanmean(fr[k] >= 1.0) * 100 for k in labs])
    sd = np.array([np.nanstd(fr[k]) for k in labs])
    ax1.plot(x, island, marker="o", ms=4, lw=1.5, color=col, label=name)
    pre = [k for k in labs if "2022-01" <= k <= "2024-12"]; post = [k for k in labs if k >= "2025-07"]
    ipre = np.mean([np.nanmean(fr[k] >= 1.0) for k in pre]) * 100; ipost = np.mean([np.nanmean(fr[k] >= 1.0) for k in post]) * 100
    spre = np.mean([np.nanstd(fr[k]) for k in pre]); spost = np.mean([np.nanstd(fr[k]) for k in post])
    chg = mean_of(fr, post) - mean_of(fr, pre)
    ok = np.isfinite(chg)
    print(f"{name:18s} {len(labs):8d} {ipre:12.1f} {ipost:13.1f} {spre:7.2f} {spost:8.2f} {np.nanpercentile(chg[ok], 90):+12.2f} {np.mean(chg[ok] >= 1.0)*100:9.1f}%")
    ax = fig.add_subplot(gs[1, i])
    im = ax.imshow(chg, cmap="RdBu_r", vmin=-3, vmax=3, interpolation="nearest")
    ax.set_title(f"{name}: warming, summers after 2025-07 minus 2022–24", loc="left", fontsize=10)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02).set_label("K")
ax1.axvline(2025.5, color="#888", lw=1, ls="--"); ax1.text(2025.55, ax1.get_ylim()[1] * 0.95, "storm (Jul 2025)", fontsize=9, color="#666", va="top")
ax1.set_ylabel("share of footprint ≥ 1 K above its own mean, %"); ax1.set_title("Heat-island fraction per summer month (May–Sep), footprint-only RF delta, one forest per frame", loc="left", fontsize=11)
ax1.legend(frameon=False); ax1.grid(alpha=0.25)
for s in ("top", "right"): ax1.spines[s].set_visible(False)
fig.tight_layout(); fig.savefig(OUT, dpi=110); print("wrote", OUT)
