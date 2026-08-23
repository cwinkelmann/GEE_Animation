---
name: presenting-vs-measuring
description: Use when a run has to serve an audience (talk, YouTube, poster) or produce numbers (a plot, a paper, a statistic) — deciding pooling, interpolation, resampling and frame geometry, and knowing which of those can be changed for free.
---

# Presenting vs measuring

The same AOI and dates serve two incompatible goals. A showcase wants no gaps,
smooth motion and legible colour; a measurement wants every frame to be real
data for its own period. **Produce two runs, not one compromise.** They share a
thumbnail cache, so the second is far cheaper than the first.

| | Presenting | Measuring |
|---|---|---|
| `pool_years` / `pool_strategy` | `gap_fill` — no month missing | **off** — no borrowed pixels |
| `render.interpolate` | 5–10, smooth motion | `0` |
| `render.upscale` | `lanczos` | `nearest` (never invent detail) |
| `viz` | narrow, so colour varies | whatever the analysis needs |
| `metadata` | off | **`true`** |
| frame `bbox` | reshaped to fill 16:9 | symmetric buffer around the region |

## Pretty and honest are not opposites

Every frame carries its own provenance: a gap-filled frame reads "image from
2021", a generated frame gets a hollow marker dot and a percentage, no-data stays
grey. So the showcase cut can be smooth *and* truthful — the caption does the
work the pixels cannot. Do not strip pooling or interpolation from a presentation
in the name of rigour; strip them from the run you compute from.

## The trap that matters most

`metadata.db` has **no column marking a frame as borrowed.** If you enable
pooling and `metadata: true` together, gap-filled frames land in the statistics
table indistinguishable from real ones, and a plot silently mixes 2019 with
2021 pixels. Several shipped example configs combine the two — do not copy that
pattern into a measurement run.

Measured cost of pooling on this AOI: **34 of 103 months** were borrowed, a third
of the series.

## Frame geometry differs by purpose

For video the frame is reshaped to fill the canvas (aspect ≈ 2.373 at 1080p; see
`rendering-showcase-animations`). For statistics that elongated rectangle makes
"outside the region" an artefact of the *video aspect* rather than a meaningful
comparison area. Use a symmetric buffer around the region instead, so
`outside_mean` describes the surrounding landscape rather than a letterbox.

## Read the statistics with their caveats

Reducers ignore masked pixels, so `aoi_mean` for a cloudy month is an average
over whatever stayed visible — not over the region. Always read `n_scenes` and
`aoi_clear_fraction` alongside any value, and treat a one-pass month as weaker
evidence than a four-pass one.

Methods are not interchangeable either: `lst` and `lst_smw` typically sit 1–3 K
apart, so a figure from one must not be compared against a figure from the other.

## What a re-cut costs

Client-side (**free**, pure cache hit): `fps`, `interpolate`, `interpolate_mode`,
`title`, `subtitle`, `credit`, `palette`, `quality`, `preset`, `aspect`, `gif`,
`frames`, `raw_frames`, `geotiffs`, `region_line_width`, `workers`, `upscale`.

Server-side (**full refetch**): `viz` min/max, `dimensions`, `scale`, `crs`,
dates, either AOI, `mask_clouds`, cloud thresholds, `cadence`, pooling,
`missions`.

The counterintuitive pair: changing the **palette** is free (applied locally),
changing the **viz range** is not (the stretch happens inside the Earth Engine
thumbnail request). So iterate on colour *ramp* freely; batch every range change.
