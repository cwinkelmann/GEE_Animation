# GEE NDVI Forest Timelapse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an installable Python package + CLI that turns a Google Earth Engine Sentinel-2 time-series over a forest AOI into an annotated NDVI timelapse (MP4 + GIF), assembled frame-by-frame locally.

**Architecture:** A config-driven package `gee_animation` with seven focused modules (`auth`, `config`, `aoi`, `collection`, `compositing`, `render`, `cli`). Pure logic (config validation, NDVI math, AOI parsing, monthly grouping, ffmpeg fallback) is unit-tested with EE mocked at module boundaries; a single opt-in integration test hits live EE. A thin CLI reads a `config.yaml` and drives the pipeline: auth → parse AOI → build masked NDVI collection → monthly median frames → render.

**Tech Stack:** Python 3.10+, `earthengine-api`, `Pillow`, `imageio`, `imageio-ffmpeg` (bundled ffmpeg), `PyYAML`, `numpy`; `pytest` for tests; `pyproject.toml` (setuptools) packaging.

## Global Constraints

- Python 3.10+ (uses `list[...]` builtins generics and `X | None` unions).
- Default GEE project id: `hnee-331218` (overridable via config).
- Sentinel-2 SR collection `COPERNICUS/S2_SR_HARMONIZED`; NDVI = (B8 − B4)/(B8 + B4).
- NDVI visualization: palette brown→green, fixed range −0.2…0.9, applied identically to every frame.
- Cadence for v1: monthly median composite (one frame per month in range).
- Outputs written to `out/` (gitignored); both `<name>.mp4` and `<name>.gif`.
- CLI framework: `argparse` (stdlib, no extra dep).
- No live EE calls in unit tests — EE is mocked/stubbed at module boundaries. The one integration test is marked `@pytest.mark.integration` and skips without credentials.
- Every module has one responsibility; keep files focused.

---

### Task 1: Project scaffolding & packaging

**Files:**
- Create: `pyproject.toml`
- Create: `gee_animation/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitignore`
- Create: `config.example.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: an installable package `gee_animation` (version `0.1.0`) with console script `gee-animation = gee_animation.cli:main`; `pytest` markers registered.

- [ ] **Step 1: Create `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/
.pytest_cache/
out/
.DS_Store
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "gee_animation"
version = "0.1.0"
description = "Google Earth Engine NDVI forest timelapse generator"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "earthengine-api>=0.1.380",
    "Pillow>=10.0",
    "imageio>=2.31",
    "imageio-ffmpeg>=0.4.9",
    "PyYAML>=6.0",
    "numpy>=1.24",
]

[project.optional-dependencies]
dev = ["pytest>=7.4"]

[project.scripts]
gee-animation = "gee_animation.cli:main"

[tool.setuptools.packages.find]
include = ["gee_animation*"]

[tool.pytest.ini_options]
markers = [
    "integration: hits live Google Earth Engine; skipped without credentials",
]
```

- [ ] **Step 3: Create package and test init files**

`gee_animation/__init__.py`:
```python
"""Google Earth Engine NDVI forest timelapse generator."""

__version__ = "0.1.0"
```

`tests/__init__.py`:
```python
```

- [ ] **Step 4: Create `config.example.yaml`**

```yaml
# Example run configuration for gee-animation.
name: barnim_ndvi           # output basename -> out/barnim_ndvi.mp4 / .gif
project: hnee-331218        # Google Earth Engine project id

aoi:
  # Either a bbox [minLon, minLat, maxLon, maxLat] ...
  bbox: [13.7, 52.8, 13.9, 52.95]
  # ... or a GeoJSON file (takes precedence if set):
  # geojson: aoi/barnim.geojson

start: "2022-01-01"         # inclusive
end: "2023-01-01"           # exclusive

sensor: sentinel2           # v1 supports: sentinel2
cadence: monthly            # v1 supports: monthly
max_cloud_percent: 60       # scene-level pre-filter before pixel masking

ndvi:
  min: -0.2
  max: 0.9
  palette: ["#a1622f", "#e8d9a0", "#3b7a2a"]  # brown -> tan -> green

render:
  fps: 4
  scale: 20                 # metres/pixel for thumbnails
  dimensions: 768           # max width/height of each frame
```

- [ ] **Step 5: Install in editable mode and verify import**

Run: `pip install -e ".[dev]"`
Then: `python -c "import gee_animation; print(gee_animation.__version__)"`
Expected: prints `0.1.0`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml gee_animation/__init__.py tests/__init__.py .gitignore config.example.yaml
git commit -m "chore: scaffold gee_animation package and packaging"
```

---

### Task 2: Configuration model (`config.py`)

