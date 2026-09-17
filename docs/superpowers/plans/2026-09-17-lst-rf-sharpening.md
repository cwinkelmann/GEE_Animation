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

## Results

### Pass 1 — as first implemented (residual against a second coarse prediction)

`out/lst_rf_validate_v1_coarse_residual.csv`, 2026-09-17 13:16–13:47. Frame-wide
RMSE (K) of the recovered 100 m field against the real 100 m LST:

| month | scenes | nearest | linear (TsHARP) | rf |
|---|---|---|---|---|
| 2018-05 | 2 | **1.308** | 1.610 | 1.434 |
| 2018-08 | 1 | **1.219** | 1.592 | 1.991 |
| 2019-10 | 1 | **0.493** | 0.582 | 0.686 |
| 2020-03 | 1 | **0.700** | 0.711 | 0.875 |
| 2022-06 | 1 | **1.577** | 1.826 | 1.635 |
| 2023-09 | 2 | **0.876** | 1.059 | 0.958 |
| 2025-02 | 1 | **0.667** | 0.705 | 0.848 |
| 2026-07 | 1 | **1.295** | 1.477 | 1.432 |

**Gate: FAIL** — rf beats linear by ≥ 0.2 K in 0/8 months, and is worse than
*no sharpening* in 8/8. So is TsHARP. Doing nothing wins every month.

A defect found while this ran, fixed before pass 2: the rf residual was taken
against a second forest prediction at the coarse predictors, `f(mean x)`. A
forest is nonlinear, so `mean f(x_fine) ≠ f(mean x)` and the sharpened field did
not aggregate back to the observed cell means — the conservation the plan
promised. Pass 2 takes the residual against the fine prediction aggregated to
the coarse grid (`sharpen.rf_sharpen`, test
`test_rf_sharpen_trains_a_regression_forest_on_lst_and_adds_the_coarse_residual`).
The linear method is unaffected (exact for a linear fit).

### Pass 2 — mean-conserving residual

`out/lst_rf_validate_v2_conserving_residual.csv`, 2026-09-17 13:48–14:17.
Same months, same inputs; only the rf residual changed.

| month | scenes | nearest | linear (TsHARP) | rf |
|---|---|---|---|---|
| 2018-05 | 2 | **1.308** | 1.610 | 1.318 |
| 2018-08 | 1 | **1.219** | 1.592 | 1.727 |
| 2019-10 | 1 | **0.493** | 0.582 | 0.619 |
| 2020-03 | 1 | **0.700** | 0.711 | 0.763 |
| 2022-06 | 1 | 1.577 | 1.826 | **1.478** |
| 2023-09 | 2 | 0.876 | 1.059 | **0.872** |
| 2025-02 | 1 | **0.667** | 0.705 | 0.762 |
| 2026-07 | 1 | 1.295 | 1.477 | **1.238** |

**Gate: FAIL** — rf beats linear by ≥ 0.2 K in 3/8 months (need 6) and is
worse than nearest in 5/8 (need 0). The conserving residual was worth up to
0.26 K (2018-08: 1.99 → 1.73) and turned three summer months into wins over
doing nothing, but the pre-registered bar is not met. TsHARP loses to nearest
in every month in both passes.

### Why sharpening cannot win this test here — measured

Pearson r between the 100 m LST field and each predictor at 100 m and 300 m,
and the LST standard deviation at both scales, over the frame:

| month | sd(LST)@100 | sd(LST)@300 | ndbi | nirv | ndvi | mndwi |
|---|---|---|---|---|---|---|
| 2018-08 | 5.79 K | 5.87 K | 0.74 | −0.57 | −0.46 | 0.02 |
| 2019-10 | 1.60 K | 1.53 K | 0.65 | −0.56 | −0.70 | 0.46 |
| 2022-06 | 5.16 K | 4.92 K | 0.68 | −0.33 | −0.28 | −0.19 |

(correlations at 100 m; at 300 m they are within ±0.07 of these.)

Two facts decide the outcome:

