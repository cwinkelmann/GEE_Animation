"""Unattended, resumable downloader for the Grumsin LGB tiles (ALS laz, bDOM, DGM1).
Reads the URL list, downloads in small Range chunks to .part files (resumes after any
interruption), unzips when complete. Safe to re-run any time.
    python fetch_grumsin_all.py"""
import os, re, time, urllib.request, urllib.error, zipfile, http.client
ROOT = "/Volumes/2TB/winmol/ALS_Data"; CHUNK = 256 << 10
DEST = {"als": f"{ROOT}/brandenburg_als", "bdom": f"{ROOT}/brandenburg_dgm_bdom", "dgm": f"{ROOT}/brandenburg_dgm_bdom"}
urls = [l.strip() for l in open(f"{ROOT}/brandenburg_grumsin_urls.txt") if l.startswith("http")]
def size_of(url):
    return int(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60).headers["Content-Length"])
def fetch_range(url, start, end):
    for a in range(60):
        try:
            d = urllib.request.urlopen(urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"}), timeout=60).read()
            if len(d) == end - start + 1: return d
        except (IOError, http.client.HTTPException) as exc:
            if isinstance(exc, http.client.IncompleteRead) and exc.partial: return bytes(exc.partial)
        time.sleep(min(30, 2 * (a + 1)))
    raise IOError("range failed 60 times")
for url in urls:
    name = url.rsplit("/", 1)[1]; prod = name.split("_")[0]; dest = DEST[prod]; os.makedirs(dest, exist_ok=True)
    stem = name[:-4]
    if any(f.startswith(stem) and f.endswith((".laz", ".tif")) for f in os.listdir(dest)): print("have", name, flush=True); continue
    part = f"{dest}/{name}.part"; size = size_of(url); have = os.path.getsize(part) if os.path.exists(part) else 0
    t0 = time.time(); start_have = have
    with open(part, "ab") as fh:
        while have < size:
            d = fetch_range(url, have, min(have + CHUNK, size) - 1); fh.write(d); fh.flush(); have += len(d)
            if have % (5 << 20) < CHUNK: print(f"    {name} {have/1e6:.1f}/{size/1e6:.1f} MB · {(have-start_have)/1e6/(time.time()-t0):.3f} MB/s", flush=True)
    os.replace(part, f"{dest}/{name}")
    try:
        z = zipfile.ZipFile(f"{dest}/{name}"); members = [m for m in z.namelist() if not m.endswith("/")]
        for m in members: open(f"{dest}/{os.path.basename(m)}", "wb").write(z.read(m))
        os.remove(f"{dest}/{name}"); print(f"{name}: {size/1e6:.1f} MB in {time.time()-t0:.0f} s → {members}", flush=True)
    except zipfile.BadZipFile: print(f"{name}: BAD ZIP, deleting", flush=True); os.remove(f"{dest}/{name}")
print("done", flush=True)