**Files:**
- Create: `gee_animation/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `PyYAML`.
- Produces:
  - `@dataclass RunConfig` with fields:
    `name: str`, `project: str`, `aoi: dict`, `start: str`, `end: str`,
    `sensor: str`, `cadence: str`, `max_cloud_percent: float`,
    `ndvi_min: float`, `ndvi_max: float`, `palette: list[str]`,
    `fps: int`, `scale: float`, `dimensions: int`, `out_dir: str = "out"`.
  - `RunConfig.from_yaml(path: str | Path) -> RunConfig` — loads + validates.
  - Raises `ConfigError(ValueError)` on invalid config.

- [ ] **Step 1: Write failing tests**

`tests/test_config.py`:
```python
import textwrap
import pytest
from gee_animation.config import RunConfig, ConfigError


def _write(tmp_path, body: str):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_from_yaml_loads_valid_config(tmp_path):
    p = _write(tmp_path, """
        name: test
        project: hnee-331218
        aoi:
          bbox: [13.7, 52.8, 13.9, 52.95]
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        cadence: monthly
        max_cloud_percent: 60
        ndvi:
          min: -0.2
          max: 0.9
          palette: ["#a1622f", "#3b7a2a"]
        render:
          fps: 4
          scale: 20
          dimensions: 768
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.name == "test"
    assert cfg.project == "hnee-331218"
    assert cfg.aoi == {"bbox": [13.7, 52.8, 13.9, 52.95]}
    assert cfg.ndvi_min == -0.2 and cfg.ndvi_max == 0.9
    assert cfg.fps == 4 and cfg.dimensions == 768
    assert cfg.out_dir == "out"


def test_rejects_end_before_start(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {bbox: [0, 0, 1, 1]}
        start: "2023-01-01"
        end: "2022-01-01"
        sensor: sentinel2
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="end.*after.*start"):
        RunConfig.from_yaml(p)


def test_rejects_unsupported_sensor(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {bbox: [0, 0, 1, 1]}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: modis
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="sensor"):
        RunConfig.from_yaml(p)


def test_requires_aoi_bbox_or_geojson(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="aoi"):
        RunConfig.from_yaml(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.config'`

- [ ] **Step 3: Implement `config.py`**

```python
"""Run configuration model: load and validate YAML into a RunConfig."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

SUPPORTED_SENSORS = {"sentinel2"}
SUPPORTED_CADENCES = {"monthly"}


class ConfigError(ValueError):
    """Raised when a run configuration is invalid."""


@dataclass
class RunConfig:
    name: str
    project: str
    aoi: dict
    start: str
    end: str
    sensor: str
    cadence: str
    max_cloud_percent: float
    ndvi_min: float
    ndvi_max: float
    palette: list[str]
    fps: int
    scale: float
    dimensions: int
    out_dir: str = "out"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RunConfig":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        try:
            ndvi = raw["ndvi"]
            render = raw["render"]
            cfg = cls(
                name=str(raw["name"]),
                project=str(raw["project"]),
                aoi=dict(raw["aoi"] or {}),
                start=str(raw["start"]),
                end=str(raw["end"]),
                sensor=str(raw["sensor"]),
                cadence=str(raw["cadence"]),
                max_cloud_percent=float(raw["max_cloud_percent"]),
                ndvi_min=float(ndvi["min"]),
                ndvi_max=float(ndvi["max"]),
                palette=list(ndvi["palette"]),
                fps=int(render["fps"]),
                scale=float(render["scale"]),
                dimensions=int(render["dimensions"]),
                out_dir=str(raw.get("out_dir", "out")),
            )
        except KeyError as exc:
            raise ConfigError(f"missing required config key: {exc}") from exc
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.sensor not in SUPPORTED_SENSORS:
            raise ConfigError(
                f"unsupported sensor {self.sensor!r}; supported: {sorted(SUPPORTED_SENSORS)}"
            )
        if self.cadence not in SUPPORTED_CADENCES:
            raise ConfigError(
                f"unsupported cadence {self.cadence!r}; supported: {sorted(SUPPORTED_CADENCES)}"
            )
        if not (self.aoi.get("bbox") or self.aoi.get("geojson")):
            raise ConfigError("aoi must define either 'bbox' or 'geojson'")
        try:
            start = date.fromisoformat(self.start)
            end = date.fromisoformat(self.end)
        except ValueError as exc:
            raise ConfigError(f"start/end must be ISO dates: {exc}") from exc
        if end <= start:
            raise ConfigError(f"end ({self.end}) must be after start ({self.start})")
        if self.ndvi_max <= self.ndvi_min:
            raise ConfigError("ndvi.max must be greater than ndvi.min")
        if not self.palette:
            raise ConfigError("ndvi.palette must be non-empty")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/config.py tests/test_config.py
git commit -m "feat: add RunConfig loading and validation"
```

---

### Task 3: NDVI math + palette helpers (`collection.py` pure part)

**Files:**
- Create: `gee_animation/imaging.py`
- Test: `tests/test_imaging.py`

**Interfaces:**
- Consumes: `numpy`.
- Produces:
  - `ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray` — (nir−red)/(nir+red), 0 where denom is 0, clipped to [−1, 1].
  - `colorize(ndvi_arr: np.ndarray, vmin: float, vmax: float, palette: list[str]) -> np.ndarray` — maps NDVI to an RGB `uint8` HxWx3 array via a linear ramp across the hex palette; values are clamped to [vmin, vmax].

_(NDVI math lives in a pure `imaging.py` so it is testable without EE; `collection.py` in Task 5 computes NDVI server-side on `ee.Image`, and `render.py` uses `colorize` for local frames.)_

- [ ] **Step 1: Write failing tests**

`tests/test_imaging.py`:
```python
import numpy as np
from gee_animation.imaging import ndvi, colorize


def test_ndvi_basic():
    nir = np.array([[0.5, 0.2]])
    red = np.array([[0.1, 0.2]])
    out = ndvi(nir, red)
    # (0.5-0.1)/(0.5+0.1)=0.666..., (0.2-0.2)/0.4=0.0
    assert np.allclose(out, [[0.4 / 0.6, 0.0]])


def test_ndvi_zero_denominator_is_zero():
    out = ndvi(np.array([0.0]), np.array([0.0]))
    assert out.tolist() == [0.0]


def test_ndvi_clipped_to_unit_range():
    out = ndvi(np.array([1.0]), np.array([-1.0]))  # would exceed 1
    assert out.max() <= 1.0 and out.min() >= -1.0


def test_colorize_shape_and_endpoints():
    arr = np.array([[-0.2, 0.9]])
    rgb = colorize(arr, -0.2, 0.9, ["#000000", "#ffffff"])
    assert rgb.shape == (1, 2, 3)
    assert rgb.dtype == np.uint8
    assert rgb[0, 0].tolist() == [0, 0, 0]        # vmin -> first colour
    assert rgb[0, 1].tolist() == [255, 255, 255]  # vmax -> last colour


def test_colorize_clamps_out_of_range():
    arr = np.array([[-5.0, 5.0]])
    rgb = colorize(arr, -0.2, 0.9, ["#000000", "#ffffff"])
    assert rgb[0, 0].tolist() == [0, 0, 0]
    assert rgb[0, 1].tolist() == [255, 255, 255]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_imaging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.imaging'`

- [ ] **Step 3: Implement `imaging.py`**

```python
"""Pure NDVI + colour-ramp helpers (no Earth Engine dependency)."""
from __future__ import annotations

import numpy as np


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    nir = np.asarray(nir, dtype=float)
    red = np.asarray(red, dtype=float)
    denom = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(denom == 0, 0.0, (nir - red) / denom)
    return np.clip(out, -1.0, 1.0)


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def colorize(
    ndvi_arr: np.ndarray, vmin: float, vmax: float, palette: list[str]
) -> np.ndarray:
    if vmax <= vmin:
        raise ValueError("vmax must be greater than vmin")
    stops = np.array([_hex_to_rgb(c) for c in palette], dtype=float)  # (K, 3)
    k = len(stops)
    arr = np.clip(np.asarray(ndvi_arr, dtype=float), vmin, vmax)
    t = (arr - vmin) / (vmax - vmin)          # 0..1
    pos = t * (k - 1)                          # 0..K-1
    lo = np.floor(pos).astype(int)
    lo = np.clip(lo, 0, k - 2) if k > 1 else np.zeros_like(lo)
    hi = np.clip(lo + 1, 0, k - 1)
    frac = (pos - lo)[..., None]
    rgb = stops[lo] * (1 - frac) + stops[hi] * frac
    return np.clip(np.round(rgb), 0, 255).astype(np.uint8)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_imaging.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/imaging.py tests/test_imaging.py
git commit -m "feat: add pure NDVI and colour-ramp helpers"
```

---

### Task 4: AOI parsing (`aoi.py`)

**Files:**
- Create: `gee_animation/aoi.py`
- Test: `tests/test_aoi.py`

**Interfaces:**
- Consumes: `ee` (mocked in tests), `config.aoi` dict.
- Produces:
  - `parse(aoi_cfg: dict, ee_module=ee) -> ee.Geometry` — bbox → `ee.Geometry.Rectangle`; `geojson` path → `ee.Geometry(<geojson geometry>)`. `geojson` wins if both present. Raises `ValueError` if neither.
  - `_load_geojson_geometry(path) -> dict` — returns the geometry dict, unwrapping a FeatureCollection/Feature if needed.

_(Tests inject a fake `ee` module so no live EE is needed. The `ee_module` parameter defaults to the real `ee` in production.)_

- [ ] **Step 1: Write failing tests**

`tests/test_aoi.py`:
```python
import json
import types
import pytest
from gee_animation.aoi import parse, _load_geojson_geometry


class FakeGeometry:
    def __init__(self, spec):
        self.spec = spec


def _fake_ee():
    m = types.SimpleNamespace()
    m.Geometry = types.SimpleNamespace(
        Rectangle=lambda coords: FakeGeometry(("rect", tuple(coords))),
    )
    # ee.Geometry(...) is also callable to wrap raw geojson:
    def geometry_call(spec):
        return FakeGeometry(("geojson", spec))
    m.Geometry = types.SimpleNamespace(
        Rectangle=lambda coords: FakeGeometry(("rect", tuple(coords))),
    )
    # make Geometry itself callable
    callable_geom = geometry_call
    callable_geom.Rectangle = lambda coords: FakeGeometry(("rect", tuple(coords)))
    m.Geometry = callable_geom
    return m


def test_parse_bbox():
    ee = _fake_ee()
    geom = parse({"bbox": [13.7, 52.8, 13.9, 52.95]}, ee_module=ee)
    assert geom.spec == ("rect", (13.7, 52.8, 13.9, 52.95))


def test_parse_geojson_feature(tmp_path):
    ee = _fake_ee()
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
        ],
    }
    p = tmp_path / "aoi.geojson"
    p.write_text(json.dumps(fc))
    geom = parse({"geojson": str(p)}, ee_module=ee)
    assert geom.spec[0] == "geojson"
    assert geom.spec[1]["type"] == "Polygon"


def test_parse_requires_something():
    ee = _fake_ee()
    with pytest.raises(ValueError, match="aoi"):
        parse({}, ee_module=ee)


def test_load_geojson_unwraps_feature(tmp_path):
    feat = {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1, 2]}}
    p = tmp_path / "f.geojson"
    p.write_text(json.dumps(feat))
    geom = _load_geojson_geometry(str(p))
    assert geom == {"type": "Point", "coordinates": [1, 2]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_aoi.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.aoi'`

- [ ] **Step 3: Implement `aoi.py`**

```python
"""Turn an AOI config (bbox or GeoJSON file) into an ee.Geometry."""
from __future__ import annotations

import json
from pathlib import Path

import ee


def _load_geojson_geometry(path: str) -> dict:
    obj = json.loads(Path(path).read_text())
    t = obj.get("type")
    if t == "FeatureCollection":
        return obj["features"][0]["geometry"]
    if t == "Feature":
        return obj["geometry"]
    return obj  # already a bare geometry


def parse(aoi_cfg: dict, ee_module=ee):
    if aoi_cfg.get("geojson"):
        geom = _load_geojson_geometry(aoi_cfg["geojson"])
        return ee_module.Geometry(geom)
    if aoi_cfg.get("bbox"):
        return ee_module.Geometry.Rectangle(list(aoi_cfg["bbox"]))
    raise ValueError("aoi must define either 'bbox' or 'geojson'")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_aoi.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/aoi.py tests/test_aoi.py
git commit -m "feat: parse AOI bbox/GeoJSON into ee.Geometry"
```

---

### Task 5: Collection builder & monthly compositing (`collection.py`, `compositing.py`)

**Files:**
- Create: `gee_animation/collection.py`
- Create: `gee_animation/compositing.py`
- Test: `tests/test_compositing.py`

**Interfaces:**
- Consumes: `ee` (mocked in tests), `RunConfig`, `ee.Geometry`.
- Produces:
  - `collection.build(cfg: RunConfig, geometry, ee_module=ee) -> ee.ImageCollection` — `COPERNICUS/S2_SR_HARMONIZED` filtered by date + bounds + `CLOUDY_PIXEL_PERCENTAGE <= max_cloud_percent`, mapped through `mask_s2_clouds` and `add_ndvi`.
  - `collection.mask_s2_clouds(image, ee_module=ee)` — masks SCL classes {3 (shadow), 8,9,10 (clouds), 11 (snow)}.
  - `collection.add_ndvi(image, ee_module=ee)` — adds band `NDVI = normalizedDifference(["B8","B4"])`.
  - `compositing.month_starts(start: str, end: str) -> list[str]` — ISO first-of-month strings in `[start, end)` (pure).
  - `compositing.monthly_median(collection, cfg, ee_module=ee) -> list[Frame]` where `Frame = namedtuple("Frame", "label image")`; one median NDVI image per non-empty month, `label` = `"YYYY-MM"`.

- [ ] **Step 1: Write failing tests (pure `month_starts` + monthly grouping with fake ee)**

`tests/test_compositing.py`:
```python
import types
from gee_animation.compositing import month_starts, monthly_median, Frame


def test_month_starts_spans_range():
    assert month_starts("2022-11-01", "2023-02-01") == [
        "2022-11-01", "2022-12-01", "2023-01-01",
    ]


def test_month_starts_excludes_end_month():
    # end is exclusive; Feb 1 -> only Jan
    assert month_starts("2023-01-01", "2023-02-01") == ["2023-01-01"]


class FakeImage:
    def __init__(self, tag):
        self.tag = tag
    def set(self, *a, **k):
        return self


class FakeFiltered:
    def __init__(self, count):
        self._count = count
    def size(self):
        return types.SimpleNamespace(getInfo=lambda: self._count)
    def median(self):
        return FakeImage("median")


class FakeCollection:
    """Records filterDate calls; returns configured counts per month."""
    def __init__(self, counts_by_start):
        self.counts = counts_by_start
    def filterDate(self, start, end):
        return FakeFiltered(self.counts.get(start, 0))


def _fake_ee():
    m = types.SimpleNamespace()
    # ee.Date(x).advance(1,'month') -> next month start string via a stub
    class FakeDate:
        def __init__(self, s): self.s = s
    m.Date = FakeDate
    return m


def test_monthly_median_skips_empty_months():
    cfg = types.SimpleNamespace(start="2022-01-01", end="2022-04-01")
    coll = FakeCollection({"2022-01-01": 5, "2022-02-01": 0, "2022-03-01": 3})
    frames = monthly_median(coll, cfg, ee_module=_fake_ee())
    labels = [f.label for f in frames]
    assert labels == ["2022-01", "2022-03"]   # Feb skipped (0 images)
    assert all(isinstance(f, Frame) for f in frames)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_compositing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.compositing'`

- [ ] **Step 3: Implement `compositing.py`**

```python
"""Group an ImageCollection into monthly median NDVI frames."""
from __future__ import annotations

from collections import namedtuple
from datetime import date

import ee

Frame = namedtuple("Frame", "label image")


def _add_month(d: date) -> date:
    year = d.year + (d.month // 12)
    month = d.month % 12 + 1
    return date(year, month, 1)


def month_starts(start: str, end: str) -> list[str]:
    cur = date.fromisoformat(start).replace(day=1)
    stop = date.fromisoformat(end)
    out: list[str] = []
    while cur < stop:
        out.append(cur.isoformat())
        cur = _add_month(cur)
    return out


def monthly_median(collection, cfg, ee_module=ee) -> list[Frame]:
    frames: list[Frame] = []
    for start in month_starts(cfg.start, cfg.end):
        nxt = _add_month(date.fromisoformat(start)).isoformat()
        monthly = collection.filterDate(start, nxt)
        if monthly.size().getInfo() == 0:
            continue
        label = start[:7]  # YYYY-MM
        frames.append(Frame(label=label, image=monthly.median()))
    return frames
```

- [ ] **Step 4: Implement `collection.py`**

```python
"""Build a cloud-masked Sentinel-2 NDVI ImageCollection."""
from __future__ import annotations

import ee

S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
# SCL classes to drop: 3 shadow, 8/9/10 cloud (med/high/cirrus), 11 snow.
_SCL_MASK_CLASSES = [3, 8, 9, 10, 11]


def mask_s2_clouds(image, ee_module=ee):
    scl = image.select("SCL")
    mask = ee_module.Image.constant(1)
    for cls in _SCL_MASK_CLASSES:
        mask = mask.And(scl.neq(cls))
    return image.updateMask(mask)


def add_ndvi(image, ee_module=ee):
    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
    return image.addBands(ndvi)


def build(cfg, geometry, ee_module=ee):
    coll = (
        ee_module.ImageCollection(S2_COLLECTION)
        .filterDate(cfg.start, cfg.end)
        .filterBounds(geometry)
        .filter(ee_module.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", cfg.max_cloud_percent))
        .map(lambda img: mask_s2_clouds(img, ee_module))
        .map(lambda img: add_ndvi(img, ee_module))
    )
    return coll
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_compositing.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add gee_animation/collection.py gee_animation/compositing.py tests/test_compositing.py
git commit -m "feat: build masked S2 NDVI collection and monthly frames"
```

---

### Task 6: Frame rendering & assembly (`render.py`)

**Files:**
- Create: `gee_animation/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Pillow`, `imageio`, `numpy`, `RunConfig`, `Frame` list.
- Produces:
  - `annotate(rgb: np.ndarray, label: str) -> np.ndarray` — stamps `label` (month) onto an RGB frame (bottom-left, semi-transparent bar). Pure; no EE.
  - `assemble(frames_rgb: list[np.ndarray], cfg, ee_module=None) -> list[Path]` — writes `out/<name>.mp4` and `out/<name>.gif` at `cfg.fps`. If MP4 writing raises (no ffmpeg), logs a warning and returns GIF path only.
  - `render(frames: list[Frame], cfg, ee_module=ee, fetch=_fetch_thumbnail) -> list[Path]` — for each `Frame`, fetch an NDVI array, `colorize`, `annotate`, then `assemble`. `fetch` is injectable for testing.

_(The EE thumbnail download is isolated in `_fetch_thumbnail(image, cfg, geometry)` and injected as `fetch` so `render()` is unit-testable without EE.)_

- [ ] **Step 1: Write failing tests**

`tests/test_render.py`:
```python
from pathlib import Path
import numpy as np
import types
import pytest
from gee_animation.render import annotate, assemble, render
from gee_animation.compositing import Frame


def _cfg(tmp_path, name="anim", fps=2):
    return types.SimpleNamespace(
        name=name, out_dir=str(tmp_path),
        ndvi_min=-0.2, ndvi_max=0.9, palette=["#000000", "#ffffff"],
        fps=fps, scale=20, dimensions=64,
    )


def test_annotate_keeps_shape_and_type():
    rgb = np.zeros((32, 32, 3), dtype=np.uint8)
    out = annotate(rgb, "2022-06")
    assert out.shape == (32, 32, 3)
    assert out.dtype == np.uint8
    # some pixels changed (text/bar drawn)
    assert out.sum() > 0


def test_assemble_writes_gif_and_mp4(tmp_path):
    cfg = _cfg(tmp_path)
    frames = [np.zeros((16, 16, 3), np.uint8), np.full((16, 16, 3), 255, np.uint8)]
    paths = assemble(frames, cfg)
    gifs = [p for p in paths if p.suffix == ".gif"]
    assert gifs and gifs[0].exists()


def test_assemble_falls_back_to_gif_when_mp4_fails(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("no ffmpeg")
    monkeypatch.setattr(r, "_write_mp4", boom)
    frames = [np.zeros((16, 16, 3), np.uint8)]
    paths = assemble(frames, cfg)
    assert all(p.suffix == ".gif" for p in paths)
    assert paths[0].exists()


def test_render_pipeline_with_injected_fetch(tmp_path):
    cfg = _cfg(tmp_path)
    # fetch returns a tiny NDVI array per frame
    def fake_fetch(image, cfg, geometry=None):
        return np.array([[0.5, -0.1], [0.9, 0.0]])
    frames = [Frame("2022-01", object()), Frame("2022-02", object())]
    paths = render(frames, cfg, ee_module=None, fetch=fake_fetch, geometry=None)
    assert any(p.suffix == ".gif" and p.exists() for p in paths)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.render'`

- [ ] **Step 3: Implement `render.py`**

```python
"""Render NDVI frames to annotated PNGs and assemble MP4 + GIF."""
from __future__ import annotations

import io
import logging
from pathlib import Path
from urllib.request import urlopen

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw

from .imaging import colorize

log = logging.getLogger(__name__)


def _fetch_thumbnail(image, cfg, geometry):
    """Download the NDVI band as a numpy array via EE getThumbURL."""
    url = image.select("NDVI").getThumbURL(
        {
            "min": cfg.ndvi_min,
            "max": cfg.ndvi_max,
            "dimensions": cfg.dimensions,
            "region": geometry,
            "format": "png",
        }
    )
    with urlopen(url) as resp:  # noqa: S310 (trusted EE URL)
        data = resp.read()
    img = Image.open(io.BytesIO(data)).convert("L")
    # EE scales min..max into 0..255; map back to NDVI units.
    arr = np.asarray(img, dtype=float) / 255.0
    return cfg.ndvi_min + arr * (cfg.ndvi_max - cfg.ndvi_min)


def annotate(rgb: np.ndarray, label: str) -> np.ndarray:
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    bar_h = max(12, h // 12)
    draw.rectangle([0, h - bar_h, w, h], fill=(0, 0, 0, 140))
    draw.text((4, h - bar_h + 1), label, fill=(255, 255, 255, 255))
    return np.asarray(img)


def _write_mp4(path: Path, frames: list[np.ndarray], fps: int) -> None:
    imageio.mimsave(path, frames, fps=fps, macro_block_size=None)


def _write_gif(path: Path, frames: list[np.ndarray], fps: int) -> None:
    imageio.mimsave(path, frames, duration=1.0 / fps)


def assemble(frames_rgb: list[np.ndarray], cfg, ee_module=None) -> list[Path]:
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gif_path = out_dir / f"{cfg.name}.gif"
    mp4_path = out_dir / f"{cfg.name}.mp4"
    paths: list[Path] = []
    try:
        _write_mp4(mp4_path, frames_rgb, cfg.fps)
        paths.append(mp4_path)
    except Exception as exc:  # ffmpeg missing / encode error
        log.warning("MP4 write failed (%s); producing GIF only", exc)
    _write_gif(gif_path, frames_rgb, cfg.fps)
    paths.append(gif_path)
    return paths


def render(frames, cfg, ee_module=None, fetch=_fetch_thumbnail, geometry=None) -> list[Path]:
    rgb_frames: list[np.ndarray] = []
    for frame in frames:
        ndvi_arr = fetch(frame.image, cfg, geometry)
        rgb = colorize(ndvi_arr, cfg.ndvi_min, cfg.ndvi_max, cfg.palette)
        rgb_frames.append(annotate(rgb, frame.label))
    return assemble(rgb_frames, cfg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_render.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/render.py tests/test_render.py
git commit -m "feat: render, annotate, and assemble NDVI frames to MP4/GIF"
```

---

### Task 7: Auth + CLI wiring (`auth.py`, `cli.py`)

**Files:**
- Create: `gee_animation/auth.py`
- Create: `gee_animation/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: all prior modules.
- Produces:
  - `auth.init(project: str, ee_module=ee) -> None` — `ee.Initialize(project=...)`; on init failure, call `ee.Authenticate()` then retry once.
  - `cli.run(config_path: str, deps=DEFAULT_DEPS) -> list[Path]` — orchestrates auth→aoi→collection→compositing→render; `deps` injects the module functions for testing.
  - `cli.main(argv=None) -> int` — argparse entry point (`gee-animation --config config.yaml`); prints written paths; returns exit code.

- [ ] **Step 1: Write failing tests**

`tests/test_cli.py`:
```python
import types
from pathlib import Path
from gee_animation.cli import run, main


def test_run_orchestrates_pipeline(tmp_path, monkeypatch):
    # minimal valid config file
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "name: t\nproject: p\naoi: {bbox: [0,0,1,1]}\n"
        'start: "2022-01-01"\nend: "2022-03-01"\n'
        "sensor: sentinel2\ncadence: monthly\nmax_cloud_percent: 60\n"
        'ndvi: {min: -0.2, max: 0.9, palette: ["#000000","#ffffff"]}\n'
        "render: {fps: 2, scale: 20, dimensions: 64}\n"
    )
    calls = []
    deps = types.SimpleNamespace(
        init=lambda project, ee_module=None: calls.append(("init", project)),
        parse=lambda aoi, ee_module=None: ("geom", aoi),
        build=lambda cfg, geom, ee_module=None: ("coll", geom),
        monthly_median=lambda coll, cfg, ee_module=None: ["f1", "f2"],
        render=lambda frames, cfg, **k: [tmp_path / "t.gif"],
    )
    out = run(str(cfg_path), deps=deps)
    assert ("init", "p") in calls
    assert out == [tmp_path / "t.gif"]


