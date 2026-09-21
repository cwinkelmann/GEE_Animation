"""SPIKE: at what block size does the per-block VH step separate damaged from
intact parts *within* Tegel? Blocks of b x b 20 m cells (b = 1..25 -> 20..500 m);
per block: VH step (12 months after 2025-07 minus 12 before, minus the median
step of stem-free blocks so intact ~ 0) and tegel-unet stems per hectare.

    python scripts/sar_block_scale.py <out png> <out csv>
"""
import csv, glob, os, re, sys, warnings
import numpy as np

STORM = "2025-07"; FILL = -9999.0
SCALES = [1, 3, 6, 12, 25]          # 20, 60, 120, 240, 500 m
TOP = 0.10                          # "damaged" = densest 10 % of blocks; "intact" = below the median density

def block(a, b, fn=np.nanmean):
    h, w = a.shape; H, W = -(-h // b) * b, -(-w // b) * b
    p = np.full((H, W), np.nan, "float32"); p[:h, :w] = a
    with warnings.catch_warnings():
        warnings.simplefilter("ignore"); return fn(p.reshape(H // b, b, W // b, b), axis=(1, 3))

def site_data(site):
    import rasterio, geopandas as gpd
    from rasterio.features import geometry_mask, rasterize
    from rasterio.enums import MergeAlg
    os.environ["WT_SITE"] = site
    for m in ("windthrow_vs_delta", "sar_windthrow_spike"): sys.modules.pop(m, None)
    sys.path.insert(0, "docs/experiments/windthrow"); sys.path.insert(0, "scripts")
    from windthrow_vs_delta import stem_density, STEMS
    from sar_windthrow_spike import CFG, OUT
    files = sorted(glob.glob(f"{OUT}/*.tif"))
    with rasterio.open(files[0]) as t:
        fp = gpd.read_file(CFG["footprint"]).to_crs(t.crs)
        inside = geometry_mask(list(fp.geometry), out_shape=(t.height, t.width), transform=t.transform, invert=True)
        _, street = stem_density(t)
        st = gpd.read_file(STEMS, layer="stems").to_crs(t.crs)
        counts = rasterize([(g, 1) for g in st.geometry.centroid], out_shape=(t.height, t.width), transform=t.transform,
                           merge_alg=MergeAlg.add, dtype="float32")
        bounds, res = t.bounds, t.res[0]
    core = inside & ~street
    pre, post = [], []
    for f in files:
        ym = re.search(r"(\d{4}-\d{2})\.tif$", f).group(1)
        y, m = int(ym[:4]), int(ym[5:]); sy, sm = int(STORM[:4]), int(STORM[5:])
        k = (y - sy) * 12 + (m - sm)                  # months since the storm month
        if -12 <= k < 12:
            with rasterio.open(f) as ds:
                vh = ds.read(2).astype("float32"); vh[vh == FILL] = np.nan
            (pre if k < 0 else post).append(vh)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        step20 = np.nanmean(np.stack(post), 0) - np.nanmean(np.stack(pre), 0)
    step20[~core] = np.nan
    return dict(step=step20, counts=np.where(core, counts, 0.0), core=core.astype("float32"), bounds=bounds, res=res,
                n_pre=len(pre), n_post=len(post), fp=fp)

def per_scale(d, b):
    from sklearn.metrics import roc_auc_score
    from scipy.stats import spearmanr
    frac = block(d["core"], b); step = block(d["step"], b)
    stems_ha = block(d["counts"], b, np.nansum) / (frac * (b * d["res"]) ** 2 / 1e4)
    ok = (frac >= 0.5) & np.isfinite(step)
    q_top, q_med = np.nanquantile(stems_ha[ok], 1 - TOP), np.nanmedian(stems_ha[ok])
    dense = ok & (stems_ha >= q_top); sparse = ok & (stems_ha <= q_med)
    step = step - np.nanmedian(step[sparse])
    rs = spearmanr(stems_ha[ok], step[ok])[0]
    lab = np.r_[np.ones(dense.sum()), np.zeros(sparse.sum())]; sc = -np.r_[step[dense], step[sparse]]
    auc = roc_auc_score(lab, sc) if dense.sum() > 1 and sparse.sum() > 1 else np.nan
    return dict(b=b, m=b * d["res"], n=int(ok.sum()), n_dense=int(dense.sum()), dense_cut=float(q_top), n_sparse=int(sparse.sum()), spearman=rs, auc=auc,
                dense_step=float(np.nanmean(step[dense])) if dense.any() else np.nan, sparse_sd=float(np.nanstd(step[sparse])),
                step=np.where(ok, step, np.nan), stems_ha=np.where(ok, stems_ha, np.nan))

def main(out_png, out_csv):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from matplotlib.colors import TwoSlopeNorm
    rows = []; show = {}
    print(f"{'site':5s} {'block m':>8s} {'blocks':>7s} {'top10%':>7s} {'>=/ha':>6s} {'intact':>6s} {'spearman':>9s} {'AUC':>6s} {'top10 step':>11s} {'intact sd':>10s}")
    for site in ("R12", "R13"):
        d = site_data(site)
        for b in SCALES:
            r = per_scale(d, b); rows.append(dict(site=site, **{k: v for k, v in r.items() if k not in ("step", "stems_ha")}))
            print(f"{site:5s} {r['m']:8.0f} {r['n']:7d} {r['n_dense']:7d} {r['dense_cut']:6.0f} {r['n_sparse']:6d} {r['spearman']:+9.2f} {r['auc']:6.2f} {r['dense_step']:+11.2f} {r['sparse_sd']:10.2f}")
            if b == 12: show[site] = (d, r)
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    fig, axes = plt.subplots(2, 3, figsize=(18, 10), facecolor="white")
    for i, site in enumerate(("R12", "R13")):
        d, r = show[site]; bb = d["bounds"]; ext = (bb.left, bb.right, bb.bottom, bb.top)
        ax = axes[i, 0]; im = ax.imshow(r["stems_ha"], extent=ext, cmap="YlOrRd", vmin=0, vmax=200); d["fp"].boundary.plot(ax=ax, color="k", lw=0.6)
        ax.set_title(f"{site}: predicted fallen stems per ha, {r['m']:.0f} m blocks", loc="left", fontsize=10); fig.colorbar(im, ax=ax, shrink=0.7, label="stems / ha")
        ax = axes[i, 1]; im = ax.imshow(r["step"], extent=ext, cmap="RdBu", norm=TwoSlopeNorm(0, -1.5, 1.5)); d["fp"].boundary.plot(ax=ax, color="k", lw=0.6)
        ax.set_title(f"{site}: VH step after storm, {r['m']:.0f} m blocks (intact median = 0)", loc="left", fontsize=10); fig.colorbar(im, ax=ax, shrink=0.7, label="dB")
        for ax in axes[i, :2]: ax.set_xticks([]); ax.set_yticks([])
        ax = axes[i, 2]; ok = np.isfinite(r["step"]) & np.isfinite(r["stems_ha"])
        ax.scatter(r["stems_ha"][ok] + 1, r["step"][ok], s=14, color="#357", alpha=0.7); ax.set_xscale("log"); ax.axhline(0, color="#888", lw=1)
        ax.axvline(r["dense_cut"] + 1, color="#c44", lw=0.8, ls="--")
        ax.set_xlabel("stems / ha + 1 (log)"); ax.set_ylabel("VH step, dB"); ax.grid(alpha=0.25)
        ax.set_title(f"{site}: {r['n']} blocks · Spearman {r['spearman']:+.2f} · AUC densest 10 % (≥ {r['dense_cut']:.0f}/ha) vs below-median {r['auc']:.2f}", loc="left", fontsize=10)
        for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    fig.suptitle("Sentinel-1 VH step (12 months after 2025-07 minus 12 before) vs windthrow density, Tegel footprints in 240 m blocks", x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out_png, dpi=110); print("wrote", out_png)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
