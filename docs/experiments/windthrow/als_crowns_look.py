"""What does the 2017 ALS crown segmentation look like, and does it bear the
fingerprints of adaptive mean shift 3D (AMS3D)?  python als_crowns_look.py <out png>"""
import sys, numpy as np, geopandas as gpd, pyogrio
from shapely.geometry import box
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
ALS = "/Volumes/2TB/winmol/training_data/WINDWURF_Tegel/ALS segmentation 2017.gpkg"
OUT = sys.argv[1]
A = pyogrio.read_dataframe(ALS, layer="ALS Kronenerfassung", read_geometry=False)
# --- sample window 120 x 120 m near the middle of the R13 coverage
cx, cy = 375500, 5827300; W = 120
win = pyogrio.read_dataframe(ALS, layer="ALS Kronenerfassung", bbox=(cx - W/2, cy - W/2, cx + W/2, cy + W/2))
win = win[win.geometry.notna()]
# overlap: share of crowns whose centroid lies inside a TALLER crown's polygon (understorey under canopy)
pts = gpd.GeoDataFrame(win[["ID", "TreeH"]], geometry=gpd.points_from_xy(win.CentrdX, win.CentrdY), crs=win.crs)
j = gpd.sjoin(pts, win[["ID", "TreeH", "geometry"]].rename(columns={"ID": "ID2", "TreeH": "TreeH2"}), predicate="within")
j = j[j.ID != j.ID2]; under = j[j.TreeH2 > j.TreeH + 2].ID.nunique(); n_win = len(win)
# crown width vs height relation over the whole layer
h = A.TreeH.values; d = A.CrwnDmt.values; cb = A.CrwnBsH.values
q = np.quantile(h, np.linspace(0.02, 0.98, 25)); mids = (q[:-1] + q[1:]) / 2
med_d = [np.median(d[(h >= a) & (h < b)]) for a, b in zip(q[:-1], q[1:])]
slope, icpt = np.polyfit(mids, med_d, 1)
print(f"crowns {len(A):,} · height 5.4–{h.max():.1f} m, median {np.median(h):.1f} · crown diameter median {np.median(d):.1f} m")
print(f"median crown diameter vs height: slope {slope:.3f} m/m, intercept {icpt:.2f} m · Pearson r(H, D) {np.corrcoef(h, d)[0,1]:.2f}")
print(f"crown base / height median {np.median(cb / h):.2f} · points per crown median {np.median(A.NPoints):.0f} · points per m² crown median {np.median(A.NPoints / A.CnvxHlA):.1f}")
print(f"window {W} m: {n_win} crowns ({n_win / (W*W/1e4):.0f}/ha); {under} ({under/n_win:.0%}) have their centroid inside a crown ≥ 2 m taller (understorey under canopy)")
print(f"heights < 10 m: {np.mean(h < 10):.0%} of crowns · 10–15 m: {np.mean((h >= 10) & (h < 15)):.0%} · ≥ 20 m: {np.mean(h >= 20):.0%}")
fig = plt.figure(figsize=(18, 12), facecolor="white"); gs = fig.add_gridspec(2, 3)
ax = fig.add_subplot(gs[0, 0]); win.sort_values("TreeH").plot(ax=ax, column="TreeH", cmap="viridis", edgecolor="k", lw=0.3, alpha=0.75, legend=True, legend_kwds=dict(shrink=0.7, label="tree height, m"))
ax.set_title(f"Plan view, {W} × {W} m: crown polygons drawn short-to-tall\n{n_win} crowns; {under/n_win:.0%} sit under a taller crown", loc="left", fontsize=10); ax.set_xticks([]); ax.set_yticks([])
ax = fig.add_subplot(gs[0, 1:]); band = win[(win.CentrdY > cy - 10) & (win.CentrdY < cy + 10)].sort_values("TreeH")
for _, r in band.iterrows():
    ax.add_patch(Rectangle((r.CentrdX - r.CrwnDmt / 2, r.CrwnBsH), r.CrwnDmt, r.CrwnLng, facecolor=plt.cm.viridis((r.TreeH - 5) / 25), edgecolor="k", lw=0.4, alpha=0.6))
    ax.plot([r.CentrdX, r.CentrdX], [0, r.CrwnBsH], color="#754", lw=1)
ax.set_xlim(cx - W/2, cx + W/2); ax.set_ylim(0, 32); ax.set_xlabel("easting, m"); ax.set_ylabel("height above ground, m")
ax.set_title("Vertical section, 20 m wide strip through the window: each crown as a box from crown base to top, width = crown diameter", loc="left", fontsize=10)
ax = fig.add_subplot(gs[1, 0]); ax.hist2d(h, d, bins=[60, 60], range=[[5, 32], [0, 16]], cmap="Blues", cmin=1); ax.plot(mids, med_d, color="#c44", marker="o", ms=3, label=f"median per height bin: D ≈ {slope:.2f}·H {icpt:+.1f}")
ax.set_xlabel("tree height, m"); ax.set_ylabel("crown diameter, m"); ax.legend(frameon=False, fontsize=9); ax.set_title("Crown diameter vs height (all crowns)", loc="left", fontsize=10)
ax = fig.add_subplot(gs[1, 1]); ax.hist2d(h, cb, bins=[60, 60], range=[[5, 32], [0, 25]], cmap="Blues", cmin=1); ax.plot([5, 32], [5, 32], color="#888", lw=1, ls="--")
ax.set_xlabel("tree height, m"); ax.set_ylabel("crown base height, m"); ax.set_title("Crown base height vs height", loc="left", fontsize=10)
ax = fig.add_subplot(gs[1, 2]); ax.hist(h, bins=60, range=(5, 32), color="#357"); ax.set_xlabel("tree height, m"); ax.set_ylabel("crowns"); ax.set_title(f"Height distribution of {len(A):,} crowns", loc="left", fontsize=10)
for a in fig.axes: [a.spines[s].set_visible(False) for s in ("top", "right")]
fig.suptitle("ALS segmentation 2017 (Spandau tiles): what the crowns look like", x=0.01, ha="left", fontsize=13)
fig.tight_layout(rect=(0, 0, 1, 0.96)); fig.savefig(OUT, dpi=110); print("wrote", OUT)
