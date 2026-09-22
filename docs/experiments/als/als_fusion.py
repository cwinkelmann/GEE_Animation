"""Fuse the 2017 ALS crown segmentation (R13 only: 354k crowns, no species) into
(a) detection and (b) PREDICTION of the 2025 windthrow, per 20 m cell.

    python docs/experiments/windthrow/als_fusion.py <sar worktree> <out dir>
"""
import csv, os, sys, warnings
import numpy as np, geopandas as gpd, pyogrio, rasterio
from rasterio.features import rasterize
from rasterio.enums import MergeAlg
from scipy.ndimage import distance_transform_edt
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SAR_WT, OUT = sys.argv[1], sys.argv[2]; sys.argv = [sys.argv[0], SAR_WT, OUT]
import sensor_figures as sf
from sensor_figures import SITES, masks, change_map, SUMMER
import detection_upgrade as du
ALS = "/Volumes/2TB/winmol/training_data/WINDWURF_Tegel/ALS segmentation 2017.gpkg"
SITE = "R13"

def als_features(grid):
    g = pyogrio.read_dataframe(ALS, layer="ALS Kronenerfassung", columns=["TreeH", "CrwnLng", "CrwnDmt", "DBH_prd", "CnvxHlA", "CentrdX", "CentrdY"], read_geometry=False)
    pts = gpd.points_from_xy(g.CentrdX, g.CentrdY)
    shp, tr = grid["shape"], grid["transform"]
    def ras(vals, alg=MergeAlg.add):
        return rasterize(zip(pts, vals), out_shape=shp, transform=tr, merge_alg=alg, dtype="float32", fill=0)
    n = ras(np.ones(len(g))); nz = np.maximum(n, 1)
    F = {"als_n": n, "als_h_mean": ras(g.TreeH.values) / nz, "als_h_max": ras(g.TreeH.values, MergeAlg.replace),
         "als_h_sd": np.sqrt(np.maximum(ras(g.TreeH.values ** 2) / nz - (ras(g.TreeH.values) / nz) ** 2, 0)),
         "als_crown_ratio": ras((g.CrwnLng / g.TreeH).values) / nz, "als_dbh": ras(g.DBH_prd.values) / nz,
         "als_cover": np.minimum(ras(g.CnvxHlA.values) / 400.0, 1.5), "als_slender": ras((g.TreeH / np.maximum(g.DBH_prd, 0.03)).values) / nz}
    F["als_h_max"] = np.where(n > 0, F["als_h_max"], 0)
    gap = n == 0; F["edge_dist_m"] = distance_transform_edt(~gap) * 20.0     # distance to the nearest crown-free 20 m cell
    return F

