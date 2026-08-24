# Learned upsampling of MODIS to fill LST gaps — design

Date: 2026-08-24 · Status: **awaiting review** · Author: pair-designed with Christian

## Problem

The Landsat LST series over Grumsin/WNE is incomplete. Of 103 months in
2018–2026, only **69** carry a real observation; the rest are lost to cloud and
revisit gaps. MODIS covers **103/103** but at 1 km, which halves the measured
forest/field contrast (−2.3 K vs −4.7 K) because of mixed pixels.

Goal: learn the mapping from coarse MODIS + time-invariant priors to
fine-resolution LST, using months where both exist, then apply it to months where
only MODIS exists — producing a complete series at Landsat-like resolution.

Non-goal: filling the small static speckle inside otherwise-good frames. That is a
separate, unrelated defect (a fixed-position QA mask, ~0.03 % of each frame,
present in all 69 frames) tracked in `docs/superpowers/plans/modis-gapfill-lst.md`.
MODIS cannot meaningfully fill 100 m holes.

## Decisions taken

| Decision | Choice | Rationale |
|---|---|---|
| Inference inputs | MODIS + **static learned priors** | Always available, so it works in total-blackout months. Detail comes from a persistent pattern, not invention. |
| Acceptance | **Validated + visibly labelled** | Hold-out RMSE reported before shipping; synthesized frames marked on-frame and in `metadata.db`. |
| V1 scope | **LST first**, generic interface | Gaps hurt most here; 95 candidate pairs; gentlest ratio (1 km → 100 m = 10×). |
| Model | **Bake-off of five**, champion pre-registered | See below. |
| Target | **Residual**, not absolute | Failure degrades to "blurry but unbiased", never to nonsense. |

## Model line-up

All five solve an identical task on identical splits and features.

| # | Model | Dependency | Role |
|---|---|---|---|
| 1 | Ridge / TsHARP-style linear | numpy | **Floor.** Nothing ships that cannot beat this. |
| 2 | Random forest | scikit-learn | Robust, no tuning, native categorical landcover. |
| 3 | Gradient-boosted trees | sklearn `HistGradientBoostingRegressor` | Usually strongest tabular; native missing-value handling. |
| 4 | Residual CNN | PyTorch (carrot) | Only deterministic model that sees spatial structure. |
| 5 | Conditional diffusion (from scratch) | PyTorch (carrot) | Sharp texture **and** per-pixel uncertainty from the sample ensemble. |

**Not** a Stable Diffusion LoRA. SD's prior is natural RGB photographs in a lossy
VAE latent; a single-channel °C field round-tripped through that VAE loses accuracy
before generation begins, and the prior contributes nothing about thermal physics.
Model 5 is a small from-scratch conditional diffusion model over the residual field
— tiny by SD standards because the domain is one channel at low resolution.

A learned *cosmetic* frame upscaler (Real-ESRGAN-class, applied post-colourization
to the presentation cut only) remains possible as a separate, clearly-fenced piece.
It is out of scope here and must never feed a GeoTIFF, `metadata.db`, or a number.

### Champion selection — pre-registered before any training

Winner = **lowest RMSE against real Landsat on held-out months, inside the AOI**,
subject to two hard gates (below). Fixed now, in writing, so five models × several
metrics cannot become a menu to pick a favourite from afterwards.

## Architecture

```
GeoTIFFs already on disk (95 Landsat LST + 103 MODIS LST months, 0 quota)
  → extract.py       warp onto ONE canonical fine grid; write the store
  → data/upsampling/lst/{static.npz, YYYY-MM.npz, manifest.json}
       ├── tabular_view()  → feature rows      → models 1,2,3
       └── patch_view()    → image patches     → models 4,5
  → train.py --model X     (on carrot)         → models/lst/X/{weights, metrics.json}
  → predict.py             → synthesized fine arrays for gap months
  → compositing.Frame(array=...)  → render._fetch_one returns it, EE never called
```

**One extraction, two views.** Models 1–3 want feature rows; 4–5 want patches. Both
are adapters over one canonical store, so a fairness bug between model families is
structurally impossible rather than merely unlikely.

### Features

- **Coarse:** MODIS LST for the month on the fine grid, plus the surrounding 3×3
  coarse-cell values (tabular models only; the CNN/diffusion get this from their
  receptive field).
- **Static priors:** Dynamic World modal class + mean class probabilities; terrain
  (elevation, slope, aspect — one small one-off EE export, static forever); and the
  per-pixel **harmonic climatology coefficients** from `smoothing.fit()`.
- **Time:** day-of-year sin/cos, year (trend).
- **Sub-cell geometry:** position within the coarse cell, so edges are learnable.

### The store

`data/upsampling/lst/` — `static.npz` (priors on the fine grid), one `YYYY-MM.npz`
per month (coarse array, fine array, both validity masks), and `manifest.json`
carrying provenance per month: real acquisition dates, scene count, and whether the
frame was **borrowed by cross-year pooling**.

Grid alignment happens once, here, onto the product's render CRS and native scale.
Requires `rasterio` (new, `[ml]` extra).

