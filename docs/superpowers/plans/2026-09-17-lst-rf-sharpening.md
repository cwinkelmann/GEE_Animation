# Random-forest thermal sharpening (`lst_rf`) — experiment plan

Status: **draft for review** (2026-09-17). Nothing implemented yet.
Branch: `experiment/lst-rf-sharpening`, checked out as a worktree at
`../GEE_animation-rf-sharpening` (from `audience_polish` HEAD 32366cd; the
uncommitted pixel-grid work and the R12 configs are NOT on it yet — see Q1).

## What is being asked

Sharpen Landsat 8/9 surface temperature (100 m thermal) to the Sentinel-2 grid
using a random-forest regression on several high-resolution predictors, instead
of the single-predictor linear TsHARP the repo already ships as `lst_sharp`
(Landsat's own NIRv at 30 m). The pasted method text calls this "Method 2" and
promises "a super-resolved 10 m LST dataset".

Two corrections to that text before designing anything:

- **10 m is optimistic.** NDBI and MNDWI need SWIR1 (Sentinel-2 B11), which is a
  **20 m** band. A product that uses them is honest at 20 m; rendering it at 10 m
  is a 2× interpolation, which `_cap_dimensions` will report. NDVI alone would be
  10 m — but a single predictor is exactly what TsHARP already is.
- **The DEM will contribute almost nothing here.** The R12 frame is 30–70 m
  above sea level; the lapse-rate signal over 40 m of relief is ~0.25 K, below
  the sharpening error. It is kept as a predictor because it is free and the
  method is meant to generalise, not because it helps in Tegel.

Prior art in this repo that the design must respect:

- `products._lst_sharp` — TsHARP: fit LST = a·NIRv + b at 100 m, apply at 30 m,
  **add the coarse residual back** so the sharpened field aggregates to the
  observed LST. That residual step is what keeps a sharpening honest; the RF
  version keeps it.
- `upsampling-data-foundation` branch (26 commits, spec
  `docs/superpowers/specs/2026-08-24-modis-upsampling-design.md`): a bake-off
  where linear / RF / GBT / CNN / diffusion were tested for MODIS→Landsat
  gap-filling with pre-registered acceptance, and the learned models
  **did not beat the baseline** (commit 6bccb37, "learned model does not win").
  Different task, same discipline: the gate below is fixed before any training.

## Classification and shape of the work

Architectural (new product, cross-sensor join, server-side ML), but it starts
with a **spike** whose output is a number, not code we keep: does RF sharpening
beat TsHARP on a held-out test? Only if it passes does the product get wired
into the animation pipeline.

### The pre-registered test (scale-invariance / "degrade and recover")

The standard validation for a disaggregation method when no finer truth exists:

1. Take real Landsat LST at 100 m for a set of months.
2. Aggregate it to **300 m** (mean). This is the "coarse" input.
3. Sharpen 300 m → 100 m with each candidate, using predictors aggregated to
   the matching scales (predictors at 300 m for training, at 100 m for
   prediction — the same 3× ratio the production run will apply at 100 m → ~30 m).
4. Compare the recovered 100 m field with the real 100 m LST.

Candidates, all with the residual correction: **(a)** nearest-neighbour, i.e.
no sharpening (floor); **(b)** TsHARP linear on NIRv (the shipped method);
**(c)** random forest on {NDVI, NDBI, MNDWI, NIRv, DEM}.

Months: 8, spread over seasons and years, taken from the 58 observed months of
`r12_lst_pretty_10yr` (e.g. 2018-05, 2018-08, 2019-10, 2020-03, 2022-06,
2023-09, 2025-02, 2026-07). Region: the 16.3 × 6.9 km R12 frame; metrics
reported both frame-wide and inside the footprint.

Metrics: RMSE and MAE (K) against real 100 m LST; plus the 99th percentile of
|sharp − coarse| inside a coarse cell, as an artefact detector.

**Gate, fixed now:** (c) ships only if its frame-wide RMSE is at least **0.2 K
below (b)** in ≥ 6 of 8 months and never worse than (a) in any month. Otherwise
the result is recorded as a negative in this file (as commit 439834f did) and
the branch stays an experiment.

## Approaches considered

**A. In-Earth-Engine product (recommended).** A new index `lst_rf` whose
per-period reducer trains `ee.Classifier.smileRandomForest(...)
.setOutputMode('REGRESSION')` on that month's Landsat LST composite at 100 m
(target) against Sentinel-2 index composites + DEM aggregated to 100 m, then
classifies the 20 m predictor stack and adds the coarse residual. One function
serves both the spike (with coarse=300, fine=100) and production (coarse=100,
fine=20). Frames render through the existing pipeline, thumbnails cache as
usual, GeoTIFF export works unchanged. Cost: EE compute per frame (a few
thousand training samples; trivial), and one architectural seam (below).

**B. Local scikit-learn on exported GeoTIFFs.** Reuse the upsampling branch's
canonical grid/store to pull monthly LST + S2 index GeoTIFFs, train locally,
write sharpened GeoTIFFs, render them with `scripts/upsample_render.py`. More
control, faster iteration on model choice — but a second pipeline, and the
result cannot be a normal `gee-animation` product without re-import.

**C. QGIS TsHARP plugin.** Manual, outside the repo, and it is the linear method
the repo already has. Not pursued.

A is recommended: the spike is ~150 lines of EE code, and if it passes, the
same code is the product.

## Design (approach A)

### Sharpening function — `gee_animation/sharpen.py`

```
rf_sharpen(lst_coarse, predictors_fine, *, coarse_m, fine_m, region,
           n_trees=100, n_samples=5000, seed=1, ee_module=ee) -> ee.Image
```

- `predictors_c = predictors_fine.reduceResolution(mean).reproject(coarse grid)`
- `train = predictors_c.addBands(lst_coarse).sample(region, scale=coarse_m,
  numPixels=n_samples, seed=seed)`
- `model = smileRandomForest(n_trees, minLeafPopulation=5, bagFraction=0.7)
  .setOutputMode('REGRESSION').train(train, 'lst', predictor_names)`
- `pred_f = predictors_fine.classify(model)`; `pred_c = predictors_c.classify(model)`
- `residual_c = lst_coarse − pred_c`; **`sharp = pred_f + residual_c`**
  (nearest, as `_lst_sharp` does; bilinear residual is a later knob).
- returns `sharp.rename(INDEX_BAND).toFloat()` with `system:time_start` kept.

Pure-numpy twin `imaging.rf_sharpen_arrays` is NOT planned: the RF lives in
EE, and the unit tests fake the `ee_module` seam like `tests/test_smw_lst.py`.

### Predictors — `sharpen.s2_predictors(start, end, geom, ee_module)`

Sentinel-2 SR monthly median over the same period, SCL-masked (reuse
`products.SENSORS['sentinel2'].mask_clouds`), bands:

| name | formula | native |
|---|---|---|
| ndvi | (B8−B4)/(B8+B4) | 10 m |
| nirv | ndvi·B8 | 10 m |
| ndbi | (B11−B8)/(B11+B8) | 20 m |
| mndwi | (B3−B11)/(B3+B11) | 20 m |
| dem | COPERNICUS/DEM/GLO30 mosaic, metres | 30 m |

Stack reprojected to the Sentinel-2 20 m grid (`fine_m = 20`).

### Pipeline seam — one small change to compositing

`Index.reduce_period(collection, ee_module)` receives the period's Landsat
collection but not its dates, and the S2 predictors need the same window. Add an
optional `Index.reduce_window(collection, cfg, p_start, p_end, ee_module)` that
`compositing._period_image` prefers when present. `pooled_composite` (gap_fill)
builds period images through the same helper, so pooling keeps working; the
harmonic `smooth` path is excluded for v1 (`validate()` refuses the pair, with
a message) because fitting a seasonal curve to a per-month-trained field is a
second experiment.

### Product registration — `products.py`

`"lst_rf": Index("lst_rf", frozenset({"landsat"}), (-10.0, 40.0, _LST_PALETTE),
compute=_lst_celsius, reduce_window=sharpen.lst_rf_window, units="°C",
bands="Thermal(100m) + S2 NDVI/NIRv/NDBI/MNDWI(20m) + DEM(30m)",
formula="RF(LST100 ~ predictors) applied at 20m + coarse residual",
display_name="Land surface temperature (RF-sharpened)", low_label="cooler",
high_label="warmer")`. Added to `THERMAL_INDICES`; `native_scale_m` returns
**20** for it (a new branch next to the `lst_sharp` → 30 case).
`cfg.sensor` stays `landsat`: all existing Landsat filters (missions, SLC-off,
cloud gating, region cloud fraction) apply unchanged; Sentinel-2 enters only
inside the reducer.

### Spike script — `scripts/lst_rf_validate.py`

Runs the pre-registered test above and writes `out/lst_rf_validate.csv`
(month, method, rmse_frame, mae_frame, rmse_region, p99_intracell) plus a
one-line verdict against the gate. Uses `rf_sharpen` with `coarse_m=300,
fine_m=100`. Lives in `scripts/`, integration-only (needs EE); no unit test.

## Steps

1. **Spike first.** `sharpen.py` (function + predictors) with fake-`ee` unit
   tests for the band names, scales and the residual identity
   (`sharp` aggregated to coarse == `lst_coarse`), then
   `scripts/lst_rf_validate.py`; run it; record the table in this file.
2. **Gate.** Passed → continue. Failed → write the negative result here, keep
   the branch, stop.
3. Product wiring: `reduce_window` seam in `compositing.py` (test: preferred
   over `reduce_period`, receives the period dates), registry entry,
   `THERMAL_INDICES`, `native_scale_m`, `test_registry_contents`, sensor
   restriction test, `validate()` refusal of `smooth: harmonic`.
4. `config/lst_rf.example.yaml`; README index table row; `docs/rendering-products.md`.
5. `config/r12_zoom_lst_rf_10yr.yaml` (the zoomed R12 frame, `pixel_grid: true`
   so the 20 m cells are visible) — the render that answers "does it look
   better than the 100 m blocks".

## Open questions for Christian

- **Q1 — branch base.** The worktree branches from committed HEAD, so it lacks
  the pixel-grid feature and the R12 configs (uncommitted on `audience_polish`).
  Commit those on `audience_polish` first and rebase the experiment on them?
  (Recommended: yes — step 5 needs both.)
- **Q2 — predictor set.** Keep DEM despite the flat site (method fidelity), or
  drop it and add Sentinel-2 `ndre` instead?
- **Q3 — the gate.** 0.2 K better than TsHARP in 6 of 8 months: too strict, too
  lax, or fine?
- **Q4 — training extent.** Train on the 16 km frame (11k coarse cells, more
  urban/water/forest variety) even for the zoomed render? The plan assumes yes:
  the model is trained on `aoi.frame`, so the zoomed config should keep a
  wide training frame — which argues for a `train_frame` knob, or for training
  on a fixed buffer around the frame. Decide before step 3.

## Not doing

- SRCNN / thermal GAN ("Method 3"): needs a paired training set that does not
  exist for this site, and the validation would be against textures the model
  invented. Out of scope until the RF result is known.
- Per-scene (rather than per-month) training.
- Any change to `lst_sharp`.