def test_main_returns_zero_on_success(tmp_path, monkeypatch):
    import gee_animation.cli as c
    monkeypatch.setattr(c, "run", lambda path, deps=None: [Path("out/x.gif")])
    rc = main(["--config", "whatever.yaml"])
    assert rc == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.cli'`

- [ ] **Step 3: Implement `auth.py`**

```python
"""Earth Engine authentication + initialization."""
from __future__ import annotations

import ee


def init(project: str, ee_module=ee) -> None:
    try:
        ee_module.Initialize(project=project)
    except Exception:
        ee_module.Authenticate()
        ee_module.Initialize(project=project)
```

- [ ] **Step 4: Implement `cli.py`**

```python
"""Command-line entry point: config.yaml -> NDVI timelapse."""
from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

from . import auth, aoi, collection, compositing, render
from .config import RunConfig

DEFAULT_DEPS = types.SimpleNamespace(
    init=auth.init,
    parse=aoi.parse,
    build=collection.build,
    monthly_median=compositing.monthly_median,
    render=render.render,
)


def run(config_path: str, deps=DEFAULT_DEPS) -> list[Path]:
    cfg = RunConfig.from_yaml(config_path)
    deps.init(cfg.project)
    geometry = deps.parse(cfg.aoi)
    coll = deps.build(cfg, geometry)
    frames = deps.monthly_median(coll, cfg)
    if not frames:
        raise SystemExit(
            "No images found for the given AOI/date range/cloud filter."
        )
    return deps.render(frames, cfg, geometry=geometry)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gee-animation")
    parser.add_argument("--config", required=True, help="path to config.yaml")
    args = parser.parse_args(argv)
    paths = run(args.config)
    for p in paths:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the full unit suite**

Run: `pytest -m "not integration" -v`
Expected: PASS (all tasks' tests green)

- [ ] **Step 7: Commit**

```bash
git add gee_animation/auth.py gee_animation/cli.py tests/test_cli.py
git commit -m "feat: wire auth and CLI pipeline"
```

---

### Task 8: Live integration test + example + docs

**Files:**
- Create: `tests/test_integration.py`
- Modify: `01_animation_single_images.py` (turn into a thin example that calls the package)
- Modify: `README.md`

**Interfaces:**
- Consumes: the full package.
- Produces: an opt-in integration test; an example script; usage docs.

- [ ] **Step 1: Write the integration test (auto-skips without credentials)**

`tests/test_integration.py`:
```python
import os
from pathlib import Path
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.environ.get("GEE_INTEGRATION"),
    reason="set GEE_INTEGRATION=1 and authenticate to run live EE test",
)
def test_tiny_three_frame_render(tmp_path):
    from gee_animation.config import RunConfig
    from gee_animation import auth, aoi, collection, compositing, render

    cfg = RunConfig(
        name="itest", project=os.environ.get("GEE_PROJECT", "hnee-331218"),
        aoi={"bbox": [13.80, 52.85, 13.83, 52.87]},
        start="2022-06-01", end="2022-09-01",
        sensor="sentinel2", cadence="monthly", max_cloud_percent=80,
        ndvi_min=-0.2, ndvi_max=0.9, palette=["#a1622f", "#3b7a2a"],
        fps=2, scale=20, dimensions=256, out_dir=str(tmp_path),
    )
    auth.init(cfg.project)
    geom = aoi.parse(cfg.aoi)
    coll = collection.build(cfg, geom)
    frames = compositing.monthly_median(coll, cfg)
    assert frames, "expected at least one monthly frame"
    paths = render.render(frames, cfg, geometry=geom)
    assert any(Path(p).exists() for p in paths)
