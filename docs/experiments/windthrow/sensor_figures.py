"""Cross-sensor figures for the report: the same windthrow event seen by
temperature, NDVI and radar. Reads the existing GeoTIFF runs (thermal delta,
data-only NDVI), the SAR spike's monthly tifs, and the windthrow series CSVs.

    python docs/experiments/windthrow/sensor_figures.py <sar worktree> <out dir>
"""
import csv, glob, os, re, sys, warnings
import numpy as np, rasterio, geopandas as gpd
from rasterio.features import geometry_mask
from rasterio.warp import reproject, Resampling
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from sklearn.metrics import roc_auc_score, roc_curve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SAR_WT, OUT = sys.argv[1], sys.argv[2]
STORM = 2025.5; FILL = -9999.0
SITES = {"R12": dict(fp="docs/aoi/r12/r12_footprint.geojson", lst="out/geotiffs/r12_focus_lst_rf_delta_local_10yr/*.tif",
                     ndvi="out/geotiffs/r12_data_ndvi/*.tif", sar=f"{SAR_WT}/out/sar_r12/*.tif",
                     series="docs/experiments/windthrow/windthrow_series.csv"),
         "R13": dict(fp="docs/aoi/r13/r13_footprint.geojson", lst="out/geotiffs/r13_zoom_lst_rf_delta_local_10yr/*.tif",
                     ndvi="out/geotiffs/r13_data_ndvi/*.tif", sar=f"{SAR_WT}/out/sar_r13/*.tif",
                     series="docs/experiments/windthrow/r13/r13_windthrow_series.csv")}
COL = {"lst": "#c8502a", "ndvi": "#2a7f4f", "vh": "#357"}
LAB = {"lst": "surface temperature Δ, K", "ndvi": "NDVI Δ", "vh": "radar VH Δ, dB"}
SUMMER = (5, 6, 7, 8, 9)

def ym_of(f): return re.search(r"(\d{4}-\d{2})\.tif$", f).group(1)
def xval(ym): return int(ym[:4]) + (int(ym[5:7]) - 0.5) / 12

def masks(site):
    os.environ["WT_SITE"] = site; sys.modules.pop("windthrow_vs_delta", None)
    from windthrow_vs_delta import stem_density, WT_MIN_M
    tmpl = sorted(glob.glob(SITES[site]["sar"]))[0]
    with rasterio.open(tmpl) as t:
        fp = gpd.read_file(SITES[site]["fp"]).to_crs(t.crs)
        inside = geometry_mask(list(fp.geometry), out_shape=(t.height, t.width), transform=t.transform, invert=True)
        dens, street = stem_density(t)
        grid = dict(crs=t.crs, transform=t.transform, shape=(t.height, t.width))
    core = inside & ~street
    return dict(core=core, wt=core & (dens >= WT_MIN_M), ctl=core & (dens == 0), dens=dens, grid=grid, fp=fp)

def on_grid(f, band, grid):
    with rasterio.open(f) as ds:
        a = ds.read(band).astype("float32"); nd = ds.nodata
        a[a == FILL] = np.nan
        if nd is not None: a[a == nd] = np.nan
        out = np.full(grid["shape"], np.nan, "float32")
        reproject(a, out, src_transform=ds.transform, src_crs=ds.crs, dst_transform=grid["transform"], dst_crs=grid["crs"],
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.average)
    return out

def change_map(pattern, band, grid, pre, post):
    """Mean of months in `post` minus mean of months in `pre` (each a predicate on 'YYYY-MM')."""
    a, b = [], []
    for f in sorted(glob.glob(pattern)):
        ym = ym_of(f)
        if pre(ym): a.append(on_grid(f, band, grid))
        elif post(ym): b.append(on_grid(f, band, grid))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore"); return np.nanmean(np.stack(b), 0) - np.nanmean(np.stack(a), 0), len(a), len(b)

def series_ndvi(site, m):
    labs, d = [], []
    for f in sorted(glob.glob(SITES[site]["ndvi"])):
        a = on_grid(f, 1, m["grid"]); ok = np.isfinite(a)
        if (ok & m["wt"]).sum() < 0.5 * m["wt"].sum(): continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore"); d.append(float(np.nanmean(a[m["wt"]]) - np.nanmean(a[m["ctl"]])))
        labs.append(ym_of(f))
    return labs, d

