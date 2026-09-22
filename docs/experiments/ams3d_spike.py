"""Spike: adaptive mean shift 3D (AMS3D, after Ferraz et al. 2012/2016) tree
crown segmentation on a small patch of the Berlin 2021 ALS tiles.

Throwaway exploration code -- not part of the gee_animation package.

Usage:
    python docs/experiments/ams3d_spike.py --patch r12   # Tegeler Forst, no reference
    python docs/experiments/ams3d_spike.py --patch r13   # Spandau, compared to reference crowns

Method (per point, seeds = every vegetation point >= HMIN above ground):
  * kernel = cylinder with horizontal radius  h_s = max(S_MIN, A_S * h)
                                 and half-height h_r = max(R_MIN, A_R * h)
    where h is the height above ground of the CURRENT mode position, so the
    window grows with tree height (that is the "adaptive" part).
  * Epanechnikov product kernel -> the mean shift step is the plain centroid of
    the points inside the cylinder.
  * converged modes closer than MERGE_XY / MERGE_Z are one tree.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import geopandas as gpd
import laspy
import numpy as np
from scipy import ndimage
from scipy.interpolate import RegularGridInterpolator
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from shapely.geometry import MultiPoint, Point, box

TILES = Path("/Volumes/2TB/winmol/ALS_Data/berlin_als_2021")
CROWNS = Path("/Volumes/2TB/winmol/training_data/WINDWURF_Tegel/ALS segmentation 2017.gpkg")
OUT = Path(__file__).resolve().parents[2] / "out" / "ams3d_spike"

PATCHES = {
    # name: (tile, xmin, ymin, size_m)
    "r12": ("3dm_33_381_5828_1_be.las", 381300, 5828300, 100),
    "r13": ("3dm_33_376_5827_1_be.las", 376400, 5827400, 100),
}

# ---- AMS3D parameters --------------------------------------------------------
HMIN = 2.0        # m above ground: below this is understorey / ground clutter
HMAX = 60.0       # m: anything higher is noise (powerlines, birds)
A_S, S_MIN = 0.12, 1.0   # horizontal bandwidth = crown radius ~ 0.12 * height
A_R, R_MIN = 0.27, 1.5   # vertical half-bandwidth = half crown length ~ 0.27 * height
MAX_ITER = 40
TOL = 0.05        # m: a mode that moves less than this has converged
MERGE_XY, MERGE_Z = 1.0, 2.0   # modes closer than this (m) are one tree
MIN_PTS = 20      # clusters with fewer points are not trees (ref file uses 20 too)
CHUNK = 4000      # seeds per neighbour query (bounds memory)


def load_patch(tile: str, xmin: float, ymin: float, size: float, buffer: float = 10.0):
    """Points of the patch (+buffer so edge trees keep their full crowns)."""
    las = laspy.read(TILES / tile)
    x, y, z = las.x, las.y, las.z
    cls = np.asarray(las.classification)
    m = (
        (x >= xmin - buffer) & (x < xmin + size + buffer)
        & (y >= ymin - buffer) & (y < ymin + size + buffer)
        & np.isin(cls, [2, 3, 4, 5])
    )
    return np.column_stack([x[m], y[m], z[m]]), cls[m]


def normalize_height(xyz: np.ndarray, cls: np.ndarray, cell: float = 1.0) -> np.ndarray:
    """Height above ground from a gridded min-z of class-2 points (holes filled
    with the nearest ground cell), bilinearly interpolated to every point."""
    g = xyz[cls == 2]
    x0, y0 = xyz[:, 0].min(), xyz[:, 1].min()
    nx = int(np.ceil((xyz[:, 0].max() - x0) / cell)) + 1
    ny = int(np.ceil((xyz[:, 1].max() - y0) / cell)) + 1
    ix = ((g[:, 0] - x0) / cell).astype(int)
    iy = ((g[:, 1] - y0) / cell).astype(int)
    dtm = np.full((ny, nx), np.inf)
    np.minimum.at(dtm, (iy, ix), g[:, 2])
    hole = ~np.isfinite(dtm)
    if hole.any():
        near = ndimage.distance_transform_edt(hole, return_distances=False, return_indices=True)
        dtm = dtm[near[0], near[1]]
    interp = RegularGridInterpolator(
        (y0 + np.arange(ny) * cell, x0 + np.arange(nx) * cell), dtm,
        method="linear", bounds_error=False, fill_value=None,
    )
    return xyz[:, 2] - interp(xyz[:, [1, 0]])


def ams3d(P: np.ndarray) -> tuple[np.ndarray, int]:
    """Run adaptive mean shift from every point of P (x, y, h). Returns the
    converged mode of each point and the number of iterations used."""
    k = A_S / A_R                    # scale h so the cylinder has aspect ratio 1
    Ps = P * [1, 1, k]
    tree = cKDTree(Ps)
    modes = P.copy()
    active = np.ones(len(P), bool)
    for it in range(1, MAX_ITER + 1):
        idx = np.flatnonzero(active)
        for chunk in np.array_split(idx, max(1, len(idx) // CHUNK)):
            q = modes[chunk]
            hs = np.maximum(S_MIN, A_S * q[:, 2])
            hr = np.maximum(R_MIN, A_R * q[:, 2])
            # a ball in scaled space that contains the cylinder
            rad = np.sqrt(hs ** 2 + (hr * k) ** 2)
            nb = tree.query_ball_point(q * [1, 1, k], rad, workers=-1, return_sorted=False)
            lens = np.fromiter(map(len, nb), dtype=np.int64, count=len(nb))
            j = np.concatenate(nb).astype(np.int64)
            i = np.repeat(np.arange(len(chunk)), lens)
            d = P[j] - q[i]
            inside = (d[:, 0] ** 2 + d[:, 1] ** 2 <= hs[i] ** 2) & (np.abs(d[:, 2]) <= hr[i])
            i, j = i[inside], j[inside]
            cnt = np.bincount(i, minlength=len(chunk)).astype(float)
            new = np.column_stack(
                [np.bincount(i, weights=P[j, c], minlength=len(chunk)) for c in range(3)]
            ) / np.maximum(cnt, 1)[:, None]
            new[cnt == 0] = q[cnt == 0]
            shift = np.linalg.norm(new - q, axis=1)
            modes[chunk] = new
            active[chunk[shift < TOL]] = False
        if not active.any():
            return modes, it
    return modes, MAX_ITER


def cluster_modes(modes: np.ndarray) -> np.ndarray:
    """Union modes within MERGE_XY horizontally and MERGE_Z vertically."""
    S = modes * [1, 1, MERGE_XY / MERGE_Z]
    tree = cKDTree(S)
    pairs = tree.query_pairs(MERGE_XY, output_type="ndarray")
    n = len(modes)
    adj = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, labels = connected_components(adj, directed=False)
    return labels


def relabel_small(P: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Attach points of tiny clusters to the nearest big cluster."""
    counts = np.bincount(labels)
    small = counts[labels] < MIN_PTS
    if small.all() or not small.any():
        return labels
    tree = cKDTree(P[~small])
    _, nn = tree.query(P[small], workers=-1)
    labels = labels.copy()
    labels[small] = labels[~small][nn]
    _, labels = np.unique(labels, return_inverse=True)
    return labels


