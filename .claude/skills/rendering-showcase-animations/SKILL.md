---
name: rendering-showcase-animations
description: Use when producing a presentation-, slide- or YouTube-grade animation from this repo — writing a new wne_cinema-style config, picking a frame bbox for 16:9, choosing pooling/interpolation, or deciding whether a re-render will be cheap or refetch everything.
---

# Rendering showcase animations

Copying `config/wne_cinema_ndvi_5yr.yaml` gets you 90% there. The 10% that
bites: the frame bbox must be **reshaped**, or a 16:9 render pillarboxes into
black bars — and knowing which edits are cache-free decides whether the next
render takes 5 minutes or 2 hours.

## Frame aspect: the bbox is not the AOI

Margins come out of the canvas *height*, so imagery only fills the width at one
specific aspect. A "square-ish" reserve bbox at `aspect: "16:9"` renders with
half the frame black.

"Two-line" means header line 2 exists **at all**. It exists if *any* of these is
true: `subtitle` is set (whatever its text), the run pools years, the run
interpolates, or the product is a titled composite. It does not have to be a
caveat.

| Preset | Header | Required **frame** aspect (w/h in **metres**) |
|---|---|---|
| 1080p / 4k / 720p | two-line — line 2 exists | **2.373** |
| 1080p / 4k / 720p | one-line — title only, no subtitle, no pooling, no interpolation | **2.136** |

Nearly every showcase run is two-line → **2.373**. Compute the bbox with
`scripts/fit_frame_bbox.py` (it handles the cos(latitude) correction — degrees
of longitude are shorter than degrees of latitude away from the equator):

```bash
python .claude/skills/rendering-showcase-animations/scripts/fit_frame_bbox.py \
    --region 13.60 52.85 14.05 53.10 --aspect 2.373
```

## Two AOIs, never one

`aoi.frame` = what the viewer sees (wide, reshaped). `aoi.region` = the subject
polygon: it drives `region_max_cloud_percent` scene gating and draws the
outline. Passing the same bbox for both gates clouds over the whole scene and
draws a rectangle around the picture. Use a shapefile/GeoJSON boundary for
`region`; keep `frame` several km larger so the subject sits in context.

No boundary polygon for the subject? Use the subject **bbox** as `region` and
still reshape `frame` around it — cloud gating then covers a rectangle rather
than the true shape, and the drawn outline is a rectangle. That is a real
downgrade, so say so in the handoff and get the polygon before publishing.
`docs/aoi/` only ships the WNE/Grumsin shapes.

## Cache economics — this is the speed lever

Thumbnails are cached on disk keyed by everything that changes what Earth
Engine computes. Re-render cost:

- **Free** (pure cache hit): `fps`, `interpolate`, `title`, `subtitle`,
  `credit`, `palette`, `quality`, `preset`, `aspect`, `gif`, `frames`,
  `raw_frames`, `geotiffs`, `region_line_width`, `workers`, `out_dir`
- **Full refetch**: `viz` min/max, `dimensions`, `crs`, `scale`, dates, either
  AOI, `mask_clouds`, cloud thresholds, `cadence`, pooling, `missions`

So iterate on look for free; batch every data-side change into one run.

## Recipe defaults that work

`cadence: monthly` · `pool_years: [firstYear-N, lastYear+N]` widened past the run
so gaps have donors · `pool_strategy: gap_fill` (the only one that keeps the year
you asked for) · `interpolate: 10` + `fps: 2` (cinema pacing, ~5.5 s per
transition) · `crs: auto` · `preset: 1080p` · `aspect: "16:9"` · `quality: 8`.

## Product traps

- **LST**: winters are mostly unobservable; frames freeze in masked holes during
  transitions. Use `cadence: quarterly` (pools 3–7 passes per frame → near
  hole-free) when the seasonal cycle matters more than monthly detail.
- **rgb / cir only**: `mask_clouds: false` shows real clouds instead of grey
  cutouts. Rejected by `validate()` for palette indices — a colorized cloud
  would read as real data.
- **evi / ndwi / ndmi**: registry defaults are a flat −1..1 that wastes half the
  ramp. Pin `viz` (evi 0..1, ndwi −0.3..0.6, ndmi −0.5..0.8).

## Delivery

Masters are large (300–600 MB). For chat/upload, downscale to 720p with
`quality: 4–5`; check with `ffprobe -show_entries stream=nb_frames`.
