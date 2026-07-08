# GEE NDVI Forest Timelapse — Design

**Date:** 2026-07-08
**Author:** Christian Winkelmann
**Status:** Approved (brainstorming)

## Purpose

A Python package with a thin CLI that uses Google Earth Engine (GEE) to build a
time-series of satellite imagery over a forest area of interest, computes NDVI,
and renders it into an animated timelapse (MP4 + GIF). Intended for
vegetation/forest change work at HNEE (Hochschule für nachhaltige Entwicklung
Eberswalde); default GEE project is `hnee-331218`.

The animation is assembled **frame-by-frame locally** (approach B): each frame is
a real image we download and annotate (date label, NDVI colorbar), then stitch
together. This gives full control over per-frame styling versus a server-side
`getVideoThumb`.

## Scope

**In scope (v1):**
- Sentinel-2 surface-reflectance NDVI timelapse over a configurable AOI.
- Monthly cloud-masked median compositing (one frame per month).
- Config-driven runs (`config.yaml`); thin CLI entry point.
- Output as both MP4 and GIF, with per-frame date labels and a shared NDVI colorbar.

**Out of scope (v1), noted for later:**
- Landsat / multi-sensor support (structure allows adding it).
- True-color RGB or land-cover/disturbance animations.
- Per-scene (non-composited) rendering — cadence is pluggable but only monthly is shipped.
- Web UI / interactive map.

## Chosen defaults

| Decision | Default | Rationale |
|---|---|---|
| Sensor | Sentinel-2 SR (2015–now, 10 m) | High resolution, best for recent multi-year forest NDVI |
| Cadence | Monthly median composite | Smooth, gap-free NDVI; cloud-robust |
| Cloud masking | Sentinel-2 SCL band | Removes cloud/shadow before median |
| NDVI | (B8 − B4) / (B8 + B4) | Standard NDVI for S2 |
| NDVI palette / scale | brown→green, fixed −0.2…0.9 | Color comparable across all frames |
| Output | MP4 + GIF | MP4 for quality, GIF for embedding |
| EE project | `hnee-331218` | From existing bootstrap script |

## Architecture

Config-driven package with a thin CLI. Each module has one clear purpose and a
narrow interface so it can be understood and tested independently.

```
gee_animation/
  __init__.py
  auth.py         # init(): ee.Authenticate() + ee.Initialize(project=...)
  config.py       # RunConfig dataclass + YAML load/validate
  aoi.py          # parse(): bbox or GeoJSON path -> ee.Geometry
  collection.py   # build(): S2 collection, cloud mask (SCL), add NDVI band
  compositing.py  # monthly_median(): group by month -> list[ee.Image] frames
  render.py       # render(frames): thumbnail -> annotate -> assemble MP4/GIF
  cli.py          # `gee-animation --config config.yaml`
config.example.yaml
out/              # generated output (gitignored)
tests/
```

### Module contracts

- **auth.init(project) -> None** — authenticates and initializes EE. Idempotent.
  Depends on: `earthengine-api`.
- **config.RunConfig** — dataclass holding AOI, date range, sensor, cadence, viz
  params, fps, output name/dir. `RunConfig.from_yaml(path)` loads and validates.
  Pure; no EE dependency.
- **aoi.parse(aoi_cfg) -> ee.Geometry** — accepts a bbox `[minLon, minLat, maxLon,
  maxLat]` or a GeoJSON file path. Depends on: `ee`.
- **collection.build(cfg, geometry) -> ee.ImageCollection** — filters S2 by date
  and geometry, applies SCL cloud/shadow mask, adds an `NDVI` band. Depends on:
  `ee`.
- **compositing.monthly_median(collection, cfg) -> list[ee.Image]** — groups the
  collection into months over the date range and reduces each to a median NDVI
  image; returns an ordered list of frame images with a `label` (e.g. `2023-06`).
  Depends on: `ee`.
- **render.render(frames, cfg) -> list[Path]** — for each frame, download an NDVI
  thumbnail PNG via `getThumbURL`, colorize with the fixed palette, stamp the
  month label, append the shared colorbar; stitch frames into MP4 (ffmpeg) and
  GIF (imageio). Returns output file paths. Depends on: `ee`, `Pillow`,
  `imageio`, `ffmpeg` (optional).

### Data flow

```
config.yaml
  -> auth.init(project)
  -> aoi.parse(cfg.aoi)                    -> ee.Geometry
  -> collection.build(cfg, geometry)       -> ee.ImageCollection (masked, +NDVI)
  -> compositing.monthly_median(coll, cfg) -> [ee.Image, ...] (one per month)
  -> render.render(frames, cfg)            -> out/<name>.mp4, out/<name>.gif
```

## Error handling

Fail fast with actionable messages:

- **EE auth/init failure** — surface EE's error plus a hint to run
  `earthengine authenticate` / check the project id.
- **Empty collection** — if the filtered collection or a month has no images,
  raise a clear error naming the AOI/date/cloud-filter that was too strict
  (skip empty months rather than emitting black frames, and log which were skipped).
- **Thumbnail download errors** — retry with exponential backoff; fail with the
  frame label if it persists.
- **Missing `ffmpeg`** — detect and fall back to GIF-only with a warning rather
  than crashing.

## Testing strategy

- **Unit tests (no live EE):** AOI parsing (bbox + GeoJSON), NDVI math on synthetic
  arrays, `RunConfig` validation (bad dates/bbox rejected), monthly grouping logic,
  ffmpeg-missing fallback. EE objects are stubbed/mocked at module boundaries.
- **Integration test (opt-in):** one test that renders a tiny 3-frame AOI against
  live EE, skipped automatically when credentials are absent (marker/env guard).
- Run a single test with `pytest tests/test_x.py::test_name`.

## Reproducibility

All run parameters live in `config.yaml`, so a run is fully described by its
config. The existing `01_animation_single_images.py` becomes a thin example that
calls the package (kept as a demo, not the core).

## Open questions / assumptions to confirm

- Confirm Sentinel-2 vs Landsat (assumed S2) — swappable later via a sensor field.
- Confirm monthly cadence (assumed) — cadence is pluggable if yearly/seasonal wanted.
- CLI framework: `argparse` (stdlib, zero deps) vs `typer` — assume `argparse` for
  v1 to keep dependencies minimal.