def series_csv(path, col="diff_K"):
    rows = list(csv.DictReader(open(path))); return [r["month"] for r in rows], [float(r[col]) for r in rows]

def series_vh(site):
    by = {}
    for r in csv.DictReader(open(f"{SAR_WT}/docs/experiments/sar/sar_tegel_series.csv")):
        if r["site"] == site and r["vh"]: by.setdefault(r["ym"], {})[r["kind"]] = float(r["vh"])
    labs = sorted(k for k, v in by.items() if len(v) == 2); return labs, [by[k]["damaged"] - by[k]["intact"] for k in labs]

def fig_series(M):
    fig, axes = plt.subplots(3, 2, figsize=(16, 9), sharex=True, facecolor="white")
    for j, site in enumerate(SITES):
        m = M[site]; data = {"lst": series_csv(SITES[site]["series"]), "ndvi": series_ndvi(site, m), "vh": series_vh(site)}
        for i, k in enumerate(("lst", "ndvi", "vh")):
            ax = axes[i, j]; labs, d = data[k]; x = np.array([xval(l) for l in labs]); y = np.array(d)
            summer = np.array([int(l[5:7]) in SUMMER for l in labs])
            ax.axhline(0, color="#888", lw=1); ax.axvline(STORM, color="#c44", lw=1.2, ls="--")
            ax.plot(x, y, color=COL[k], lw=1.1); ax.scatter(x[summer], y[summer], s=16, color=COL[k], zorder=3)
            ax.scatter(x[~summer], y[~summer], s=16, facecolor="white", edgecolor=COL[k], zorder=3)
            pre = y[summer & (x < STORM)]; post = y[summer & (x > STORM)]
            ax.text(0.01, 0.95, f"{site} · {LAB[k]}\nsummer mean before {pre.mean():+.2f} → after {post.mean():+.2f}", transform=ax.transAxes, va="top", fontsize=9)
            ax.grid(alpha=0.25); [ax.spines[s].set_visible(False) for s in ("top", "right")]
            if j == 0: ax.set_ylabel(LAB[k])
    for ax in axes[-1]: ax.set_xlabel("year")
    fig.suptitle("One event, three sensors: windthrow cells minus intact canopy, monthly (filled = May–September), storm dashed", x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(f"{OUT}/sensor_comparison_series.png", dpi=110); plt.close(fig)

def changes(site, m):
    g = m["grid"]; s = SITES[site]
    summer_pre = lambda ym: "2022-01" <= ym <= "2024-12" and int(ym[5:]) in SUMMER
    summer_post = lambda ym: ym >= "2025-07" and int(ym[5:]) in SUMMER
    lst, a1, b1 = change_map(s["lst"], 1, g, summer_pre, summer_post)
    ndvi, a2, b2 = change_map(s["ndvi"], 1, g, summer_pre, summer_post)
    vh, a3, b3 = change_map(s["sar"], 2, g, lambda ym: "2024-07" <= ym <= "2025-06", lambda ym: "2025-07" <= ym <= "2026-06")
    print(f"{site}: months pre/post  lst {a1}/{b1}  ndvi {a2}/{b2}  vh {a3}/{b3}")
    return {"lst": lst, "ndvi": ndvi, "vh": vh}

def fig_roc(M, C):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5), facecolor="white")
    for ax, site in zip(axes, SITES):
        m = M[site]
        for k in ("lst", "ndvi", "vh"):
            ch = C[site][k]; ok = np.isfinite(ch)
            lab = np.r_[np.ones((ok & m["wt"]).sum()), np.zeros((ok & m["ctl"]).sum())]
            sc = np.r_[ch[ok & m["wt"]], ch[ok & m["ctl"]]] * (1 if k == "lst" else -1)   # warmer / less green / darker = damage
            fpr, tpr, _ = roc_curve(lab, sc); auc = roc_auc_score(lab, sc)
            ax.plot(fpr, tpr, color=COL[k], lw=2, label=f"{LAB[k].split(',')[0]} · AUC {auc:.2f}")
            print(f"{site} {k}: AUC {auc:.3f}  n wt {int((ok & m['wt']).sum())}  n ctl {int((ok & m['ctl']).sum())}")
        ax.plot([0, 1], [0, 1], color="#888", lw=1, ls="--"); ax.set_xlabel("false positive rate (intact canopy flagged)"); ax.set_ylabel("true positive rate (windthrow cells found)")
        ax.set_title(f"{site}: per-cell change after vs before the storm as a windthrow detector", loc="left", fontsize=10); ax.legend(loc="lower right", frameon=False)
        ax.grid(alpha=0.25); [ax.spines[s].set_visible(False) for s in ("top", "right")]
    fig.suptitle("ROC curves: windthrow-dense cells (≥ 20 m stem) against stem-free canopy, 20 m cells", x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95)); fig.savefig(f"{OUT}/roc_curves.png", dpi=120); plt.close(fig)

