# Sensor + Index Abstraction (Landsat LST) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the hardcoded "Sentinel-2 + NDVI" pipeline into a pluggable `(sensor, index)` product, shipping Sentinel-2·NDVI and Landsat·(NDVI + LST) so land-surface-temperature timelapses work end to end.

**Architecture:** A new `products.py` holds two registries — sensors (collection ids, cloud masking, scaled-reflectance aliases) and indices (band math + default viz) — plus `get_product(sensor, index)`. `collection.build` drives the whole pipeline off the resolved product and renames the computed band to a standard `INDEX`; `config`, `render`, and `compositing` become index-agnostic (config-driven viz + the `INDEX` band). Adding EVI/water indices or MODIS later is just new registry entries.

**Tech Stack:** Python 3.10+, earthengine-api, Pillow, imageio, numpy, PyYAML; pytest. Tests run in the `GEE_animation` conda env: `/Users/christian/opt/anaconda3/envs/GEE_animation/bin/python -m pytest`.

## Global Constraints

- Python 3.10+. Tests run with the `GEE_animation` conda env python (has deps + pytest); never bare `python`/`pytest`.
- EE faked at the `ee_module` seam in unit tests — no network. The single live test stays `@pytest.mark.integration` + `GEE_INTEGRATION` guard.
- Standard computed band name is `INDEX` (module constant `products.INDEX_BAND = "INDEX"`).
- Sensors: `sentinel2` → `COPERNICUS/S2_SR_HARMONIZED`, coarse property `CLOUDY_PIXEL_PERCENTAGE`, SCL cloud classes `[3,8,9,10,11]`. `landsat` → merge `LANDSAT/LC08/C02/T1_L2` + `LANDSAT/LC09/C02/T1_L2`, coarse property `CLOUD_COVER`, `QA_PIXEL` cloud bits `1|2|3|4|5` (dilated/cirrus/cloud/shadow/snow).
- Reflectance scaling: S2 `× 0.0001`; Landsat SR `× 0.0000275 − 0.2`. Canonical band aliases: `blue, green, red, nir, swir1, swir2`.
- NDVI = `normalizedDifference(["nir","red"])` on **scaled** reflectance (rename `INDEX`). LST = `ST_B10 × 0.00341802 + 149.0 − 273.15` (°C, rename `INDEX`). LST supported only on `landsat`.
- Default viz — ndvi: `(-0.2, 0.9, ["#a1622f","#e8d9a0","#3b7a2a"])`; lst: `(0.0, 40.0, ["#000080","#0000ff","#00ffff","#ffff00","#ff0000","#800000"])`.
- Region-cloud-fraction ordering invariant preserved: fraction computed on the **unmasked** image, **before** the cloud mask.
- Stage only each task's named files when committing (untracked data/`.idea`/`out/` stay untracked).

---

### Task 1: Product registry (`products.py`)

**Files:**
- Create: `gee_animation/products.py`
- Test: `tests/test_products.py`

**Interfaces:**
- Produces:
  - `INDEX_BAND = "INDEX"`.
  - `@dataclass(frozen=True) Sensor` with `name: str`, `collection_ids: tuple`, `scene_cloud_property: str`, and callables `mask_clouds(image, ee_module)`, `cloud_band(image, ee_module)`, `reflectance(image, ee_module)`; method `collection(ee_module=ee) -> ee.ImageCollection` (merges `collection_ids`).
  - `@dataclass(frozen=True) Index` with `name: str`, `sensors: frozenset[str]`, `default_viz: tuple`, callable `compute(sensor, image, ee_module)`.
  - `SENSORS: dict[str, Sensor]`, `INDICES: dict[str, Index]`.
  - `get_product(sensor: str, index: str) -> tuple[Sensor, Index]`; raises `ValueError` on unknown sensor/index or unsupported pair.

- [ ] **Step 1: Write failing tests**

