"""Can detection be pushed further? Four ideas tested on the same 20 m cells:
1. sensor fusion (logistic regression, spatially blocked 5-fold CV, plus cross-site transfer)
2. change-point detection on the full monthly VH series (seasonally adjusted)
3. extra indices (NDMI, NDRE, EVI) and 3x3 texture of the change rasters
4. patch-level "capture curve": share of all predicted stem captured vs share of footprint flagged

    python docs/experiments/windthrow/detection_upgrade.py <sar worktree> <out dir>
"""
import csv, glob, os, re, sys, warnings
import numpy as np, rasterio
from scipy.ndimage import uniform_filter, generic_filter, label
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SAR_WT, OUT = sys.argv[1], sys.argv[2]; os.makedirs(OUT, exist_ok=True)
sys.argv = [sys.argv[0], SAR_WT, OUT]
import sensor_figures as sf
from sensor_figures import SITES, masks, on_grid, change_map, ym_of, SUMMER

EXTRA = {"R12": {k: f"out/geotiffs/r12_data_{k}/*.tif" for k in ("ndmi", "ndre", "evi")},
         "R13": {k: f"out/geotiffs/r13_data_{k}/*.tif" for k in ("ndmi", "ndre", "evi")}}
STORM_YM = "2025-07"

def summer_pre(ym): return "2022-01" <= ym <= "2024-12" and int(ym[5:]) in SUMMER
def summer_post(ym): return ym >= STORM_YM and int(ym[5:]) in SUMMER

def vh_stack(site, grid):
    labs, arrs = [], []
    for f in sorted(glob.glob(SITES[site]["sar"])):
        with rasterio.open(f) as ds:
            a = ds.read(2).astype("float32"); a[a == sf.FILL] = np.nan
        labs.append(ym_of(f)); arrs.append(a)
    return labs, np.stack(arrs)

def changepoint(labs, S, core):
    """Seasonally adjust (per calendar month, pre-storm years), then for every candidate break
    month t return the two-sample t statistic (after − before, min 6 months each side).
    Returns: t-stat at the storm month, the strongest |t| anywhere, its month, and the step there."""
    mon = np.array([int(l[5:]) for l in labs]); pre = np.array([l < STORM_YM for l in labs])
    A = S.copy()
    for m in range(1, 13):
        sel = (mon == m) & pre
        with warnings.catch_warnings():
            warnings.simplefilter("ignore"); A[mon == m] -= np.nanmean(S[sel], axis=0)
    n = len(labs); best_t = np.zeros(core.shape, "float32"); best_i = np.zeros(core.shape, "int16"); best_step = np.zeros(core.shape, "float32"); storm_t = None
    i_storm = labs.index(STORM_YM)
    for i in range(6, n - 6):
        if labs[i] < "2019-01": continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            b, a = A[:i], A[i:]
            mb, ma = np.nanmean(b, 0), np.nanmean(a, 0); vb, va = np.nanvar(b, 0), np.nanvar(a, 0)
            nb, na = np.sum(np.isfinite(b), 0), np.sum(np.isfinite(a), 0)
            t = (ma - mb) / np.sqrt(vb / np.maximum(nb, 1) + va / np.maximum(na, 1) + 1e-6)
        t = np.where(core, t, 0)
        if i == i_storm: storm_t = t
        upd = np.abs(t) > np.abs(best_t); best_t[upd] = t[upd]; best_i[upd] = i; best_step[upd] = (ma - mb)[upd]
    return storm_t, best_t, best_i, best_step

def texture(a):
    a = np.where(np.isfinite(a), a, np.nanmean(a))
    return uniform_filter(a, 3), generic_filter(a, np.std, 3)

def block_folds(shape, b=25, k=5, seed=0):
    r, c = np.indices(shape); blk = (r // b) * 1000 + (c // b); ids = np.unique(blk)
    rng = np.random.default_rng(seed); fold_of = dict(zip(ids, rng.integers(0, k, len(ids)))); return np.vectorize(fold_of.get)(blk)

def cv_auc(X, y, folds):
    p = np.zeros(len(y))
    for k in np.unique(folds):
        te = folds == k; m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=500, class_weight="balanced")).fit(X[~te], y[~te])
        p[te] = m.predict_proba(X[te])[:, 1]
    return roc_auc_score(y, p), p

def capture_curve(score, dens, core):
    ok = core & np.isfinite(score); s = score[ok]; d = dens[ok]; o = np.argsort(-s)
    frac_area = np.arange(1, len(o) + 1) / len(o); frac_stem = np.cumsum(d[o]) / d.sum(); return frac_area, frac_stem