def crowns_from_labels(P: np.ndarray, xy_abs: np.ndarray, labels: np.ndarray, aoi) -> gpd.GeoDataFrame:
    rows = []
    for lab in np.unique(labels):
        m = labels == lab
        pts = xy_abs[m]
        top = np.argmax(P[m, 2])
        apex = Point(pts[top])
        if not apex.within(aoi):
            continue
        hull = MultiPoint(pts).convex_hull
        h = P[m, 2]
        rows.append(dict(
            tree_id=int(lab), n_pts=int(m.sum()), height=float(h.max()),
            crown_base=float(np.percentile(h, 2)), apex_x=apex.x, apex_y=apex.y,
            hull_area=float(hull.area), crown_diam=float(2 * np.sqrt(hull.area / np.pi)),
            geometry=hull,
        ))
    return gpd.GeoDataFrame(rows, crs="EPSG:25833")


def compare_to_reference(crowns: gpd.GeoDataFrame, aoi) -> dict:
    ref = gpd.read_file(CROWNS, bbox=aoi.bounds)
    ref = ref[ref.geometry.centroid.within(aoi)]
    ref["geometry"] = ref.geometry.buffer(0)
    apex = gpd.GeoDataFrame(crowns[["tree_id", "height"]],
                            geometry=gpd.points_from_xy(crowns.apex_x, crowns.apex_y), crs=crowns.crs)
    hit = gpd.sjoin(apex, ref[["ID", "TreeH", "CrwnDmt", "geometry"]], predicate="within", how="inner")
    hit = hit[(hit.height - hit.TreeH).abs() <= 3.0]
    # one AMS3D apex per reference crown at most
    hit = hit.sort_values("height", ascending=False).drop_duplicates("ID")
    out = dict(
        n_ref=int(len(ref)), n_ams3d=int(len(crowns)), n_matched=int(len(hit)),
        recall=float(len(hit) / max(len(ref), 1)),
        precision=float(len(hit) / max(len(crowns), 1)),
        ref_height_mean=float(ref.TreeH.mean()), ams3d_height_mean=float(crowns.height.mean()),
        ref_diam_mean=float(ref.CrwnDmt.mean()), ams3d_diam_mean=float(crowns.crown_diam.mean()),
        matched_height_rmse=float(np.sqrt(((hit.height - hit.TreeH) ** 2).mean())) if len(hit) else None,
    )
    return out, ref


