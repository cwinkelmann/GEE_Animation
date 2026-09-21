"""SPIKE (throwaway): does Sentinel-1 backscatter show the R12 windthrow?

Monthly medians of S1 GRD (IW, both orbit passes) VV and VH in dB, plus the
cross-ratio VH − VV, exported from Earth Engine at 20 m over the R12 zoom frame
(2017-01 .. 2026-09) and cached under out/sar/. Then the same test as the
thermal work: windthrow cells (>= 20 m predicted stem per 20 m cell) minus
stem-free canopy per month, post−pre change maps, and the AUC of the change
for windthrow-dense cells.

    python scripts/sar_windthrow_spike.py fetch     # EE exports (cached)
    python scripts/sar_windthrow_spike.py analyse <out png>
"""
import glob, os, re, sys, urllib.request, warnings
import numpy as np
FRAME = [13.2162, 52.5680, 13.2862, 52.6102]     # r12 zoom frame (footprint + 0.4 km)
CRS, SCALE = "EPSG:32633", 20
START, END = "2017-01", "2026-09"
OUT = "out/sar"
FILL = -9999.0

def months():
    y, m = int(START[:4]), int(START[5:]); ye, me = int(END[:4]), int(END[5:])
    while (y, m) <= (ye, me):
        yield f"{y}-{m:02d}", f"{y}-{m:02d}-01", (f"{y+1}-01-01" if m == 12 else f"{y}-{m+1:02d}-01")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

def fetch():
    import ee
    from gee_animation import auth
    auth.init("hnee-331218"); ee.data.setDeadline(120_000)
    os.makedirs(OUT, exist_ok=True)
    region = ee.Geometry.Rectangle(FRAME)
    s1 = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(region)
          .filter(ee.Filter.eq("instrumentMode", "IW"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
          .select(["VV", "VH"]))
    for ym, s, e in months():
        path = f"{OUT}/{ym}.tif"
        if os.path.exists(path): continue
        coll = s1.filterDate(s, e)
        n = coll.size().getInfo()
        if n == 0:
            print(ym, "no scenes"); continue
        med = coll.median()
        img = (med.addBands(med.select("VH").subtract(med.select("VV")).rename("ratio"))
               .set("n", n).unmask(FILL).toFloat())
        url = img.getDownloadURL({"region": region, "crs": CRS, "scale": SCALE, "format": "GEO_TIFF"})
        data = urllib.request.urlopen(url, timeout=300).read()
        open(path, "wb").write(data); print(ym, f"{n} scenes, {len(data)/1e3:.0f} kB", flush=True)

def analyse(out_png):
    import rasterio, geopandas as gpd, matplotlib
    from rasterio.features import geometry_mask
    from sklearn.metrics import roc_auc_score
    matplotlib.use("Agg"); import matplotlib.pyplot as plt
    sys.path.insert(0, "docs/experiments/windthrow")
    from windthrow_vs_delta import stem_density, WT_MIN_M
    files = sorted(glob.glob(f"{OUT}/*.tif"))
    with rasterio.open(files[0]) as t:
        dens, street = stem_density(t)
        fp = gpd.read_file("docs/aoi/r12/r12_footprint.geojson").to_crs(t.crs)
        inside = geometry_mask(list(fp.geometry), out_shape=(t.height, t.width), transform=t.transform, invert=True)
    core = inside & ~street; wt, ctl = core & (dens >= WT_MIN_M), core & (dens == 0)
    bands = ["VV", "VH", "VH − VV"]; labs, vals = [], {b: [] for b in bands}; stack = {}
    for f in files:
        lab = re.search(r"(\d{4}-\d{2})\.tif$", f).group(1)
        with rasterio.open(f) as ds:
            a = ds.read().astype("float32"); a[a == FILL] = np.nan
        stack[lab] = a; labs.append(lab)
        for i, b in enumerate(bands):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore"); vals[b].append(float(np.nanmean(a[i][wt]) - np.nanmean(a[i][ctl])))
    x = np.array([int(l[:4]) + (int(l[5:7]) - 0.5) / 12 for l in labs])
    summer = np.array([int(l[5:7]) in (5, 6, 7, 8, 9) for l in labs])
    pre_keys = [l for l in labs if "2022-04" <= l <= "2024-09" and int(l[5:7]) in (4,5,6,7,8,9)]
    post_keys = [l for l in labs if l >= "2025-07" and int(l[5:7]) in (4,5,6,7,8,9)]
    fig, axes = plt.subplots(2, 3, figsize=(17, 9), facecolor="white", gridspec_kw=dict(height_ratios=[1, 1.1]))
    for i, b in enumerate(bands):
        y = np.array(vals[b]); ax = axes[0, i]
        ax.axhline(0, color="#888", lw=1); ax.axvline(2025.5, color="#888", lw=1, ls="--")
        ax.plot(x, y, color="#357", lw=1.2); ax.scatter(x[summer], y[summer], s=18, color="#357", zorder=3)
        ax.scatter(x[~summer], y[~summer], s=18, facecolor="white", edgecolor="#357", zorder=3)
        pre = y[(x < 2025.5)]; post = y[x >= 2025.5]
        ax.set_title(f"{b}: windthrow − canopy, dB · before {pre.mean():+.2f} → after {post.mean():+.2f}", loc="left", fontsize=10); ax.grid(alpha=0.25)
        for s in ("top", "right"): ax.spines[s].set_visible(False)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            chg = np.nanmean(np.stack([stack[k][i] for k in post_keys]), 0) - np.nanmean(np.stack([stack[k][i] for k in pre_keys]), 0)
        ok = core & np.isfinite(chg)
        auc = roc_auc_score((dens[ok] >= WT_MIN_M).astype(int), chg[ok])
        auc = max(auc, 1 - auc)
        ax2 = axes[1, i]; lim = 2.0
        im = ax2.imshow(np.where(inside, chg, np.nan), cmap="RdBu_r", vmin=-lim, vmax=lim, interpolation="nearest")
        ax2.set_title(f"{b}: post − pre change · AUC for windthrow {auc:.2f} · wt {chg[ok & (dens >= WT_MIN_M)].mean():+.2f} vs canopy {chg[ok & (dens == 0)].mean():+.2f} dB", loc="left", fontsize=9)
        ax2.set_xticks([]); ax2.set_yticks([])
        for s in ax2.spines.values(): s.set_visible(False)
        fig.colorbar(im, ax=ax2, fraction=0.046, pad=0.02).set_label("dB")
        print(f"{b:8s} months {len(labs)} | windthrow−canopy before {pre.mean():+.2f} after {post.mean():+.2f} dB | post−pre change: windthrow-dense {chg[ok & (dens >= WT_MIN_M)].mean():+.2f} vs stem-free {chg[ok & (dens == 0)].mean():+.2f} dB | AUC {auc:.3f}")
    fig.suptitle("R12 · Sentinel-1 GRD monthly median (IW, both orbits) vs predicted windthrow", x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out_png, dpi=110); print("wrote", out_png)

if __name__ == "__main__":
    {"fetch": lambda: fetch(), "analyse": lambda: analyse(sys.argv[2])}[sys.argv[1]]()