## Correctness risks and their controls

**1. Climatology leakage (highest risk).** The harmonic prior is derived from
Landsat — the very data being predicted. Fitted over all months and then used to
predict a held-out month, that month's truth has leaked into its own input and the
reported RMSE is optimistically wrong.
→ **Control:** refit the climatology *inside each fold, on training months only*.
Any long-term mean feature obeys the same rule. A test asserts a fold's prior is
independent of its held-out months.

**2. Training on borrowed frames.** `wne_cinema_lst_2018_2026` has 95 months, but
that run used cross-year pooling — some frames are borrowed from other years.
Training on them teaches the model that a borrowed 2019 May is a real 2022 May.
→ **Control:** `manifest.json` flags provenance; training excludes borrowed months
by default. Expect the real training set nearer **69** than 95.

**3. Patch-level splits.** Patches from one month leak almost perfectly into each
other and would produce a beautiful, meaningless RMSE.
→ **Control:** splits are **by month**, never by patch. Headline metric is
**leave-one-year-out** — predicting a year never seen, which is the actual use case.
K-fold over months is reported as a secondary, more forgiving number.

**4. Contrast flattening.** A model can post a respectable RMSE while regressing the
forest/field contrast toward the MODIS value, which would destroy precisely the
signal these videos exist to show.
→ **Control:** forest-minus-field ΔT is a **first-class acceptance metric**, not a
diagnostic. **Hard gate:** synthesized months must preserve contrast within a stated
tolerance of the observed-month contrast.

**5. The model may be ignoring MODIS entirely.** If it just redraws climatology, the
"fill" carries no information about the month it claims to depict — while still
scoring well, because climatology is genuinely predictive.
→ **Control — hard gate:** re-run inference with the MODIS input **shuffled between
months**. Accuracy must degrade materially. If it does not, the model is a lookup
table and no frame ships.

**6. Diffusion non-determinism.** A single sample is sharp but unverifiable;
averaging samples blurs toward the regression models.
→ **Control:** fixed seeds; ensemble size fixed and recorded; the **ensemble mean**
is scored on RMSE like everything else, and the spread ships as an uncertainty
layer. Diffusion wins only if the mean is competitive *and* the spread is
informative — both testable.

## Pipeline integration

`Frame = namedtuple("Frame", "label image n_scenes source", defaults=(None, None))`
gains an `array` field (defaulted, so backward-compatible). `render._fetch_one`
returns that array directly when present and never calls `fetch` — Earth Engine is
untouched for synthesized frames, and the injected-`fetch` test seam is unchanged.

- Provenance: `source="upsampled:<model>"` flows into `metadata.db` and `_caveats()`,
  which already emits `"modelled: harmonic fit, N harmonics"`.
- On-frame marker for every synthesized frame, per the acceptance decision.
- Exported GeoTIFFs of synthesized frames are tagged as such in filename and
  metadata — a synthesized tif must never be mistakable for an observation.
- Local cache keyed by model hash + month, so re-cuts stay free.

## Training workflow (carrot)

Extract locally → rsync `data/upsampling/` to `/raid/cwinkelmann/w` → train all five
on carrot → pull back weights + `metrics.json` → inference locally (trees in
seconds; 34 CNN/diffusion frames fine on CPU). Core package deps unchanged; ML deps
live in an `[ml]` extra. Fixed seeds throughout; the split definition is written to
disk alongside the metrics.

## Acceptance criteria

1. Leave-one-year-out RMSE, MAE and bias reported for all five models in one table.
2. Champion beats the ridge floor by a margin worth its dependency.
3. **Gate:** contrast preserved within tolerance.
4. **Gate:** shuffled-MODIS ablation degrades accuracy materially.
5. Every synthesized frame visibly labelled, with provenance in `metadata.db`.
6. 103/103 months present, or an explicit stated reason per missing month.

## Thresholds (adopted; revisable only before training starts)

Fixed here rather than left open, so they cannot be tuned to fit results:

- **Contrast gate:** synthesized-month forest/field ΔT must be within **1.0 K** of
  the observed-month contrast. (Observed ≈ −4.7 K; raw MODIS ≈ −2.3 K, so this
  tolerance comfortably separates "preserved" from "flattened toward MODIS".)
- **Ablation gate:** shuffling MODIS between months must raise RMSE by **≥ 50 %**.
  Below that, the model is substantially a climatology lookup and nothing ships.
- **Floor margin:** the champion must beat ridge by **≥ 10 % RMSE** to justify an
  ML dependency; models 4–5 must additionally beat the best tree model to justify
  PyTorch and carrot.

## Known blockers

- The terrain prior needs one small EE export — blocked until quota resets. Every
  other input is already on disk, so extraction and models 1–3 can proceed without
  it (terrain enters as an additive feature).
- Training-set size likely falls to ~69 months once borrowed frames are excluded.
  If that proves too small for models 4–5, the next lever is widening the training
  *area* beyond the AOI — more spatial samples per month — at the cost of new EE
  exports. Decide only after seeing models 1–3 results.
