"""SPIKE: does the Sentinel-1 VH drop (and the pre-storm decline) hold on OTHER storms?

For every digitised windthrow polygon in the WINMOL corpus: monthly VH median inside
the polygon vs. in a ring of intact forest around it (100–500 m, masked to ESA
WorldCover tree cover, minus every damage polygon), 2016-01 .. 2026-09, all
server-side (reduceRegions), one getInfo. Then per site: the polygon−ring series,
the step across the storm date and the pre-storm slope.

    python scripts/sar_multistorm.py fetch <out csv>
    python scripts/sar_multistorm.py plot <csv> <out png>
"""
import csv, glob, os, sys, warnings
import numpy as np
GIS = "/Volumes/2TB/winmol/training_data/WINMOL_Trainings_Data/GIS/Polygone"
SITES = {   # polygon file stem -> (label, storm date)
    "20171016_EW_WW_Campus_AOE": ("Eberswalde Campus (Xavier)", "2017-10-05"),
    "20171114_EW_WW_Bachsee_north_AOE": ("Bachsee north (Xavier)", "2017-10-05"),
    "2017_Xavier_BB_Eberswalde_UAV_AOE": ("Eberswalde survey polygon (Xavier)", "2017-10-05"),
    "20210706_EW_WW_Kaufland_AOE": ("Kaufland (summer storm 2021, date approx.)", "2021-06-30"),
    "20220226_EW_WW_Campus_Oberheide_AOE": ("Campus Oberheide (Zeynep)", "2022-02-18"),
    "2022_Zeynep_BB_Eberswalde_UAV_AOE": ("Eberswalde survey polygon (Zeynep)", "2022-02-18"),
    "20220212_Barnekow_3_AOE": ("Barnekow 3 (Zeynep)", "2022-02-18"),
    "20220212_Barnekow_5_AOE": ("Barnekow 5 (Zeynep)", "2022-02-18"),
    "20220212_Barnekow_6_AOI": ("Barnekow 6 (Zeynep)", "2022-02-18"),
    "2022_Zeynep_MV_Barnekow_AOE": ("Barnekow survey polygon (Zeynep)", "2022-02-18"),
    "2022_Zeynep_MV_Bremerhagen_AOE": ("Bremerhagen survey polygon (Zeynep)", "2022-02-18"),
    "20220209_Bremerhagen_3_AOE": ("Bremerhagen 3 (Zeynep)", "2022-02-18"),
}

