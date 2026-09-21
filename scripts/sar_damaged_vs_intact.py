"""SPIKE: absolute Sentinel-1 VH time series, damaged vs intact forest, per site.

Two lines per panel — the windthrow part of the site and the undamaged part —
with the storm date marked. Tegel (R12, R13): windthrow-dense cells (>= 20 m
predicted stem per 20 m cell) vs stem-free cells of the footprint, from the
monthly GeoTIFFs of sar_windthrow_spike.py. WINMOL corpus: digitised damage
polygon vs the 100–500 m ring of intact tree cover, from sar_multistorm.csv.

    WT_SITE=R12 python scripts/sar_damaged_vs_intact.py series out/sar_tegel_series.csv
    WT_SITE=R13 python scripts/sar_damaged_vs_intact.py series out/sar_tegel_series.csv
    python scripts/sar_damaged_vs_intact.py plot out/sar_tegel_series.csv docs/experiments/sar/sar_multistorm.csv <out png>
"""
import csv, glob, os, re, sys, warnings
import numpy as np

FILL = -9999.0
TEGEL = {"R12": ("Tegeler Forst R12 (2025 storm)", "2025-07-01"), "R13": ("Spandauer Forst R13 (2025 storm)", "2025-07-01")}

def series(out_csv):
    import rasterio, geopandas as gpd
    from rasterio.features import geometry_mask
    sys.path.insert(0, "docs/experiments/windthrow")
    from windthrow_vs_delta import stem_density, WT_MIN_M
    from sar_windthrow_spike import SITE, CFG, OUT
    files = sorted(glob.glob(f"{OUT}/*.tif"))
    with rasterio.open(files[0]) as t:
        fp = gpd.read_file(CFG["footprint"]).to_crs(t.crs)
        inside = geometry_mask(list(fp.geometry), out_shape=(t.height, t.width), transform=t.transform, invert=True)
        dens, street = stem_density(t)
    core = inside & ~street
    masks = {"damaged": core & (dens >= WT_MIN_M), "intact": core & (dens == 0)}
    rows = []
    if os.path.exists(out_csv):
        rows = [r for r in csv.DictReader(open(out_csv)) if r["site"] != SITE]
    for f in files:
        ym = re.search(r"(\d{4}-\d{2})\.tif$", f).group(1)
        with rasterio.open(f) as ds:
            vh = ds.read(2).astype("float32"); vh[vh == FILL] = np.nan
        for kind, m in masks.items():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore"); v = float(np.nanmean(vh[m]))
            rows.append(dict(site=SITE, kind=kind, ym=ym, vh="" if np.isnan(v) else v))
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["site", "kind", "ym", "vh"]); w.writeheader(); w.writerows(rows)
    print(f"{SITE}: damaged {int(masks['damaged'].sum())} cells, intact {int(masks['intact'].sum())} cells, {len(files)} months -> {out_csv}")

def _load(csv_path, kinds):
    by = {}
    for r in csv.DictReader(open(csv_path)):
        if r["vh"] in ("", "None"): continue
        by.setdefault(r["site"], {}).setdefault(r["ym"], {})[kinds[r["kind"]]] = float(r["vh"])
    return by

def plot(tegel_csv, corpus_csv, out_png):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from sar_multistorm import SITES as CORPUS
    panels = []   # (label, storm, {ym: {damaged, intact}})
    tg = _load(tegel_csv, {"damaged": "damaged", "intact": "intact"})
    for s, (label, storm) in TEGEL.items():
        if s in tg: panels.append((label, storm, tg[s], "windthrow cells vs stem-free cells"))
    cp = _load(corpus_csv, {"polygon": "damaged", "ring": "intact"})
    for s, (label, storm) in CORPUS.items():
        if s in cp: panels.append((label, storm, cp[s], "damage polygon vs 100–500 m ring"))
    n = len(panels); ncol = 2; nrow = (n + 1) // 2
    fig, axes = plt.subplots(nrow, ncol, figsize=(16, 2.6 * nrow), sharex=True, facecolor="white")
    axes = axes.ravel()
    print(f"{'site':40s} {'damaged step':>13s} {'intact step':>12s} {'difference':>11s}")
    for ax, (label, storm, d, how) in zip(axes, panels):
        sy = int(storm[:4]) + (int(storm[5:7]) - 0.5) / 12
        labs = sorted(k for k, v in d.items() if "damaged" in v and "intact" in v)
        x = np.array([int(k[:4]) + (int(k[5:7]) - 0.5) / 12 for k in labs])
        dam = np.array([d[k]["damaged"] for k in labs]); ok = np.array([d[k]["intact"] for k in labs])
        pre = (x < sy) & (x >= sy - 1); post = (x > sy) & (x <= sy + 1)
        sd, so = dam[post].mean() - dam[pre].mean(), ok[post].mean() - ok[pre].mean()
        ax.axvspan(sy, x.max() + 0.1, color="#c44", alpha=0.04)
        ax.axvline(sy, color="#c44", lw=1.2, ls="--")
        ax.plot(x, ok, color="#2a7f4f", lw=1.3, marker="o", ms=2.5, label="intact forest")
        ax.plot(x, dam, color="#c8502a", lw=1.3, marker="o", ms=2.5, label="damaged (windthrow)")
        ax.set_ylabel("VH, dB"); ax.grid(alpha=0.25)
        for sp in ("top", "right"): ax.spines[sp].set_visible(False)
        ax.text(0.01, 0.97, f"{label}\n{how} · storm {storm}", transform=ax.transAxes, ha="left", va="top", fontsize=8.5)
        ax.text(0.99, 0.03, f"12 mo after − before: damaged {sd:+.2f} dB, intact {so:+.2f} dB → {sd-so:+.2f} dB",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=8, color="#333")
        print(f"{label[:40]:40s} {sd:+13.2f} {so:+12.2f} {sd-so:+11.2f}")
    for ax in axes[n:]: ax.axis("off")
    axes[0].legend(loc="lower left", fontsize=8, frameon=False)
    for ax in axes[-ncol:]: ax.set_xlabel("year")
    fig.suptitle("Sentinel-1 VH backscatter (monthly median, IW, both orbits): damaged vs intact forest at each site, storm date dashed",
                 x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.98)); fig.savefig(out_png, dpi=110); print("wrote", out_png)

if __name__ == "__main__":
    sys.path.insert(0, "scripts")
    {"series": lambda: series(sys.argv[2]), "plot": lambda: plot(sys.argv[2], sys.argv[3], sys.argv[4])}[sys.argv[1]]()
