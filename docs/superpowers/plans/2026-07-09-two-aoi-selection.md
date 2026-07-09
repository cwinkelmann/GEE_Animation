# Two-AOI Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support two areas of interest — a rectangle that defines the animation frame (rendered extent, aspect ratio preserved) and a polygon "important region" that scenes must have <10% cloud cover over — in the `gee_animation` Sentinel-2 NDVI pipeline.

**Architecture:** Config gains `aoi.frame` + `aoi.region` (each bbox or GeoJSON) and `aoi.region_max_cloud_percent`. `collection.build` computes cloud fraction *within the region* server-side (SCL remap → `reduceRegion(mean)` on the UNMASKED image) and filters on it before masking. `cli.run` parses both AOIs, filters/builds with both, and renders over the frame. `render` keeps aspect ratio by requesting a single-int `dimensions` thumbnail.

**Tech Stack:** Python 3.10+, earthengine-api, Pillow, imageio, imageio-ffmpeg, PyYAML, numpy; pytest. Runs in the `GEE_animation` conda env.

## Global Constraints

- Python 3.10+ (`list[...]`, `X | None`).
- Sentinel-2 SR `COPERNICUS/S2_SR_HARMONIZED`; SCL cloud classes dropped = `[3, 8, 9, 10, 11]`.
- Region cloud fraction is computed on the **unmasked** image, **before** `mask_s2_clouds`; filter is `Filter.lt("region_cloud_fraction", region_max_cloud_percent/100)`.
- `build` pipeline order: `filterDate → filterBounds(frame) → coarse CLOUDY_PIXEL_PERCENTAGE ≤ max_cloud_percent → map(add_region_cloud_fraction over region) → filter(region frac) → map(mask_s2_clouds) → map(add_ndvi)`.
- Render region = frame geometry; `dimensions` passed as a single int (never `"WxH"`) so EE preserves aspect ratio.
- `region_max_cloud_percent` default = 10; valid range `0..100` inclusive.
- No live EE in unit tests — EE faked at the `ee_module` seam; the one integration test is `@pytest.mark.integration` and skips without `GEE_INTEGRATION`.
- Stage only the files each task names when committing (untracked files like `CLAUDE.md`, `.idea/*` must stay untracked).

---

### Task 1: Two-AOI config schema (`config.py`)

