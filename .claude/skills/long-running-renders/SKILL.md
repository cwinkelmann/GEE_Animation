---
name: long-running-renders
description: Use when launching a render spanning many years or a daily collection, running several renders at once, or when a render produces no output for a long time — covers chunking, concurrency limits, reading silence, and stopping a run cleanly.
---

# Long-running renders

A run over many years is one fragile job. Three separate runs here stalled or
died on the same cause, and the fix is always the same shape: make it smaller.

## Chunk anything long — do this first, not after it fails

```bash
python scripts/render_chunked.py --config config/X.yaml          # yearly
python scripts/render_chunked.py --config config/X.yaml --years 5
```

Each chunk is independently retryable and completed chunks never refetch (their
thumbnails are cached). Parts concatenate without re-encoding.

**Measured cases**, all the same failure:

| Run | As one job | Chunked |
|---|---|---|
| `lst_sharp`, 9 years | died in `pooled_composite` | fine |
| anomaly, 103 months | zero output in 25 min | a year per chunk |
| MODIS, 2018–2026 **daily** | zero output in 10 min | 2 parts in the first minute |

MODIS is the clearest: a daily collection over 8.5 years is ~3,100 images whose
timestamps `composite()` fetches in **one** `aggregate_array` request. One year
is ~365 and goes straight through. Suspect this whenever the collection is
daily or the span is long.

**The one cost:** interpolation is computed within a chunk, so the generated
transition across each boundary is missing — one small jump per boundary. With
`interpolate: 0` there is no cost at all. So chunk as coarsely as the run
survives: the delivered ten-year Sentinel-2 cuts use `--years 5` (one seam);
a yearly-chunked twin of a sibling cut came out with 1077 frames against 1257
and had to be re-rendered. Runs whose fetches are already cached (`lst_rf`
re-renders) need no chunking at all, and `smooth: harmonic` must never be
chunked — each chunk would fit its own seasonal curve.

## Run renders sequentially

Each `gee-animation` process already uses `cfg.workers` fetch threads. Three
processes at once stack their concurrency onto one Earth Engine quota and draw
**HTTP 429**, which is what killed a completed-95%-of-the-way `lst_sharp` run
here. The retry policy (3 attempts, 2 s/6 s) does not save you from sustained
contention.

Run one at a time. A shell loop is enough:

```bash
for c in config/a.yaml config/b.yaml; do gee-animation --config "$c"; done
```

## Reading silence

The CLI prints only `wrote <path>` lines, so quiet is normal *early*. What
distinguishes a slow run from a blocked one is **which** output has appeared:

- `capping fetch dimensions …` is logged inside `render()`. **Seen it?** the run
  reached rendering — it is slow, not stuck.
- **Zero bytes of log at all** means it never got past `composite()`, which is
  the chunking case above.

Launch background runs with `python -u` (or expect nothing): stdout buffering
kept one run's log at 61 bytes for eight hours here, which made a working run
look dead and a dead run look working.

```bash
ps -Ao pid,etime,rss,command | grep '[g]ee-animation --config'
```

## A stalled run after a network blip

Two Sentinel-2 runs sat for over an hour at **0 % CPU** after a transient
error (`thumbnail fetch attempt 1/3 failed (The read operation timed out)`,
then a DNS failure) and never resumed. The download loop retries with
timeouts; the hang is in a call outside it. The process looks alive, so judge
by progress, not by `ps`:

```bash
stat -f "%Sm" -t "%H:%M" $(ls -t out/<name>_part*_20*.png | head -1)   # newest raw frame
ps -Ao pid,etime,%cpu,command | grep '[g]ee-animation --config'          # 0.0 → stalled
```

No new frame for 20 minutes with the network back means kill the whole tree
and relaunch; the rerun resumes from cache. A chunk that *dies* with
`Not signed up for Earth Engine or project is not registered` hit a passing
auth blip: rerun that chunk alone (a config with the chunk's `start`/`end` and
`name: <name>_partNN`) and `ffmpeg -f concat -c copy` it to the finished parts.

## Stopping a run cleanly

**Killing the parent is not enough.** `render_chunked.py` launches
`gee-animation` as a child; killing the script leaves that child rendering and
still spending quota. Check for the orphan by the config it holds:

```bash
pkill -f "render_chunked"
ps -Ao pid,command | grep '[g]ee-animation --config'   # any survivor?
```

Killing mid-run is safe: cache writes are atomic, so a rerun resumes from
cached thumbnails rather than starting over.

## Red flags

| Symptom | Read it as |
|---|---|
| no log output at all after several minutes | blocked before rendering → chunk it |
| HTTP 429 | too many concurrent renders, not a transient blip |
| a run "finished" but wrote fewer frames than periods | months with no usable scene, not an error — check the count |
| stopped a chunked run and quota keeps burning | orphaned child process |
| a run with no new raw frame for 20+ min, 0 % CPU, after a network error | stalled, not slow — kill and relaunch |
| two `gee-animation` processes with the same config | a queue was restarted over an orphan — kill the older one |
