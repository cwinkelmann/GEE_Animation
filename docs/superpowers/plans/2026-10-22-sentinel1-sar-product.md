# Sentinel-1 radar as a package product — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `animate(region, sensor="sentinel1", index="sar_vv", start, end, cadence="10day")` renders a Sentinel-1 backscatter animation with everything the optical products get for free — period medians, the inside/outside chart, metadata, GeoTIFF export, the CLI and the GUI — and notebook 09 (Baltic sea ice) shrinks from a hand-rolled Earth Engine pipeline to a few package calls.

**Why now:** the radar videos of the windthrow report were made by two throwaway scripts on `spike/sar-windthrow` (raw EE exports to GeoTIFF, then a runtime-registered fake index pushed through the renderer). Nothing reached `gee_animation/`; notebook 09 reimplements the same pipeline by hand. One sensor entry and two products replace both.

**Architecture:** one new `Sensor` ("sentinel1": `COPERNICUS/S1_GRD`, IW mode, no cloud metadata, no reflectance) and two `Index` entries (`sar_vv`, `sar_vh`) in `gee_animation/products.py`, following the `modis_lst`/`lst_modis` precedent exactly (own sensor, own-sensor-only index, `_no_reflectance`). Each index supplies `build_collection` so only scenes carrying its polarisation are used. `api.animate` gains a `cadence` argument (the pipeline already supports `10day`). The renderer's Copernicus credit covers Sentinel-1. No change to compositing, render, interpolation or caching: a dB band is just another single-band float index.

**Tech Stack:** `gee_animation` (products registry, `collection.build`, `compositing`, `api.animate`), pytest with the fake-`ee` pattern of `tests/test_products.py`, one live-EE check through the CLI, nbformat for the notebook.

## Global Constraints

- Branch `feat/sentinel1-sar`, worktree `../GEE_animation-sar`, cut from `feat/random_forest_classification` (b3fbcfb) so notebook 09 and its AOI are present. PR target: `main` once the notebooks branch is merged, else that branch.
- **Commits carry no Claude attribution lines** (standing instruction).
- Tests: `PYTHONPATH=$PWD conda run -n GEE_animation python -m pytest -m "not integration" -q`. **Never `pip install` into the env.**
- Test first, every task: a failing test precedes the code that makes it pass.
- Live-EE runs use the Baltic AOI (`docs/aoi/balticsea_ice/aoi.geojson`), Dec 2025 – Apr 2026, one at a time, `EE_PROJECT` from the environment; the project id never lands in a committed file.
- Notebook hygiene per `scripts/check_notebooks.py`: no stored outputs, no execution counts, no operator project id in code cells, Colab badge + install cell on top, "Make it yours" at the end.

## Design decisions (edit here if you disagree)

| Decision | Choice | Alternative considered |
|---|---|---|
| Collection | `COPERNICUS/S1_GRD` (already log-scaled, dB), `instrumentMode == "IW"` | EW mode too (polar seas): dropped, IW is the land/coast mode and mixing swath modes changes the noise floor |
| Products | `sar_vv`, `sar_vh` (one band each, dB) | a `sar_ratio` (VH − VV) index: deferred, easy to add later via the same pattern |
| Orbits | both passes pooled in a period median | an `orbit:` config key (ascending/descending): deferred; note in docs that incidence-angle differences between passes add brightness steps at swath edges |
| Period reducer | the default median (speckle suppression is exactly what a median does) | mean in linear power: more "correct" radiometrically, invisible in a picture; deferred |
| Cloud machinery | `scene_cloud_property=None`, `mask_clouds` = identity, `cloud_band` = constant 0 → `region_cloud_fraction` 0, every scene passes | — |
| Native scale | 10 m (`_SENSOR_NATIVE_M["sentinel1"] = 10`); `api._SCALE["sentinel1"] = 20` for the render/chart/GeoTIFF default (GRD is 10 m pixels of ~20 m resolution) | — |
| Default viz | `sar_vv` −25 … 0 dB, `sar_vh` −30 … −5 dB, grey ramp `#000000 → #ffffff` — **a guess from the Baltic notebook and the spike (VH −20…−8 over forest)**; Task 4 checks it against real frames | the spike's blue-yellow palette: radar in grey is the convention viewers know |
| Legend words | low "smooth / open water", high "rough / ice, built-up" | — |
| Credit | `Contains modified Copernicus Sentinel data {years}` (same wording as Sentinel-2; the licence is the same) | — |
| `animate(cadence=...)` | new keyword, default `"monthly"`, passed to `RunConfig` unchanged | — |

