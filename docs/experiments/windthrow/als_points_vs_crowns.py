"""Berlin ALS points against the 2017 crown segmentation, 120 m window in Spandau.
    python als_points_vs_crowns.py <out png>"""
import sys, numpy as np, laspy, geopandas as gpd, pyogrio
from scipy.spatial import cKDTree
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
TILE = "/Volumes/2TB/winmol/training_data/WINDWURF_Tegel/ALS_2017_tiles/3dm_33_375_5827_1_be.las"
ALS = "/Volumes/2TB/winmol/training_data/WINDWURF_Tegel/ALS segmentation 2017.gpkg"
cx, cy, W = 375500, 5827300, 120; OUT = sys.argv[1]
las = laspy.read(TILE); x, y, z, c = las.x, las.y, las.z, np.asarray(las.classification)
sel = (x > cx - W/2) & (x < cx + W/2) & (y > cy - W/2) & (y < cy + W/2); x, y, z, c = x[sel], y[sel], z[sel], c[sel]
print(f"tile {las.header.version}, point format {las.header.point_format.id}, {len(las.x):,} points; window {sel.sum():,} points, classes {dict(zip(*np.unique(c, return_counts=True)))}")
ground = c == 2; tree = cKDTree(np.c_[x[ground], y[ground]]); _, idx = tree.query(np.c_[x, y], k=8)
zg = np.median(z[ground][idx], axis=1); h = z - zg                       # height above local ground
crowns = pyogrio.read_dataframe(ALS, layer="ALS Kronenerfassung", bbox=(cx - W/2, cy - W/2, cx + W/2, cy + W/2)); crowns = crowns[crowns.geometry.notna()].reset_index(drop=True)
pts = gpd.GeoDataFrame({"h": h, "i": np.arange(len(h))}, geometry=gpd.points_from_xy(x, y), crs=crowns.crs)
veg = pts[pts.h > 2]
j = gpd.sjoin(veg, crowns[["TreeH", "CrwnBsH", "geometry"]], predicate="within", how="inner")
j = j[(j.h >= j.CrwnBsH - 1) & (j.h <= j.TreeH + 1)]                    # inside the crown's vertical extent too
j = j.sort_values("TreeH").drop_duplicates("i", keep="last")             # if several crowns contain it, take the tallest whose box fits
assigned = np.full(len(h), -1); assigned[j.i.values] = j.index_right.values
inside_plan = veg.i.isin(gpd.sjoin(veg, crowns[["geometry"]], predicate="within").i).mean()
print(f"crowns in window {len(crowns)} · vegetation points (> 2 m) {len(veg):,} · inside some crown polygon in plan {inside_plan:.0%} · inside a crown's 3-D box {np.mean(assigned[veg.i] >= 0):.0%}")
print(f"points per crown: median {np.median(np.bincount(assigned[assigned >= 0], minlength=len(crowns))):.0f} (crown file says NPoints median {crowns.NPoints.median():.0f})")
rng = np.random.default_rng(0); cols = rng.random((len(crowns), 3)) * 0.8
fig, axes = plt.subplots(2, 1, figsize=(18, 13), facecolor="white", gridspec_kw=dict(height_ratios=[1, 1.1]))
strip = (y > cy - 10) & (y < cy + 10); ax = axes[0]
a = assigned[strip]; ax.scatter(x[strip][a < 0], h[strip][a < 0], s=1.5, color="#bbb", label="not in any crown box")
ax.scatter(x[strip][a >= 0], h[strip][a >= 0], s=2, c=cols[a[a >= 0]])
for _, r in crowns[(crowns.CentrdY > cy - 10) & (crowns.CentrdY < cy + 10)].iterrows():
    ax.plot([r.CentrdX - r.CrwnDmt/2, r.CentrdX + r.CrwnDmt/2, r.CentrdX + r.CrwnDmt/2, r.CentrdX - r.CrwnDmt/2, r.CentrdX - r.CrwnDmt/2], [r.CrwnBsH, r.CrwnBsH, r.TreeH, r.TreeH, r.CrwnBsH], color="k", lw=0.5, alpha=0.6)
ax.set_xlim(cx - W/2, cx + W/2); ax.set_ylim(-1, 33); ax.set_ylabel("height above ground, m"); ax.legend(loc="upper right", frameon=False, markerscale=6)
ax.set_title("Vertical section, 20 m strip: ALS points coloured by the crown they were assigned to; black boxes = crown base to top, width = crown diameter", loc="left", fontsize=10)
ax = axes[1]; top = h > 2; ax.scatter(x[top][assigned[top] < 0], y[top][assigned[top] < 0], s=0.8, color="#ccc")
ax.scatter(x[top][assigned[top] >= 0], y[top][assigned[top] >= 0], s=0.8, c=cols[assigned[top][assigned[top] >= 0]]); crowns.boundary.plot(ax=ax, color="k", lw=0.3)
ax.axhspan(cy - 10, cy + 10, color="#c44", alpha=0.08); ax.set_aspect("equal"); ax.set_xlim(cx - W/2, cx + W/2); ax.set_ylim(cy - W/2, cy + W/2)
ax.set_title(f"Plan view: vegetation points (> 2 m) coloured by crown, crown outlines in black; red band = the strip above · {len(crowns)} crowns, {len(veg):,} points", loc="left", fontsize=10)
for a_ in axes: [a_.spines[s].set_visible(False) for s in ("top", "right")]
fig.suptitle("Berlin ALS 2017 point cloud (tile 3dm_33_375_5827) against the crown segmentation", x=0.01, ha="left", fontsize=13)
fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(OUT, dpi=110); print("wrote", OUT)
