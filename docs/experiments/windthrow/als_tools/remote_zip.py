"""Read members of a huge remote zip via HTTP range requests (no full download)."""
import io, re, sys, urllib.request

class RemoteFile(io.RawIOBase):
    def __init__(self, url, chunk=4 << 20):
        self.url, self.pos, self.chunk = url, 0, chunk
        req = urllib.request.Request(url, method="HEAD"); r = urllib.request.urlopen(req, timeout=60)
        self.size = int(r.headers["Content-Length"]); self.ranges = r.headers.get("Accept-Ranges")
        self.cache_start, self.cache = -1, b""
    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos
    def seek(self, off, whence=0):
        self.pos = {0: off, 1: self.pos + off, 2: self.size + off}[whence]; return self.pos
    def _fetch(self, start, n):
        import http.client, time
        for attempt in range(40):
            try:
                req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{start + n - 1}"})
                data = urllib.request.urlopen(req, timeout=300).read()
                if len(data) == n: return data
                raise IOError(f"short range read {len(data)} of {n}")
            except (IOError, http.client.HTTPException) as exc:
                print(f"  range retry {attempt + 1}: {str(exc)[:80]}", flush=True); time.sleep(min(60, 5 * (attempt + 1)))
        raise IOError("range request failed repeatedly")
    def read(self, n=-1):
        if n < 0: n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n == 0: return b""
        if not (self.cache_start <= self.pos and self.pos + n <= self.cache_start + len(self.cache)):
            want = min(max(n, self.chunk), self.size - self.pos); self.cache_start = self.pos; self.cache = self._fetch(self.pos, want)
        off = self.pos - self.cache_start; out = self.cache[off:off + n]; self.pos += len(out); return out

if __name__ == "__main__":
    import zipfile
    for url in sys.argv[1:]:
        rf = RemoteFile(url); print(url, f"{rf.size/1e9:.1f} GB", "Accept-Ranges:", rf.ranges, flush=True)
        z = zipfile.ZipFile(rf); names = z.namelist(); print("  members:", len(names), "e.g.", names[:2])
        hits = [n for n in names if re.search(r"3dm_33_37[3-8]_582[5-9]", n)]
        print("  Spandau tiles here:", len(hits)); [print("   ", n, f"{z.getinfo(n).file_size/1e6:.0f} MB", "compress", z.getinfo(n).compress_type) for n in hits[:25]]
