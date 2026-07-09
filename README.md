# Satellite Imagery Animation Generation

Generate annotated satellite index timelapses (MP4 + GIF) from Google Earth Engine
imagery (Sentinel-2 or Landsat), assembled frame-by-frame locally.

## Install

Use the project's conda environment so the package and its dependencies land in
the right interpreter (the same one your Jupyter kernel uses):

```bash
conda activate GEE_animation
pip install -e ".[dev,notebook]"              # package + deps into this env
# add `shapefile` if you point AOIs at .shp files (pulls geopandas):
# pip install -e ".[dev,notebook,shapefile]"
earthengine authenticate                      # one-time; or the tool prompts on first run
```

Run the notebook (`notebooks/ndvi_timelapse.ipynb`) with the `GEE_animation`
environment selected as the kernel.

## Usage

```bash
cp config.example.yaml config.yaml   # edit AOI, dates, palette, fps
gee-animation --config config.yaml
```

Output is written to `out/<name>.mp4` and `out/<name>.gif`.

## Configuration

See `config.example.yaml` (Sentinel-2 NDVI) or `config.lst.example.yaml` (Landsat LST). Key fields:

- **`aoi`** (area of interest) defines two geometries, each given as a `bbox`
  `[minLon, minLat, maxLon, maxLat]`, `geojson` (inline dict or file path), or
  `shapefile` (path to a `.shp`; auto-reprojected to EPSG:4326 — needs the
  `shapefile` extra, multiple features are dissolved into one):
  - **`frame`**: the animation extent (rectangle); aspect ratio is preserved when rendering.
  - **`region`**: the important region (polygon); only scenes where cloud coverage over this region is less than `region_max_cloud_percent` (default: 10%) are included, and its outline is drawn on each frame when `draw_region` is set.
- **`start`/`end`**: ISO dates (end exclusive).
- **`sensor`**: `sentinel2`, `landsat`, or `modis` (MOD09A1, 8-day 500 m).
- **`index`**: `ndvi`, `evi`, `ndwi` (McFeeters, open water), `ndmi` (moisture) — all sensors; or `lst` (Landsat only).
- **`max_cloud_percent`**: scene-level pre-filter threshold (Sentinel-2/Landsat only; MODIS has no per-scene cloud metadata, so this is ignored and only the region filter applies).
- **`region_max_cloud_percent`**: region-level cloud filter (kept only if cloud over region < threshold).
- **`viz`** (optional; min/max/palette): fixed range for colorization so colour is comparable across frames. If omitted, per-index defaults apply (NDVI −0.2..0.9 green; EVI 0..1 green; NDWI −0.3..0.6 brown→blue; NDMI −0.5..0.8 brown→teal; LST 0..40°C thermal).
- **`render`** (fps/scale/dimensions): rendering parameters.
- **`out_dir`**: output directory (default `out`).

Default GEE project is `hnee-331218`.

Each frame is a monthly cloud-masked median index composite, colorized with the
fixed `viz` range (or per-index default) so colour is comparable across frames, annotated with
the month label and a shared index colorbar. Cloud/no-data pixels are rendered in
a neutral grey rather than an index colour.

**Adding new sensors/indices:** register them in `gee_animation/products.py` (define a `Sensor` subclass and an `Index` function, then add both to the registry).

## Notes / limitations (v1)

- `render.scale` (metres/pixel) is informational; thumbnail size is driven by
  `render.dimensions`.
- Sensors: Sentinel-2 and Landsat. One cadence (monthly) is supported; config
  is structured to add more.
- Sensors: Sentinel-2, Landsat, MODIS. Indices: NDVI, EVI, NDWI, NDMI (all
  sensors), LST (Landsat only). Adding new indices/sensors requires registry
  changes in `products.py`.
- The region cloud filter averages only over the pixels a scene actually covers.
  A scene that clips a small clear corner of the region can still pass the
  `region_max_cloud_percent` threshold; use a region well inside the frame extent.
- Landsat LST has fewer scenes than Sentinel-2 NDVI; relax `region_max_cloud_percent`
  and widen the date window if "No images found" is reported.

## Development

```bash
pytest -m "not integration"          # fast unit tests (no network)
GEE_INTEGRATION=1 pytest -m integration   # live EE test (needs auth)
```