```

- [ ] **Step 2: Verify it skips cleanly without credentials**

Run: `pytest tests/test_integration.py -v`
Expected: SKIPPED (reason mentions `GEE_INTEGRATION`)

- [ ] **Step 3: Turn `01_animation_single_images.py` into a thin example**

```python
"""Example: build an NDVI timelapse from a config file.

Usage:
    python 01_animation_single_images.py            # uses config.example.yaml
    gee-animation --config config.example.yaml      # equivalent via CLI
"""
from gee_animation.cli import run

if __name__ == "__main__":
    paths = run("config.example.yaml")
    for p in paths:
        print(f"wrote {p}")
```

- [ ] **Step 4: Update `README.md`**

```markdown
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
```

- [ ] **Step 5: Run the full suite and confirm skips**

Run: `pytest -v`
Expected: unit tests PASS, integration test SKIPPED.

- [ ] **Step 6: Commit**

```bash
git add tests/test_integration.py 01_animation_single_images.py README.md
git commit -m "test: add opt-in EE integration test; example + docs"
```

---

## Self-Review Notes

- **Spec coverage:** auth (T7), config (T2), aoi (T4), collection+cloud mask+NDVI (T5), monthly compositing (T5), render/annotate/assemble+ffmpeg fallback (T6), CLI (T7), unit vs integration testing (T2–T8), reproducibility via config + thin example (T8), empty-collection error (T5 skip + T7 SystemExit). All spec sections mapped.
- **Type consistency:** `Frame(label, image)` defined in T5, consumed in T6/T8; `RunConfig` fields defined in T2, used identically in T5–T8; `colorize`/`ndvi` signatures from T3 used in T3/T6; `parse/build/monthly_median/render` signatures match `DEFAULT_DEPS` wiring in T7.
- **Placeholders:** none — every code step is complete.
- **Note for implementer:** NDVI is computed server-side on `ee.Image` in `collection.add_ndvi` (real EE) and locally re-derived from the downloaded grayscale thumbnail in `render._fetch_thumbnail`; the pure `imaging.ndvi` exists for unit-testing the math and is available if a future task computes NDVI from raw band arrays.
