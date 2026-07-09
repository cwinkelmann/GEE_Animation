# Sensor + Index Abstraction (Landsat LST) — Design

**Date:** 2026-07-09
**Status:** Draft — awaiting user review

## Context

`docs/FEATURES.md` asks for (2) Landsat Surface Temperature and (3) computing/
visualizing multiple indices (NDVI, EVI, LST, water/humidity) across **Landsat,
MODIS, and Sentinel-2**. The current pipeline hardcodes **Sentinel-2 + NDVI**:

- `config.py` validates `sensor ∈ {sentinel2}` and carries `ndvi_min/max/palette`.
- `collection.py` uses `COPERNICUS/S2_SR_HARMONIZED`, SCL cloud masking, and
  `add_ndvi` (B8/B4 → band `"NDVI"`).
- `render._fetch_thumbnail` selects the `"NDVI"` band and colours it with the
  `ndvi_*` viz.

This spec covers the **first increment** of the generalization: a **sensor +
index abstraction** that ships **Sentinel-2 (NDVI)** and **Landsat (NDVI + LST)**.
It delivers feature 2 (LST) and proves the multi-sensor / multi-index design.

**Deferred to follow-up increments (not this spec):** EVI, water/humidity indices
(NDWI/NDMI), and the MODIS sensor. Once this abstraction exists, each of those is a
new registry entry, not a pipeline change.

## Goals / non-goals

- **Goal:** one config can select `(sensor, index)`; the pipeline computes/masks/
  composites/renders that product; LST works end to end on Landsat.
- **Goal:** the render/compositing layers become index-agnostic.
- **Non-goal:** blending sensors in one animation; per-frame multi-index panels;
  sub-monthly cadence. One `(sensor, index)` per run.

## Config changes

```yaml
sensor: landsat            # sentinel2 | landsat
index: lst                 # ndvi | lst   (must be supported by the sensor)
viz:                       # OPTIONAL — overrides the index's built-in defaults
  min: 0
  max: 40
  palette: ["#000080", "#00ffff", "#ffff00", "#ff0000", "#800000"]
max_cloud_percent: 60      # coarse scene pre-filter (sensor's own cloud property)
```

- Replace the `ndvi: {min,max,palette}` block with an **optional** `viz:` block.
  When omitted, each index supplies default `min/max/palette`.
- `RunConfig` fields: add `index: str`; replace `ndvi_min/max/palette` with
  `viz_min/viz_max/palette` that default (in `from_yaml`) to the resolved index's
  built-ins when `viz:` is absent.
- Validation: `sensor ∈ SENSORS`; `index ∈ INDICES`; `sensor ∈ index.sensors`
  (e.g. LST only on `landsat`) — else `ConfigError` naming the offending pair.

## Architecture

New module `gee_animation/products.py` with two registries and a resolver. Each
piece has one responsibility and is unit-testable with a fake `ee`.

### SensorSpec (per sensor)
- `collection(ee_module) -> ee.ImageCollection` — e.g. Landsat merges
  `LANDSAT/LC08/C02/T1_L2` + `LANDSAT/LC09/C02/T1_L2`.
- `scene_cloud_property: str` — coarse filter field (`CLOUDY_PIXEL_PERCENTAGE`
  for S2, `CLOUD_COVER` for Landsat).
- `mask_clouds(image, ee_module) -> image` — S2: SCL classes `[3,8,9,10,11]`;
  Landsat: `QA_PIXEL` bits (dilated cloud=1, cirrus=2, cloud=3, shadow=4, snow=5).
- `cloud_band(image, ee_module) -> 0/1 image` — the cloudy-pixel indicator used
  for the in-region cloud fraction (S2: SCL remap; Landsat: QA_PIXEL bits).
- `reflectance(image, ee_module) -> image` — scaled SR bands under **canonical
  aliases** `red, nir, blue, green, swir1, swir2` (S2: ×1e-4 on B4/B8/…; Landsat:
  `SR_B* × 2.75e-5 − 0.2`). Indices consume canonical aliases so their formulas
  are written once.

### IndexSpec (per index)
- `sensors: set[str]` — which sensors can compute it (ndvi: both; lst: {landsat}).
- `compute(image, sensor, ee_module) -> ee.Image` — one band renamed **`INDEX`**.
  - `ndvi` = `(nir − red)/(nir + red)` on `sensor.reflectance(image)` (scale-then-
    ratio; Landsat SR has an offset so raw `normalizedDifference` would be wrong).
  - `lst` = `ST_B10 × 0.00341802 + 149.0 − 273.15` (°C).
- `default_viz: (min, max, palette)` — ndvi: `(-0.2, 0.9, brown→green)`;
  lst: `(0, 40, blue→cyan→yellow→red)`.

### Resolver
`get_product(sensor, index) -> Product` bundling the SensorSpec + IndexSpec, or
raising `ValueError`/`ConfigError` for an unsupported pair.

## Pipeline changes

- **`collection.build(cfg, frame_geom, region_geom)`** drives off `get_product`:
  `sensor.collection().filterDate.filterBounds(frame).filter(scene_cloud_property ≤
  max_cloud_percent).map(add region cloud fraction via sensor.cloud_band).filter(<
  region_max/100).map(sensor.mask_clouds).map(index.compute → "INDEX")`.
  The existing region-fraction ordering invariant (fraction on the **unmasked**
  image, before masking) is preserved.
- **`render`** selects the `"INDEX"` band and colours with `cfg.viz_min/viz_max/
  palette` (already index-agnostic once the band + viz are config-driven). The
  `_thumb_params` single-int-dimensions aspect-ratio contract is unchanged.
- **`compositing.monthly_median`** is unchanged (it medians the collection; frames
  carry the `INDEX` band).
- **`cli`** unchanged except it passes the (already-present) config through.

## Error handling

- Unsupported `(sensor, index)` → `ConfigError` at config load.
- Empty collection / month → existing `RuntimeError` / skip behavior.
- Landsat has fewer scenes than S2; the coarse `max_cloud_percent` + region `<10%`
  may drop months — the existing "empty month skipped" and "no frames" messaging
  covers it (documented as a note).

## Testing strategy

- **Unit (fake `ee`, no network):** registry resolves the right collection ids/
  properties; `mask_clouds` uses the correct SCL classes (S2) and QA_PIXEL bits
  (Landsat); `cloud_band` remaps correctly; NDVI uses scaled reflectance aliases;
  LST applies the `0.00341802/149/−273.15` scale; unsupported pair raises;
  `build` pipeline order (region fraction before mask) holds for both sensors;
  render selects `"INDEX"` with config viz.
- **Integration (opt-in, live EE):** a tiny Landsat-LST run over the WNE frame
  renders ≥1 frame; skipped without `GEE_INTEGRATION`.
- **Live end-to-end (manual):** `gee-animation --config` with `sensor: landsat,
  index: lst` writes an MP4/GIF in °C with a thermal palette.

## Decisions / assumptions to confirm

- **Landsat collections:** merge L8 + L9 C2 L2 Tier-1 (`LC08`/`LC09`) for scene
  density (assumed). L7/earlier excluded (SLC-off gaps).
- **LST units:** °C (assumed; K available by dropping the −273.15).
- **Config rename:** `ndvi:` → optional `viz:` with per-index defaults (assumed).
- **Standard band name:** `"INDEX"` for the computed band (assumed).
- **This increment ships NDVI + LST only;** EVI/water/MODIS follow as registry
  additions.
