# Satellite Imagery Animation Generation

Generate annotated NDVI forest timelapses (MP4 + GIF) from Google Earth Engine
Sentinel-2 imagery, assembled frame-by-frame locally.

## Install

Use the project's conda environment so the package and its dependencies land in
the right interpreter (the same one your Jupyter kernel uses):

```bash
conda activate GEE_animation
pip install -e ".[dev,notebook]"   # package + deps into this env
earthengine authenticate           # one-time; or the tool prompts on first run
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

See `config.example.yaml`. Key fields: `aoi` (bbox or GeoJSON path), `start`/`end`
(ISO, end exclusive), `max_cloud_percent`, `ndvi` (min/max/palette), `render`
(fps/scale/dimensions), and optional `out_dir` (output directory, default `out`).
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

## Development

```bash
pytest -m "not integration"          # fast unit tests (no network)
GEE_INTEGRATION=1 pytest -m integration   # live EE test (needs auth)
```
