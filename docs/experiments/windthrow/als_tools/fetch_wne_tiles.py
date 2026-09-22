"""Brandenburg ALS tiles (LGB open data, dl-de/by-2.0) intersecting the Grumsin reserve (+100 m).
Per-tile zips: https://data.geobasis-bb.de/geobasis/daten/als/laz/als_33<E>-<N>.zip"""
import io, os, sys, time, urllib.request, urllib.error, zipfile, http.client
import geopandas as gpd
from shapely.geometry import box
DEST = "/Volumes/2TB/winmol/ALS_Data/brandenburg_als"; os.makedirs(DEST, exist_ok=True)
fp = gpd.read_file("/Users/christian/work/hnee/GEE_animation-rf-sharpening/docs/aoi/wne/wne.shp").to_crs(25833).union_all().buffer(100)
tiles = [(e, n) for e in range(423, 429) for n in range(5869, 5873) if fp.intersects(box(e*1000, n*1000, (e+1)*1000, (n+1)*1000))]
print(len(tiles), "tiles intersect Grumsin:", tiles, flush=True)
def get(url, chunk=1 << 20):
    """The LGB server drops connections after ~1 MB: fetch in 1 MB Range requests, each retried."""
    try: size = int(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60).headers["Content-Length"])
    except urllib.error.HTTPError as exc:
        if exc.code == 404: return None
        raise
    buf = bytearray(); t0 = time.time()
    while len(buf) < size:
        end = min(len(buf) + chunk, size) - 1
        for a in range(30):
            try:
                r = urllib.request.urlopen(urllib.request.Request(url, headers={"Range": f"bytes={len(buf)}-{end}"}), timeout=60)
                data = r.read()
                if len(data) != end - len(buf) + 1: raise IOError(f"short {len(data)}")
                buf += data; break
            except (IOError, http.client.HTTPException) as exc:
                if isinstance(exc, http.client.IncompleteRead) and exc.partial: buf += exc.partial; end = min(len(buf) + chunk, size) - 1
                time.sleep(min(30, 2 * (a + 1)))
        else: raise IOError("range chunk failed repeatedly")
        if len(buf) % (20 << 20) < chunk: print(f"    {len(buf)/1e6:.0f}/{size/1e6:.0f} MB, {len(buf)/1e6/(time.time()-t0):.2f} MB/s", flush=True)
    return bytes(buf)
total = 0; missing = []
for e, n in tiles:
    name = f"als_33{e}-{n}"; zpath = f"{DEST}/{name}.zip"
    if any(f.startswith(name) and f.endswith(".laz") for f in os.listdir(DEST)): print("have", name, flush=True); continue
    t0 = time.time(); data = get(f"https://data.geobasis-bb.de/geobasis/daten/als/laz/{name}.zip")
    if data is None: missing.append(name); print("not available:", name, flush=True); continue
    z = zipfile.ZipFile(io.BytesIO(data)); members = z.namelist()
    for m in members:
        out = f"{DEST}/{os.path.basename(m)}"
        if m.endswith("/"): continue
        open(out, "wb").write(z.read(m)); total += os.path.getsize(out)
    print(f"{name}: zip {len(data)/1e6:.0f} MB in {time.time()-t0:.0f} s → {members}", flush=True)
print(f"done: {total/1e9:.2f} GB extracted · missing tiles: {missing}", flush=True)