## Review Focus

1. **A radar run silently loses scenes to the cloud filters** — pinned by `test_sentinel1_has_no_clouds_to_mask` (cloud band is zeros) and the live check's scene count equalling the notebook's inventory (209 VV scenes).
2. **The VH product picks up VV-only scenes (or vice versa)** — pinned by `test_sar_build_collection_keeps_only_scenes_with_that_polarisation`.
3. **The registry test and GUI pins break for unrelated-looking reasons** — `test_registry_contents` and `test_indices_for_filters_by_sensor` updated in the same commit as the registry.
4. **The default viz misrepresents the data** — Task 4 reports the p2/p98 of a December and a February frame and adjusts the range before the notebook uses it.
5. **Notebook 09 regresses hygiene** — `scripts/check_notebooks.py notebooks/09_sar_baltic_ice.ipynb` green in Task 5.

## File map

| File | Task | Responsibility |
|---|---|---|
| `gee_animation/products.py` | 1 | sensor + two indices + native scale |
| `tests/test_products.py` | 1 | registry, maths, polarisation filter, no-cloud, restriction tests |
| `gee_animation/api.py`, `tests/test_api.py` | 2 | `cadence` keyword, `_SCALE` entry |
| `gee_animation/render.py`, `tests/test_render.py` | 3 | Copernicus credit for `sentinel1` |
| `tests/test_gui.py` | 3 | `indices_for("sentinel1")` |
| `config/sar_vv.example.yaml` | 3 | CLI example (Baltic, 10day) |
| `docs/rendering-products.md`, `docs/publishing-animations.md`, `README.md` | 3 | product table rows, credit row, index list |
| `notebooks/09_sar_baltic_ice.ipynb` | 5 | rebuilt on `animate()` |

## Tasks

### Task 1 — register the sensor and the two products (products.py)

- [ ] Tests first in `tests/test_products.py`: extend `test_registry_contents` (sensor + indices), `test_native_scale_m_per_product` (`sentinel1/sar_vv` = 10); add `test_sentinel1_collection_is_grd_in_iw_mode`, `test_sar_indices_select_the_polarisation_band_in_db_and_keep_time`, `test_sar_build_collection_keeps_only_scenes_with_that_polarisation`, `test_sentinel1_has_no_clouds_to_mask`, `test_sar_is_sentinel1_only_and_sentinel1_is_sar_only`. Run: they fail on `KeyError`/`ValueError`.
- [ ] `products.py`: `_S1_ID`, `_s1_collection(ee_module)` (IW filter), `_s1_mask_clouds` (identity), `_s1_cloud_band` (`ee.Image.constant(0).rename("cloud")`), `_sar_band(band)` compute factory (select, rename `INDEX_BAND`, copy `system:time_start`), `_sar_collection(band)` build-collection factory (date, bounds, `listContains("transmitterReceiverPolarisation", band)`), the `SENSORS["sentinel1"]` entry, `INDICES["sar_vv"]`/`["sar_vh"]` with `display_name`, `units="dB"`, labels, `bands`, `formula`, `default_viz`; `_SENSOR_NATIVE_M["sentinel1"] = 10`. Generalise `_no_reflectance`'s message ("this sensor has no reflectance bands").
- [ ] Suite green. Commit: "Sentinel-1 radar: sentinel1 sensor, sar_vv and sar_vh products".

### Task 2 — `animate(cadence=...)`

