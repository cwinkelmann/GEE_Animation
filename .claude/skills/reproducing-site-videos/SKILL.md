---
name: reproducing-site-videos
description: Use when re-rendering, extending or delivering the Tegel (R12, R13) and Grumsin video sets — which config makes which video, which worktree's code renders it, chunking per product, the stall-and-restart recipe, verification, and where the masters, Drive copies and PDF guides go.
---

# Reproducing the site video sets

Every delivered video is one config file plus one command. What costs time is
knowing *which* code renders it, *how* to chunk it, and recognising a stalled
run. All three are below; the rest is waiting.

## Where things are

| What | Where |
|---|---|
| Site configs (`r12_*`, `r13_*`, `wne_*`) | `config/` on branch `experiment/windthrow-analysis` (worktree `../GEE_animation-windthrow`) |
| Renderer code to use | `main` (or the worktree that is byte-equal to it) |
| Stem-density overlays, footprints | `docs/aoi/r12/`, `docs/aoi/r13/` on both branches |
| Radar videos | `spike/sar-windthrow`: `WT_SITE=R13 SAR_DRAW_REGION=0 python scripts/sar_video.py abs` / `delta` |
| Render output | `out/` in each worktree, a symlink to `/Volumes/2TB/winmol/GEE_Animation/_tmp_out/<worktree>_out` |
| Masters | `/Volumes/2TB/winmol/GEE_Animation/out/HNEE_LST/videos` (linked from `~/Downloads/HNEE/LST`) |
| Shared copies | Google Drive `public/HNEE/WINMOL/{R12,R13,Grumsin_control}`, each folder with a PDF guide |
| Guide generator | `docs/experiments/windthrow/video_guide/video_guide.py` (windthrow branch) |

## The code that renders is not the code in front of you

`gee-animation` and `python -m gee_animation` resolve through the **editable
install**, which points at one fixed clone — whichever branch that clone has
checked out. A worktree on `main` can silently render with an older branch's
code (one run here lost the overlay feature that way). Always pin it:

```bash
cd /path/to/worktree && export PYTHONPATH=$PWD
python -c "import gee_animation; print(gee_animation.__file__)"   # must be this worktree
```

`scripts/render_chunked.py` spawns `gee-animation` as a child; the exported
`PYTHONPATH` reaches it. Configs reference `docs/aoi/...` relative to the cwd,
so run from the worktree that has the AOI files.

## Which config makes which video

Names are compositional; the suffixes are single config keys, and most are
client-side (a pure cache hit on the base run's thumbnails):

| Suffix | Config difference | Refetches? |
|---|---|---|
| `cinema_` / `zoom_` / `focus_` | wide 16:9 frame / footprint + 0.4 km / footprint + `region_only: true` | frame change: yes |
| `_grid` | `render.pixel_grid: true`, `upscale: nearest` | no |
| `_noaoi` | `draw_region: false` | no |
| `_stems` | `overlay:` block pointing at `docs/aoi/r1x/r1x_stem_kde40_5m.tif` | no |
| `lst_rf_local` / `lst_rf_delta_local` | `sharpen: local` / plus `relative: region_mean` | no between the two |
| `lst_pretty` | `smooth: harmonic`, `harmonics: 2`, Landsat L8/L9 only | — |
| `nbr` / `ndvi` / `rgb` | `index:`; NBR and the composite use `scale: 20` / `mask_clouds: false` | yes |
| `_2025_2026` | `start/end` and `pool_years` limited to the post-storm months, `fps: 3` | no |

To add an edition, copy the closest config, change the one key, change `name`.
The six configs added on 2026-10-05 were made exactly that way (see the commit
"Site configs for the six videos that complete the R12/R13 sets").

## Chunking per product — decided before launching

| Product | How to run | Why |
|---|---|---|
| Sentinel-2 ten-year runs (`ndvi`, `nbr`, `rgb`) | `render_chunked.py --years 5` | one seam (Jan 2022) like the delivered NDVI cuts; yearly chunks put a jump at every January |
| `lst_rf_*` ten-year runs | **unchunked** `python -u -m gee_animation.cli` | the forest trains per frame; once thumbnails are cached a full run is ~20 min and keeps every transition |
| `lst_pretty*` (harmonic) | **never chunk** | each chunk would fit its own seasonal curve |
| short runs (`_2025_2026`) | direct | 16 months |

Run one job at a time (shared Earth Engine quota). A queue is a shell loop with
`python -u` and a START/END line per job:

```bash
run(){ echo "[$(date +%T)] START $1"; python -u -m gee_animation.cli --config config/$1.yaml > logs/$1.log 2>&1; echo "[$(date +%T)] END $1 rc=$?"; }
```

## A stalled run looks finished

Twice in one evening a Sentinel-2 run sat for over an hour at 0 % CPU after a
transient network error (`read operation timed out`, then a DNS failure),
never to continue. The download loop has timeouts and retries; the hang is
outside it. Detect it by the newest raw frame, not by the process:

```bash
stat -f "%Sm" -t "%H:%M" $(ls -t out/<name>_part*_20*.png | head -1)   # vs now
ps -Ao pid,etime,%cpu,command | grep '[g]ee-animation --config'          # 0.0 %cpu = stalled
```

Then kill the whole tree (queue script, `render_chunked`, `gee-animation`) and
relaunch: everything fetched so far is cached, so the rerun costs minutes.

A chunk that dies with `Not signed up for Earth Engine or project is not
registered` hit a passing auth blip, not a real auth problem. Rerun **that chunk
alone** rather than the whole set — write the chunk's config with its `start`/
`end` and `name: <name>_partNN`, render it, then join:

```bash
printf "file '%s'\nfile '%s'\n" $PWD/out/X_part01.mp4 $PWD/out/X_part02.mp4 > out/_parts.txt
ffmpeg -f concat -safe 0 -i out/_parts.txt -c copy out/X.mp4
```

## Verify before copying

`ffprobe -v error -select_streams v:0 -show_entries stream=nb_frames -of csv=p=0 X.mp4`
and compare with the sibling cut of the same kind. Ten-year monthly runs with
`interpolate: 10` give **1256** (Sentinel-2) or **1257** (`lst_rf`) frames
unchunked, **1171** for the harmonic LST cuts, **164** for `_2025_2026`. A
yearly-chunked run shows ~1077: nine missing transitions, re-render it. Also
count the raw frames per chunk (`ls out | grep -c 'X_part02_20..-..\.png'`): a
chunk that finished with fewer months than its span failed silently.

## Deliver

1. `cp -p out/X.mp4 /Volumes/2TB/winmol/GEE_Animation/out/HNEE_LST/videos/`
2. copy to the Drive folder by site (`r12_*` → R12, `r13_*` → R13, `wne_*` → Grumsin_control); check sizes match.
3. regenerate the guides: `python video_guide.py <videos_dir> <R12 dir> <R13 dir> <work dir> [<Grumsin dir>]` — it extracts a post-storm thumbnail from every video and typesets one PDF per site (needs pandoc + LuaLaTeX).
4. update the deliverables line in the report's Appendix B and the `HNEE_LST/README.md` count.

## Layout generations

Videos rendered before 2026-10-06 draw the legend on the map and size all text by
frame height (portrait canvases got doubled text). From that date the legend sits
in the header's right part and sizes follow the 16:9-equivalent height. Do not mix
the two generations in one delivery: a config change that forces a re-render of one
cut means re-rendering its siblings too, and all of it is cache-hit encode time.
