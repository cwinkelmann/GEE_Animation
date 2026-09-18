#!/usr/bin/env python3
"""Render a long run in per-year chunks, then concatenate the parts.

Why: a single run over many years asks Earth Engine for one long, heavy
computation. When the index is expensive (lst_smw's TOA/emissivity join, an
anomaly's multi-year climatology) that can exceed the request budget or draw
rate limiting, and the run stalls or dies partway. Chunking turns one fragile
job into N independent ones — any chunk can be retried alone, and completed
chunks are never redone (their thumbnails are cached anyway).

    python scripts/render_chunked.py --config config/wne_lst_anomaly.yaml
    python scripts/render_chunked.py --config X.yaml --years 2 --no-concat

THE ONE HONEST COST: interpolation is computed within a chunk, so the generated
transition ACROSS each boundary is missing. With yearly chunks that is one
absent transition per year boundary (December -> January), which reads as a
small jump. Everything else — observed frames, labels, provenance — is
identical to an unchunked run. Set `render.interpolate: 0` and there is no
cost at all, because there are no generated frames to lose.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def year_chunks(start: str, end: str, years: int):
    """[(start, end)] covering [start, end) in `years`-long pieces."""
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while a < b:
        nxt = min(date(a.year + years, 1, 1), b)
        out.append((a.isoformat(), nxt.isoformat()))
        a = nxt
    return out


def concat(parts, dest: Path) -> bool:
    """Join MP4 parts without re-encoding (identical codec/size/fps)."""
    listing = dest.with_suffix(".parts.txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", str(dest)],
        capture_output=True, text=True)
    listing.unlink(missing_ok=True)
    if proc.returncode != 0:
        print(proc.stderr[-800:], file=sys.stderr)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--years", type=int, default=1, help="chunk length (default 1)")
    ap.add_argument("--no-concat", action="store_true",
                    help="render the chunks but leave them as separate videos")
    a = ap.parse_args()

    cfg_path = Path(a.config)
    cfg = yaml.safe_load(cfg_path.read_text())
    name, out_dir = cfg["name"], Path(cfg.get("out_dir", "out"))
    chunks = year_chunks(cfg["start"], cfg["end"], a.years)
    print(f"{name}: {cfg['start']}..{cfg['end']} -> {len(chunks)} chunk(s)")

    parts, failed = [], []
    for i, (s, e) in enumerate(chunks, 1):
        part = dict(cfg)
        part["name"], part["start"], part["end"] = f"{name}_part{i:02d}", s, e
        tmp = out_dir / f"_chunk_{i:02d}.yaml"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(yaml.safe_dump(part, sort_keys=False))
        print(f"  [{i}/{len(chunks)}] {s} .. {e}", flush=True)
        rc = subprocess.run(["gee-animation", "--config", str(tmp)]).returncode
        tmp.unlink(missing_ok=True)
        mp4 = out_dir / f"{part['name']}.mp4"
        if rc == 0 and mp4.exists():
            parts.append(mp4)
        else:
            failed.append(f"{s}..{e}")
            print(f"      FAILED (rc={rc}) — retry this chunk alone", flush=True)

    if failed:
        print(f"\n{len(failed)} chunk(s) failed: {', '.join(failed)}")
        print("Completed chunks are kept; re-running skips their fetches (cached).")
    if parts and not a.no_concat:
        dest = out_dir / f"{name}.mp4"
        print(f"\nconcatenating {len(parts)} part(s) -> {dest}")
        if concat(parts, dest):
            print(f"done: {dest} ({dest.stat().st_size / 1048576:.0f} MiB)")
            print("NOTE: the generated transition across each chunk boundary is "
                  "absent; observed frames are unaffected.")
        else:
            print("concat failed; the parts remain individually usable")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
