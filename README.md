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

## Generate each index animation

Ready-to-run example configs (WNE AOI) live at the repo root. Each writes
`out/<name>.mp4` + `.gif`:

| Index | Sensor      | Command                                            | Output basename   |
|-------|-------------|----------------------------------------------------|-------------------|
| NDVI  | Sentinel-2  | `gee-animation --config config.example.yaml`       | `wne_ndvi`        |
| EVI   | Sentinel-2  | `gee-animation --config config.evi.example.yaml`   | `wne_evi`         |
| NDWI  | Sentinel-2  | `gee-animation --config config.ndwi.example.yaml`  | `wne_ndwi`        |
| NDMI  | Sentinel-2  | `gee-animation --config config.ndmi.example.yaml`  | `wne_ndmi`        |
| LST   | Landsat     | `gee-animation --config config.lst.example.yaml`   | `wne_lst`         |
| ECOSTRESS | Landsat | `gee-animation --config config.ecostress.example.yaml` | `wne_ecostress` |
| NDVI  | MODIS       | `gee-animation --config config.modis.example.yaml` | `wne_modis_ndvi`  |

(`ecostress` = NDVI-sharpened Landsat LST — an approximation, since real ECOSTRESS data isn't in Earth Engine.)

Generate them all in one go:

```bash
for c in example evi.example ndwi.example ndmi.example lst.example ecostress.example modis.example; do
  gee-animation --config "config.$c.yaml"
done
```

Any reflectance index (`ndvi`, `evi`, `ndwi`, `ndmi`) runs on any sensor —
copy a config and change `sensor:` / `index:` (see Configuration). `lst` is
Landsat-only.

## GUI (Gradio)

A simple web UI over the same pipeline — no config file needed:

```bash
pip install -e ".[gui,shapefile]"    # gradio (+ geopandas for shapefile uploads)
earthengine authenticate             # one-time
gee-animation-gui                     # or: python -m gee_animation.gui
```

Then in the browser: **upload an AOI** (GeoJSON or a zipped shapefile — this is
the cloud-filtered region), set a **frame buffer in metres** (the animation
extent is the AOI's bounding box expanded by this), pick a **sensor** and
**index** (choices update per sensor), a **date range** and cloud threshold, and
click *Generate*. The MP4 plays inline with a GIF download.

## Configuration

See `config.example.yaml` (Sentinel-2 NDVI) or `config.lst.example.yaml` (Landsat LST). Key fields:

- **`aoi`** (area of interest) defines two geometries, each given as a `bbox`
  `[minLon, minLat, maxLon, maxLat]`, `geojson` (inline dict or file path), or
  `shapefile` (path to a `.shp`; auto-reprojected to EPSG:4326 — needs the
  `shapefile` extra, multiple features are dissolved into one):
  - **`frame`**: the animation extent (rectangle); aspect ratio is preserved when rendering.
  - **`region`**: the important region (polygon); only scenes where cloud coverage over this region is less than `region_max_cloud_percent` (default: 10%) are included, and its outline is drawn on each frame when `draw_region` is set.
- **`start`/`end`**: ISO dates (end exclusive).
- **`sensor`**: `sentinel2`, `landsat` (Collection-2 L2, missions 4/5/7/8/9 harmonized — ~1984→present), or `modis` (MOD09A1, 8-day 500 m).
- **`index`**: `ndvi`, `evi`, `ndwi` (McFeeters, open water), `ndmi` (moisture) — all sensors; or `lst` / `ecostress` (Landsat only). `ecostress` is an NDVI-sharpened LST (an approximation — real ECOSTRESS data is not in the Earth Engine catalog).
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
  sensors), LST and `ecostress` (Landsat only). Adding new indices/sensors
  requires registry changes in `products.py`.
- The `landsat` sensor spans **Collection-2 missions 4/5/7/8/9** (~1984→present):
  each mission's bands are renamed to a canonical set at collection build
  (TM/ETM+ `SR_B1–B5,B7` + `ST_B6`; OLI/TIRS `SR_B2–B7` + `ST_B10`), so every
  Landsat index runs across the whole record. Landsat 7 (post-2003 SLC-off) has
  wedge-shaped data gaps, softened by monthly medians.
- `ecostress` approximates high-resolution LST by NDVI-guided thermal-sharpening
  of Landsat `ST_B10` (real ECOSTRESS data is not available in Earth Engine). It
  injects native-30 m NDVI detail into the coarser thermal field using an
  empirical slope (`_ECOSTRESS_NDVI_SLOPE` in `products.py`, tune to taste).
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
