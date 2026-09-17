"""Do predicted windthrown stems sit on a temperature deviation?

Inputs: the local lst_rf delta GeoTIFFs (K, departure from the footprint mean,
20 m, EPSG:32633, clipped to the footprint) and the tegel-unet stem predictions
(LineStrings, EPSG:32633). Per 20 m cell: metres of predicted stem. Streets are
excluded (steet_mask.gpkg). Per month: mean delta in windthrow cells vs. cells
with no predicted stem, Spearman rho of delta vs stem density, binned means.

    python windthrow_vs_delta.py <geotiff dir or glob> <out csv>
"""
import glob, sys, re
import numpy as np, geopandas as gpd, rasterio
from rasterio.features import rasterize
from scipy.stats import spearmanr

import os
R12 = next(p for p in ("/Volumes/storage/Datasets/Winmol/training_data/WINDWURF_Tegel/Revier_12",   # NAS, the reliable one
                       "/Volumes/2TB/winmol/training_data/WINDWURF_Tegel/Revier_12", os.path.expanduser("~/data/Winmol/training_data/WINDWURF_Tegel/Revier_12")) if os.path.isdir(p))
STEMS = f"{R12}/predictions_cw_2026/R12_stems_tegel-unet_2026-08.gpkg"
STREETS = f"{R12}/steet_mask.gpkg"
WT_MIN_M = 20.0          # ≥ 20 m of predicted stem per 20 m cell (~4 stems) = "windthrow cell"
BINS = [0, 0.01, 10, 30, 60, 1e9]
BIN_NAMES = ["none", "0-10 m", "10-30 m", "30-60 m", ">60 m"]

def stem_density(template):
    """metres of predicted stem per cell of `template` (an open rasterio dataset)."""
    g = gpd.read_file(STEMS, layer="stems").to_crs(template.crs)
    pts = []
    for line in g.geometry:
        n = max(2, int(line.length / 0.5))
        for t in np.linspace(0, 1, n, endpoint=False):
            p = line.interpolate(t, normalized=True); pts.append((p.x, p.y, line.length / n))
    pts = np.array(pts)
    rows, cols = rasterio.transform.rowcol(template.transform, pts[:, 0], pts[:, 1])
    dens = np.zeros((template.height, template.width), "float32")
    ok = (rows >= 0) & (rows < template.height) & (cols >= 0) & (cols < template.width)
    np.add.at(dens, (np.asarray(rows)[ok], np.asarray(cols)[ok]), pts[ok, 2])
    streets = gpd.read_file(STREETS).to_crs(template.crs)
    street = rasterize([(geom, 1) for geom in streets.geometry], out_shape=dens.shape,
                       transform=template.transform, fill=0, all_touched=True).astype(bool)
    return dens, street

def main():
    pattern, out_csv = sys.argv[1], sys.argv[2]
    files = sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"no GeoTIFFs match {pattern}")
    with rasterio.open(files[0]) as t:
        dens, street = stem_density(t)
    rows = []
    for f in files:
        label = re.search(r"(\d{4}-\d{2})\.tif$", f).group(1)
        with rasterio.open(f) as ds:
            d = ds.read(1).astype("float32"); d[d == ds.nodata] = np.nan
        inside = np.isfinite(d) & ~street
        wt, ctrl = inside & (dens >= WT_MIN_M), inside & (dens == 0)
        rho = spearmanr(dens[inside], d[inside]).statistic if inside.sum() > 10 else np.nan
        binned = {}
        idx = np.digitize(dens, BINS[1:], right=False)
        for i, name in enumerate(BIN_NAMES):
            m = inside & (idx == i); binned[name] = (float(np.nanmean(d[m])) if m.sum() else np.nan, int(m.sum()))
        rows.append(dict(month=label, n_wt=int(wt.sum()), n_ctrl=int(ctrl.sum()),
                         delta_wt=float(np.nanmean(d[wt])), delta_ctrl=float(np.nanmean(d[ctrl])),
                         diff_K=float(np.nanmean(d[wt]) - np.nanmean(d[ctrl])), spearman=float(rho),
                         **{f"bin_{k}": v[0] for k, v in binned.items()}))
        print(f"{label}: windthrow cells {wt.sum():4d} mean {rows[-1]['delta_wt']:+.2f} K | "
              f"no-stem cells {ctrl.sum():4d} mean {rows[-1]['delta_ctrl']:+.2f} K | "
              f"diff {rows[-1]['diff_K']:+.2f} K | rho {rho:+.3f} | bins "
              + " ".join(f"{k}:{v[0]:+.2f}({v[1]})" for k, v in binned.items()), flush=True)
    import csv
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("wrote", out_csv)
    print(f"cells: total inside {int((np.isfinite(d) & ~street).sum())}, street-excluded {int(street[np.isfinite(d)].sum())}, "
          f"stem density max {dens.max():.0f} m/cell, cells with any stem {(dens > 0).sum()}")

if __name__ == "__main__":
    main()