1. **There is almost no sub-300 m thermal variance to recover.** sd(LST) is
   the same at 100 m and 300 m. The USGS C2 L2 ST field over Berlin is smooth
   below ~300 m — TIRS is 100 m native and the product is resampled — so the
   floor (nearest) is already within 0.5–1.6 K, and any detail a sharpener adds
   is scored against a field that does not contain it.
2. **The predictors carry the coarse structure, not the fine.** Their
   correlation with LST barely changes between 100 m and 300 m, so a model fit
   at 300 m has nothing extra to say at 100 m. NDBI (built-up) is the best
   single predictor by a wide margin; NIRv — TsHARP's predictor — is weak in
   summer, which is why the forest beat the linear method even while both lost
   to doing nothing.

The same reasoning applies one step down: the production ratio (100 m → 20 m)
has **no independent truth at all**, and the 20 m detail an `lst_rf` frame
shows is the predictors' texture with the coarse temperature painted on. It may
look better than 100 m blocks; this experiment cannot show that it *is* better,
and the test that could was failed at the only ratio where it can be run.

### Verdict

**Negative result, recorded.** `lst_rf` stays on this branch as an
experimental product (registry entry, tests, example config, validation
script) and is not merged into a showcase. One illustration frame
(`out/r12_zoom_lst_rf_2024-07_2024-07.png`, July 2024, zoomed R12 frame) shows
what the sharpened field looks like next to the 100 m blocks of
`r12_zoom_lst_pretty_10yr_grid`. If a genuinely finer thermal truth ever exists
for this site (an airborne TIR flight, or ECOSTRESS at 70 m), rerun
`scripts/lst_rf_validate.py` against it before believing any 20 m frame.

Steps 3–5 of the plan were executed ahead of the gate (the wiring is needed to
render the illustration and costs nothing if unused); the harmonic-smoothing
refusal and the `reduce_period_cfg` seam are the only touches outside the new
module.

## Spike: train on our own hardware instead of in Earth Engine (2026-09-17)

Question: can the per-month training and prediction leave Earth Engine, so EE
only serves composites (cheap exports) and the forest runs locally? Throwaway
probe: `docs/experiments/spike_local_rf_training.py`, one month (2024-07), the
R12 frame buffered by 3 km (10.7 km square).

| step | Earth Engine version | local version |
|---|---|---|
| inputs from EE | trains + predicts server-side: **78 s**, and 1 of 116 frames timed out in the full run | two GeoTIFF exports (LST 1.8 MB, five predictors 5.6 MB): **42 s**, no model compute |
| training (100 trees, 5,000 cells) | inside the 78 s | **0.2 s** (scikit-learn, 8 cores) |
| prediction on the 20 m grid (549×552) | inside the 78 s | **0.3 s** |
| residual conservation | exact by construction | exact: max |agg(sharp) − LST₁₀₀| = 0.0000 K |
| agreement, local vs EE | — | RMSE **0.70 K**, bias 0.00 K; two local seeds differ by 0.59 K, so the gap is forest randomness |
| feature importance (local) | not exposed | ndbi 0.65 · mndwi 0.26 · dem 0.04 · ndvi 0.03 · nirv 0.03 |

Figure: `docs/experiments/spike_local_vs_ee_2024-07.png`.

One trap found: `getDownloadURL` GeoTIFFs carry masked pixels as **0**, not
NaN, unless a nodata value is set. Untreated, the ~1 % cloud/QA holes pulled
their 100 m block means down and produced −12.8 K artefacts; treating 0 as
no-data (or exporting with an explicit `noData`) fixes it, and a real
implementation must reuse `render._geotiff_params` which already handles this.

**Recommendation: yes, move training and prediction local.** It removes the
heaviest EE compute (the forest), removes the timeout failure mode, cuts EE time
per month roughly in half, and makes models persistable trivially (joblib) and
inspectable (feature importance, held-out scores). EE keeps doing what it is
good at — cloud-masked composites over an archive. Design for the real thing:

- `sharpen.local`: `fetch_inputs(month) -> (lst_20m, predictors_20m)` via the
  existing cached GeoTIFF path, then `train(pooled months per calendar month)`,
  `predict(month)`, residual, written as a GeoTIFF the renderer can ingest.
- The renderer needs one new input path: a frame whose pixels come from a local
  GeoTIFF rather than an EE thumbnail (the upsampling branch's
  `scripts/upsample_render.py` already does this — reuse).
- Per-calendar-month models across years, leave-one-year-out score per model,
  models under `docs/models/` with joblib; ~1 day with tests.

**Implemented** the same day as `sharpen: local` (`gee_animation/sharpen_local.py`,
`gee_animation/local_image.py`; branches in render/focus/geotiff export): approved
with "the local one is awesome and good enough". The EE-trained delta render was
stopped in favour of `config/r12_focus_lst_rf_delta_local_10yr.yaml`.

## Windthrow test (2026-09-17) — the thermal profile does show where the trees fell

Question (Christian): "my assumption would be that we can see where most trees
fell by the thermal profile". Data: the tegel-unet stem predictions
(`predictions_cw_2026/R12_stems_tegel-unet_2026-08.gpkg`, 8,323 stems from the
2025 flight) rasterised to metres of stem per 20 m cell; the local lst_rf delta
run (`r12_focus_lst_rf_delta_local_10yr`, 107 observed/gap-filled months); streets
excluded via `steet_mask.gpkg`. Scripts and outputs: `docs/experiments/windthrow/`.

**Time series** (windthrow cells with ≥ 20 m stem, n = 620, minus stem-free
canopy, n = 9,110): within ±0.3 K for every month from 2017-01 to 2025-06, then
+1.36 K (2025-07), +1.75 (2025-08), +1.13 (2025-09), +1.50 (2026-04), +1.99
(2026-05), +0.65 to +1.11 (2026-06 to 08). Every post-event month is a real
observation, not a donor; the step is in the month the stems appear in the
imagery. Pre-event summers with real observations (2018, 2019, 2022, 2023, 2024)
sit at −0.3 to +0.3 K, so the signal is not the model "expecting" gaps to be
warm — those cells were canopy then and looked like canopy.

**Maps, post-event summers only (2025-07 → 2026-08):**

| scale | Spearman ρ | top-10 % stem density vs stem-free |
|---|---|---|
| 20 m sharpened | +0.10 | +0.77 K vs −0.05 K → **+0.83 K** |
| 100 m = observed Landsat | **+0.32** | +0.71 K vs −0.29 K → **+1.00 K** |

The 100 m row is measured temperature, independent of the forest. The dense
windthrow band across the north-centre of the footprint and the diagonal line
of throws read as warm patches; the hottest cells remain the western buildings
and the eastern edge, which are not windthrow — so the profile finds the dense
clusters, not every stem.

Caveats: gap-filled winter months repeat donor frames (identical values across
years in the CSV are the same imagery); 2021-05 (+1.41 K) is a mostly-masked
frame with 65 cells and should be ignored; the leave-one-year-out RMSE of the
monthly forests is 1.7–5.2 K (`lst_rf_local_models_loyo.csv`), i.e. the
absolute 20 m field is not accurate — only its within-cell contrast is used here.

## Noise fix (2026-09-17 evening): one forest per frame, not per calendar month

Christian, on the finished ten-year delta animation: "looks like some overfitted
noise, in the end not even the street is visible". Measured on 2024-07, the same
month two ways:

| forest | sd of field | high-frequency noise (field − 3×3 median) |
|---|---|---|
| pooled July 2017–2026 (the run) | 2.21 K | **1.79 K** |
| trained on that month alone (the trial) | 1.30 K | **0.60 K** |

