# Satellite Imagery Animation Generation

Generate annotated NDVI forest timelapses (MP4 + GIF) from Google Earth Engine
Sentinel-2 imagery, assembled frame-by-frame locally.

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

See `config.example.yaml`. Key fields:

- **`aoi`** (area of interest) defines two geometries, each given as a `bbox`
  `[minLon, minLat, maxLon, maxLat]`, `geojson` (inline dict or file path), or
  `shapefile` (path to a `.shp`; auto-reprojected to EPSG:4326 — needs the
  `shapefile` extra, multiple features are dissolved into one):
  - **`frame`**: the animation extent (rectangle); aspect ratio is preserved when rendering.
  - **`region`**: the important region (polygon); only scenes where cloud coverage over this region is less than `region_max_cloud_percent` (default: 10%) are included, and its outline is drawn on each frame when `draw_region` is set.
- **`start`/`end`**: ISO dates (end exclusive).
- **`max_cloud_percent`**: scene-level pre-filter threshold before pixel masking.
- **`region_max_cloud_percent`**: region-level cloud filter (kept only if cloud over region < threshold).
- **`ndvi`** (min/max/palette): fixed range for colorization so colour is comparable across frames.
- **`render`** (fps/scale/dimensions): rendering parameters.
- **`out_dir`**: output directory (default `out`).

Default GEE project is `hnee-331218`.

Each frame is a monthly cloud-masked median NDVI composite, colorized with the
fixed `ndvi` palette/range so colour is comparable across frames, annotated with
the month label and a shared NDVI colorbar. Cloud/no-data pixels are rendered in
a neutral grey rather than a vegetation colour.

## Notes / limitations (v1)

- `render.scale` (metres/pixel) is informational; thumbnail size is driven by
  `render.dimensions`.
- One sensor (Sentinel-2) and one cadence (monthly) are supported; the config
  layer is structured so more can be added.
- The region cloud filter averages only over the pixels a scene actually covers.
  A scene that clips a small clear corner of the region can still pass the
  `region_max_cloud_percent` threshold; use a region well inside the frame extent.

## Development

```bash
pytest -m "not integration"          # fast unit tests (no network)
GEE_INTEGRATION=1 pytest -m integration   # live EE test (needs auth)
```
