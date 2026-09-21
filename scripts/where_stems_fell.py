"""Report figure: where the stems fell (predicted stem density) next to what the
sensors saw there — post-storm thermal departure change and VH backscatter change —
for R12 and R13. Also prints the affected area."""
import glob, os, re, sys, warnings
import numpy as np, rasterio, geopandas as gpd, matplotlib
from rasterio.features import geometry_mask
from scipy import ndimage
matplotlib.use("Agg"); import matplotlib.pyplot as plt
sys.path.insert(0, "docs/experiments/windthrow")
RF = "/Users/christian/work/hnee/GEE_animation-rf-sharpening"
SITES = {"R12": dict(fp="docs/aoi/r12/r12_footprint.geojson", lst=f"{RF}/out/geotiffs/r12_focus_lst_rf_delta_local_10yr", sar="out/sar_r12", ha=515),
         "R13": dict(fp="docs/aoi/r13/r13_footprint.geojson", lst=f"{RF}/out/geotiffs/r13_focus_lst_rf_delta_local_10yr", sar="out/sar_r13", ha=1033)}
def stack(pattern, band, lo, hi, months=(4,5,6,7,8,9)):
    arrs = []
    for f in sorted(glob.glob(pattern)):
        m = re.search(r"(\d{4}-\d{2})\.tif$", f).group(1)
        if not (lo <= m <= hi and int(m[5:]) in months): continue
        with rasterio.open(f) as ds:
            a = ds.read(band).astype("float32"); a[(a == ds.nodata) | (a == -9999.0)] = np.nan; arrs.append(a)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore"); return np.nanmean(np.stack(arrs), axis=0)
fig, axes = plt.subplots(2, 3, figsize=(17, 10.5), facecolor="white")
for row, (site, c) in enumerate(SITES.items()):
    os.environ["WT_SITE"] = site
    import importlib, windthrow_vs_delta; importlib.reload(windthrow_vs_delta)
    from windthrow_vs_delta import stem_density, WT_MIN_M
    lst_files = sorted(glob.glob(f"{c['lst']}/*.tif"))
    with rasterio.open(lst_files[0]) as t:
        dens, street = stem_density(t)
        fp = gpd.read_file(c["fp"]).to_crs(t.crs)
        inside = geometry_mask(list(fp.geometry), out_shape=(t.height, t.width), transform=t.transform, invert=True)
    core = inside & ~street
    lst_chg = stack(f"{c['lst']}/*.tif", 1, "2025-07", "2026-12") - stack(f"{c['lst']}/*.tif", 1, "2022-01", "2024-12")
    sar_chg = stack(f"{c['sar']}/*.tif", 2, "2025-07", "2026-12") - stack(f"{c['sar']}/*.tif", 2, "2022-01", "2024-12")
    # the SAR export grid can differ from the LST grid by a row or column (EE bbox handling): crop to the common shape
    h, w = min(sar_chg.shape[0], inside.shape[0]), min(sar_chg.shape[1], inside.shape[1])
    sar_chg = np.full(inside.shape, np.nan, 'float32'); sar_chg[:h, :w] = (stack(f"{c['sar']}/*.tif", 2, "2025-07", "2026-12") - stack(f"{c['sar']}/*.tif", 2, "2022-01", "2024-12"))[:h, :w]
    wt = core & (dens >= WT_MIN_M); lab, n = ndimage.label(wt)
    cells_ha = 0.04; area = wt.sum() * cells_ha; total_m = dens[core].sum()
    print(f"{site}: windthrow-dense cells {wt.sum()} = {area:.0f} ha of {c['ha']} ({area/c['ha']*100:.1f}%), {n} clusters, "
          f"stem length inside dense cells {dens[wt].sum()/total_m*100:.0f}% of all predicted stem, cells with any stem {(core & (dens > 0)).sum()*cells_ha:.0f} ha")
    panels = [(np.where(inside, dens, np.nan), f"{site}: predicted fallen stem, m per 20 m cell (2025 flight)", "Greys", 0, 60, "m / cell"),
              (np.where(inside, lst_chg, np.nan), f"{site}: temperature departure, post-storm summers minus 2022–24", "RdBu_r", -3, 3, "K"),
              (np.where(inside, sar_chg, np.nan), f"{site}: VH backscatter, post-storm summers minus 2022–24", "RdBu_r", -2, 2, "dB")]
    for ax, (img, title, cmap, lo, hi, unit) in zip(axes[row], panels):
        im = ax.imshow(img, cmap=cmap, vmin=lo, vmax=hi, interpolation="nearest")
        ax.contour(wt.astype(float), levels=[0.5], colors="#c44" if cmap == "Greys" else "k", linewidths=0.5)
        ax.set_title(title, loc="left", fontsize=10); ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values(): s.set_visible(False)
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02).set_label(unit)
fig.suptitle("Where the stems fell, and what the sensors saw there · outlines = windthrow-dense cells (≥ 20 m stem)", x=0.01, ha="left", fontsize=12)
fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(sys.argv[1], dpi=110); print("wrote", sys.argv[1])
