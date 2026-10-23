# How thermal sharpening can work at all — and how much it does here

What `lst_sharp` and `lst_rf` do, why a rule learned at 100 m says anything
about 20 m, what this repo's own validation found, and how the procedure
compares with the paper it descends from, Hutengs & Vohland (2016). Written
against `gee_animation/sharpen.py`, `sharpen_local.py`, `products._lst_sharp`,
the pre-registered gate in
`docs/superpowers/plans/2026-09-17-lst-rf-sharpening.md`, and the 2026-10-08
predictor-smoothing study. Notebook 04 walks the same steps on one real frame.

## 1. The objection is right: no new temperature information is created

A Landsat thermal pixel is 100 m. Nothing in the method measures anything finer.
So the sharpened 20 m field cannot be "the temperature at 20 m". What it is:

> the **observed 100 m temperature, redistributed inside each 100 m cell**
> according to a rule that says where in the cell the heat probably is.

Two things make that more than decoration: the rule is learned from the thermal
data itself (not invented), and the redistribution is forced to add up to what
was measured (the residual step). Everything else is detail.

## 2. Why a rule learned at 100 m says anything about 20 m: mixed pixels

Think of a 100 m cell as a mixture of a few surface types: canopy, clearing,
road, water. At noon these have genuinely different surface temperatures —
canopy is cooled by transpiration, dry bare ground and asphalt are not, water
is thermally inert. Differences of 5–15 K between them are routine in airborne
thermal imagery. A 100 m cell that is 60 % canopy and 40 % clearing measures
roughly the area-weighted mean of the two (strictly radiance mixes linearly in
T⁴, but over a 10 K range that is close enough to linear in T).

Now look across *many* 100 m cells with *different* mixtures. Their measured
temperatures vary with their composition, and composition is what NDVI, NIRv,
NDBI and MNDWI see. A regression of LST on those indices across the cells is
therefore, in effect, recovering the **end-member temperatures**: a cell that is
30 % road is hotter by X, so pure road must be hotter by about X / 0.3.

That is the whole trick. The coarse cells are a natural experiment in mixing,
the regression unmixes it, and a 20 m pixel is simply a purer mixture than a
100 m one, so the same rule evaluated on its indices gives the end-member
temperature it is mostly made of. The literature calls the assumption behind
this *scale invariance* of the index–temperature relation; it is the same
assumption TsHARP makes with one predictor and a straight line.

### A toy example

| cell | canopy fraction (NDVI as proxy) | measured LST |
|---|---|---|
| A | 0.9 | 21 °C |
| B | 0.6 | 24 °C |
| C | 0.2 | 28 °C |

The regression across cells gives roughly −10 K per unit canopy fraction, i.e.
canopy ≈ 20 °C, clearing ≈ 30 °C. Inside cell B, assign 20 °C to its canopy
pixels and 30 °C to its clearing pixels. Their mean is 0.6 × 20 + 0.4 × 30 =
24 °C, which is what Landsat measured, so the residual is zero. If cell B had
measured 25 °C (a sunlit slope, a drier day), the residual +1 K is added to
every pixel in it: 21 °C and 31 °C. The pattern comes from the indices, the
level comes from Landsat. Nothing else happens in the method.

## 3. What the repo actually does with that idea

- **TsHARP (`lst_sharp`)**: one predictor (NIRv), one straight line fitted at
  100 m over the whole scene, applied at fine scale, coarse residual added back.
  Server-side, per Landsat scene. `products.py`, `_lst_sharp`.
- **Random forest (`lst_rf`, `sharpen: local`)**: five predictors (NDVI, NIRv,
  NDBI, MNDWI, DEM elevation), a forest fitted on *that frame's own* 100 m
  cells, applied on the 20 m Sentinel-2 grid, same residual step
  (`sharpen_local.py`: `fit_forest`, `sharpen_frame`). The forest exists
  because the relation is not one line: water and built-up sit off it, and a
  bright low-NDVI surface is a hot car park or a cold lake depending on MNDWI.
  One forest per frame, because the index→temperature relation does not
  transfer between dates (sun, wind, soil moisture, phenology); pooling ten
  Julys tripled the 20 m noise.