- [ ] Test first: `test_animate_cadence_defaults_to_monthly_and_passes_through` in `tests/test_api.py` (monthly default; `cadence="10day"` reaches the RunConfig; `scale == 20` for sentinel1).
- [ ] `api.py`: keyword, docstring line, `_SCALE["sentinel1"] = 20`.
- [ ] Suite green. Commit.

### Task 3 — credit, GUI, example config, docs

- [ ] Test first: `tests/test_render.py` — `_default_credit` for `sensor="sentinel1"` yields the Copernicus wording with years; `tests/test_gui.py` — `indices_for("sentinel1") == {"sar_vv", "sar_vh"}`, `sar_vv` absent for sentinel2.
- [ ] `render.py`: treat `sentinel1` like `sentinel2` in `_default_credit`.
- [ ] `config/sar_vv.example.yaml`: Baltic AOI, `cadence: 10day`, `render: {scale: 20, preset: 1080p, interpolate: auto}`; `docs/rendering-products.md` two rows + a "radar" paragraph (dB, IW, both orbits, speckle = median); `docs/publishing-animations.md` credit row; README index list.
- [ ] Suite green. Commit.

### Task 4 — live check through the CLI (one run)

- [ ] `EE_PROJECT=… gee-animation --config config/sar_vv.example.yaml` from the worktree with `PYTHONPATH=$PWD`; expect 15 observed periods (Dec 1 … Apr 20), the scene count in the log, and frames that match the notebook's hand-rolled version (Feb: ice in the lagoons, Apr: open water).
- [ ] From the exported GeoTIFFs (`geotiffs: true`), report p2/p98 of VV for 2025-12-01 and 2026-02-09; adjust `default_viz` if the ice/water contrast sits outside −25 … 0. Same for VH on one period. Record the numbers in this plan.
- [x] Commit any viz change with the numbers in the message.

**Task 4 record (2026-10-22, live run of `config/sar_vv.example.yaml`):** 15 observed
periods, 155 frames with interpolation, scene counts 12–21 per period (same archive the
notebook's inventory counts). 10-day medians at 100 m over the AOI, p2 / p50 / p98:
VV −22.5 / −18.8 / −7.0 (Dec), −19.8 / −16.0 / −9.1 (Feb, ice), −22.4 / −17.8 / −8.4 (Apr);
VH −36.1 / −32.7 / −13.6 (Dec), −35.1 / −30.3 / −14.9 (Feb). Defaults set to VV −25…−5,
VH −35…−10. **Finding:** every GeoTIFF export was refused — the size backstop estimated
4 bytes/sample and EE charges a fifth byte (mask) per pixel; fixed in `render.py`
(`_GEOTIFF_MASK_BYTES_PER_PIXEL`, test `test_geotiff_fit_scale_counts_the_mask_byte_ee_charges_for`).

### Task 5 — rebuild notebook 09 on the package

- [ ] Keep: title, install, auth, AOI, the archive inventory cell (modes/polarisations — the lesson that EW/HH exists), the "what optical would have given you" metadata cell.
- [ ] Replace the frames/assemble cells with one `animate(REGION, sensor="sentinel1", index="sar_vv", start=START, end=END, cadence="10day", preset="720p", name="baltic_ice", out_dir="out", geotiffs=EXPORT_GEOTIFF)` call, `EXPORT_GEOTIFF = False` as the manual switch; show `anim`, then `anim.series` as the inside/outside chart the package already draws.
- [ ] Optical counterpart through the package too: `animate(..., sensor="sentinel2", index="rgb", cadence="10day", region_max_cloud_percent=100)` and a side-by-side from `anim.frames` / `opt.frames` (the frame PNGs are the same frame geometry).
- [ ] Drop the manual GeoTIFF cell (the package export supersedes it); keep the QGIS nodata note.
- [ ] Execute headless from `notebooks/` (`jupyter nbconvert --execute` on a copy), then strip outputs; `scripts/check_notebooks.py` green. Commit.

### Task 6 — PR

- [ ] `git push -u origin feat/sentinel1-sar`; PR titled "Sentinel-1 radar products (sar_vv, sar_vh) and notebook 09 on animate()"; body lists the design table and the Task 4 numbers; target per Global Constraints.
