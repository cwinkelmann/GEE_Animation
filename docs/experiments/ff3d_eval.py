"""Evaluate ForestFormer3D prediction PLYs (x, y, z, semantic_pred, instance_pred,
score) produced on carrot.

  python docs/experiments/ff3d_eval.py forinstance PRED.ply SOURCE.las
      -> per-tree IoU against the treeID column of a FOR-instance plot
  python docs/experiments/ff3d_eval.py berlin PRED.ply r12|r13
      -> crown hulls, figure and (r13) comparison with the reference crown file,
         using the same code as the AMS3D spike
Throwaway spike code.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import laspy
import numpy as np
from plyfile import PlyData
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ams3d_spike as ams  # noqa: E402

OUT = HERE.parents[1] / "out" / "ff3d_spike"


def load_pred(ply: str):
    v = PlyData.read(ply)["vertex"].data
    xyz = np.column_stack([v["x"], v["y"], v["z"]]).astype(np.float64)
    return xyz, np.asarray(v["semantic_pred"]), np.asarray(v["instance_pred"]), np.asarray(v["score"])


def join_to_source(xyz_pred, xyz_src, tol=0.02):
    """Index of the source point for every predicted point (coordinates are
    preserved by the pipeline, so this should be an exact match)."""
    # the pipeline writes predictions in a frame shifted by (mean x, mean y, min z)
    # of the input; the point set itself is unchanged, so realign by the means
    shift = xyz_src.mean(0) - xyz_pred.mean(0) if len(xyz_src) == len(xyz_pred) else xyz_src.min(0) - xyz_pred.min(0)
    d, idx = cKDTree(xyz_src).query(xyz_pred + shift, workers=-1)
    print(f"join: {np.mean(d < tol) * 100:.1f}% of {len(xyz_pred)} pred points within {tol} m of a source point")
    return idx


def eval_forinstance(pred_ply: str, src_las: str):
    xyz, sem, inst, score = load_pred(pred_ply)
    las = laspy.read(src_las)
    src = np.column_stack([las.x, las.y, las.z])
    tree_id = np.asarray(las.treeID)
    idx = join_to_source(xyz, src)
    gt = tree_id[idx]
    # tree points in FOR-instance: treeID > 0 (0 = not a tree). Ignore ground/low veg.
    gt_ids = np.unique(gt[gt > 0])
    pred_ids = np.unique(inst[inst >= 0])
    print(f"GT trees {len(gt_ids)}, predicted instances {len(pred_ids)}")
    # IoU matrix via contingency table (sparse-ish, fine for a plot)
    from scipy.sparse import coo_matrix
    m = (gt > 0) | (inst >= 0)
    g = np.searchsorted(gt_ids, gt[m]); g[gt[m] <= 0] = len(gt_ids)
    p = np.searchsorted(pred_ids, inst[m]); p[inst[m] < 0] = len(pred_ids)
    C = coo_matrix((np.ones(m.sum()), (g, p)), shape=(len(gt_ids) + 1, len(pred_ids) + 1)).toarray()
    inter = C[:-1, :-1]
    gt_sz = C[:-1, :].sum(1, keepdims=True)
    pr_sz = C[:, :-1].sum(0, keepdims=True)
    iou = inter / np.maximum(gt_sz + pr_sz - inter, 1)
    best = iou.max(1) if iou.size else np.zeros(len(gt_ids))
    # greedy one-to-one at IoU >= 0.5
    matched = 0
    used = set()
    for gi in np.argsort(-best):
        pj = int(np.argmax(iou[gi]))
        if iou[gi, pj] >= 0.5 and pj not in used:
            used.add(pj); matched += 1
    # FOR-instance class 3 = trees outside the annotated plot (no treeID): a
    # predicted instance that is mostly class 3 is not a false positive
    cls = np.asarray(las.classification)[idx]
    out_frac = np.array([np.mean(cls[inst == pid] == 3) for pid in pred_ids]) if len(pred_ids) else np.zeros(0)
    n_outside = int((out_frac > 0.5).sum())
    stats = dict(
        gt_trees=int(len(gt_ids)), pred_instances=int(len(pred_ids)), pred_mostly_outside_plot=n_outside,
        precision_inside_plot=matched / max(len(pred_ids) - n_outside, 1), matched_iou50=matched,
        detection_rate=matched / max(len(gt_ids), 1), precision=matched / max(len(pred_ids), 1),
        mean_best_iou=float(best.mean()), median_best_iou=float(np.median(best)),
        semantic_classes=dict(zip(*[a.tolist() for a in np.unique(sem, return_counts=True)])),
    )
    print(json.dumps(stats, indent=1))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (Path(pred_ply).stem + "_forinstance_eval.json")).write_text(json.dumps(stats, indent=1))
    return stats


def eval_berlin(pred_ply: str, patch: str, tag: str = ""):
    from shapely.geometry import box
    tile, xmin, ymin, size = ams.PATCHES[patch]
    xyz, sem, inst, score = load_pred(pred_ply)
    # height above ground: reuse the spike's ground grid on the same source points
    tile_path = ams.TILES / tile
    if tile_path.exists():
        las = laspy.read(tile_path)
        m = (las.x >= xmin) & (las.x < xmin + size) & (las.y >= ymin) & (las.y < ymin + size) & np.isin(las.classification, [2, 3, 4, 5])
        src = np.column_stack([las.x[m], las.y[m], las.z[m]])
        cls = np.asarray(las.classification)[m]
    else:  # 2TB drive unmounted: the exported patch is the same point set in local coords
        patch_las = next((Path.home() / "work/hnee/ForestFormer3D_runs/berlin_in").glob(f"{patch}_*_E{xmin}_N{ymin}_100m.las"))
        las = laspy.read(patch_las)
        src = np.column_stack([las.x + xmin, las.y + ymin, las.z])
        cls = np.asarray(las.classification)
    h_src = ams.normalize_height(src, cls)
    # predictions are the same point set in a mean-shifted frame: move them back
    xyz_abs = xyz + (src.mean(0) - xyz.mean(0))
    xy_abs = xyz_abs[:, :2]
    idx = join_to_source(xyz_abs, src)
    h = h_src[idx]
    keep = inst >= 0
    _, labels = np.unique(inst[keep], return_inverse=True)
    P = np.column_stack([xyz[keep, 0], xyz[keep, 1], h[keep]])
    aoi = box(xmin, ymin, xmin + size, ymin + size)
    crowns = ams.crowns_from_labels(P, xy_abs[keep], labels, aoi)
    stats = dict(pred_points=int(len(xyz)), tree_points=int(keep.sum()), instances=int(labels.max() + 1),
                 semantic_classes=dict(zip(*[a.tolist() for a in np.unique(sem, return_counts=True)])),
                 veg_points_above_2m=int((h_src >= 2).sum()),
                 frac_veg_above_2m_in_instances=float(np.mean(keep[h >= 2])),
                 trees_in_patch=int(len(crowns)), trees_per_ha=float(len(crowns) / (size * size / 1e4)),
                 height_mean=float(crowns.height.mean()), diam_mean=float(crowns.crown_diam.mean()))
    ref = None
    if patch == "r13" and ams.CROWNS.exists():
        cmp, ref = ams.compare_to_reference(crowns, aoi)
        stats.update(cmp)
    elif patch == "r13":
        stats["reference"] = f"skipped, {ams.CROWNS} not mounted"
    print(json.dumps(stats, indent=1))
    OUT.mkdir(parents=True, exist_ok=True)
    name = f"ff3d_{patch}{tag}"
    crowns.to_file(OUT / f"{name}_crowns.gpkg", layer="crowns", driver="GPKG")
    (OUT / f"{name}_stats.json").write_text(json.dumps(stats, indent=1))
    ams.OUT = OUT
    ams.plot(P, xy_abs[keep], labels, crowns, ref, aoi, name, stats)
    return stats


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "forinstance":
        eval_forinstance(sys.argv[2], sys.argv[3])
    else:
        eval_berlin(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
