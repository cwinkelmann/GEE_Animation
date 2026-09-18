# Filling the LST holes with MODIS — investigation plan

Status: **draft for review** (2026-08-24). Nothing implemented yet.
Trigger: run when the Earth Engine quota is out of restricted mode.

## The premise needs splitting first

"The holes" are two unrelated problems. MODIS helps with exactly one of them.

| | (A) speckle inside frames | (B) missing months |
|---|---|---|
| what | small white patches, fixed geographic positions | 34 of 103 months have no frame at all |
| size | ~440–610 px/frame ≈ **0.03 %** of the frame | **33 %** of the time axis |
| when | **all 69 frames**, Mar–Nov, every year 2018–2026 | winter-heavy; see the S2 inventory |
| cloud? | **no** — a static mask cannot be weather | yes, genuinely cloud/revisit |
| MODIS fix? | cosmetic only (see §2) | **yes, this is the real win** |

Measured, not assumed: `holes2.py` over the 69 rendered frames shows every frame
holed at the same places across every calendar month and every year. That rules out
cloud, which is what prompted "there is no way there was cloud at that time" — the
observation was correct.

## 1. Diagnose (A) before touching it — 2 cheap EE queries

Cause is **not yet identified**. Two candidate gates in the Landsat LST path:

- `products.ST_QA_MAX_K = 5.0` — `_lst()` masks pixels whose retrieval uncertainty
  exceeds 5 K (`products.py:211`).
- `QA_PIXEL` cloud / shadow / cirrus **confidence bit-pairs**, masked at medium
  confidence and above (`products.py:113-115`, `_L_CONF_SHIFTS`).

Both fire persistently over water, and water was the obvious guess — but comparing
the hole mask against the Dynamic World water class gave **~0 % overlap**, so water
is not supported. (Caveat: the two renders may not share an identical imagery rect,
so treat this as "unsupported", not "disproved".)

**Do this:**
1. For one clear summer scene, export `ST_QA` and `QA_PIXEL` as GeoTIFFs over the
   frame bbox and read them at the hole coordinates. One `getDownloadURL`, tiny.
2. Report which gate fires, and whether the pixels are water, built-up, or
   something else entirely.

Only then decide. If it is ST_QA, raising `ST_QA_MAX_K` to 6–7 K may remove most of
(A) at no cost — but it also weakens the gate that removes undetected-cloud cold
smears, so it must be judged on rendered frames, not in the abstract.

**Free win regardless of cause:** `render.NODATA_RGB = (240, 240, 240)` is
near-white. On the dark end of a thermal palette those specks *glow*. They are 0.03 %
of the frame and read as 10× that. Making the no-data colour configurable (and
defaulting it to a dark neutral for thermal products) is a client-side change, so it
is a **cache hit — zero EE compute, works today under restricted quota**. Do this
first and re-judge whether (A) is still a problem.

## 2. Why MODIS cannot really fix (A)

MODIS LST is **1 km**. The frame is 22.2 × 9.35 km ≈ **22 × 9 MODIS pixels**. One
MODIS pixel covers ~1100× the area of a typical speck. Filling a 100 m hole from
MODIS paints a kilometre-scale average into it: plausible-looking, but it is not a
measurement of that spot. Acceptable for a presentation cut **if labelled**; not
acceptable in anything measurement-grade. Given the free fix above, (A) is probably
not worth spending MODIS on at all.

## 3. Filling (B) — the real work, four options

Ranked by cost/benefit. `metadata.db` already proves the raw material exists:
**MODIS has 103/103 months, Landsat 69**.

### Option 1 — harmonic gap-fill, Landsat only (recommended first)
`smoothing.apply()` already fits `value(t) = c + m·t + Σ(aₖsin + bₖcos)` per pixel.
Today it is only *evaluated* at the 69 observed periods. Evaluating the same fitted
surface at all 103 gives a complete, smooth series with **no new sensor and no
fusion**.