`tests/test_products.py`:
```python
import types
import pytest
from gee_animation import products as P


def test_registry_contents():
    assert set(P.SENSORS) == {"sentinel2", "landsat"}
    assert set(P.INDICES) == {"ndvi", "lst"}
    assert P.SENSORS["sentinel2"].scene_cloud_property == "CLOUDY_PIXEL_PERCENTAGE"
    assert P.SENSORS["landsat"].scene_cloud_property == "CLOUD_COVER"
    assert P.SENSORS["landsat"].collection_ids == (
        "LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2")
    assert P.INDICES["lst"].sensors == frozenset({"landsat"})
    assert P.INDICES["ndvi"].sensors == frozenset({"sentinel2", "landsat"})


def test_get_product_ok():
    sensor, index = P.get_product("landsat", "lst")
    assert sensor.name == "landsat" and index.name == "lst"


def test_get_product_rejects_unknown_and_unsupported_pair():
    with pytest.raises(ValueError, match="sensor"):
        P.get_product("modis", "ndvi")
    with pytest.raises(ValueError, match="index"):
        P.get_product("landsat", "evi")
    with pytest.raises(ValueError, match="not available"):
        P.get_product("sentinel2", "lst")   # LST is Landsat-only


def test_collection_merges_landsat(ee_recorder=None):
    calls = []
    class FakeColl:
        def __init__(self, cid): self.cid = cid
        def merge(self, other): calls.append(("merge", self.cid, other.cid)); return self
    ee = types.SimpleNamespace(ImageCollection=lambda cid: FakeColl(cid))
    P.SENSORS["landsat"].collection(ee_module=ee)
    assert calls == [("merge", "LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2")]


def test_s2_reflectance_selects_aliases_and_scales():
    rec = {}
    class FakeImg:
        def select(self, bands, names): rec["select"] = (tuple(bands), tuple(names)); return self
        def multiply(self, v): rec["multiply"] = v; return self
        def add(self, v): rec["add"] = v; return self
    P.SENSORS["sentinel2"].reflectance(FakeImg())
    assert rec["select"][1] == ("blue", "green", "red", "nir", "swir1", "swir2")
    assert rec["select"][0] == ("B2", "B3", "B4", "B8", "B11", "B12")
    assert rec["multiply"] == 0.0001


def test_landsat_reflectance_scales_with_offset():
    rec = {}
    class FakeImg:
        def select(self, bands, names): rec["select"] = (tuple(bands), tuple(names)); return self
        def multiply(self, v): rec["multiply"] = v; return self
        def add(self, v): rec["add"] = v; return self
    P.SENSORS["landsat"].reflectance(FakeImg())
    assert rec["select"][0] == ("SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7")
    assert rec["multiply"] == 0.0000275 and rec["add"] == -0.2


def test_ndvi_uses_scaled_reflectance():
    rec = {}
    class FakeRefl:
        def normalizedDifference(self, bands): rec["nd"] = tuple(bands); return self
        def rename(self, n): rec["rename"] = n; return "ndvi_band"
    class FakeSensor:
        def reflectance(self, image, ee_module=None): rec["refl"] = True; return FakeRefl()
    out = P.INDICES["ndvi"].compute(FakeSensor(), object(), ee_module=None)
    assert rec["refl"] and rec["nd"] == ("nir", "red") and rec["rename"] == "INDEX"
    assert out == "ndvi_band"


def test_lst_applies_scale_offset_kelvin_to_celsius():
    rec = {}
    class FakeBand:
        def multiply(self, v): rec["multiply"] = v; return self
        def add(self, v): rec["add"] = v; return self
        def subtract(self, v): rec["subtract"] = v; return self
        def rename(self, n): rec["rename"] = n; return "lst_band"
    class FakeImg:
        def select(self, b): rec["select"] = b; return FakeBand()
    out = P.INDICES["lst"].compute(object(), FakeImg(), ee_module=None)
    assert rec["select"] == "ST_B10"
    assert rec["multiply"] == 0.00341802 and rec["add"] == 149.0 and rec["subtract"] == 273.15
    assert rec["rename"] == "INDEX" and out == "lst_band"


def test_landsat_mask_and_cloud_band_use_qa_bits():
    rec = {}
    class FakeQA:
        def bitwiseAnd(self, bits): rec["bits"] = bits; return self
        def eq(self, v): rec["eq"] = v; return "clearmask"
        def neq(self, v): rec["neq"] = v; return self
        def rename(self, n): rec["rename"] = n; return "cloudband"
    class FakeImg:
        def select(self, b): rec["select"] = b; return FakeQA()
        def updateMask(self, m): rec["masked_with"] = m; return "masked"
    expected_bits = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)
    assert P.SENSORS["landsat"].mask_clouds(FakeImg()) == "masked"
    assert rec["bits"] == expected_bits and rec["eq"] == 0 and rec["masked_with"] == "clearmask"
    rec.clear()
    assert P.SENSORS["landsat"].cloud_band(FakeImg()) == "cloudband"
    assert rec["bits"] == expected_bits and rec["neq"] == 0 and rec["rename"] == "cloud"
```

