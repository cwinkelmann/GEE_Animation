"""Is the R13 pre-storm backscatter drift real? Show the ABSOLUTE backscatter of the
windthrow stands and of the canopy separately, the scene/orbit mix per month, and the
windthrow − canopy difference within a single orbit pass (ascending and descending
exported separately), so an orbit-mix artefact cannot masquerade as a trend.

    WT_SITE=R13 python scripts/sar_canopy_check.py fetch      # per-orbit exports (cached)
    WT_SITE=R13 python scripts/sar_canopy_check.py plot <out png>
"""
import glob, os, re, sys, urllib.request, warnings
import numpy as np
sys.path.insert(0, "scripts"); sys.path.insert(0, "docs/experiments/windthrow")
from sar_windthrow_spike import SITES, SITE, CFG, FRAME, CRS, SCALE, START, END, FILL, months, OUT
PASSES = ("ASCENDING", "DESCENDING")

def fetch():
    import ee
    from gee_animation import auth
    auth.init("hnee-331218"); ee.data.setDeadline(120_000)
    region = ee.Geometry.Rectangle(FRAME)
    s1 = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(region)
          .filter(ee.Filter.eq("instrumentMode", "IW"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH")).select(["VV", "VH"]))
    counts = {}
    for ym, s, e in months():
        coll = s1.filterDate(s, e)
        # scene and orbit mix per month from metadata only (no image compute)
        props = coll.aggregate_array("orbitProperties_pass").getInfo()
        rel = coll.aggregate_array("relativeOrbitNumber_start").getInfo()
        plat = coll.aggregate_array("platform_number").getInfo()
        counts[ym] = dict(asc=props.count("ASCENDING"), desc=props.count("DESCENDING"), orbits=sorted(set(rel)), platforms=sorted(set(plat)))
        for p in PASSES:
            path = f"{OUT}_{p[:3].lower()}/{ym}.tif"; os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path): continue
            c = coll.filter(ee.Filter.eq("orbitProperties_pass", p))
            if c.size().getInfo() == 0: continue
            url = c.median().unmask(FILL).toFloat().getDownloadURL({"region": region, "crs": CRS, "scale": SCALE, "format": "GEO_TIFF"})
            open(path, "wb").write(urllib.request.urlopen(url, timeout=300).read())
        print(ym, counts[ym], flush=True)
    import json; json.dump(counts, open(f"{OUT}_orbits.json", "w"))

def load(pattern):
    import rasterio
    out = {}
    for f in sorted(glob.glob(pattern)):
        with rasterio.open(f) as ds:
            a = ds.read().astype("float32"); a[a == FILL] = np.nan
        out[re.search(r"(\d{4}-\d{2})\.tif$", f).group(1)] = a
    return out

def plot(out_png):
    import rasterio, geopandas as gpd, json, matplotlib
    from rasterio.features import geometry_mask
    matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from windthrow_vs_delta import stem_density, WT_MIN_M
    both = load(f"{OUT}/*.tif")
    first = sorted(glob.glob(f"{OUT}/*.tif"))[0]
    with rasterio.open(first) as t:
        dens, street = stem_density(t)
        fp = gpd.read_file(CFG["footprint"]).to_crs(t.crs)
        inside = geometry_mask(list(fp.geometry), out_shape=(t.height, t.width), transform=t.transform, invert=True)
    core = inside & ~street; wt, ctl = core & (dens >= WT_MIN_M), core & (dens == 0)
    counts = json.load(open(f"{OUT}_orbits.json")) if os.path.exists(f"{OUT}_orbits.json") else {}
    def ser(frames, band, mask):
        labs = sorted(frames); x = np.array([int(l[:4]) + (int(l[5:7]) - 0.5) / 12 for l in labs])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore"); y = np.array([np.nanmean(frames[l][band][mask]) for l in labs])
        return x, y
    fig, axes = plt.subplots(4, 1, figsize=(13, 13), sharex=True, facecolor="white", gridspec_kw=dict(height_ratios=[1.2, 1.2, 0.8, 1.2]))
    for ax, (band, bi) in zip(axes[:2], (("VH", 1), ("VV", 0))):
        for mask, name, col in ((wt, "windthrow stands (≥ 20 m stem / cell)", "#c44"), (ctl, "stem-free canopy", "#2a6"), (core, "whole footprint", "#888")):
            x, y = ser(both, bi, mask); ax.plot(x, y, color=col, lw=1.3, label=name)
        ax.axvline(2025.5, color="#888", lw=1, ls="--"); ax.set_ylabel(f"{band}, dB (absolute)"); ax.grid(alpha=0.25); ax.legend(frameon=False, fontsize=9, loc="lower left")
        for s in ("top", "right"): ax.spines[s].set_visible(False)
    ax = axes[2]
    if counts:
        labs = sorted(counts); x = np.array([int(l[:4]) + (int(l[5:7]) - 0.5) / 12 for l in labs])
        asc = np.array([counts[l]["asc"] for l in labs]); desc = np.array([counts[l]["desc"] for l in labs])
        ax.bar(x, asc, width=0.07, color="#69c", label="ascending scenes"); ax.bar(x, desc, width=0.07, bottom=asc, color="#e9a", label="descending scenes")
        ax.set_ylabel("scenes / month"); ax.legend(frameon=False, fontsize=9); ax.grid(alpha=0.25, axis="y")
        b_months = [l for l in labs if "B" in counts[l]["platforms"]]
        if b_months: ax.axvspan(x[0], int(b_months[-1][:4]) + int(b_months[-1][5:7]) / 12, color="#000", alpha=0.04); ax.text(x[0] + 0.1, ax.get_ylim()[1] * 0.85, "Sentinel-1B available", fontsize=9, color="#555")
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    ax = axes[3]
    for p, col in (("asc", "#69c"), ("des", "#e9a")):
        fr = load(f"{OUT}_{p}/*.tif")
        if not fr: continue
        x, yw = ser(fr, 1, wt); _, yc = ser(fr, 1, ctl); ax.plot(x, yw - yc, color=col, lw=1.3, marker="o", ms=3, label=f"VH windthrow − canopy, {'ascending' if p == 'asc' else 'descending'} only")
    x, yw = ser(both, 1, wt); _, yc = ser(both, 1, ctl); ax.plot(x, yw - yc, color="#333", lw=1.0, ls=":", label="VH windthrow − canopy, both orbits (as before)")
    ax.axhline(0, color="#888", lw=1); ax.axvline(2025.5, color="#888", lw=1, ls="--"); ax.set_ylabel("Δ VH, dB"); ax.set_xlabel("year"); ax.grid(alpha=0.25); ax.legend(frameon=False, fontsize=9)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    axes[0].set_title(f"{SITE}: is the pre-storm backscatter drift real? absolute levels per class, orbit mix, and the difference within one orbit pass", loc="left", fontsize=11)
    fig.tight_layout(); fig.savefig(out_png, dpi=110); print("wrote", out_png)
    # numbers: linear trend of the difference 2018-2024 per orbit
    for p in ("asc", "des"):
        fr = load(f"{OUT}_{p}/*.tif")
        if not fr: continue
        x, yw = ser(fr, 1, wt); _, yc = ser(fr, 1, ctl); d = yw - yc; m = (x >= 2018) & (x < 2025)
        slope = np.polyfit(x[m], d[m], 1)[0]
        print(f"{p}: VH windthrow−canopy slope 2018–2024 {slope:+.3f} dB/yr | mean 2018 {d[(x>=2018)&(x<2019)].mean():+.2f} → 2024 {d[(x>=2024)&(x<2025)].mean():+.2f} dB | after storm {d[x>=2025.5].mean():+.2f} dB")
    x, yw = ser(both, 1, wt); _, yc = ser(both, 1, ctl); d = yw - yc; m = (x >= 2018) & (x < 2025)
    print(f"both: slope {np.polyfit(x[m], d[m], 1)[0]:+.3f} dB/yr | windthrow abs VH 2018 {yw[(x>=2018)&(x<2019)].mean():.2f} → 2024 {yw[(x>=2024)&(x<2025)].mean():.2f} | canopy abs VH 2018 {yc[(x>=2018)&(x<2019)].mean():.2f} → 2024 {yc[(x>=2024)&(x<2025)].mean():.2f}")

if __name__ == "__main__":
    {"fetch": lambda: fetch(), "plot": lambda: plot(sys.argv[2])}[sys.argv[1]]()
