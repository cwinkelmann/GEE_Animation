# How each product is rendered

Reference for what the pipeline actually computes, per product. Every value here
was read out of `gee_animation/products.py`; if the two disagree, the code wins
and this file is stale.

For *why* a frame looks the way it does to a viewer (labels, provenance, no-data),
see `how-the-timeseries-is-made.md`. For picking config values, see the
`rendering-showcase-animations` skill.

---

## The shared path

Every product goes through the same eight steps. Only steps 2–4 differ per
product.

1. **Collection** — `collection.build()` loads the sensor's collection and filters
   by date and frame bounds. Cross-year pooling widens the date range first, so
   donor scenes are in scope from the start.
2. **Auxiliary join** — Sentinel-2 only: the s2cloudless probability image is
   joined onto each granule as a `cloud_prob` band (`Sensor.attach_aux`). A
   granule with no probability match is *dropped* — a scene that cannot be
   cloud-screened must not enter a composite as if it were clear.
3. **Scene gating** — two filters, both skippable only by `inventory.py`:
   `max_cloud_percent` against the sensor's own scene metadata, then
   `region_max_cloud_percent` against a cloud fraction computed over the region
   polygon.
4. **Per-pixel cloud mask** — see the sensor table below. Skipped entirely when
   `mask_clouds: false`, which `config.validate()` permits only for `rgb`/`cir`.
5. **Index computation** — the formulas below, producing a single `INDEX` band
   (or three bands `R`/`G`/`B` for composites). Source properties are copied
   forward so pooling and inventory can still read scene metadata.
6. **Compositing** — `compositing.composite()` buckets scenes into cadence
   periods and takes a per-period median. Pooled strategies (`gap_fill`,
   `least_cloudy`, `median`) may substitute another year's imagery; the borrowed
   year is carried on `Frame.source` and drawn on the frame.
7. **Fetch and colorize** — one `getThumbURL` per frame, capped to the product's
   native resolution, decoded to index units, then coloured with the fixed `viz`
   range so colour means the same thing in every frame. Composites arrive from
   Earth Engine already coloured.
8. **Overlays** — no-data grey, upscale, region outline, scale bar, north arrow,
   letterbox, header, credit, marker dot, legend. Georeferenced overlays are
   drawn *before* the canvas grows, or they would slide off their pixels.

---

## Cloud screening, per sensor

| Sensor | Per-pixel mask | Notes |
|---|---|---|
| `sentinel2` | SCL classes 3, 8, 9, 10, 11 (shadow, cloud med/high, cirrus, snow) **AND** s2cloudless `probability ≤ 40` | SCL alone detects cloud cores but misses thin edges and haze, which then colorize as plausible index values. The threshold is stricter than the usual 50–60: over a temperate forest, over-masking costs a few grey pixels, under-masking costs credibility. |
| `landsat` | `QA_PIXEL` flag bits 1–5 (dilated cloud, cirrus, cloud, shadow, snow) **AND** medium-or-higher confidence for cloud (bits 8–9), shadow (10–11), cirrus (14–15) | The confidence pairs are the Landsat counterpart of the s2cloudless screen. |
| `modis` | `StateQA` cloud state, cloud shadow, internal cloud flag | Coarse; MODIS has no per-scene cloud metadata, so `max_cloud_percent` is ignored and only the region filter applies. |
| `modis_lst` | `QC_Day` mandatory-QA ≤ 1 | LST is only retrieved under clear sky, so the band is already masked; this drops "not produced" pixels too. |

Additionally, the `lst` product gates on **`ST_QA` ≤ 5 K** retrieval
uncertainty. Cloud-contaminated thermal retrievals carry high uncertainty even
when CFMask never flags a cloud — that gate is what removes the cold and hot
smears undetected cloud leaves behind.

---

## Products

Bands are canonical roles; each sensor maps them to its own (Sentinel-2 NIR =
B8, Landsat NIR = SR_B5 for OLI). Reflectance scaling is per sensor: Sentinel-2
`×0.0001`, Landsat C2 L2 `×0.0000275 − 0.2`, MODIS `×0.0001`.

