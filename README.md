# Satellite Imagery Animation Generation

Generate annotated NDVI forest timelapses (MP4 + GIF) from Google Earth Engine
Sentinel-2 imagery, assembled frame-by-frame locally.

## Install

```bash
pip install -e ".[dev]"
earthengine authenticate      # one-time; or the tool prompts on first run
```

## Usage

```bash
cp config.example.yaml config.yaml   # edit AOI, dates, palette, fps
gee-animation --config config.yaml
```

Output is written to `out/<name>.mp4` and `out/<name>.gif`.

## Configuration

See `config.example.yaml`. Key fields: `aoi` (bbox or GeoJSON path), `start`/`end`
(ISO, end exclusive), `max_cloud_percent`, `ndvi` (min/max/palette), `render`
(fps/scale/dimensions). Default GEE project is `hnee-331218`.

## Development

```bash
pytest -m "not integration"          # fast unit tests (no network)
GEE_INTEGRATION=1 pytest -m integration   # live EE test (needs auth)
```