- Cost: one evaluation per synthesized month — ~34 extra frames. Moderate, one-off.
- Risk: a January with zero Landsat passes in *any* year is extrapolation, not
  interpolation. The 2-harmonic fit will still produce a number, and it will look
  confident. **Must** carry a distinct label ("modelled, no observation") and a
  distinct treatment in `_caveats()`, which already emits
  `"modelled: harmonic fit, N harmonics"`.
- Verification: hold out 10 observed months, refit, compare predicted vs actual
  AOI mean. Report RMSE. If RMSE > ~2 K the model is not good enough to fill with.

### Option 2 — MODIS as the gap filler, at MODIS resolution
Use Landsat where it exists, MODIS resampled where it does not.

- Cost: low — MODIS frames are already cached for 103 months.
- Risk: **the resolution changes mid-animation.** A viewer sees the forest boundary
  dissolve into blocks and reappear. Previously measured: MODIS halves the observed
  forest/field contrast (−2.3 K vs −4.7 K) because of 1 km mixed pixels, so the
  reserve's cool island visibly weakens in exactly the filled months.
- Mitigation: heavy smoothing plus an on-frame source label. Honest, but ugly.

### Option 3 — statistically downscaled MODIS (TsHARP / DisTrad)
Regress MODIS LST on a same-date fine-resolution predictor (NDVI/NDRE from
Sentinel-2, or a Landsat-derived one), apply the regression at 10–30 m, add the
residual back. The repo already has a sharpening product (`lst_sharp`) to build on.

- Cost: **highest** — a per-period regression plus a fine-resolution predictor for
  every filled month. This is the option to price carefully before committing quota.
- Risk: needs a *cloud-free fine-resolution predictor in the same month* — but the
  months needing filling are exactly the cloudy ones. Sentinel-2 inventory says
  36/133 months have zero usable S2 scenes, and those overlap the Landsat gaps.
  **This may simply not be available when needed**; check the overlap first (cheap,
  the two inventories are already on disk).
- Payoff if it works: gaps filled at native resolution, no visible resolution break.

### Option 4 — Landsat + MODIS harmonic fit in one model
Fit the harmonic to both sensors jointly, with a per-sensor offset term to absorb
the systematic MODIS−Landsat bias, then evaluate at Landsat resolution. MODIS
constrains the *seasonal shape* in months Landsat never sees, while the rendered
output stays Landsat-resolution.

- Cost: moderate — one fit over a bigger stack.
- Risk: the offset is assumed constant; it is not (it varies with land cover, since
  the bias comes from mixed pixels). Trees vs crops would need separate offsets,
  which the Dynamic World product could supply — `landcover_temperature.py` already
  measured 5.6 K between those classes.
- This is the most *interesting* option and the best candidate for the "nice
  temperature product". It is also the least proven.

## 4. Proposed order

1. Recolour no-data (free, today) → re-judge whether (A) matters at all.
2. Diagnose (A) with 2 tiny queries when quota returns.
3. Check Landsat-gap vs S2-gap overlap from the two CSVs on disk (free) — this
   decides whether Option 3 is even feasible.
4. Option 1 with hold-out validation. If RMSE is acceptable, this may be the whole
   answer and no MODIS is needed.
5. Only if Option 1 under-performs in deep winter: Option 4, falling back to
   Option 2 with explicit labelling.

## 5. Acceptance criteria

- 103/103 months present, or an explicit stated reason per missing month.
- Every synthesized frame is **visibly labelled** as modelled/filled, and the
  source is recorded per frame in `metadata.db` (`Frame.source` already exists).
- Hold-out RMSE reported in the deliverable, not just in a log.
- The forest/field contrast in filled months is not silently weaker than in
  observed months — if it is (Option 2 guarantees this), say so on the frame.

## 6. Open question

Quota reset time is **not exposed by the API** — `getProjectConfig()` returns only
`registrationState`, with no quota headers. So this plan cannot be scheduled; it has
to be started manually once compute succeeds again.