- **The residual** is what keeps it honest: every 100 m block of the output
  averages back to the observed value. By default the residual is added
  block-wise (so block edges can show); the v3 delivery recipe smooths it.

## 4. How much it works: the repo's own evidence

There is no 20 m thermal truth, so the test degrades real 100 m LST to 300 m,
sharpens back to 100 m, and scores against the real 100 m field (RMSE, K).

**Pre-registered gate (2026-09-17), eight months, Tegel:** the forest beat the
TsHARP line in 3 of 8 months and was *worse than no sharpening at all* (nearest
resampling) in 5 of 8. TsHARP lost to nearest in every month. **Gate: FAIL.**

The reason was measured, and it is the real answer to "how can this work": the
Landsat thermal product has **almost the same variance at 100 m as at 300 m**
(e.g. 5.79 K vs 5.87 K in August 2018). There is very little structure below
about 300 m in the product to recover, so a sharpener at that scale is adding
texture that the reference field does not contain, and is penalised for it.
(The delivered 30 m LST band is already a resampling of 100 m thermal pixels;
the "100 m blockiness" in notebook 04 is that.)

**Predictor-smoothing study (2026-10-08), 64 frames, 300 → 100 m, mean RMSE:**

| method | RMSE |
|---|---|
| no sharpening (nearest) | 0.82 K |
| production forest | 1.17 K |
| forest, 20 m predictors Gaussian-smoothed (σ ≈ 1 px) | 0.69 K |
| spline / OLS with the same smoothing | 0.63 K |
| TabPFN with the same smoothing | 0.60 K (300× slower) |

So the production forest was *worse* than doing nothing, and the fault was not
the learner but **predictor pixel noise**: NDVI at 20 m has ~0.06 of
high-frequency noise, multiplied by a steep temperature slope, and that noise
was being painted into the thermal field as speckle. Smoothing the predictors
by about a third of a coarse cell turns the method into a modest, real gain
(0.82 → 0.6–0.7 K at 300 → 100 m). That is the state of the art here: the v3
recipe (forest, seasonal predictor composite, smoothed residual).

Whether the gain survives at the production ratio (100 m → 20 m, a factor 5
instead of 3) is **unverifiable**: there is no finer truth. It is an assumption
that the mixing logic of §2 keeps holding two more halvings down.

## 5. What to trust in a sharpened frame

- **The level of every 100 m block**: that is Landsat's measurement, untouched.
- **Which part of a block is warmer**: a plausible guess from composition, usually
  right in sign (a clearing is warmer than the canopy around it at noon), not
  reliable in magnitude, and wrong wherever temperature is driven by something the
  indices do not see — shadow, wind exposure, wet soil after rain, a roof's material.
- **Within-frame contrasts between areas larger than a few blocks**: usable, which is
  why the windthrow analyses compare windthrow cells with canopy cells in the same
  frame and render `relative: region_mean`.
- **Absolute 20 m values, or 20 m differences between dates**: no. The texture is
  index texture wearing temperature units, and it changes with the predictor
  composite, not only with the heat.

## 6. Conformance with Hutengs & Vohland (2016)