| Product | Sensors | Native | Default viz | Units | Legend reads |
|---|---|---|---|---|---|
| `ndvi` | S2 / Landsat / MODIS | 10 / 30 / 500 m | −0.2 … 1.0 | — | water → dense vegetation |
| `evi` | S2 / Landsat / MODIS | 10 / 30 / 500 m | −1.0 … 1.0 | — | bare → dense vegetation |
| `ndwi` | S2 / Landsat / MODIS | 10 / 30 / 500 m | −1.0 … 1.0 | — | dry → water |
| `ndmi` | S2 / Landsat / MODIS | **20** / 30 / 500 m | −1.0 … 1.0 | — | dry → moist |
| `ndre` | Sentinel-2 only | **20** m | −0.2 … 1.0 | — | bare/stressed → dense vegetation |
| `rgb` | S2 / Landsat / MODIS | 10 / 30 / 500 m | 0.0 … 0.3 | — | *(composite: no legend)* |
| `cir` | S2 / Landsat / MODIS | 10 / 30 / 500 m | 0.0 … 0.3 | — | *(composite: no legend)* |
| `lst` | Landsat | 100 m | −10 … 40 | °C | cooler → warmer |
| `lst_smw` | Landsat | 100 m | −10 … 40 | °C | cooler → warmer |
| `lst_sharp` | Landsat | **30** m | −10 … 40 | °C | cooler → warmer |
| `lst_rf` (experimental) | Landsat (+ Sentinel-2 predictors) | **20** m | −10 … 40 | °C | cooler → warmer |
| `lst_modis` | MODIS (MOD11A1) | 1000 m | −10 … 40 | °C | cooler → warmer |
| `landcover` | Dynamic World | 10 m | *(classes)* | — | 9 labelled swatches |

### Vegetation and water indices

- **NDVI** — `(NIR − Red) / (NIR + Red)`. The default ramp is CVD-safe: two blue
  stops below 0 for water, a pale-yellow buffer, then ColorBrewer greens. It is
  *not* a water mask — turbid or vegetated water with slightly positive NDVI
  still renders brownish.
- **EVI** — `2.5·(NIR − Red) / (NIR + 6·Red − 7.5·Blue + 1)`. Standard MODIS
  coefficients, computed on scaled reflectance because the offset matters.
  Saturates less than NDVI over dense canopy.
- **NDWI** — `(Green − NIR) / (Green + NIR)`, McFeeters open water.
- **NDMI** — `(NIR − SWIR1) / (NIR + SWIR1)`, canopy/soil moisture. Uses the
  20 m SWIR band on Sentinel-2, so it renders at 20 m, not 10 m.
- **NDRE** — `(NIR − RedEdge) / (NIR + RedEdge)`, Sentinel-2 B5. Chlorophyll and
  nitrogen sensitive; saturates later than NDVI. **Its viz range and palette are
  conventional guesses, not an empirical sweep** — check them against real
  imagery before publishing.

The registry defaults of −1…1 on EVI/NDWI/NDMI waste half the ramp for most
scenes; the showcase configs pin tighter ranges (EVI 0…1, NDWI −0.3…0.6,
NDMI −0.5…0.8).

### Composites

- **RGB** — true colour, R/G/B from the red, green and blue bands.
- **CIR** — false colour, R←NIR, G←Red, B←Green, so vegetation reads red.

Composites arrive already coloured, so they have no `INDEX` band, no legend,
and no meaningful `aoi_mean`. They are the only products allowed to set
`mask_clouds: false`, which keeps real white clouds instead of grey cutouts —
for a photo-like product a cloud looks like a cloud, whereas a palette index
would colorize it into a plausible false value.

### Land cover (categorical)

`landcover` is the only **classified** product, and it behaves differently at
three points in the pipeline:

- **Compositing.** A period's image is the argmax of the *mean* class
  probabilities, not a median. The median of `{water=0, trees=1, built=6}` is
  "trees" — an artefact of the numbering, not a fact about the ground. Averaging
  probabilities is Dynamic World's own recipe and uses every pass's confidence
  rather than only its winner. The hook is `Index.reduce_period`, and every site
  that forms a period image routes through `compositing._period_image`.