The two fields correlate at only 0.41; every summer of the pooled run sits at
1.6–1.8 K noise; the predictors' own speckle is 7–35 % of their variance, so
they are not the source. Root cause: the index→temperature relation does not
transfer between dates (the LOYO 2–5 K said so already), and a forest fitted
across ten Julys answers each 20 m pixel with a compromise that flips between
leaves. Fix: `sharpen_local.train_frame_models` — one forest per frame, scored
out-of-bag (R² 0.60–0.87 on 2025–2026 frames). On May 2025–Aug 2026 the
high-frequency noise falls from 1.84 K to 0.65 K (ratio 0.38) and the warm
patches become coherent blobs on the windthrow clusters.

### Focused analysis, May 2025 – August 2026 (per-frame forests)

Windthrow cells minus stem-free canopy: −0.12 K (2025-05, pre-event), then
+1.60 (07), +2.29 (08), +0.91 (09), −0.14 (11), +0.11 (2026-02), +0.66 (03),
+1.58 (04), +2.00 (05), +2.26 (06), +1.62 (07), +1.91 (08). Note 2025-06 has no
Landsat pass and is a donor copy of 2026-06 (identical values). Maps, post-event
summers: Spearman ρ **+0.25 at 20 m** (was +0.10 with the pooled forests) and
+0.30 at 100 m; top-10 % stem-density cells +1.22 K (20 m) / +0.99 K (100 m)
above stem-free cells. Config: `config/r12_focus_lst_rf_delta_local_2025_2026.yaml`.

### Heat islands vs detected fallen trees — object-level test (2026-09-17)

`docs/experiments/windthrow/heat_islands.py`. Islands = connected patches ≥ 1 K
above the footprint mean in the post-event summer mean (2025-07..2026-08, ≥ 3
cells, footprint edge and streets excluded); "new" = not warm in the 2022–24
summers. Windthrow clusters = ≥ 2 connected cells with ≥ 20 m predicted stem.

| set | n | stem per cell | windthrow-dense share |
|---|---|---|---|
| footprint base rate (cells) | 10,485 | 3.3 m | 5 % |
| all heat islands | 51 | 18.1 m | 39 % |
| **new** islands (appeared after the storm) | 29 | **25.7 m** | **59 %** |
| old islands (warm before too: paths, buildings) | 22 | 8.0 m | 14 % |

Permutation null (500 random shifts of the stem map): new-island stem density
0.7 ± 1.2 m vs observed 25.7 m, p < 0.002. From the other side: 78 windthrow
clusters, mean post-event departure +1.12 K (pre-event 0.00 K), 56 % warmed
by ≥ 1 K, 45 % sit inside a heat island. Cell-level Spearman between stem
density and post−pre warming +0.24; windthrow-dense cells warmed +1.58 K,
stem-free cells −0.11 K.

Reading: the heat islands that appeared after the storm are the windthrow
areas eight times more often than chance; about half of the predicted
windthrow clusters produce a heat island, the other half (smaller or sparser
throws, or gaps already closing by 2026) do not.

### Could pre-storm warmth predict where trees fall? — no (2026-09-17)

`docs/experiments/windthrow/pre_storm.py`, 26 observed pre-storm summer months
(2017–2025-05) vs 8 post-storm months, per-frame forests, edge (< 40 m) and
streets excluded. Future windthrow-dense cells (n = 531) vs stem-free (n = 7,678):

| | windthrow cells | stem-free | difference | AUC as a predictor |
|---|---|---|---|---|
| pre-storm Δ | −0.02 K | −0.07 K | **+0.04 K** (p = 7e-8, but tiny) | **0.56** |
| post-storm Δ | +1.67 K | −0.20 K | +1.87 K | 0.83 |
| post − pre warming | | | | 0.84 |
| distance to stand edge alone | | | | 0.57 |

Windthrow-dense rate by pre-storm warmth quintile: 4.3, 4.1, 4.6, 5.2, 7.2 % —
a 1.7× enrichment in the warmest fifth, about what edge distance alone gives
(cells 40–200 m from the edge are +0.5 K warmer and have the highest windthrow
rate). The thermal signal is a consequence of the throws, not a precursor;
whatever weak pre-signal exists is the edge/thin-stand exposure that the
temperature and the storm both respond to.

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