Hutengs, C. & Vohland, M. (2016): *Downscaling land surface temperatures at
regional scales with random forest regression.* Remote Sensing of Environment
178, 127–141, [doi:10.1016/j.rse.2016.03.006](https://doi.org/10.1016/j.rse.2016.03.006).
This is the paper `lst_rf` descends from. The full text is paywalled; the
paragraph on what the paper does is taken from its abstract, the procedural
details marked "(body)" are from a reading of the paper that was not re-verified
for this note.

**What the paper does.** A random forest regression downscales MODIS LST from
about 1 km to about 250 m. Predictors: topographic variables from the SRTM DEM,
MODIS land cover (MCD12Q1), and red and NIR reflectance from the daily MOD09GQ
product at 250 m. Test site: the Jordan River region. Validation in two tiers:
an *ideal case* where Landsat-7 ETM+ LST is aggregated to the coarse scale and
the downscaled result is scored against Landsat-7 LST at the fine scale (RMSE
1.02–1.43 K over six dates; up to 19 % better than TsHARP), and a *real case*
on the MODIS LST product (RMSE 1.41–1.92 K), where TsHARP and plain resampling
were "only slightly worse" because low thermal contrast hindered training. An
extended forest with all Landsat spectral bands did best (0.98–1.33 K). (Body:)
the model is fitted on the coarse pixels of one acquisition and applied to the
fine predictors of the same date, and coarse-scale residuals are added back as
in TsHARP.

**Step by step:**

| step | Hutengs & Vohland 2016 | this repo | verdict |
|---|---|---|---|
| fit at coarse scale, apply at fine scale | yes | yes (`fit_forest` on 100 m block means, `predict` on the 20 m grid) | same |
| residual added back so coarse cells are conserved | yes, TsHARP convention (body) | yes, block-wise; smoothed in the v3 recipe | same in principle |
| one model per acquisition | yes (body) | one forest per frame | same |
| learner | random forest | random forest (100 trees, min leaf 5, 70 % bag, ≤ 5000 cells) | same family; settings not compared |
| predictors | red + NIR reflectance, land cover class, DEM-derived terrain; best variant adds all spectral bands | NDVI, NIRv, NDBI, MNDWI, DEM elevation; no land cover, no slope/aspect, no raw bands | partial — the repo's set is index-only; the paper's best variant used raw bands |
| predictor timing | same-day reflectance (daily MODIS) | Sentinel-2 median over the month ± 30 days; the LST itself is a monthly median of several passes | **departure** |
| scale ratio | 4× (1 km → 250 m) | 5× (100 m → 20 m) | similar |
| validation | degrade real LST, recover, score vs finer truth; compare forest, TsHARP, resampling | the same triple, pre-registered gate | same design |
| validation ratio | the production ratio, with Landsat as the finer truth | 3× (300 → 100 m), because nothing finer than 100 m exists here | **weaker** — production ratio untested |
| training domain | regional scene | frame + 3 km buffer | smaller; less surface variety per fit |

**Reading of the verdict.** The skeleton is the paper's: coarse fit, fine
application, conserved residual, one model per date, and the paper's own
three-way validation. The repo departs in three places.

1. **Composites instead of a single acquisition.** The mixed-pixel argument of
   §2 is about one moment: one sun, one soil moisture, one canopy state. The
   paper's predictors are from the same day as the thermal image. Here the
   thermal field is a monthly median of several passes and the predictors a
   two-month Sentinel-2 median, so the fit relates two things that were not seen
   together. This is the largest conceptual difference, and it is forced by the
   animation use case (one frame per month, few clear Landsat passes).
2. **A reduced predictor set.** No land cover, no terrain derivatives beyond
   elevation, no raw reflectance bands. On the flat Brandenburg and Berlin sites
   terrain carries nothing (notebook 04 shows DEM importance near zero), and land
   cover is implicit in the indices, but the paper's best result came from adding
   the raw bands, which the repo never tried.
3. **Validation below the production ratio.** The paper could score its 250 m
   output against Landsat. Here the finest thermal truth is the 100 m product
   itself, so the gate runs at 300 → 100 m and the 100 → 20 m step is taken on
   faith. That is the right test given the data, but it is a weaker claim than
   the paper's.

The repo's negative result does not contradict the paper. The paper's *real
case* — a low-contrast thermal product, where the forest beat resampling only
slightly — is exactly the situation of a temperate forest district seen through
a 100 m product with little variance below 300 m. What the repo adds that the
paper does not have is the predictor-smoothing finding of §4, which is where
the remaining accuracy was.

## 7. One-paragraph version

Sharpening does not measure anything below 100 m. It notices that, across the
scene, coarse pixels with more canopy are cooler and those with more bare ground
or buildings are hotter, learns that rule from the thermal data itself, and uses
it to decide how to split each measured 100 m temperature among its 20 m pixels,
under the constraint that the pixels still average to what was measured. It is
mixed-pixel unmixing applied as a visualisation, the Hutengs & Vohland (2016)
scheme with composite inputs and an index-only predictor set. In this repo's
own tests it only beats doing nothing once the predictors are smoothed, by a
few tenths of a kelvin at a 3× ratio, and the 5× production ratio cannot be tested.
