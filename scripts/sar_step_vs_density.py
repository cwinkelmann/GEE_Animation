"""SPIKE: does the VH step at the storm scale with how much fell inside the polygon?

x = annotated stems per hectare inside the polygon (WINMOL stem outlines; Tegel:
tegel-unet predicted stems over the whole footprint), y = VH step of the polygon
relative to its intact reference (12 months after the storm minus 12 before).
Tegel enters as whole footprints vs their stem-free cells, i.e. the same
"polygon containing damage" definition as the corpus.

    python scripts/sar_step_vs_density.py <out png> <out csv>
"""
import csv, glob, os, re, sys, warnings
import numpy as np

GIS = "/Volumes/2TB/winmol/training_data/WINMOL_Trainings_Data/GIS"
STORM = {"R12": "2025-07-01", "R13": "2025-07-01"}
TEGEL_ROOTS = ("/Volumes/storage/Datasets/Winmol/training_data/WINDWURF_Tegel",
               "/Volumes/2TB/winmol/training_data/WINDWURF_Tegel", os.path.expanduser("~/data/Winmol/training_data/WINDWURF_Tegel"))

def _step(x, y, storm):
    sy = int(storm[:4]) + (int(storm[5:7]) - 0.5) / 12
    pre = (x < sy) & (x >= sy - 1); post = (x > sy) & (x <= sy + 1)
    return float(y[post].mean() - y[pre].mean())

def _ym_x(labs): return np.array([int(k[:4]) + (int(k[5:7]) - 0.5) / 12 for k in labs])

def corpus_rows():
    import geopandas as gpd
    sys.path.insert(0, "scripts"); from sar_multistorm import SITES
    by = {}
    for f in ["docs/experiments/sar/sar_multistorm.csv"] + glob.glob("out/sar_multistorm_*.csv"):
        for r in csv.DictReader(open(f)):
            if r["vh"] in ("", "None"): continue
            by.setdefault(r["site"], {}).setdefault(r["ym"], {})[r["kind"]] = float(r["vh"])
    rows = []
    for stem, (label, storm) in SITES.items():
        base = re.sub(r"_AO[EI]$", "", stem)
        sf = f"{GIS}/Training_Data/{base}.shp"
        if stem not in by or not os.path.exists(sf): continue
        poly = gpd.read_file(f"{GIS}/Polygone/{stem}.shp").to_crs(32633).union_all()
        st = gpd.read_file(sf).to_crs(32633); st = st[st.geometry.notna()]
        ha = poly.area / 1e4
        labs = sorted(k for k, v in by[stem].items() if "polygon" in v and "ring" in v)
        d = np.array([by[stem][k]["polygon"] - by[stem][k]["ring"] for k in labs])
        rows.append(dict(site=label, kind="corpus", ha=ha, stems=len(st), stems_ha=len(st) / ha,
                         stem_area_pct=100 * st.area.sum() / poly.area, step=_step(_ym_x(labs), d, storm),
                         date_ok=int("approx" not in label)))
    return rows

def tegel_rows():
    import rasterio, geopandas as gpd
    from rasterio.features import geometry_mask
    rows = []
    for site in ("R12", "R13"):
        os.environ["WT_SITE"] = site
        for m in list(sys.modules):
            if m in ("windthrow_vs_delta", "sar_windthrow_spike"): del sys.modules[m]
        sys.path.insert(0, "docs/experiments/windthrow"); sys.path.insert(0, "scripts")
        from windthrow_vs_delta import stem_density, STEMS
        from sar_windthrow_spike import CFG, OUT
        files = sorted(glob.glob(f"{OUT}/*.tif"))
        with rasterio.open(files[0]) as t:
            fp = gpd.read_file(CFG["footprint"]).to_crs(t.crs); fpg = fp.union_all()
            inside = geometry_mask(list(fp.geometry), out_shape=(t.height, t.width), transform=t.transform, invert=True)
            dens, street = stem_density(t)
        core = inside & ~street; ctl = core & (dens == 0)
        st = gpd.read_file(STEMS, layer="stems").to_crs(t.crs); st = st[st.intersects(fpg)]
        labs, d = [], []
        for f in files:
            with rasterio.open(f) as ds:
                vh = ds.read(2).astype("float32"); vh[vh == -9999.0] = np.nan
            with warnings.catch_warnings():
                warnings.simplefilter("ignore"); v = np.nanmean(vh[core]) - np.nanmean(vh[ctl])
            if not np.isnan(v): labs.append(re.search(r"(\d{4}-\d{2})\.tif$", f).group(1)); d.append(v)
        ha = fpg.area / 1e4
        rows.append(dict(site=f"Tegel {site} footprint (2025)", kind="tegel", ha=ha, stems=len(st), stems_ha=len(st) / ha,
                         stem_area_pct=float("nan"), step=_step(_ym_x(labs), np.array(d), STORM[site]), date_ok=1))
    return rows

def main(out_png, out_csv):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from scipy.stats import spearmanr, pearsonr
    rows = corpus_rows() + tegel_rows()
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"{'site':40s} {'ha':>7s} {'stems':>6s} {'stems/ha':>9s} {'stem area %':>12s} {'step dB':>8s}")
    for r in rows: print(f"{r['site'][:40]:40s} {r['ha']:7.2f} {r['stems']:6d} {r['stems_ha']:9.0f} {r['stem_area_pct']:12.1f} {r['step']:+8.2f}")
    ok = [r for r in rows if r["date_ok"]]
    x = np.array([r["stems_ha"] for r in ok]); y = np.array([r["step"] for r in ok])
    rs, ps = spearmanr(np.log10(x), y); rp, pp = pearsonr(np.log10(x), y)
    print(f"n={len(ok)} (dated storms) Spearman r={rs:+.2f} p={ps:.3f} · Pearson r(log10 stems/ha, step)={rp:+.2f} p={pp:.3f}")
    fig, ax = plt.subplots(figsize=(9, 6), facecolor="white")
    for r in rows:
        c = {"corpus": "#357", "tegel": "#c8502a"}[r["kind"]]
        ax.scatter(r["stems_ha"], r["step"], s=60, color=c, facecolor="white" if not r["date_ok"] else c, zorder=3)
        ax.annotate(r["site"].replace(" (Zeynep)", " Z").replace(" (Xavier)", " X").replace(" (summer storm 2021, date approx.)", " 2021?"),
                    (r["stems_ha"], r["step"]), xytext=(6, 4), textcoords="offset points", fontsize=8)
    b = np.polyfit(np.log10(x), y, 1); xx = np.logspace(1, 3, 50); ax.plot(xx, np.polyval(b, np.log10(xx)), color="#888", lw=1, ls="--")
    ax.set_xscale("log"); ax.axhline(0, color="#888", lw=1); ax.grid(alpha=0.25)
    ax.set_xlabel("fallen stems per hectare inside the polygon (annotated; Tegel: tegel-unet predictions over the footprint)")
    ax.set_ylabel("VH step at the storm, polygon − intact reference, dB")
    ax.set_title(f"Sentinel-1 VH step vs windthrow density · dated storms: Spearman r = {rs:+.2f} (p = {ps:.3f}), n = {len(ok)}\n"
                 "blue: corpus polygon vs ring · orange: Tegel footprint vs stem-free cells · open: storm date uncertain", loc="left", fontsize=9.5)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    fig.tight_layout(); fig.savefig(out_png, dpi=120); print("wrote", out_png)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