- **Legend.** Labelled swatches (`render.add_class_legend`), not a ramp: a ramp
  answers "how much", a class map answers "what". Classes absent from a frame
  stay listed so the legend does not change length between frames.
- **Interpolation is forbidden.** `validate()` rejects `render.interpolate > 0`,
  because blending two class colours yields a colour no class owns and a viewer
  would read the in-between frames as a category that does not exist. Use
  `upscale: nearest` for the same reason.

Dynamic World is Sentinel-2 derived, 10 m, from 2015-06-27, and already
cloud-screened at source — unseen pixels arrive masked, so the sensor applies no
cloud mask of its own and carries no per-scene cloud metadata. Quarterly cadence
is the sensible default: monthly land cover flickers between classes on marginal
pixels without anything having changed.

Class colours are applied with `remap`, never a palette stretch, so each class
lands on its exact Dynamic World colour.

### Thermal

Four different things, all reported in °C:

- **`lst`** — the USGS Collection-2 Level-2 Surface Temperature band,
  `ST_B × 0.00341802 + 149.0 − 273.15`, with the `ST_QA ≤ 5 K` gate.
- **`lst_smw`** — the Statistical **Mono**-Window algorithm of Ermida et al.
  (2020): `A·Tb/ε + B/ε + C` from TOA brightness temperature, ASTER-GED
  emissivity and NCEP water vapour, with per-satellite coefficients for
  L4/L5/L7/L8/L9. (Mono-window uses *one* thermal band; split-window is the
  two-band family such as MODIS MxD11 — this is not that.)
- **`lst_sharp`** — TsHARP: fit LST against NIRv at 100 m, apply the fit at
  30 m, add the coarse residual. An **approximation** that sharpens an existing
  retrieval; it invents no new thermal information and is not ECOSTRESS.
- **`lst_modis`** — MOD11A1 daily `LST_Day_1km × 0.02 − 273.15`. Coarse, but its
  daily revisit fills months Landsat's 16-day repeat misses entirely.

Thermal products default to **L8/L9 only** — Landsat 7's SLC-off gaps and the
coarser TM/ETM+ thermal band otherwise stripe a few-scene median. Override with
`missions:` when you want the full archive, and expect that trade.

`lst` and `lst_smw` typically agree within about 1–3 K.

---

## Per-frame statistics

With `metadata: true`, every frame writes a row to `out/metadata.db`
(`frame_clouds` table):

| Column | Meaning |
|---|---|
| `month` | period key — `YYYY-MM`, `YYYY-MM-DD` or `YYYY-Qn` |
| `n_scenes` | satellite passes composited into the frame |
| `aoi_cloud_fraction` / `aoi_clear_fraction` | share of region pixels with no / some valid value |
| `aoi_mean`, `aoi_p10`, `aoi_p90` | index distribution inside the region |
| `outside_mean`, `outside_p10`, `outside_p90` | same, over the frame **minus** the region |

Composites leave every statistic null — three colour channels have no single
meaningful value. Reducers ignore masked pixels, so a cloudy month does not drag
the mean down; read `n_scenes` and `aoi_clear_fraction` alongside any value.

---

## What a re-render costs

Cached thumbnails are keyed on everything that changes what Earth Engine
computes:

- **Free** — `fps`, `interpolate`, `title`, `subtitle`, `credit`, `palette`,
  `quality`, `preset`, `aspect`, `gif`, `frames`, `raw_frames`, `geotiffs`,
  `region_line_width`, `pixel_grid`, `workers`, `out_dir`
- **Refetches everything** — `viz`, `dimensions`, `crs`, `scale`, dates, either
  AOI, `mask_clouds`, cloud thresholds, `cadence`, pooling, `missions`

Changing the masking code also invalidates the cache, via `cache.CACHE_VERSION`.

## Exports

`raw_frames: true` writes `<name>_raw_<period>.png` — the colorized map with the
region outline and nothing else (no header, legend, scale bar, credit or
marker). `geotiffs: true` writes `out/geotiffs/<name>/<name>_<period>.tif`:
real float values rather than colours, on the native grid in the run's CRS,
cast to float32 and clamped so the request stays under Earth Engine's 48 MiB
synchronous download cap.