def fig_dose(M, C):
    edges = [(0, 0, "none"), (0, 10, "0–10 m"), (10, 30, "10–30 m"), (30, 60, "30–60 m"), (60, 1e9, "> 60 m")]
    fig, axes = plt.subplots(2, 3, figsize=(15, 8), facecolor="white")
    for i, site in enumerate(SITES):
        m = M[site]; dens = m["dens"]
        for j, k in enumerate(("lst", "ndvi", "vh")):
            ax = axes[i, j]; ch = C[site][k]; ok = np.isfinite(ch) & m["core"]
            means, err, ns = [], [], []
            for lo, hi, _ in edges:
                sel = ok & ((dens == 0) if hi == 0 else ((dens > lo) & (dens <= hi)))
                v = ch[sel]; means.append(v.mean()); err.append(1.96 * v.std() / np.sqrt(max(len(v), 1))); ns.append(len(v))
            xs = np.arange(len(edges)); ax.bar(xs, means, yerr=err, color=COL[k], alpha=0.85, capsize=3)
            ax.set_xticks(xs); ax.set_xticklabels([f"{e[2]}\nn={n:,}" for e, n in zip(edges, ns)], fontsize=8)
            ax.axhline(0, color="#888", lw=1); ax.set_title(f"{site}: {LAB[k]}, after − before", loc="left", fontsize=10)
            ax.grid(alpha=0.25, axis="y"); [ax.spines[s].set_visible(False) for s in ("top", "right")]
    fig.suptitle("Dose–response by predicted stem per 20 m cell: the more stem, the larger the change in every sensor (mean ± 95 % CI)", x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.96)); fig.savefig(f"{OUT}/dose_response.png", dpi=110); plt.close(fig)

def fig_seasonality():
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True, facecolor="white")
    for ax, site in zip(axes, SITES):
        labs, d = series_csv(SITES[site]["series"]); mon = np.array([int(l[5:7]) for l in labs]); x = np.array([xval(l) for l in labs]); y = np.array(d)
        for sel, col, name in ((x < STORM, "#888", "before (2017 – Jun 2025)"), (x > STORM, "#c8502a", "after (Jul 2025 – 2026)")):
            mu = [y[sel & (mon == k)].mean() if (sel & (mon == k)).any() else np.nan for k in range(1, 13)]
            sd = [y[sel & (mon == k)].std() if (sel & (mon == k)).sum() > 1 else 0 for k in range(1, 13)]
            ax.errorbar(range(1, 13), mu, yerr=sd, color=col, marker="o", lw=1.5, capsize=3, label=name)
        ax.axhline(0, color="#888", lw=1); ax.set_xticks(range(1, 13)); ax.set_xticklabels(list("JFMAMJJASOND"))
        ax.set_title(f"{site}: windthrow cells minus canopy, by calendar month", loc="left", fontsize=10); ax.grid(alpha=0.25)
        ax.legend(frameon=False, fontsize=9); [ax.spines[s].set_visible(False) for s in ("top", "right")]
    axes[0].set_ylabel("temperature contrast, K")
    fig.suptitle("The thermal signal is a summer signal: opened canopy heats only under high sun", x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.94)); fig.savefig(f"{OUT}/thermal_seasonality.png", dpi=120); plt.close(fig)

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    M = {s: masks(s) for s in SITES}
    fig_series(M); fig_seasonality()
    C = {s: changes(s, M[s]) for s in SITES}
    fig_roc(M, C); fig_dose(M, C)
    print("wrote", OUT)