def main():
    m = masks(SITE); g = m["grid"]; core, wt, ctl, dens = m["core"], m["wt"], m["ctl"], m["dens"]
    A = als_features(g)
    labs, S = du.vh_stack(SITE, g); ymi = {l: i for i, l in enumerate(labs)}
    def mean_months(pred):
        idx = [i for l, i in ymi.items() if pred(l)]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore"); return np.nanmean(S[idx], 0)
    P = {"vh_2017_18": mean_months(lambda l: l < "2019-01"), "vh_2022_24": mean_months(lambda l: "2022-01" <= l <= "2024-12" and int(l[5:]) in SUMMER)}
    P["vh_loss"] = P["vh_2022_24"] - P["vh_2017_18"]
    import glob
    pre_files = [f for f in sorted(glob.glob(SITES[SITE]["ndvi"])) if du.summer_pre(sf.ym_of(f))]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore"); P["ndvi_pre"] = np.nanmean(np.stack([sf.on_grid(f, 1, g) for f in pre_files]), 0)
    P["ndvi_pre"] = np.nan_to_num(P["ndvi_pre"], nan=np.nanmean(P["ndvi_pre"]))
    # detection features (post-storm) from detection_upgrade
    D = {}
    D["lst"], _, _ = change_map(SITES[SITE]["lst"], 1, g, du.summer_pre, du.summer_post)
    D["ndvi"], _, _ = change_map(SITES[SITE]["ndvi"], 1, g, du.summer_pre, du.summer_post)
    for k, pat in du.EXTRA[SITE].items(): D[k], _, _ = change_map(pat, 1, g, du.summer_pre, du.summer_post)
    i0, i1 = ymi["2024-07"], ymi[du.STORM_YM]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore"); D["vh"] = np.nanmean(S[i1:i1 + 12], 0) - np.nanmean(S[i0:i1], 0)
    D["vh_storm_t"], _, _, _ = du.changepoint(labs, S, core)
    for k in ("ndvi", "vh", "lst"): D[f"{k}_mean3"], D[f"{k}_sd3"] = du.texture(D[k])
    F = {**A, **P, **D}
    sel = (wt | ctl) & (A["als_n"] > 0)
    for k in F: sel &= np.isfinite(F[k])
    y = wt[sel].astype(int); folds = du.block_folds(core.shape)[sel]
    print(f"{SITE}: {int(sel.sum())} labelled cells with ALS cover ({y.sum()} windthrow), ALS crowns/ha inside footprint {A['als_n'][core].sum() / (core.sum() * 0.04):.0f}")
    def run(name, cols, kind, model="logit"):
        X = np.column_stack([F[c][sel] for c in cols])
        if len(cols) == 1:
            auc = roc_auc_score(y, X[:, 0]); auc = max(auc, 1 - auc)
        else:
            p = np.zeros(len(y))
            for k in np.unique(folds):
                te = folds == k
                mdl = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, class_weight="balanced")) if model == "logit" else HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_depth=4, class_weight="balanced")
                mdl.fit(X[~te], y[~te]); p[te] = mdl.predict_proba(X[te])[:, 1]
            auc = roc_auc_score(y, p)
        res.append(dict(site=SITE, kind=kind, features=name, model=model if len(cols) > 1 else "single", n_features=len(cols), auc=auc))
        print(f"  {kind:10s} {name:44s} {model if len(cols) > 1 else 'single':6s} AUC {auc:.3f}")
    res = []
    print("PREDICTION (pre-storm information only):")
    als_cols = ["als_n", "als_h_mean", "als_h_max", "als_h_sd", "als_crown_ratio", "als_dbh", "als_cover", "als_slender"]
    for c in als_cols + ["edge_dist_m", "vh_loss", "vh_2022_24", "ndvi_pre"]: run(c, [c], "predict")
    run("ALS structure 2021 (8 features)", als_cols, "predict"); run("ALS structure 2021 (8 features)", als_cols, "predict", "gbt")
    run("ALS + edge distance", als_cols + ["edge_dist_m"], "predict", "gbt")
    run("radar loss + NDVI level (satellite only)", ["vh_loss", "vh_2022_24", "ndvi_pre"], "predict", "gbt")
    allpre = als_cols + ["edge_dist_m", "vh_loss", "vh_2022_24", "ndvi_pre"]
    run("ALS + edge + satellite pre-storm", allpre, "predict"); run("ALS + edge + satellite pre-storm", allpre, "predict", "gbt")
    print("DETECTION (post-storm change + ALS):")
    det = ["lst", "ndvi", "vh", "ndmi", "ndre", "evi", "vh_storm_t", "ndvi_mean3", "ndvi_sd3", "vh_mean3", "vh_sd3", "lst_mean3"]
    run("all change features (as before)", det, "detect"); run("all change features + ALS structure", det + als_cols, "detect")
    run("all change features + ALS structure", det + als_cols, "detect", "gbt")
    with open(f"{OUT}/als_fusion.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(res[0])); w.writeheader(); w.writerows(res)
    # figure: prediction AUC bars + partial dependence of windthrow rate on height and edge distance + map
    fig = plt.figure(figsize=(18, 10), facecolor="white"); gs = fig.add_gridspec(2, 3)
    ax = fig.add_subplot(gs[0, :]); rows = [r for r in res if r["kind"] == "predict"]
    names = [f"{r['features']}" + (f" [{r['model']}]" if r["model"] != "single" else "") for r in rows]
    ax.barh(range(len(rows)), [r["auc"] for r in rows], color=["#357" if r["model"] == "single" else "#c8502a" for r in rows])
    for i, r in enumerate(rows): ax.text(r["auc"] + 0.003, i, f"{r['auc']:.2f}", va="center", fontsize=8)
    ax.set_yticks(range(len(rows))); ax.set_yticklabels(names, fontsize=8); ax.set_xlim(0.5, 0.85); ax.axvline(0.5, color="#888", lw=1); ax.invert_yaxis()
    ax.set_title("R13: PREDICTING the 2025 windthrow cells from pre-storm information only (AUC; blue = single feature, orange = model, 500 m-block CV)", loc="left", fontsize=10); ax.grid(alpha=0.25, axis="x")
    def rate_by(feature, bins, ax, xlabel):
        v = F[feature][sel]; q = np.quantile(v, np.linspace(0, 1, bins + 1)); mids, rates, lo, hi = [], [], [], []
        for a, b in zip(q[:-1], q[1:]):
            s = (v >= a) & (v <= b); r = y[s].mean(); n = s.sum(); rates.append(r); mids.append((a + b) / 2); e = 1.96 * np.sqrt(r * (1 - r) / max(n, 1)); lo.append(r - e); hi.append(r + e)
        ax.plot(mids, rates, marker="o", color="#c8502a"); ax.fill_between(mids, lo, hi, color="#c8502a", alpha=0.15); ax.axhline(y.mean(), color="#888", lw=1, ls="--")
        ax.set_xlabel(xlabel); ax.set_ylabel("share of cells that became windthrow"); ax.grid(alpha=0.25); [ax.spines[s].set_visible(False) for s in ("top", "right")]
    rate_by("als_h_mean", 10, fig.add_subplot(gs[1, 0]), "mean tree height in the cell, 2021 ALS (m), deciles")
    rate_by("edge_dist_m", 10, fig.add_subplot(gs[1, 1]), "distance to nearest crown-free cell (m), deciles")
    rate_by("vh_loss", 10, fig.add_subplot(gs[1, 2]), "VH change 2017–18 → 2022–24 summers (dB), deciles")
    fig.suptitle("Fusing the 2021 ALS crown segmentation (file named 2017): what pre-storm stand structure says about where trees fell (R13)", x=0.01, ha="left", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.96)); fig.savefig(f"{OUT}/als_fusion.png", dpi=110); print("wrote", f"{OUT}/als_fusion.png")

if __name__ == "__main__":
    main()