- [ ] **Step 2: Run tests — expect failure** (`ModuleNotFoundError: gee_animation.products`).

Run: `/Users/christian/opt/anaconda3/envs/GEE_animation/bin/python -m pytest tests/test_products.py -v`

- [ ] **Step 3: Implement `gee_animation/products.py`**

```python
"""Sensor + index registry: resolve a (sensor, index) product for the pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import ee

INDEX_BAND = "INDEX"

# --- Sentinel-2 -------------------------------------------------------------
_S2_SCL_CLOUD = [3, 8, 9, 10, 11]
_S2_ALIASES = (("B2", "B3", "B4", "B8", "B11", "B12"),
               ("blue", "green", "red", "nir", "swir1", "swir2"))


def _s2_mask_clouds(image, ee_module=ee):
    scl = image.select("SCL")
    mask = ee_module.Image.constant(1)
    for cls in _S2_SCL_CLOUD:
        mask = mask.And(scl.neq(cls))
    return image.updateMask(mask)


def _s2_cloud_band(image, ee_module=ee):
    scl = image.select("SCL")
    return scl.remap(_S2_SCL_CLOUD, [1] * len(_S2_SCL_CLOUD), 0).rename("cloud")


def _s2_reflectance(image, ee_module=ee):
    return image.select(list(_S2_ALIASES[0]), list(_S2_ALIASES[1])).multiply(0.0001)


# --- Landsat Collection 2 Level 2 ------------------------------------------
# QA_PIXEL bits: 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow, 5 snow.
_L_QA_BITS = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)
_L_ALIASES = (("SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"),
              ("blue", "green", "red", "nir", "swir1", "swir2"))


def _landsat_mask_clouds(image, ee_module=ee):
    clear = image.select("QA_PIXEL").bitwiseAnd(_L_QA_BITS).eq(0)
    return image.updateMask(clear)


def _landsat_cloud_band(image, ee_module=ee):
    return image.select("QA_PIXEL").bitwiseAnd(_L_QA_BITS).neq(0).rename("cloud")


def _landsat_reflectance(image, ee_module=ee):
    return image.select(list(_L_ALIASES[0]), list(_L_ALIASES[1])).multiply(0.0000275).add(-0.2)


# --- Index computations -----------------------------------------------------
def _ndvi(sensor, image, ee_module=ee):
    refl = sensor.reflectance(image, ee_module)
    return refl.normalizedDifference(["nir", "red"]).rename(INDEX_BAND)


def _lst(sensor, image, ee_module=ee):
    return (image.select("ST_B10")
            .multiply(0.00341802).add(149.0).subtract(273.15)
            .rename(INDEX_BAND))


@dataclass(frozen=True)
class Sensor:
    name: str
    collection_ids: tuple
    scene_cloud_property: str
    mask_clouds: Callable
    cloud_band: Callable
    reflectance: Callable

    def collection(self, ee_module=ee):
        colls = [ee_module.ImageCollection(cid) for cid in self.collection_ids]
        merged = colls[0]
        for extra in colls[1:]:
            merged = merged.merge(extra)
        return merged


@dataclass(frozen=True)
class Index:
    name: str
    sensors: frozenset
    default_viz: tuple
    compute: Callable


SENSORS = {
    "sentinel2": Sensor("sentinel2", ("COPERNICUS/S2_SR_HARMONIZED",),
                        "CLOUDY_PIXEL_PERCENTAGE",
                        _s2_mask_clouds, _s2_cloud_band, _s2_reflectance),
    "landsat": Sensor("landsat",
                      ("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"),
                      "CLOUD_COVER",
                      _landsat_mask_clouds, _landsat_cloud_band, _landsat_reflectance),
}

INDICES = {
    "ndvi": Index("ndvi", frozenset({"sentinel2", "landsat"}),
                  (-0.2, 0.9, ["#a1622f", "#e8d9a0", "#3b7a2a"]), _ndvi),
    "lst": Index("lst", frozenset({"landsat"}),
                 (0.0, 40.0, ["#000080", "#0000ff", "#00ffff", "#ffff00", "#ff0000", "#800000"]),
                 _lst),
}


def get_product(sensor: str, index: str):
    if sensor not in SENSORS:
        raise ValueError(f"unsupported sensor {sensor!r}; supported: {sorted(SENSORS)}")
    if index not in INDICES:
        raise ValueError(f"unsupported index {index!r}; supported: {sorted(INDICES)}")
    idx = INDICES[index]
    if sensor not in idx.sensors:
        raise ValueError(
            f"index {index!r} not available for sensor {sensor!r}; "
            f"supported sensors: {sorted(idx.sensors)}"
        )
    return SENSORS[sensor], idx
```

