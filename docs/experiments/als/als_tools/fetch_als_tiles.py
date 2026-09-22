"""Extract the LAS tiles used by the crown segmentation from Berlin's regional zips, by HTTP range."""
import os, sqlite3, struct, sys, time, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote_zip import RemoteFile
GPKG = "/Volumes/2TB/winmol/training_data/WINDWURF_Tegel/ALS segmentation 2017.gpkg"
DEST = "/Volumes/2TB/winmol/training_data/WINDWURF_Tegel/ALS_2021_tiles"; os.makedirs(DEST, exist_ok=True)
tiles = sorted(r[0] for r in sqlite3.connect(GPKG).execute('select distinct layer from "ALS Kronenerfassung"'))
print(len(tiles), "tiles referenced by the crown file", flush=True)
zips = {"Nordwest": "https://gdi.berlin.de/data/a_als/atom/Nordwest.zip", "West": "https://gdi.berlin.de/data/a_als/atom/West.zip"}
opened = {k: zipfile.ZipFile(RemoteFile(u, chunk=32 << 20)) for k, u in zips.items()}
where = {}
for k, z in opened.items():
    for n in z.namelist():
        if n in tiles: where[n] = k
missing = [t for t in tiles if t not in where]; print("not found in either zip:", missing, flush=True)
total = 0
for t in tiles:
    if t not in where: continue
    out = f"{DEST}/{t}"; info = opened[where[t]].getinfo(t)
    if os.path.exists(out) and os.path.getsize(out) == info.file_size: print("have", t, flush=True); total += info.file_size; continue
    t0 = time.time()
    with opened[where[t]].open(t) as src, open(out + ".part", "wb") as dst:
        while True:
            b = src.read(16 << 20)
            if not b: break
            dst.write(b)
    os.replace(out + ".part", out); total += info.file_size
    with open(out, "rb") as fh:
        hdr = fh.read(375); magic = hdr[:4]; ver = f"{hdr[24]}.{hdr[25]}"; npts = struct.unpack("<Q", hdr[247:255])[0] if hdr[25] >= 4 else struct.unpack("<I", hdr[107:111])[0]
    print(f"{t}: {info.file_size/1e6:.0f} MB in {time.time()-t0:.0f} s · LAS {ver} magic {magic} · {npts:,} points", flush=True)
print(f"done: {total/1e9:.1f} GB in {DEST}", flush=True)
