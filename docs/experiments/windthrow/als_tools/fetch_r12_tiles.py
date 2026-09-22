"""Berlin ALS 2021 tiles intersecting the R12 footprint (+100 m), from Nordwest.zip by HTTP range."""
import os, struct, sys, time, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote_zip import RemoteFile
import geopandas as gpd
from shapely.geometry import box
DEST = "/Volumes/2TB/winmol/ALS_Data/berlin_als_2021"; os.makedirs(DEST, exist_ok=True)
fp = gpd.read_file("/Users/christian/work/hnee/GEE_animation-rf-sharpening/docs/aoi/r12/r12_footprint.geojson").to_crs(25833).union_all().buffer(100)
tiles = [f"3dm_33_{e}_{n}_1_be.las" for e in range(379, 384) for n in range(5825, 5831) if fp.intersects(box(e*1000, n*1000, (e+1)*1000, (n+1)*1000))]
print(len(tiles), "tiles intersect the R12 footprint:", tiles, flush=True)
z = zipfile.ZipFile(RemoteFile("https://gdi.berlin.de/data/a_als/atom/Nordwest.zip", chunk=32 << 20)); names = set(z.namelist())
missing = [t for t in tiles if t not in names]; print("not in Nordwest.zip:", missing, flush=True)
total = 0
for t in tiles:
    if t not in names: continue
    out = f"{DEST}/{t}"; info = z.getinfo(t)
    if os.path.exists(out) and os.path.getsize(out) == info.file_size: print("have", t, flush=True); total += info.file_size; continue
    t0 = time.time()
    with z.open(t) as src, open(out + ".part", "wb") as dst:
        while True:
            b = src.read(16 << 20)
            if not b: break
            dst.write(b)
    os.replace(out + ".part", out); total += info.file_size
    with open(out, "rb") as fh:
        hdr = fh.read(375); npts = struct.unpack("<Q", hdr[247:255])[0]
    print(f"{t}: {info.file_size/1e6:.0f} MB in {time.time()-t0:.0f} s · magic {hdr[:4]} · {npts:,} points", flush=True)
print(f"done: {total/1e9:.1f} GB", flush=True)