def main():
    res = []; keep = {}
    for site in SITES:
        m = masks(site); g = m["grid"]; core = m["core"]; dens = m["dens"]
        F = {}
        F["lst"], _, _ = change_map(SITES[site]["lst"], 1, g, summer_pre, summer_post)
        F["ndvi"], _, _ = change_map(SITES[site]["ndvi"], 1, g, summer_pre, summer_post)
        for k, pat in EXTRA[site].items(): F[k], _, _ = change_map(pat, 1, g, summer_pre, summer_post)
        labs, S = vh_stack(site, g)
        i0, i1 = labs.index("2024-07"), labs.index(STORM_YM)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore"); F["vh"] = np.nanmean(S[i1:i1 + 12], 0) - np.nanmean(S[i0:i1], 0)
        F["vh_storm_t"], best_t, best_i, best_step = changepoint(labs, S, core)
        F["vh_best_t"] = best_t
        for k in ("ndvi", "vh", "lst"):
            F[f"{k}_mean3"], F[f"{k}_sd3"] = texture(F[k])
        # labels and folds
        wt, ctl = m["wt"], m["ctl"]; sel = (wt | ctl)
        for k in F: sel &= np.isfinite(F[k])
        y = wt[sel].astype(int); folds = block_folds(core.shape)[sel]
        sets = {"temperature": ["lst"], "NDVI": ["ndvi"], "radar VH (12-month means)": ["vh"], "radar VH change-point t at storm": ["vh_storm_t"],
                "three sensors": ["lst", "ndvi", "vh"], "+ NDMI, NDRE, EVI": ["lst", "ndvi", "vh", "ndmi", "ndre", "evi"],
                "+ change-point": ["lst", "ndvi", "vh", "ndmi", "ndre", "evi", "vh_storm_t"],
                "+ 3x3 texture": ["lst", "ndvi", "vh", "ndmi", "ndre", "evi", "vh_storm_t", "ndvi_mean3", "ndvi_sd3", "vh_mean3", "vh_sd3", "lst_mean3"]}
        for name, cols in sets.items():
            X = np.column_stack([F[c][sel] for c in cols])
            auc, p = cv_auc(X, y, folds) if len(cols) > 1 else (roc_auc_score(y, X[:, 0] * (1 if cols[0] == "lst" else -1)), None)
            res.append(dict(site=site, features=name, n_features=len(cols), auc=auc)); print(f"{site} {name:36s} AUC {auc:.3f}")
        # unsupervised: where does the strongest VH break fall?
        bm_wt = best_i[wt & core]; bm_ctl = best_i[ctl & core]
        near = lambda idx: np.mean([abs(i - i1) <= 2 for i in idx])
        print(f"{site}: strongest VH break within ±2 months of {STORM_YM}: windthrow cells {near(bm_wt):.0%}, canopy {near(bm_ctl):.0%}")
        res.append(dict(site=site, features="break within ±2 mo of storm, windthrow cells", n_features=0, auc=near(bm_wt)))
        res.append(dict(site=site, features="break within ±2 mo of storm, canopy cells", n_features=0, auc=near(bm_ctl)))
        # full-footprint fused score for maps and capture curves
        cols = sets["+ 3x3 texture"]; ok = core.copy()
        for c in cols: ok &= np.isfinite(F[c])
        Xall = np.column_stack([F[c][ok] for c in cols]); yall = wt[ok].astype(int)
        # fit on labelled cells (wt|ctl) only, predict everywhere
        lab = (wt | ctl)[ok]; mdl = make_pipeline(StandardScaler(), LogisticRegression(max_iter=500, class_weight="balanced")).fit(Xall[lab], yall[lab])
        fused = np.full(core.shape, np.nan, "float32"); fused[ok] = mdl.predict_proba(Xall)[:, 1]
        keep[site] = dict(fused=fused, dens=dens, core=core, best_i=best_i, labs=labs, i1=i1, wt=wt, ctl=ctl, F=F, grid=g, fp=m["fp"])
        # cross-site transfer stored for later
        keep[site]["X3"] = (np.column_stack([F[c][sel] for c in ["lst", "ndvi", "vh"]]), y)
    # transfer: train on one site, test on the other (three sensors)
    for a, b in (("R12", "R13"), ("R13", "R12")):
        Xa, ya = keep[a]["X3"]; Xb, yb = keep[b]["X3"]
        mdl = make_pipeline(StandardScaler(), LogisticRegression(max_iter=500, class_weight="balanced")).fit(Xa, ya)
        auc = roc_auc_score(yb, mdl.predict_proba(Xb)[:, 1]); res.append(dict(site=b, features=f"three sensors, trained on {a}", n_features=3, auc=auc)); print(f"train {a} → test {b}: AUC {auc:.3f}")
    with open(f"{OUT}/detection_upgrade.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(res[0])); w.writeheader(); w.writerows(res)
    # ---- figure
    fig = plt.figure(figsize=(18, 13), facecolor="white"); gs = fig.add_gridspec(3, 3, height_ratios=[1, 1, 1.15])
    ax = fig.add_subplot(gs[0, :2]); names = [r["features"] for r in res if r["site"] == "R12" and r["n_features"] > 0 and "trained" not in r["features"]]
    xs = np.arange(len(names)); wdt = 0.38
    for j, site in enumerate(SITES):
        vals = [next(r["auc"] for r in res if r["site"] == site and r["features"] == n) for n in names]
        ax.bar(xs + (j - 0.5) * wdt, vals, wdt, color=["#c8502a", "#357"][j], label=site)
        for x, v in zip(xs, vals): ax.text(x + (j - 0.5) * wdt, v + 0.005, f"{v:.2f}", ha="center", fontsize=7.5)
    ax.set_xticks(xs); ax.set_xticklabels(names, rotation=20, ha="right", fontsize=8.5); ax.set_ylim(0.5, 1.0); ax.set_ylabel("AUC, spatially blocked 5-fold CV")
    ax.set_title("Detection AUC by feature set (windthrow-dense vs stem-free cells, 20 m)", loc="left", fontsize=10); ax.legend(frameon=False); ax.grid(alpha=0.25, axis="y")
    ax = fig.add_subplot(gs[0, 2])
    for site, col in zip(SITES, ("#c8502a", "#357")):
        k = keep[site]; x = np.array([sf.xval(l) for l in k["labs"]])
        for cls, ls in (("wt", "-"), ("ctl", ":")):
            idx = k["best_i"][k[cls] & k["core"]]; h = np.bincount(idx, minlength=len(x)) / max(len(idx), 1)
            ax.plot(x, h, color=col, ls=ls, lw=1.5, label=f"{site} {'windthrow' if cls == 'wt' else 'canopy'}")
    ax.axvline(2025.5, color="#c44", lw=1, ls="--"); ax.set_xlim(2019, 2026.8); ax.set_ylabel("share of cells"); ax.set_xlabel("month of the strongest VH break")
    ax.set_title("Unsupervised: when does each cell's VH series break?", loc="left", fontsize=10); ax.legend(frameon=False, fontsize=8); ax.grid(alpha=0.25)
    for j, site in enumerate(SITES):
        k = keep[site]; bb = rasterio.transform.array_bounds(*k["core"].shape, k["grid"]["transform"]); ext = (bb[0], bb[2], bb[1], bb[3])
        ax = fig.add_subplot(gs[1, j]); im = ax.imshow(np.where(k["core"], k["fused"], np.nan), extent=ext, cmap="magma_r", vmin=0, vmax=1); k["fp"].boundary.plot(ax=ax, color="k", lw=0.5)
        ax.set_title(f"{site}: fused windthrow probability (all features)", loc="left", fontsize=10); ax.set_xticks([]); ax.set_yticks([]); fig.colorbar(im, ax=ax, shrink=0.8)
    ax = fig.add_subplot(gs[1, 2]); k = keep["R12"]; bb = rasterio.transform.array_bounds(*k["core"].shape, k["grid"]["transform"]); ext = (bb[0], bb[2], bb[1], bb[3])
    im = ax.imshow(np.where(k["core"], k["dens"], np.nan), extent=ext, cmap="magma_r", vmin=0, vmax=60); k["fp"].boundary.plot(ax=ax, color="k", lw=0.5)
    ax.set_title("R12: predicted stem per cell, m (reference)", loc="left", fontsize=10); ax.set_xticks([]); ax.set_yticks([]); fig.colorbar(im, ax=ax, shrink=0.8)
    for j, site in enumerate(SITES):
        ax = fig.add_subplot(gs[2, j]); k = keep[site]
        for name, sc, col in (("fused, all features", k["fused"], "#000"), ("NDVI change alone", -k["F"]["ndvi"], "#2a7f4f"), ("radar VH change alone", -k["F"]["vh"], "#357"), ("temperature change alone", k["F"]["lst"], "#c8502a")):
            fa, fs = capture_curve(sc, k["dens"], k["core"]); ax.plot(fa, fs, color=col, lw=1.8, label=name)
            i10 = np.searchsorted(fa, 0.10); print(f"{site} {name:26s}: flag 10 % of footprint → {fs[i10]:.0%} of predicted stem; 25 % → {fs[np.searchsorted(fa, 0.25)]:.0%}")
        ax.plot([0, 1], [0, 1], color="#888", lw=1, ls="--"); ax.set_xlabel("share of footprint flagged (cells ranked by score)"); ax.set_ylabel("share of all predicted stem captured")
        ax.set_title(f"{site}: capture curve — how much of the damage a flagged area contains", loc="left", fontsize=10); ax.legend(frameon=False, fontsize=8, loc="lower right"); ax.grid(alpha=0.25)
    ax = fig.add_subplot(gs[2, 2]); ax.axis("off")
    tr = [r for r in res if "trained" in r["features"]]
    txt = "Cross-site transfer (three sensors):\n" + "\n".join(f"  {r['features'].replace('three sensors, ', '')} → test {r['site']}: AUC {r['auc']:.2f}" for r in tr)
    txt += "\n\nCV folds are 500 m blocks, so neighbouring cells\nnever sit in train and test together.\nLogistic regression, class-balanced."
    ax.text(0, 0.95, txt, va="top", fontsize=10, family="monospace")
    fig.suptitle("Pushing detection: sensor fusion, change-point radar, texture, and patch capture", x=0.01, ha="left", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(f"{OUT}/detection_upgrade.png", dpi=110); print("wrote", f"{OUT}/detection_upgrade.png")

if __name__ == "__main__":
    main()
