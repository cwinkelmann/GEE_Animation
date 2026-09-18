---
name: diagnosing-render-artifacts
description: Use when a rendered frame or animation looks wrong — saturated colour, areas that appear to move or flicker, a region that should stand out but does not, or a viewer reporting "these must be artifacts" — before changing any masking, compositing or config.
---

# Diagnosing a frame that looks wrong

Most "artifacts" reported on these animations were not artifacts. Two rounds of
cloud-masking fixes were built here to chase a saturated-looking LST frame whose
data was correct all along; the actual fault was the colour ramp. Check in the
order below — it is cheapest-first *and* likeliest-first.

## 1. Is it the ramp, not the data?

**Do this before anything else.** A fixed `viz` range wide enough to span winter
and summer compresses summer's real spread into one colour band.

Worked example: Grumsin LST measured **28.2 °C inside the reserve vs 30.5 °C
outside** — a real, physically correct signal. On a −3…46 °C ramp that 2.3 °C
difference is ~5% of the width, and the frame reads as uniform red. On a
15…45 °C ramp the reserve is an obvious cool island. Same data, same file.

Rule of thumb: if the data's actual spread occupies **less than ~20%** of
`viz_max − viz_min`, the ramp is the problem. Get the spread from
`out/metadata.db` (see §3) or a GeoTIFF export — never by eye, since the whole
failure mode is that your eye cannot see it.

## 2. Apply the physical sanity check

Ask what the scene *must* look like if the data is right:

- **Thermal**: forest runs cooler than bare or harvested field. Water is cooler
  still by day. If forest and field are indistinguishable in summer, either the
  ramp is compressing them (§1) or the retrieval is contaminated.
- **NDVI**: water is negative, dense canopy high, bare soil low-positive.
- **Surface ≠ air temperature.** 40–46 °C on dry soil under clear summer sun is
  ordinary. Do not treat a high number as evidence of a bug.

## 3. Query the numbers before theorising

`out/metadata.db`, table `frame_clouds`: `aoi_mean`, `aoi_p10`, `aoi_p90`,
`outside_mean`, `outside_p10`, `outside_p90`, `n_scenes`, `aoi_clear_fraction`.

**Rows exist only for runs configured with `metadata: true`** — check
`SELECT DISTINCT name FROM frame_clouds` first. Querying a run that never wrote
stats looks like "no signal" and is really "no data", which has misled a
diagnosis here already.

## 4. Separate the scenes before blaming the composite

`debug_month: "YYYY-MM"` exports each input scene *and* the composite to
`out/debug/<month>/`. Two independently clear scenes that agree means the
imagery is fine and the problem is downstream. One clear and one contaminated
means the median smeared them.

## 5. Only now suspect masking or compositing

Real causes, in the order they actually occurred here:

- **Thin composites.** `min_scenes: 1` lets a month rest on a single pass, so
  consecutive frames sample unrelated weather. Raise `min_scenes`, or use a
  coarser `cadence` (quarterly pools 3–7 passes).
- **Apparent motion is usually absence.** `blend()` deliberately *holds* the
  valid endpoint where one side is masked rather than fading through grey. With
  1–2 passes per month, whole regions freeze through a transition and then snap
  — it reads as movement but it is a hole.
- **Undetected cloud** genuinely happens (thin edges, haze) and shows as
  plausible-but-wrong values. This is the *last* hypothesis, not the first.

## Red flags

| You are about to… | Stop, because |
|---|---|
| change the cloud mask on visual evidence alone | that was wrong twice here; get numbers first |
| conclude "contaminated" from one frame | check whether *both* input scenes agree (§4) |
| trust a `metadata.db` query returning nothing | the run may not have written stats at all |
| call a high surface temperature impossible | it is skin temperature, not air temperature |