**Files:**
- Modify: `gee_animation/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `RunConfig` with `frame_aoi: dict`, `region_aoi: dict`, `region_max_cloud_percent: float` (replacing the single `aoi: dict`); `from_yaml` reads them from `raw["aoi"]["frame"|"region"]` and `raw["aoi"].get("region_max_cloud_percent", 10)`.

- [ ] **Step 1: Update the failing tests** — replace the single-AOI YAML in `tests/test_config.py` with the two-AOI schema and add the new cases:

```python
def test_from_yaml_loads_two_aois(tmp_path):
    p = _write(tmp_path, """
        name: test
        project: hnee-331218
        aoi:
          frame:
            bbox: [13.7, 52.8, 13.9, 52.95]
          region:
            geojson:
              type: Polygon
              coordinates: [[[13.75, 52.83], [13.85, 52.83], [13.85, 52.90], [13.75, 52.83]]]
          region_max_cloud_percent: 10
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000", "#ffffff"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.frame_aoi == {"bbox": [13.7, 52.8, 13.9, 52.95]}
    assert cfg.region_aoi["geojson"]["type"] == "Polygon"
    assert cfg.region_max_cloud_percent == 10


def test_region_max_cloud_percent_defaults_to_10(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {bbox: [0, 0, 1, 1]}
          region: {bbox: [0, 0, 1, 1]}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    assert RunConfig.from_yaml(p).region_max_cloud_percent == 10


def test_rejects_missing_frame_or_region(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {}
          region: {bbox: [0, 0, 1, 1]}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="frame"):
        RunConfig.from_yaml(p)


def test_rejects_region_cloud_percent_out_of_range(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {bbox: [0, 0, 1, 1]}
          region: {bbox: [0, 0, 1, 1]}
          region_max_cloud_percent: 150
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="region_max_cloud_percent"):
        RunConfig.from_yaml(p)
```

Also update the existing valid-config and other tests in the file that still use the old flat `aoi:` / `aoi: {bbox: ...}` to the new `aoi: {frame: ..., region: ...}` shape (they must keep passing).

- [ ] **Step 2: Run tests — expect failures** (`AttributeError`/`ConfigError`/`KeyError` on `frame_aoi`).

Run: `pytest tests/test_config.py -v`

- [ ] **Step 3: Implement the schema.** In `gee_animation/config.py`:

Replace the `aoi: dict` field with:
```python
    frame_aoi: dict
    region_aoi: dict
```
and add (as a required field, before `out_dir`):
```python
    region_max_cloud_percent: float
```

In `from_yaml`, replace the `aoi=dict(raw["aoi"] or {})` line and add the region threshold:
```python
            aoi = raw["aoi"]
            cfg = cls(
                name=str(raw["name"]),
                project=str(raw["project"]),
                frame_aoi=dict(aoi["frame"] or {}),
                region_aoi=dict(aoi["region"] or {}),
                start=str(raw["start"]),
                end=str(raw["end"]),
                sensor=str(raw["sensor"]),
                cadence=str(raw["cadence"]),
                max_cloud_percent=float(raw["max_cloud_percent"]),
                region_max_cloud_percent=float(aoi.get("region_max_cloud_percent", 10)),
                ndvi_min=float(ndvi["min"]),
                ndvi_max=float(ndvi["max"]),
                palette=list(ndvi["palette"]),
                fps=int(render["fps"]),
                scale=float(render["scale"]),
                dimensions=int(render["dimensions"]),
                out_dir=str(raw.get("out_dir", "out")),
            )
```

In `validate()`, replace the single-AOI check with:
```python
        for label, a in (("frame", self.frame_aoi), ("region", self.region_aoi)):
            if not (a.get("bbox") or a.get("geojson")):
                raise ConfigError(
                    f"aoi.{label} must define either 'bbox' or 'geojson'"
                )
        if not 0 <= self.region_max_cloud_percent <= 100:
            raise ConfigError("region_max_cloud_percent must be between 0 and 100")
```

- [ ] **Step 4: Run tests — expect pass.** Run: `pytest tests/test_config.py -v`
- [ ] **Step 5: Commit** `gee_animation/config.py tests/test_config.py` — "feat: two-AOI (frame + region) config schema".

---

### Task 2: Inline GeoJSON support (`aoi.py`)

**Files:**
- Modify: `gee_animation/aoi.py`
- Test: `tests/test_aoi.py`

**Interfaces:**
- `_load_geojson_geometry(src)` accepts a dict (used directly) or a str path (loaded). `parse` unchanged in signature.

- [ ] **Step 1: Failing tests** — add to `tests/test_aoi.py`:

```python
def test_parse_inline_geojson_dict():
    ee = _fake_ee()
    geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    result = parse({"geojson": geom}, ee_module=ee)
    assert result.spec == ("geojson", geom)


def test_load_geojson_accepts_inline_feature_dict():
    feat = {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1, 2]}}
    assert _load_geojson_geometry(feat) == {"type": "Point", "coordinates": [1, 2]}
```

- [ ] **Step 2: Run — expect failure** (current `_load_geojson_geometry` calls `Path(src).read_text()` on a dict → `TypeError`). Run: `pytest tests/test_aoi.py -v`

- [ ] **Step 3: Implement.** In `gee_animation/aoi.py`, change the first line of `_load_geojson_geometry`:
```python
def _load_geojson_geometry(src):
    obj = src if isinstance(src, dict) else json.loads(Path(src).read_text())
    t = obj.get("type")
    if t == "FeatureCollection":
        return obj["features"][0]["geometry"]
    if t == "Feature":
        return obj["geometry"]
    return obj  # already a bare geometry
```
(`parse` already forwards `aoi_cfg["geojson"]` — now a dict or path — unchanged.)

- [ ] **Step 4: Run — expect pass.** Run: `pytest tests/test_aoi.py -v`
- [ ] **Step 5: Commit** `gee_animation/aoi.py tests/test_aoi.py` — "feat: accept inline GeoJSON geometry in aoi.parse".

---

### Task 3: Region cloud filter + build signature (`collection.py`)

**Files:**
- Modify: `gee_animation/collection.py`
- Test: `tests/test_collection.py`

**Interfaces:**
- `add_region_cloud_fraction(image, region, scale, ee_module=ee)` sets `region_cloud_fraction`.
- `build(cfg, frame_geom, region_geom, ee_module=ee)` — new 3-geometry signature and pipeline order.

- [ ] **Step 1: Failing tests** — extend `tests/test_collection.py`. Add a fraction test and rewrite the build test to assert the new order (region filter BEFORE mask). Use fakes that record calls:

```python
def test_add_region_cloud_fraction_sets_property():
    rec = {"selected": None, "remap": None, "reduce": None, "prop": None}

    class FakeReduced:
        def get(self, k): rec["reduce_get"] = k; return "FRAC"

    class FakeCloud:
        def rename(self, n): return self
        def reduceRegion(self, **kw): rec["reduce"] = kw; return FakeReduced()

    class FakeSCL:
        def remap(self, frm, to, default):
            rec["remap"] = (frm, to, default); return FakeCloud()

    class FakeImg:
        def select(self, n): rec["selected"] = n; return FakeSCL()
        def set(self, k, v): rec["prop"] = (k, v); return "img+frac"

    import types
    ee = types.SimpleNamespace(Reducer=types.SimpleNamespace(mean=lambda: "MEAN"))
    out = C.add_region_cloud_fraction(FakeImg(), "REGION", 20, ee_module=ee)
    assert rec["selected"] == "SCL"
    assert rec["remap"][0] == [3, 8, 9, 10, 11] and rec["remap"][2] == 0
    assert rec["reduce"]["geometry"] == "REGION" and rec["reduce"]["scale"] == 20
    assert rec["prop"] == ("region_cloud_fraction", "FRAC")
    assert out == "img+frac"


def test_build_filters_region_cloud_before_masking():
    calls = []

    class FakeColl:
        def filterDate(self, s, e): calls.append(("filterDate", s, e)); return self
        def filterBounds(self, g): calls.append(("filterBounds", g)); return self
        def filter(self, f): calls.append(("filter", f)); return self
        def map(self, fn): calls.append(("map",)); return self

    import types
    ee = types.SimpleNamespace(
        ImageCollection=lambda cid: (calls.append(("ImageCollection", cid)) or FakeColl()),
        Filter=types.SimpleNamespace(
            lte=lambda name, val: ("lte", name, val),
            lt=lambda name, val: ("lt", name, val),
        ),
    )
    cfg = types.SimpleNamespace(
        start="2022-01-01", end="2022-02-01",
        max_cloud_percent=60, region_max_cloud_percent=10, scale=20,
    )
    C.build(cfg, "FRAME", "REGION", ee_module=ee)
    names = [c[0] for c in calls]
    # ImageCollection, filterDate, filterBounds, filter(lte), map(frac), filter(lt), map(mask), map(ndvi)
    assert calls[0] == ("ImageCollection", "COPERNICUS/S2_SR_HARMONIZED")
    assert ("filterBounds", "FRAME") in calls
    assert ("filter", ("lte", "CLOUDY_PIXEL_PERCENTAGE", 60)) in calls
    assert ("filter", ("lt", "region_cloud_fraction", 0.1)) in calls
    # region-fraction filter (lt) must come before the mask map:
    lt_idx = names.index("filter", names.index("map"))  # first filter after first map
    assert names.count("map") == 3
    # order: the lt filter precedes the 2nd map (mask)
    idx_lt = next(i for i, c in enumerate(calls) if c == ("filter", ("lt", "region_cloud_fraction", 0.1)))
    idx_maps = [i for i, c in enumerate(calls) if c == ("map",)]
    assert idx_maps[0] < idx_lt < idx_maps[1] < idx_maps[2]
```

- [ ] **Step 2: Run — expect failure** (no `add_region_cloud_fraction`; `build` arity). Run: `pytest tests/test_collection.py -v`

- [ ] **Step 3: Implement.** In `gee_animation/collection.py`:
```python
def add_region_cloud_fraction(image, region, scale, ee_module=ee):
    scl = image.select("SCL")
    cloud = scl.remap(_SCL_MASK_CLASSES, [1] * len(_SCL_MASK_CLASSES), 0).rename("cloud")
    frac = cloud.reduceRegion(
        reducer=ee_module.Reducer.mean(),
        geometry=region,
        scale=scale,
        bestEffort=True,
        maxPixels=int(1e9),
    ).get("cloud")
    return image.set("region_cloud_fraction", frac)


def build(cfg, frame_geom, region_geom, ee_module=ee):
    coll = (
        ee_module.ImageCollection(S2_COLLECTION)
        .filterDate(cfg.start, cfg.end)
        .filterBounds(frame_geom)
        .filter(ee_module.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", cfg.max_cloud_percent))
        .map(lambda img: add_region_cloud_fraction(img, region_geom, cfg.scale, ee_module))
        .filter(ee_module.Filter.lt("region_cloud_fraction", cfg.region_max_cloud_percent / 100.0))
        .map(lambda img: mask_s2_clouds(img, ee_module))
        .map(lambda img: add_ndvi(img, ee_module))
    )
    return coll
```

- [ ] **Step 4: Run — expect pass.** Run: `pytest tests/test_collection.py -v`
- [ ] **Step 5: Commit** `gee_animation/collection.py tests/test_collection.py` — "feat: filter scenes by cloud fraction within the region polygon".

---

### Task 4: Wire two AOIs through the CLI (`cli.py`)

**Files:**
- Modify: `gee_animation/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- `run()` parses `cfg.frame_aoi` and `cfg.region_aoi`, calls `build(cfg, frame_geom, region_geom)` and `render(frames, cfg, geometry=frame_geom)`.

- [ ] **Step 1: Update the orchestration test** in `tests/test_cli.py` — new config YAML + assert frame/region threading:

```python
def test_run_orchestrates_pipeline(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "name: t\nproject: p\n"
        "aoi:\n  frame: {bbox: [0,0,1,1]}\n  region: {bbox: [0,0,1,1]}\n"
        "  region_max_cloud_percent: 10\n"
        'start: "2022-01-01"\nend: "2022-03-01"\n'
        "sensor: sentinel2\ncadence: monthly\nmax_cloud_percent: 60\n"
        'ndvi: {min: -0.2, max: 0.9, palette: ["#000000","#ffffff"]}\n'
        "render: {fps: 2, scale: 20, dimensions: 64}\n"
    )
    calls = []
    parsed = {"count": 0}

    def fake_parse(aoi):
        parsed["count"] += 1
        tag = "FRAME" if parsed["count"] == 1 else "REGION"
        calls.append(("parse", tag)); return tag

    deps = types.SimpleNamespace(
        init=lambda project: calls.append(("init", project)),
        parse=fake_parse,
        build=lambda cfg, frame, region: (calls.append(("build", frame, region)) or "COLL"),
        monthly_median=lambda coll, cfg: (calls.append(("monthly_median", coll)) or ["f1", "f2"]),
        render=lambda frames, cfg, geometry=None: (calls.append(("render", frames, geometry)) or [tmp_path / "t.gif"]),
    )
    out = run(str(cfg_path), deps=deps)
    assert [c[0] for c in calls] == ["init", "parse", "parse", "build", "monthly_median", "render"]
    assert ("build", "FRAME", "REGION") in calls
    assert ("render", ["f1", "f2"], "FRAME") in calls
    assert out == [tmp_path / "t.gif"]
```

Update the empty-frames test's config to the two-AOI schema as well (same `aoi:` block).

- [ ] **Step 2: Run — expect failure.** Run: `pytest tests/test_cli.py -v`

- [ ] **Step 3: Implement.** In `gee_animation/cli.py` `run()`, replace the AOI/build/render lines:
```python
    frame_geom = deps.parse(cfg.frame_aoi)
    region_geom = deps.parse(cfg.region_aoi)
    coll = deps.build(cfg, frame_geom, region_geom)
    frames = deps.monthly_median(coll, cfg)
    if not frames:
        raise RuntimeError(
            "No images found for the given AOI/date range/cloud filter."
        )
    return deps.render(frames, cfg, geometry=frame_geom)
```
(`DEFAULT_DEPS` names are unchanged.)

- [ ] **Step 4: Run — expect pass.** Run: `pytest tests/test_cli.py -v`
- [ ] **Step 5: Commit** `gee_animation/cli.py tests/test_cli.py` — "feat: wire frame + region AOIs through the CLI pipeline".

---

### Task 5: Aspect-ratio contract in the thumbnail request (`render.py`)

**Files:**
- Modify: `gee_animation/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Extract `_thumb_params(cfg, geometry) -> dict` (pure) used by `_fetch_thumbnail`; guarantees a single-int `dimensions` and `region` (aspect preserved).

- [ ] **Step 1: Failing test** — add to `tests/test_render.py`:
```python
def test_thumb_params_preserve_aspect_ratio():
    from gee_animation.render import _thumb_params
    cfg = _cfg(__import__("pathlib").Path("."))  # _cfg provides ndvi_min/max, dimensions
    params = _thumb_params(cfg, "GEOM")
    assert isinstance(params["dimensions"], int)   # single int -> EE preserves aspect
    assert params["region"] == "GEOM"
    assert params["min"] == cfg.ndvi_min and params["max"] == cfg.ndvi_max
```

- [ ] **Step 2: Run — expect failure** (`_thumb_params` undefined). Run: `pytest tests/test_render.py -v`

- [ ] **Step 3: Implement.** In `gee_animation/render.py`, add and use the helper:
```python
def _thumb_params(cfg, geometry) -> dict:
    # single-int `dimensions` -> EE fits the largest side and preserves aspect ratio
    return {
        "min": cfg.ndvi_min,
        "max": cfg.ndvi_max,
        "dimensions": cfg.dimensions,
        "region": geometry,
        "format": "png",
    }
```
and change `_fetch_thumbnail`'s URL line to:
```python
    url = image.select("NDVI").getThumbURL(_thumb_params(cfg, geometry))
```

- [ ] **Step 4: Run — expect pass** (plus full render suite). Run: `pytest tests/test_render.py -v`
- [ ] **Step 5: Commit** `gee_animation/render.py tests/test_render.py` — "refactor: extract _thumb_params; lock aspect-ratio-preserving thumbnail".

---

### Task 6: Example config, integration test, README

**Files:**
- Modify: `config.example.yaml`
- Modify: `tests/test_integration.py`
- Modify: `README.md`

- [ ] **Step 1: Update `config.example.yaml`** to the two-AOI schema (preserve the existing `start`/`end` dates already in the file):
```yaml
aoi:
  frame:                         # rectangle -> animation extent (aspect ratio preserved)
    bbox: [13.7, 52.8, 13.9, 52.95]   # [minLon, minLat, maxLon, maxLat]
  region:                        # polygon -> important region (cloud-filtered)
    geojson:                     # inline GeoJSON (or a file path string)
      type: Polygon
      coordinates: [[[13.75, 52.83], [13.85, 52.83], [13.85, 52.90], [13.75, 52.90], [13.75, 52.83]]]
  region_max_cloud_percent: 10   # keep scenes only if cloud over the region < 10%
```
(Leave `max_cloud_percent`, `ndvi`, `render`, dates as they are.)

- [ ] **Step 2: Update `tests/test_integration.py`** — construct `RunConfig` with the new fields and call `build` with both geometries:
```python
    cfg = RunConfig(
        name="itest", project=os.environ.get("GEE_PROJECT", "hnee-331218"),
        frame_aoi={"bbox": [13.80, 52.85, 13.83, 52.87]},
        region_aoi={"bbox": [13.805, 52.855, 13.825, 52.865]},
        region_max_cloud_percent=80,     # relaxed so the tiny test reliably finds scenes
        start="2022-06-01", end="2022-09-01",
        sensor="sentinel2", cadence="monthly", max_cloud_percent=80,
        ndvi_min=-0.2, ndvi_max=0.9, palette=["#a1622f", "#3b7a2a"],
        fps=2, scale=20, dimensions=256, out_dir=str(tmp_path),
    )
    auth.init(cfg.project)
    frame = aoi.parse(cfg.frame_aoi)
    region = aoi.parse(cfg.region_aoi)
    coll = collection.build(cfg, frame, region)
    frames = compositing.monthly_median(coll, cfg)
    assert frames, "expected at least one monthly frame"
    paths = render.render(frames, cfg, geometry=frame)
    assert any(Path(p).exists() for p in paths)
```

- [ ] **Step 3: Update `README.md`** — document the two-AOI config: `aoi.frame` (rectangle, animation extent, aspect ratio preserved) and `aoi.region` (polygon, kept only if cloud over it `< region_max_cloud_percent`, default 10). Note `region` accepts inline GeoJSON or a file path.

- [ ] **Step 4: Verify.** Run: `pytest -m "not integration" -v` (all green, pristine) and `pytest tests/test_integration.py -v` (SKIPPED without `GEE_INTEGRATION`).

- [ ] **Step 5: Commit** `config.example.yaml tests/test_integration.py README.md` — "docs: two-AOI example config, integration test, README".

---

## Self-Review Notes

- **Spec coverage:** frame vs region (T1/T4), aspect ratio (T5), region <10% cloud via in-region fraction before masking (T3), inline polygon in example (T2/T6). All FEATURES.md two-AOI points mapped.
- **Type consistency:** `frame_aoi`/`region_aoi`/`region_max_cloud_percent` defined in T1 and consumed identically in T3/T4/T6; `build(cfg, frame_geom, region_geom)` defined T3, called T4/T6; `_thumb_params` T5 used by `_fetch_thumbnail`.
- **Ordering invariant** (fraction before mask) is asserted explicitly by the T3 build test.
- **Note for implementers:** `add_region_cloud_fraction` must run on the unmasked image; do not reorder it after `mask_s2_clouds`. Run all deps/tests in the `GEE_animation` conda env, not the system Python.
