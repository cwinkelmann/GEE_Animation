"""DTM, DSM and CHM at 1 m from classified ALS tiles (LAS/LAZ), plus per-area mosaics.
    python chm_dtm.py <tile dir> <out dir> [<area name> <footprint file> ...]
DTM: mean ground (class 2) z per 1 m cell, holes filled by linear then nearest interpolation.
DSM: max z of all non-noise returns per cell. CHM: DSM − DTM, clipped at 0."""
import glob, os, sys
import numpy as np, laspy, rasterio
from rasterio.transform import from_origin
from rasterio.merge import merge
from scipy.interpolate import griddata, NearestNDInterpolator
from scipy.ndimage import binary_dilation
RES = 1.0; CRS = "EPSG:25833"
tile_dir, out = sys.argv[1], sys.argv[2]; os.makedirs(out, exist_ok=True)

def grid_tile(path):
    las = laspy.read(path); x, y, z, c = np.asarray(las.x), np.asarray(las.y), np.asarray(las.z), np.asarray(las.classification)
    keep = ~np.isin(c, (7, 9, 18, 20))         # drop noise, water and LGB's synthetic water/wet-surface class 20
    x, y, z, c = x[keep], y[keep], z[keep], c[keep]
    x0, y0 = np.floor(x.min() / 1000) * 1000, np.floor(y.min() / 1000) * 1000          # snap to the 1 km tile origin
    n = int(1000 / RES); col = ((x - x0) / RES).astype(int); row = ((y0 + 1000 - y) / RES).astype(int)
    ok = (col >= 0) & (col < n) & (row >= 0) & (row < n); col, row, z, c = col[ok], row[ok], z[ok], c[ok]
    idx = row * n + col
    dsm = np.full(n * n, -np.inf, "float64"); np.maximum.at(dsm, idx, z); dsm[~np.isfinite(dsm)] = np.nan; dsm = dsm.reshape(n, n)
    g = c == 2; gs = np.zeros(n * n); gc = np.zeros(n * n); np.add.at(gs, idx[g], z[g]); np.add.at(gc, idx[g], 1)
    dtm = np.where(gc > 0, gs / np.maximum(gc, 1), np.nan).reshape(n, n)
    rr, cc = np.mgrid[0:n, 0:n]; have = np.isfinite(dtm)
    if have.sum() < n * n:
        pts = np.c_[rr[have], cc[have]]; vals = dtm[have]
        filled = griddata(pts, vals, (rr[~have], cc[~have]), method="linear")
        dtm[~have] = filled; still = ~np.isfinite(dtm)
        if still.any(): dtm[still] = NearestNDInterpolator(pts, vals)(rr[still], cc[still])
    cover = binary_dilation(np.isfinite(dsm), iterations=15)            # only fill holes within 15 m of any return; no extrapolation past the scan edge
    dtm[~cover] = np.nan
    chm = np.clip(dsm - dtm, 0, None); chm[~np.isfinite(dsm)] = np.nan
    tr = from_origin(x0, y0 + 1000, RES, RES)
    return dict(dtm=dtm.astype("float32"), dsm=dsm.astype("float32"), chm=chm.astype("float32")), tr, int(g.sum()), int(len(z))

def write(arr, tr, path):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32", crs=CRS, transform=tr, nodata=-9999, compress="deflate", tiled=True) as d:
        d.write(np.where(np.isfinite(arr), arr, -9999).astype("float32"), 1)

tiles = sorted(glob.glob(f"{tile_dir}/*.las") + glob.glob(f"{tile_dir}/*.laz"))
for t in tiles:
    stem = os.path.splitext(os.path.basename(t))[0]
    if os.path.exists(f"{out}/{stem}_chm.tif"): continue
    layers, tr, ng, npts = grid_tile(t)
    for k, a in layers.items(): write(a, tr, f"{out}/{stem}_{k}.tif")
    print(f"{stem}: {npts:,} pts, {ng:,} ground · DTM {np.nanmin(layers['dtm']):.1f}–{np.nanmax(layers['dtm']):.1f} m · CHM p99 {np.nanpercentile(layers['chm'], 99):.1f} m", flush=True)
# mosaics per area (footprint bbox + 200 m)
import geopandas as gpd
args = sys.argv[3:]
for name, fp in zip(args[::2], args[1::2]):
    b = gpd.read_file(fp).to_crs(25833).total_bounds; b = (b[0] - 200, b[1] - 200, b[2] + 200, b[3] + 200)
    for k in ("dtm", "dsm", "chm"):
        srcs = []
        for f in glob.glob(f"{out}/*_{k}.tif"):
            ds = rasterio.open(f); bb = ds.bounds
            if bb.right > b[0] and bb.left < b[2] and bb.top > b[1] and bb.bottom < b[3]: srcs.append(ds)
            else: ds.close()
        if not srcs: continue
        arr, tr = merge(srcs, bounds=b, nodata=-9999); [s.close() for s in srcs]
        with rasterio.open(f"{out}/{name}_{k}_1m.tif", "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2], count=1, dtype="float32", crs=CRS, transform=tr, nodata=-9999, compress="deflate", tiled=True) as d: d.write(arr[0], 1)
        print(f"{name}_{k}_1m.tif: {arr.shape[2]} x {arr.shape[1]} px from {len(srcs)} tiles", flush=True)
print("done", flush=True)