def fetch(out_csv):
    """One small request per month (a single flattened request hit EE's
    'Too many concurrent aggregations' limit)."""
    import ee, geopandas as gpd, json, time
    from gee_animation import auth
    auth.init("hnee-331218"); ee.data.setDeadline(120_000)
    polys = {}
    only = os.environ.get("MS_ONLY")            # MS_ONLY=a,b fetches a subset (appended by hand)
    for stem in [k for k in SITES if not only or k in only.split(",")]:
        f = f"{GIS}/{stem}.shp"
        if not os.path.exists(f): print("missing", stem); continue
        g = gpd.read_file(f).to_crs(4326); geom = g.geometry.union_all()
        polys[stem] = ee.Geometry(json.loads(gpd.GeoSeries([geom], crs=4326).to_json())["features"][0]["geometry"])
    damage = ee.FeatureCollection([ee.Feature(p) for p in polys.values()]).geometry()
    trees = ee.ImageCollection("ESA/WorldCover/v200").first().eq(10)
    feats = [ee.Feature(eg, {"site": stem, "kind": "polygon"}) for stem, eg in polys.items()]
    feats += [ee.Feature(eg.buffer(500).difference(eg.buffer(100)).difference(damage), {"site": stem, "kind": "ring"}) for stem, eg in polys.items()]
    fc = ee.FeatureCollection(feats)
    s1 = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(fc.geometry())
          .filter(ee.Filter.eq("instrumentMode", "IW"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH")).select("VH"))
    out = []
    y, m = 2016, 1
    while (y, m) <= (2026, 9):
        ym = f"{y}-{m:02d}"; start = ee.Date(f"{ym}-01"); end = start.advance(1, "month")
        coll = s1.filterDate(start, end)
        img = coll.median().updateMask(trees).rename("VH")
        stats = coll.size().getInfo()
        if stats:
            for attempt in range(3):
                try:
                    res = img.reduceRegions(fc, ee.Reducer.mean(), scale=20).getInfo()["features"]; break
                except Exception as exc:
                    print(ym, "retry", attempt + 1, str(exc)[:80], flush=True); time.sleep(10 * (attempt + 1)); res = []
            for ft in res:
                pr = ft["properties"]; out.append([pr["site"], pr["kind"], ym, stats, pr.get("mean")])
        print(ym, stats, "scenes", flush=True)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    with open(out_csv, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["site", "kind", "ym", "n_scenes", "vh"]); w.writerows(out)
    print("wrote", out_csv, len(out), "rows")

def plot(csv_path, out_png):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    rows = list(csv.DictReader(open(csv_path)))
    by = {}
    for r in rows:
        if r["vh"] in ("", "None"): continue
        by.setdefault(r["site"], {}).setdefault(r["ym"], {})[r["kind"]] = float(r["vh"])
    sites = [s for s in SITES if s in by]
    fig, axes = plt.subplots(len(sites), 1, figsize=(13, 2.2 * len(sites)), sharex=True, facecolor="white")
    print(f"{'site':44s} {'pre12':>7s} {'post12':>7s} {'step':>7s} {'slope24 dB/yr':>14s} {'n mo':>5s}")
    for ax, s in zip(np.atleast_1d(axes), sites):
        label, storm = SITES[s]; sy = int(storm[:4]) + (int(storm[5:7]) - 0.5) / 12
        labs = sorted(k for k, v in by[s].items() if "polygon" in v and "ring" in v)
        x = np.array([int(k[:4]) + (int(k[5:7]) - 0.5) / 12 for k in labs]); d = np.array([by[s][k]["polygon"] - by[s][k]["ring"] for k in labs])
        pre = d[(x < sy) & (x >= sy - 1)]; post = d[(x > sy) & (x <= sy + 1)]
        m = (x < sy) & (x >= sy - 2); slope = np.polyfit(x[m], d[m], 1)[0] if m.sum() > 6 else np.nan
        ax.axhline(0, color="#888", lw=1); ax.axvline(sy, color="#c44", lw=1, ls="--")
        ax.plot(x, d, color="#357", lw=1.2, marker="o", ms=3); ax.set_ylabel("Δ VH dB"); ax.grid(alpha=0.25)
        for sp in ("top", "right"): ax.spines[sp].set_visible(False)
        ax.text(0.995, 0.92, f"{label} · storm {storm} · polygon − ring: 12 mo before {pre.mean():+.2f} → after {post.mean():+.2f} dB · pre-storm slope {slope:+.2f} dB/yr",
                transform=ax.transAxes, ha="right", va="top", fontsize=8.5)
        print(f"{label[:44]:44s} {pre.mean():+7.2f} {post.mean():+7.2f} {post.mean()-pre.mean():+7.2f} {slope:+14.2f} {len(labs):5d}")
    np.atleast_1d(axes)[0].set_title("Sentinel-1 VH: digitised windthrow polygon minus surrounding intact forest ring, monthly, three storms", loc="left", fontsize=11)
    np.atleast_1d(axes)[-1].set_xlabel("year"); fig.tight_layout(); fig.savefig(out_png, dpi=110); print("wrote", out_png)

if __name__ == "__main__":
    {"fetch": lambda: fetch(sys.argv[2]), "plot": lambda: plot(sys.argv[2], sys.argv[3])}[sys.argv[1]]()