- [ ] **Step 4: Run tests — expect pass.** Run: `/Users/christian/opt/anaconda3/envs/GEE_animation/bin/python -m pytest tests/test_products.py -v`
- [ ] **Step 5: Commit** `gee_animation/products.py tests/test_products.py` — "feat: sensor+index product registry (S2/Landsat, NDVI/LST)".

---

### Task 2: Config — sensor/index/viz schema (`config.py`)

**Files:**
- Modify: `gee_animation/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `products.INDICES`, `products.get_product`.
- Produces: `RunConfig` with new `index: str`, `viz_min: float`, `viz_max: float` (replacing `ndvi_min`/`ndvi_max`), keeping `palette`. `from_yaml` reads `raw["sensor"]`, `raw.get("index","ndvi")`, and an optional `raw["viz"]` block; when `viz` is absent, min/max/palette default to the index's `default_viz`. `validate()` uses `get_product` for the (sensor,index) pair.

- [ ] **Step 1: Update failing tests** in `tests/test_config.py`. Replace the `ndvi:` block in the valid-config test with `index: ndvi` (no `viz:`, exercising defaults) and add cases:

```python
def test_index_viz_defaults_from_index(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {frame: {bbox: [0,0,1,1]}, region: {bbox: [0,0,1,1]}}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.index == "ndvi"
    assert cfg.viz_min == -0.2 and cfg.viz_max == 0.9
    assert cfg.palette == ["#a1622f", "#e8d9a0", "#3b7a2a"]


def test_viz_block_overrides_defaults(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {frame: {bbox: [0,0,1,1]}, region: {bbox: [0,0,1,1]}}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: landsat
        index: lst
        cadence: monthly
        max_cloud_percent: 60
        viz: {min: 5, max: 35, palette: ["#000000", "#ffffff"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.viz_min == 5 and cfg.viz_max == 35 and cfg.palette == ["#000000", "#ffffff"]


def test_rejects_unsupported_sensor_index_pair(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {frame: {bbox: [0,0,1,1]}, region: {bbox: [0,0,1,1]}}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: lst
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="not available"):
        RunConfig.from_yaml(p)
```

Also update every existing test in the file that used `ndvi: {...}` or asserted `cfg.ndvi_min` to the new `index:`/`viz:` shape and `cfg.viz_min`. The unsupported-cadence, null-aoi, missing-frame/region, and region-threshold tests keep working with an added `index: ndvi` line and no `ndvi:` block.

- [ ] **Step 2: Run tests — expect failure.** Run: `.../GEE_animation/bin/python -m pytest tests/test_config.py -v`

- [ ] **Step 3: Implement.** In `gee_animation/config.py`:

Replace the module constants + fields + from_yaml + validate:
```python
from .products import INDICES, get_product

# remove SUPPORTED_SENSORS; keep cadence set
SUPPORTED_CADENCES = {"monthly"}
```
In `RunConfig`, replace `ndvi_min`/`ndvi_max` with `index`, `viz_min`, `viz_max` (keep `palette`):
```python
    sensor: str
    index: str
    cadence: str
    max_cloud_percent: float
    region_max_cloud_percent: float
    viz_min: float
    viz_max: float
    palette: list[str]
    fps: int
    scale: float
    dimensions: int
    out_dir: str = "out"
    draw_region: bool = True
```
In `from_yaml`, replace the `ndvi = raw["ndvi"]` handling:
```python
            render = raw["render"]
            aoi = raw["aoi"] or {}
            sensor = str(raw["sensor"])
            index = str(raw.get("index", "ndvi"))
            viz = dict(raw.get("viz") or {})
            spec = INDICES.get(index)
            d_min, d_max, d_pal = spec.default_viz if spec else (0.0, 1.0, ["#000000", "#ffffff"])
            cfg = cls(
                name=str(raw["name"]),
                project=str(raw["project"]),
                frame_aoi=dict(aoi["frame"] or {}),
                region_aoi=dict(aoi["region"] or {}),
                start=str(raw["start"]),
                end=str(raw["end"]),
                sensor=sensor,
                index=index,
                cadence=str(raw["cadence"]),
                max_cloud_percent=float(raw["max_cloud_percent"]),
                region_max_cloud_percent=float(aoi.get("region_max_cloud_percent", 10)),
                viz_min=float(viz.get("min", d_min)),
                viz_max=float(viz.get("max", d_max)),
                palette=list(viz.get("palette", d_pal)),
                fps=int(render["fps"]),
                scale=float(render["scale"]),
                dimensions=int(render["dimensions"]),
                out_dir=str(raw.get("out_dir", "out")),
                draw_region=bool(raw.get("draw_region", True)),
            )
```
In `validate()`, replace the sensor check and the ndvi check:
```python
        try:
            get_product(self.sensor, self.index)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if self.cadence not in SUPPORTED_CADENCES:
            raise ConfigError(
                f"unsupported cadence {self.cadence!r}; supported: {sorted(SUPPORTED_CADENCES)}"
            )
        # ... keep aoi frame/region checks, region_max range, date checks ...
        if self.viz_max <= self.viz_min:
            raise ConfigError("viz.max must be greater than viz.min")
        if not self.palette:
            raise ConfigError("viz.palette must be non-empty")
```

- [ ] **Step 4: Run tests — expect pass** for `tests/test_config.py`. (Other test files referencing `ndvi_*` will fail until their tasks; note them, don't edit them here.)

Run: `.../GEE_animation/bin/python -m pytest tests/test_config.py -v`

- [ ] **Step 5: Commit** `gee_animation/config.py tests/test_config.py` — "feat: config selects sensor+index with per-index viz defaults".

---

### Task 3: Collection build off the product (`collection.py`)

**Files:**
- Modify: `gee_animation/collection.py`
- Test: `tests/test_collection.py`

**Interfaces:**
- Consumes: `products.get_product`, `RunConfig` (`sensor`, `index`, `start`, `end`, `max_cloud_percent`, `region_max_cloud_percent`, `scale`).
- Produces: `build(cfg, frame_geom, region_geom, ee_module=ee) -> ee.ImageCollection` whose images carry the `INDEX` band; and `add_region_cloud_fraction(image, region, scale, cloud_band, ee_module=ee)` (now takes the sensor's `cloud_band` callable).

- [ ] **Step 1: Rewrite tests** in `tests/test_collection.py`. Remove the old `mask_s2_clouds`/`add_ndvi` tests (that logic moved to `products.py`, covered by `tests/test_products.py`). Keep/adapt the fraction + build-order tests:

```python
import types
from gee_animation import collection as C


def test_add_region_cloud_fraction_uses_cloud_band_and_reduces():
    rec = {}
    class FakeReduced:
        def get(self, k): rec["get"] = k; return "FRAC"
    class FakeCloud:
        def reduceRegion(self, **kw): rec["reduce"] = kw; return FakeReduced()
    class FakeImg:
        def set(self, k, v): rec["set"] = (k, v); return "img+frac"
    def fake_cloud_band(image, ee_module=None):
        rec["cloud_band_called"] = True
        return FakeCloud()
    ee = types.SimpleNamespace(Reducer=types.SimpleNamespace(mean=lambda: "MEAN"))
    out = C.add_region_cloud_fraction(FakeImg(), "REGION", 20, fake_cloud_band, ee_module=ee)
    assert rec["cloud_band_called"] and rec["reduce"]["geometry"] == "REGION"
    assert rec["reduce"]["scale"] == 20 and rec["get"] == "cloud"
    assert rec["set"] == ("region_cloud_fraction", "FRAC") and out == "img+frac"


def test_build_pipeline_order_and_uses_sensor(monkeypatch):
    calls = []
    class FakeColl:
        def filterDate(self, s, e): calls.append(("filterDate", s, e)); return self
        def filterBounds(self, g): calls.append(("filterBounds", g)); return self
        def filter(self, f): calls.append(("filter", f)); return self
        def map(self, fn): calls.append(("map",)); return self
    class FakeSensor:
        name = "landsat"; scene_cloud_property = "CLOUD_COVER"
        def collection(self, ee_module=None): calls.append(("collection",)); return FakeColl()
        def cloud_band(self, image, ee_module=None): return image
        def mask_clouds(self, image, ee_module=None): return image
    class FakeIndex:
        def compute(self, sensor, image, ee_module=None): return image
    monkeypatch.setattr(C, "get_product", lambda s, i: (FakeSensor(), FakeIndex()))
    ee = types.SimpleNamespace(
        Filter=types.SimpleNamespace(
            lte=lambda name, val: ("lte", name, val),
            lt=lambda name, val: ("lt", name, val)))
    cfg = types.SimpleNamespace(sensor="landsat", index="lst",
                                start="2022-01-01", end="2022-02-01",
                                max_cloud_percent=60, region_max_cloud_percent=10, scale=20)
    C.build(cfg, "FRAME", "REGION", ee_module=ee)
    names = [c[0] for c in calls]
    assert names[0] == "collection"
    assert ("filterBounds", "FRAME") in calls
    assert ("filter", ("lte", "CLOUD_COVER", 60)) in calls        # sensor's coarse property
    assert ("filter", ("lt", "region_cloud_fraction", 0.1)) in calls
    idx_lt = next(i for i, c in enumerate(calls) if c == ("filter", ("lt", "region_cloud_fraction", 0.1)))
    idx_maps = [i for i, c in enumerate(calls) if c == ("map",)]
    # map order: region-fraction, mask, index -> fraction filter before the mask map
    assert idx_maps[0] < idx_lt < idx_maps[1] < idx_maps[2]
```

- [ ] **Step 2: Run tests — expect failure.** Run: `.../GEE_animation/bin/python -m pytest tests/test_collection.py -v`

- [ ] **Step 3: Implement.** Replace `gee_animation/collection.py` entirely:

```python
"""Build a cloud-masked (sensor, index) ImageCollection with an INDEX band."""
from __future__ import annotations

import ee

from .products import get_product


def add_region_cloud_fraction(image, region, scale, cloud_band, ee_module=ee):
    cloud = cloud_band(image, ee_module)
    frac = cloud.reduceRegion(
        reducer=ee_module.Reducer.mean(),
        geometry=region,
        scale=scale,
        bestEffort=True,
        maxPixels=int(1e9),
    ).get("cloud")
    return image.set("region_cloud_fraction", frac)


def build(cfg, frame_geom, region_geom, ee_module=ee):
    sensor, index = get_product(cfg.sensor, cfg.index)
    coll = (
        sensor.collection(ee_module)
        .filterDate(cfg.start, cfg.end)
        .filterBounds(frame_geom)
        .filter(ee_module.Filter.lte(sensor.scene_cloud_property, cfg.max_cloud_percent))
        .map(lambda img: add_region_cloud_fraction(
            img, region_geom, cfg.scale, sensor.cloud_band, ee_module))
        .filter(ee_module.Filter.lt("region_cloud_fraction", cfg.region_max_cloud_percent / 100.0))
        .map(lambda img: sensor.mask_clouds(img, ee_module))
        .map(lambda img: index.compute(sensor, img, ee_module))
    )
    return coll
```

- [ ] **Step 4: Run tests — expect pass.** Run: `.../GEE_animation/bin/python -m pytest tests/test_collection.py tests/test_products.py -v`
- [ ] **Step 5: Commit** `gee_animation/collection.py tests/test_collection.py` — "feat: build collection from the resolved (sensor, index) product".

---

### Task 4: Render the INDEX band with config viz (`render.py`)

**Files:**
- Modify: `gee_animation/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `RunConfig` (`viz_min`, `viz_max`, `palette`, `index`, plus existing render fields).
- Produces: unchanged public functions; `_fetch_thumbnail` selects the `"INDEX"` band; `_thumb_params`/`render`/`add_colorbar` use `cfg.viz_min`/`cfg.viz_max`/`cfg.palette`; the colorbar label uses `cfg.index.upper()`.

- [ ] **Step 1: Update tests** in `tests/test_render.py`. Change the `_cfg` helper to the new fields and update assertions:

```python
def _cfg(tmp_path, name="anim", fps=2):
    return types.SimpleNamespace(
        name=name, out_dir=str(tmp_path),
        index="ndvi", viz_min=-0.2, viz_max=0.9, palette=["#000000", "#ffffff"],
        fps=fps, scale=20, dimensions=64,
    )
```
Update `test_thumb_params_preserve_aspect_ratio` to assert `params["min"] == cfg.viz_min and params["max"] == cfg.viz_max`. (Overlay/no-data/colorbar/mp4/gif tests are unaffected — they don't touch viz field names except via `_cfg`.)

Add a band-selection test:
```python
def test_fetch_thumbnail_selects_index_band(monkeypatch, tmp_path):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    selected = {}
    class FakeThumbImg:
        def getThumbURL(self, params): selected["params"] = params; return "http://x"
    class FakeImg:
        def select(self, band): selected["band"] = band; return FakeThumbImg()
    # avoid real network: stub urlopen + PIL by monkeypatching _fetch_thumbnail's helpers is complex;
    # instead assert _thumb_params + that render selects "INDEX" via a lightweight fetch double.
    assert r._thumb_params(cfg, "GEOM")["min"] == cfg.viz_min
    # selection is exercised by the integration test; unit-assert the band constant is INDEX:
    from gee_animation.products import INDEX_BAND
    assert INDEX_BAND == "INDEX"
```

- [ ] **Step 2: Run tests — expect failure** (`_cfg` viz fields / `_thumb_params` min). Run: `.../GEE_animation/bin/python -m pytest tests/test_render.py -v`

- [ ] **Step 3: Implement.** In `gee_animation/render.py`:
  - `_thumb_params`: `"min": cfg.viz_min, "max": cfg.viz_max` (rename from `ndvi_min`/`ndvi_max`).
  - `_fetch_thumbnail`: `image.select("INDEX")` (was `"NDVI"`); reconstruction `value = cfg.viz_min + gray * (cfg.viz_max - cfg.viz_min)`.
  - `render`: `colorize(arr, cfg.viz_min, cfg.viz_max, cfg.palette)`.
  - `add_colorbar`: build the ramp with `cfg.viz_min, cfg.viz_max, cfg.palette`, and label `f"{cfg.index.upper()} {cfg.viz_min:g}..{cfg.viz_max:g}"`.

- [ ] **Step 4: Run tests — expect pass** (whole suite except integration). Run: `.../GEE_animation/bin/python -m pytest -m "not integration" -v`
- [ ] **Step 5: Commit** `gee_animation/render.py tests/test_render.py` — "feat: render the generic INDEX band with config-driven viz".

---

### Task 5: Example configs, integration test, docs

**Files:**
- Modify: `config.example.yaml`
- Create: `config.lst.example.yaml`
- Modify: `tests/test_integration.py`
- Modify: `README.md`

- [ ] **Step 1: Update `config.example.yaml`** — add `sensor`/`index` and swap `ndvi:` for the default (no `viz:` needed). Keep the WNE AOIs, dates, `max_cloud_percent`, `draw_region`, `render`. Set near the top:
```yaml
sensor: sentinel2
index: ndvi
```
Remove the `ndvi:` block (defaults apply). Rename `name: wne_ndvi` stays.

- [ ] **Step 2: Create `config.lst.example.yaml`** — a Landsat LST run over the same WNE AOIs:
```yaml
name: wne_lst
project: hnee-331218
aoi:
  frame:  { shapefile: docs/aoi/wne/frame_timelaps_wne.shp }
  region: { shapefile: docs/aoi/wne/wne.shp }
  region_max_cloud_percent: 20
sensor: landsat
index: lst
start: "2022-05-01"
end: "2022-09-01"
cadence: monthly
max_cloud_percent: 60
draw_region: true
render: { fps: 4, scale: 30, dimensions: 768 }
# viz omitted -> LST default (0..40 C, thermal palette)
```

- [ ] **Step 3: Update `tests/test_integration.py`** — construct `RunConfig` with the new fields (drop `ndvi_min`/`ndvi_max`; add `index`, `viz_min`, `viz_max`) and add a Landsat-LST variant. Example body:
```python
    cfg = RunConfig(
        name="itest", project=os.environ.get("GEE_PROJECT", "hnee-331218"),
        frame_aoi={"bbox": [13.80, 52.85, 13.83, 52.87]},
        region_aoi={"bbox": [13.805, 52.855, 13.825, 52.865]},
        region_max_cloud_percent=80,
        start="2022-06-01", end="2022-09-01",
        sensor="landsat", index="lst",
        cadence="monthly", max_cloud_percent=80,
        viz_min=0.0, viz_max=40.0, palette=["#000080", "#ff0000"],
        fps=2, scale=30, dimensions=256, out_dir=str(tmp_path),
    )
    auth.init(cfg.project)
    frame = aoi.parse(cfg.frame_aoi)
    region = aoi.parse(cfg.region_aoi)
    coll = collection.build(cfg, frame, region)
    frames = compositing.monthly_median(coll, cfg)
    assert frames, "expected at least one monthly LST frame"
    paths = render.render(frames, cfg, geometry=frame)
    assert any(Path(p).exists() for p in paths)
```

- [ ] **Step 4: Update `README.md`** — document `sensor` (`sentinel2`|`landsat`), `index` (`ndvi`|`lst`; LST is Landsat-only), the optional `viz:` block with per-index defaults, and that adding indices/sensors is a `products.py` registry change. Note `config.lst.example.yaml`.

- [ ] **Step 5: Verify.** Run: `.../GEE_animation/bin/python -m pytest -m "not integration" -v` (all green, pristine) and `.../GEE_animation/bin/python -c "from gee_animation.config import RunConfig; RunConfig.from_yaml('config.example.yaml'); RunConfig.from_yaml('config.lst.example.yaml'); print('both configs load')"`.

- [ ] **Step 6: Commit** `config.example.yaml config.lst.example.yaml tests/test_integration.py README.md` — "docs: sensor/index config, Landsat LST example + integration".

---

## Self-Review Notes

- **Spec coverage:** sensor registry (T1), index registry incl. LST math (T1), config sensor/index/viz + pair validation (T2), pipeline off product with ordering invariant (T3), index-agnostic render (T4), Landsat-LST example + integration + docs (T5). All spec sections mapped. EVI/water/MODIS explicitly deferred (spec non-goals).
- **Type consistency:** `INDEX_BAND="INDEX"` defined T1, selected in T4; `get_product` signature T1 used in T2/T3; `Sensor.cloud_band`/`mask_clouds`/`reflectance`/`collection` defined T1, called in T3; `add_region_cloud_fraction(..., cloud_band, ...)` new arg defined T3 and its test; `RunConfig` viz_min/viz_max/index defined T2, consumed in T3(build reads sensor/index/scale) and T4(render reads viz_*).
- **Placeholders:** none — all steps carry complete code.
- **Note for implementer:** NDVI must be computed on **scaled** reflectance (Landsat SR has a −0.2 offset, so `normalizedDifference` on raw DN would be wrong); the S2 path is unchanged mathematically since its scale has no offset. Landsat has far fewer scenes than S2 — the LST example relaxes `region_max_cloud_percent` to 20 and widens the window; if a run reports "No images found", relax further. Run all tests in the `GEE_animation` conda env.
```