def chm(P: np.ndarray, xy_abs: np.ndarray, bounds, cell=0.5):
    xmin, ymin, xmax, ymax = bounds
    nx, ny = int((xmax - xmin) / cell), int((ymax - ymin) / cell)
    ix = np.clip(((xy_abs[:, 0] - xmin) / cell).astype(int), 0, nx - 1)
    iy = np.clip(((xy_abs[:, 1] - ymin) / cell).astype(int), 0, ny - 1)
    grid = np.zeros((ny, nx))
    np.maximum.at(grid, (iy, ix), P[:, 2])
    return grid


def plot(P, xy_abs, labels, crowns, ref, aoi, name, stats):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    b = aoi.bounds
    fig, axes = plt.subplots(1, 3, figsize=(21, 7.4), constrained_layout=True)
    ax = axes[0]
    im = ax.imshow(chm(P, xy_abs, b), origin="lower", extent=[b[0], b[2], b[1], b[3]], cmap="viridis")
    crowns.boundary.plot(ax=ax, color="white", linewidth=0.6)
    ax.scatter(crowns.apex_x, crowns.apex_y, s=6, c="red")
    if ref is not None:
        ref.boundary.plot(ax=ax, color="orange", linewidth=0.6, linestyle="--")
    fig.colorbar(im, ax=ax, shrink=0.8, label="height above ground [m]")
    ax.set_title(f"CHM 0.5 m, AMS3D crowns (white, red apex)"
                 + (", reference crowns (orange)" if ref is not None else ""))

    ax = axes[1]
    rng = np.random.default_rng(0)
    colors = rng.random((labels.max() + 1, 3))
    inside = (xy_abs[:, 0] >= b[0]) & (xy_abs[:, 0] < b[2]) & (xy_abs[:, 1] >= b[1]) & (xy_abs[:, 1] < b[3])
    order = np.argsort(P[inside, 2])
    ax.scatter(xy_abs[inside, 0][order], xy_abs[inside, 1][order], s=0.3, c=colors[labels[inside][order]])
    ax.set_aspect("equal"); ax.set_xlim(b[0], b[2]); ax.set_ylim(b[1], b[3])
    ax.set_title(f"points coloured by AMS3D cluster ({len(crowns)} trees with apex in patch)")

    ax = axes[2]
    ymid = (b[1] + b[3]) / 2
    sl = inside & (np.abs(xy_abs[:, 1] - ymid) < 5)
    ax.scatter(xy_abs[sl, 0], P[sl, 2], s=0.8, c=colors[labels[sl]])
    ax.set_title("10 m wide E-W cross-section through the patch centre")
    ax.set_xlabel("easting [m]"); ax.set_ylabel("height above ground [m]")
    txt = "\n".join(f"{k}: {v:.3g}" if isinstance(v, float) else f"{k}: {v}" for k, v in stats.items())
    ax.text(0.01, 0.99, txt, transform=ax.transAxes, va="top", fontsize=8,
            bbox=dict(facecolor="white", alpha=0.8))
    for a in axes[:2]:
        a.set_xlabel("easting [m]"); a.set_ylabel("northing [m]")
    fig.suptitle(f"AMS3D spike, Berlin ALS 2021, patch {name} ({b[0]:.0f}-{b[2]:.0f} E, {b[1]:.0f}-{b[3]:.0f} N)")
    fig.savefig(OUT / f"ams3d_{name}.png", dpi=130)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch", choices=PATCHES, default="r12")
    ap.add_argument("--tag", default="", help="suffix for output files")
    for name in ("A_S", "S_MIN", "A_R", "R_MIN", "MERGE_XY", "MERGE_Z", "TOL"):
        ap.add_argument(f"--{name.lower()}", type=float, default=None)
    ap.add_argument("--min_pts", type=int, default=None)
    args = ap.parse_args()
    for name in ("A_S", "S_MIN", "A_R", "R_MIN", "MERGE_XY", "MERGE_Z", "TOL", "MIN_PTS"):
        if getattr(args, name.lower()) is not None:
            globals()[name] = getattr(args, name.lower())
    run = args.patch + (f"_{args.tag}" if args.tag else "")
    tile, xmin, ymin, size = PATCHES[args.patch]
    aoi = box(xmin, ymin, xmin + size, ymin + size)
    OUT.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    xyz, cls = load_patch(tile, xmin, ymin, size)
    h = normalize_height(xyz, cls)
    veg = (h >= HMIN) & (h <= HMAX) & (cls != 2)
    P = np.column_stack([xyz[veg, 0] - xmin, xyz[veg, 1] - ymin, h[veg]])   # local coords
    xy_abs = xyz[veg, :2]
    t1 = time.time()
    print(f"{args.patch}: {len(xyz)} pts loaded, {veg.sum()} vegetation pts >= {HMIN} m  ({t1 - t0:.1f}s)")

    modes, iters = ams3d(P)
    t2 = time.time()
    print(f"mean shift converged: {iters} iterations, {t2 - t1:.1f}s")

    labels = relabel_small(P, cluster_modes(modes))
    crowns = crowns_from_labels(P, xy_abs, labels, aoi)
    t3 = time.time()
    stats = dict(
        veg_points=int(veg.sum()), iterations=iters, mean_shift_s=round(t2 - t1, 1),
        clusters_total=int(labels.max() + 1), trees_in_patch=int(len(crowns)),
        trees_per_ha=float(len(crowns) / (size * size / 1e4)),
        height_mean=float(crowns.height.mean()), diam_mean=float(crowns.crown_diam.mean()),
    )
    ref = None
    if args.patch == "r13":
        cmp, ref = compare_to_reference(crowns, aoi)
        stats.update(cmp)
    print(json.dumps(stats, indent=1))
    crowns.to_file(OUT / f"ams3d_{run}_crowns.gpkg", layer="crowns", driver="GPKG")
    (OUT / f"ams3d_{run}_stats.json").write_text(json.dumps(
        dict(stats, params=dict(HMIN=HMIN, A_S=A_S, S_MIN=S_MIN, A_R=A_R, R_MIN=R_MIN,
                                MERGE_XY=MERGE_XY, MERGE_Z=MERGE_Z, MIN_PTS=MIN_PTS)), indent=1))
    plot(P, xy_abs, labels, crowns, ref, aoi, run, stats)
    print(f"wrote {OUT}/ams3d_{run}_* ({time.time() - t3:.1f}s plotting)")


if __name__ == "__main__":
    main()
