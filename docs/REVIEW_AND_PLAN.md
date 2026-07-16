# `gee-animation` — Review & Work Plan

**Date:** 2026-07-11
**Repo:** satellite index timelapse generator (Sentinel-2 / Landsat / MODIS → MP4 + GIF + per-month PNG)
**Primary AOI:** WNE / Grumsin beech forest, Brandenburg, DE (~53.0° N, ~13.9° E)
**Default GEE project:** `hnee-331218`

> **How to use this file.** Sections are ordered by priority. P0 items are correctness bugs — the
> current output is *wrong*, not just improvable. Each task has an acceptance criterion. Do not skip
> §1 (Evidence): it is the forensic basis for P0-1 through P0-4 and prevents re-litigating them.

---

## Contents

- [§1 Evidence — the `lst_smw` striping investigation](#1-evidence)
- [§2 P0 — Correctness bugs](#2-p0--correctness-bugs)
- [§3 P0 — README factual corrections](#3-p0--readme-factual-corrections)
- [§4 P1 — Methodology](#4-p1--methodology)
- [§5 P2 — New indices & sensors](#5-p2--new-indices--sensors)
- [§6 Known limits / explicitly out of scope](#6-known-limits--out-of-scope)
- [§7 Reference: collection IDs](#7-reference--collection-ids)
- [§8 Appendix: reproduce the forensics](#8-appendix--reproduce-the-forensics)

---

## §1 Evidence

Investigation of the frame `out/wne_lst_smw_2021-04.png`, which shows soft banding across the whole
scene. The palette was inverted through the frame's own colorbar to recover values, then the field
was analysed for directional structure.

### Findings

| Quantity | Measured | Notes |
|---|---|---|
| Render ground sampling | **2.75 m/px** | scale bar = 363 px per 1 km |
| Gradient anisotropy | `mean\|∂/∂y\| / mean\|∂/∂x\|` = **1.97** | structure is E–W elongated |
| Banding direction (Radon peak) | **+8° from E–W** | 0.97 at +12°, 0.85 at +4°/+16° |
| Power at along-track (~82°) | **0.07** | 14× weaker — push-broom striping ruled out |
| Recovered value range (p1/p50/p99) | **10.5 / 14.9 / 20.2 °C** | on a 0–40 °C palette |

### Geometry check

Landsat inclination = 98.2°. Ground-track tilt from N–S at latitude φ:

```
α = asin(|cos 98.2°| / cos φ) = asin(0.1426 / 0.6018) = 13.7°   at φ = 53° N
```

So **cross-track scan lines sit 13.7° off E–W on the ground**. The frame is rendered in EPSG:4326
(plate carrée), where at 53° N the x-axis is compressed by `cos 53° = 0.60`, so those scan lines
should *appear* at:

```
atan(cos 53° · tan 13.7°) = atan(0.1467) = 8.4° from horizontal
```

**Predicted 8.4°. Measured 8°.** The banding is aligned with Landsat's scan lines.

### Conclusion

Cross-track banding ⇒ **whiskbroom** sensor. In April 2021 the only whiskbroom Landsat flying is
**Landsat 7 ETM+** (L8's TIRS is push-broom and would band along-track at ~82°, where there is
almost no power; L9 launched Sept 2021; L5 was decommissioned in 2013).

**Mechanism — this is the important part.** The stripes are *not* simply the SLC-off gaps. They are
the fact that **the median's sample composition changes across the gap boundary**:

- outside a gap: `median({L7, L8})`
- inside a gap: `median({L8})` — L7 contributes nothing

Two scenes, different days, different weather, different calibration, different SMW coefficient
sets ⇒ the two populations sit several K apart, and the boundary between them is a scan line.
The SLC-off gaps repeat once per ETM+ scan: **16 detectors × 30 m = 480 m** along-track (thermal
band 6: 8 detectors × 60 m = the same 480 m).

The README currently asserts these gaps are *"softened by monthly medians."* They are not. In April,
in Brandenburg, at `region_max_cloud_percent ≤ 10`, the composite is almost certainly **n = 1–3
scenes**, and a median over 1–3 samples softens nothing. A median of two is just a mean of two.

**Secondary factor to verify:** Landsat 7's orbit maintenance had ended by 2021 and its overpass had
drifted earlier in the morning than L8's. If so, L7 samples a cooler part of the morning — a
systematic cool bias stacked on top of the geometric artifact.

### Two further problems the same analysis surfaced

1. **Massive render oversampling.** 2.75 m/px, but TIRS is **100 m native** and ETM+ band 6 is
   **60 m native**, both delivered on a 30 m grid. This is an 11–36× upsample; everything finer than
   30 m is Earth Engine interpolating. It is *why* the 480 m SLC-off bands appear as soft cloud-like
   blobs rather than hard stripes — each band is smeared across ~170 screen pixels.

2. **Non-square pixels.** The 8° tilt only comes out that way in EPSG:4326. At 53° N that means
   ground-x per pixel ≠ ground-y per pixel (ratio 0.60), so the single "1 km" scale bar can only be
   correct along one axis.

3. **The AOI is thermally invisible.** The Grumsin outline produces *no* contrast in the LST field.
   A beech stand at a ~10:00 April overpass should differ from the surrounding fields by several K.
   When the sensor artifact has more amplitude than the land cover does, the frame is not showing
   land surface temperature.

---

## §2 P0 — Correctness bugs

### P0-1 — Exclude Landsat 7 from LST composites

**Where:** `gee_animation/products.py` (Landsat `Sensor` collection build), config schema.

The `landsat` sensor harmonises missions 4/5/7/8/9. For **thermal** that is actively harmful: L7
costs ~22% of pixels to SLC-off, has a 60 m band-6, and carries a mission offset. From 2013 onward
L8/L9 alone give better coverage than L7 adds.

**Do:**
- Add a `missions:` list to the config schema (e.g. `missions: [LANDSAT_8, LANDSAT_9]`).
- Default `lst`, `lst_smw`, `lst_sharp` to **L8/L9 only** for dates ≥ 2013-04-11.
- For pre-2013 dates, allow TM/ETM+ but **emit a warning** naming the mission and the artifact.
- Keep L7 available for the reflectance indices, where the gaps are cosmetic rather than biasing.

**Acceptance:** regenerating `wne_lst_smw_2021-04.png` with L8-only produces a frame whose Radon
sweep (see §8) shows **no peak** at +8°, i.e. normalised power at +8° drops below ~0.3.

---

### P0-2 — Expose and enforce the scene count per frame

**Where:** compositing step + frame annotation.

Right now a frame built from a single scene is indistinguishable from one built from twelve. This is
the root enabler of P0-1 and of every future compositing surprise.

**Do:**
- Log `coll.aggregate_array('system:index').getInfo()` per frame at DEBUG.
- Add `n_scenes` to the frame's info bar (top-left, next to the month).
- Add `min_scenes:` to config (default `3`). Below it: skip the frame, or emit it flagged, but do
  **not** silently produce a median-of-one.
- Add an optional `n_obs` debug product: `coll.select(<thermal band>).count()` rendered as its own
  layer. **This is the decisive diagnostic** — if an artifact shows up in the count map it is
  gap/masking geometry; if the count is flat but the values stripe, it is radiometry.

**Acceptance:** every PNG carries `n=<int>`; a config with `min_scenes: 3` skips 2021-04 for
`wne_lst_smw` unless enough scenes exist.

---

### P0-3 — Derive `render.dimensions` from the sensor's native resolution

**Where:** render module. The README already names this bug:

> *"`render.scale` (metres/pixel) is informational; thumbnail size is driven by `render.dimensions`."*

**Do:**
- Add a `native_scale_m` attribute to each `Sensor`/`Index` in the registry:

  | product | native scale |
  |---|---|
  | Sentinel-2 reflectance (10 m bands) | 10 |
  | Sentinel-2 reflectance (20 m bands: SWIR, red-edge) | 20 |
  | Landsat reflectance | 30 |
  | Landsat `lst` / `lst_smw` (TIRS) | **100** |
  | Landsat `lst` / `lst_smw` (ETM+ / TM) | 60 / 120 |
  | MODIS | 500 |

- Compute `dimensions` so the render is **no finer than `native_scale_m`**. If the user forces a
  finer render, log a warning stating the true native resolution.
- Optionally add `render.upsample: {none|bilinear}` and default to `none` (nearest) for thermal, so
  the pixels are visibly honest.

**Acceptance:** `wne_lst_smw` renders at ≥ 100 m/px unless explicitly overridden; the warning fires
on override.

---

### P0-4 — Render in a metric CRS

**Where:** `getThumbURL` call.

Currently plate carrée. Pass an explicit `crs`:

- **EPSG:25833** (ETRS89 / UTM 33N) — correct for Brandenburg.
- **EPSG:3035** (ETRS89-LAEA Europe) — better if AOIs will span zones.
- Expose as `render.crs` in the config; default to UTM zone derived from AOI centroid.

**Acceptance:** pixels are square in ground units; the scale bar is correct on both axes; the
(remaining, if any) Landsat scan artifacts appear at their true **13.7°** rather than 8°.

---

### P0-5 — Fix the LST visualisation range

The recovered frame spans **10.5–20.2 °C** on a **0–40 °C** palette — the whole scene sits in the
bottom quarter, which is why everything is cyan.

**Do:** keep the fixed range (comparability across frames matters) but make it *seasonally* sensible,
or better, adopt the anomaly mode in §4 (P1-1), which solves this properly.

---

## §3 P0 — README factual corrections

These are wrong in the current README and cheap to fix.

### P0-6 — ECOSTRESS **is** in the Earth Engine catalog now

README says *"real ECOSTRESS data isn't in the Earth Engine catalog."* No longer true:

```
ee.ImageCollection("NASA/ECOSTRESS/L2T_LSTE/V2")
```

70 m; bands `LST`, `LST_err`, `QC`, `EmisWB`, `cloud`, `height`, `water`, `view_zenith`;
2018-07-09 → present; daily cadence.

**But two catches, and the second is fatal for Grumsin:**

1. The catalog page states: *"Currently, only tiles covering the Los Angeles metro area have been
   ingested into Earth Engine. We plan to expand coverage in the future."* → **no Grumsin data.**
2. Even at full global ingest, **ECOSTRESS barely reaches 53° N.** It rides the ISS (51.5°
   inclination). LP DAAC states coverage between **52° N and 52° S**; NASA's `ECOSTRESS-Data-Resources`
   repo puts it at **53.6° N/S** thanks to the ~384 km swath. Grumsin (~53.0° N) is inside that
   margin ⇒ edge-of-swath at best: high view zenith, degraded GSD (70 m nadir → ~90 m at the edge),
   very few scenes.

**Do:** rewrite the ECOSTRESS note to say the collection exists but is LA-only, and that the AOI
latitude is at the coverage edge regardless. Add a `TODO` to watch the
[EE changelog](https://developers.google.com/earth-engine/docs/data-catalog/release-notes) for
ingest expansion.

> **Note for other AOIs:** Galápagos (0° N) is *prime* ECOSTRESS territory — dense coverage, and the
> precessing ISS orbit samples the diurnal cycle. Far better home for this dataset than Brandenburg.

---

### P0-7 — Rename the `ecostress` index to `lst_sharp`

**Where:** `products.py` registry, `config.ecostress.example.yaml` → `config.lst_sharp.example.yaml`,
README table.

The current `ecostress` product is an NDVI-sharpened Landsat LST, **not** ECOSTRESS data. In a
teaching repo this *will* get cited as ECOSTRESS by a student, and now that a real
`NASA/ECOSTRESS/*` collection exists in GEE the name collision is worse.

**Acceptance:** no config, band name, output filename or README row says "ecostress" for the
synthetic product.

---

### P0-8 — MODIS is being decommissioned; document the orbital drift

- Terra & Aqua **begin shutting down in late 2026 / early 2027**; MODIS data collection stops and a
  final reprocessing follows. NASA's stated plan: **Terra MODIS Feb 2027, Aqua MODIS Sept 2027**
  (other NASA pages give Aqua "end of mission August 2026" — the dates are not consistent across
  sources; treat as "imminent").
- **Both platforms are already drifting out of their designed orbits**, shifting equatorial crossing
  times.

**Consequence for `config.modis.example.yaml`:** a long MODIS NDVI loop has a **moving overpass time
baked into its recent years** — a real confound for a phenology animation, and a larger one for LST.

**Do:** add the caveat to the README; add VIIRS as the successor sensor (see §5).

---

## §4 P1 — Methodology

### P1-1 — Add an anomaly rendering mode ⭐ *highest leverage change in the repo*

A raw LST loop mostly animates **the weather on the overpass day**. A monthly median over 2–3 clear
scenes says more about what the sky was doing that morning than about the forest. Same problem, less
acutely, for NDVI/NDMI/EVI.

**Do:** add `anomaly:` to the config, supporting:

- `anomaly: climatology` — render `(x − mean_month(x)) / std_month(x)`, i.e. a per-pixel z-score
  against that pixel's own long-term value **for that calendar month**. Needs a `baseline_years:`
  range.
- `anomaly: reference` (thermal only) — render `LST − ERA5-Land T₂ₘ` (`ECMWF/ERA5_LAND/HOURLY`,
  bands `temperature_2m` / `skin_temperature`), sampled at the scene's acquisition time.

Use a **diverging** palette and a symmetric range. This turns every loop from decorative into
diagnostic, and it subsumes P0-5.

**Acceptance:** `wne_lst_smw` with `anomaly: climatology` produces a frame where the Grumsin polygon
is *visible* as a coherent thermal unit.

---

### P1-2 — Replace the hand-tuned sharpening slope

**Where:** `_ECOSTRESS_NDVI_SLOPE` in `products.py`.

A hardcoded constant is the weak link. The literature method (**DisTrad / TsHARP**; Kustas 2003,
Agam 2007) does two things the current code does not:

1. **Fits** the LST ↔ fractional-vegetation-cover relation **from the coarse data itself, per
   scene**, rather than assuming a slope.
2. **Adds the coarse residual back** after applying the fit at fine resolution, so the sharpened
   field aggregates back to the observed coarse LST. Without this the output is not conservative —
   it is a texture, not a temperature.

The modern version is the **Data Mining Sharpener** (Gao et al. 2012; `pyDMS`, from ESA's SEN-ET
project): a regression-tree / RF ensemble mapping multiple fine bands → LST.

**Critical AOI-specific trap:** *NDVI saturates in a closed beech canopy.* In July, Grumsin sits at
~0.85–0.9 essentially everywhere, so NDVI carries almost no dynamic range to drive the sharpening —
the residual ends up doing all the work. Feed the sharpener predictors that actually vary there:

- **NDMI / SWIR** (moisture varies where greenness does not)
- **albedo**
- **canopy height / DSM** (shade and structure drive canopy temperature)
- **slope / aspect** (Copernicus DEM)
- **NIRv or a red-edge index** instead of raw NDVI

**Acceptance:** step 2 (residual correction) is implemented and unit-tested — aggregating the
sharpened output back to the coarse grid reproduces the input LST to within float tolerance.

---

### P1-3 — Cross-check `lst` vs `lst_smw`

Free bisection tool. Render both for the same month and diff:

- artifact in **both** ⇒ shared input (scene selection, the composite, or ASTER-GED emissivity —
  which both algorithms use, and which enters as a *divisor*, so its own seams propagate straight
  into LST).
- artifact in **`lst_smw` only** ⇒ the SMW implementation (TOA/L1 vs SR/L2 collection mismatch,
  emissivity handling, or the NCEP TCWV coefficient lookup).

**Note a likely latent bug:** `lst_smw` needs **TOA brightness temperature** (a Level-1 product),
while the `landsat` sensor is described as **Collection-2 Level-2**. If the region cloud filter is
computed on one collection and the pixels pulled from another, the scene sets can silently diverge.
Verify the L1/L2 join.

**Reference implementation to diff against:** Ermida et al. (2020), *Remote Sensing* 12(9), 1471 —
public EE repo `users/sofiaermida/landsat_smw_lst`.

---

## §5 P2 — New indices & sensors

### Indices — ordered by value for *this* AOI

Add to the registry in `products.py`. Bands given for Sentinel-2.

| Index | Formula (S2) | Why, for a closed beech canopy |
|---|---|---|
| **NIRv** ⭐ | `NDVI × B8` | GPP proxy (Badgley 2017). One-liner. Fixes NDVI saturation. |
| **kNDVI** ⭐ | `tanh(NDVI²)` | Camps-Valls 2021. Nonlinear; same saturation fix, different flavour. |
| **PSRI** | `(B4 − B2) / B6` | *The* senescence index. Makes the autumn half of a phenology loop actually move. |
| **NDRE** | `(B8 − B5) / (B8 + B5)` | Red-edge. Earlier stress detection, no saturation. |
| **CIre** | `(B7 / B5) − 1` | Chlorophyll, red-edge. |
| **IRECI** | `(B7 − B4) / (B5 / B6)` | Red-edge, S2-specific. |
| **NBR** | `(B8 − B12) / (B8 + B12)` | Not fire — **disturbance**: windthrow, Borkenkäfer, sanitary logging. Most teachable missing layer for German forests. |
| **TCW** (Tasseled Cap Wetness) | sensor-specific coefficients | Classic forest disturbance/moisture. Coefficients differ per sensor (Crist 1985 / Baig 2014 for OLI; Nedkov 2017 for S2) — do **not** reuse across sensors. |
| **MNDWI** | `(B3 − B11) / (B3 + B11)` | Strictly better than the McFeeters NDWI already in the repo. Relevant for Grumsin's Sölle. |

> ⚠️ **Note:** PSRI is `(Red − Blue) / RedEdge2` = `(B4 − B2) / B6`, following Merzlyak's original
> 678/500/750 nm formulation. Not green.

### Sensors

| Sensor | Collection | Why |
|---|---|---|
| **HLS** ⭐ | `NASA/HLS/HLSL30/v002` (L8/9)<br>`NASA/HLS/HLSS30/v002` (S2 A/B/C) | 30 m NBAR, harmonised, common MGRS grid, **~1.4–2.3 day revisit**. For phenology loops this beats S2-alone on cloud-free sample count. Verify the current end date in the catalog — historical ingest was still in progress. **Known issue:** WRS-2 path/row boundary artifacts in L30 reflectance and cloud masks (abrupt changes across row boundaries) — relevant given §1. |
| **Sentinel-1** ⭐ | `COPERNICUS/S1_GRD` | **No cloud gaps.** Nov–Feb S2 in Brandenburg is nearly unusable; an S1 loop has no missing frames at all. Add VV, VH, and the VH/VV ratio. |
| **VIIRS LST** | `NASA/VIIRS/002/VNP21A1D` (day)<br>`NASA/VIIRS/002/VNP21A1N` (night) | 1 km, daily, TES-based. **Adds night** — Landsat is descending-node, ~10:00–10:30 local, so the repo currently has only one time of day. Day−night ΔT gives apparent thermal inertia. ⚠️ NASA flags that TES-based VNP21/MxD21 show more extreme hot outliers than the heritage split-window MxD11 — filter QC hard. |
| **VIIRS reflectance** | (successor to MOD09A1) | MODIS replacement path — see P0-8. |

### Phenology products (worth a separate config)

`MODIS/061/MCD12Q2` and `NOAA/VIIRS/001/VNP22Q2` give **per-pixel greenup / peak / senescence /
dormancy DOY**. 500 m, but animating *greenup DOY across years* is a stronger story than a
within-year NDVI loop, and it is the actual quantitative phenology layer.

---

## §6 Known limits / out of scope

Documenting these saves future time.

- **Sentinel-3 SLSTR is not in Earth Engine.** GEE carries only `COPERNICUS/S3/OLCI` (300 m TOA
  radiances, 21 VNIR bands, **no thermal**). SLSTR would give 1 km TIR, day *and* night, sub-daily
  revisit at 53° N, and it is the European operational LST. Available via **CDSE / openEO /
  Sentinel Hub** — and it is the input to the SEN-ET chain (SLSTR + S2 → ~20 m LST via `pyDMS`).
  If "best available thermal for a German forest" is the goal, that is the answer, at the cost of
  leaving Earth Engine. **Out of scope for this repo unless a non-GEE backend is added.**

- **GEDI does not cover the AOI.** Same ISS orbit as ECOSTRESS but a 4 km swath instead of 384 km,
  so coverage stops hard at **~51.6° N**. Grumsin (53.0° N) has **zero** GEDI footprints. Do not
  attempt it. For structure use Copernicus DEM `COPERNICUS/DEM/GLO30`, or the ETH (10 m) / Meta-WRI
  (1 m) canopy-height layers.

- **Deadwood is a lidar/UAV problem, not a Sentinel-2 problem.** Brandenburg's open LiDAR
  (DGM1 / DOM1 from LGB) at 1 m will do more than anything in the GEE catalog.

- **Future thermal missions** (for the teaching module): **TRISHNA** — 57 m TIR, 3-day revisit,
  0.3 °C precision; launch has slipped from CNES's stated 2026 to Oct 2027 per the CEOS database.
  Then Copernicus **LSTM** and NASA/ASI **SBG** (schedule uncertain under budget pressure).

- **UAV thermal** (Mavic 3T / M4T) is the only way to get a genuine validation target for any
  sharpening below 30 m. Worth a flight over the AOI if P1-2 is pursued seriously.

---

## §7 Reference — collection IDs

```
# thermal
NASA/ECOSTRESS/L2T_LSTE/V2      70 m, 2018-07→present, LA TILES ONLY (as of 2026-07)
NASA/VIIRS/002/VNP21A1D         1 km daily LST&E, DAY
NASA/VIIRS/002/VNP21A1N         1 km daily LST&E, NIGHT
ECMWF/ERA5_LAND/HOURLY          skin_temperature, temperature_2m — the anomaly baseline
NASA/ASTER_GED/AG100_003        emissivity (used by SMW; enters as a divisor)
NCEP_RE/surface_wv              TCWV for the SMW coefficient lookup

# reflectance
NASA/HLS/HLSL30/v002            30 m NBAR, Landsat 8/9
NASA/HLS/HLSS30/v002            30 m NBAR, Sentinel-2 A/B/C
COPERNICUS/S1_GRD               C-band SAR — cloud-free
COPERNICUS/S3/OLCI              300 m TOA radiance — NO THERMAL, OLCI only

# phenology / structure
MODIS/061/MCD12Q2               land cover dynamics: greenup/peak/senescence DOY
NOAA/VIIRS/001/VNP22Q2          land surface phenology, 500 m
COPERNICUS/DEM/GLO30            30 m DSM
```

---

## §8 Appendix — reproduce the forensics

Drop this in `scripts/frame_forensics.py`. It takes any annotated PNG the pipeline emits, inverts
the palette through the frame's own colorbar, and reports stripe orientation. Use it as the
regression check for P0-1 and P0-4.

```python
"""Detect and orient banding artifacts in a rendered gee-animation frame."""
import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter, rotate

def analyse(png, colorbar_row=112, colorbar_x=(8, 819), img_rows=(145, 875),
            vmin=0.0, vmax=40.0):
    a = np.array(Image.open(png).convert("RGB")).astype(np.float32)

    # 1. invert the palette using the frame's own colorbar
    ramp = a[colorbar_row, colorbar_x[0]:colorbar_x[1]]
    lut = ramp[np.linspace(0, len(ramp) - 1, 256).astype(int)]
    pos = np.linspace(0, 1, 256)

    sub = a[img_rows[0]:img_rows[1]]
    h, w, _ = sub.shape
    flat = sub.reshape(-1, 3)
    val = np.empty(len(flat), np.float32)
    dist = np.empty(len(flat), np.float32)
    for i in range(0, len(flat), 300_000):
        ch = flat[i:i + 300_000]
        d = ((ch[:, None, :] - lut[None, :, :]) ** 2).sum(-1)
        j = d.argmin(1)
        val[i:i + 300_000] = pos[j]
        dist[i:i + 300_000] = np.sqrt(d[np.arange(len(ch)), j])

    v = np.where(dist.reshape(h, w) > 45, np.nan, val.reshape(h, w))   # drop outline/text
    print(f"values p1/p50/p99: "
          f"{np.nanpercentile(v, [1, 50, 99]) * (vmax - vmin) + vmin}")
    v = np.where(np.isnan(v), np.nanmean(v), v).astype(np.float64)

    # 2. bandpass, then Radon sweep: peak = direction the BANDS run
    band = uniform_filter(v, 15) - uniform_filter(v, 220)
    n = min(h, w) // 2
    cy, cx = h // 2, w // 2
    c0 = band[cy - n // 2:cy + n // 2, cx - n // 2:cx + n // 2]   # square, so rotation is unbiased

    out = []
    for th in range(-88, 92, 2):
        r = rotate(c0, th, reshape=False, order=1, mode="reflect")[40:-40, 40:-40]
        out.append((th, r.mean(axis=1).var()))
    out = np.array(out)
    out[:, 1] /= out[:, 1].max()

    best = out[out[:, 1].argmax(), 0]
    print(f"banding runs at {best:+.0f}deg from E-W")
    for th, s in out[::4]:
        print(f"  {th:+4.0f} : {s:.2f} " + "#" * int(s * 40))
    return best, out


def expected_scan_angle(lat_deg, inclination=98.2, plate_carree=True):
    """Angle (from E-W) at which Landsat cross-track scan lines should appear."""
    phi = np.radians(lat_deg)
    alpha = np.arcsin(abs(np.cos(np.radians(inclination))) / np.cos(phi))
    if plate_carree:                       # EPSG:4326: x compressed by cos(lat)
        return np.degrees(np.arctan(np.cos(phi) * np.tan(alpha)))
    return np.degrees(alpha)               # metric CRS: true angle


if __name__ == "__main__":
    best, _ = analyse("out/wne_lst_smw_2021-04.png")
    print(f"expected (EPSG:4326, 53N): {expected_scan_angle(53.0):+.1f}deg")
    print(f"expected (metric CRS, 53N): {expected_scan_angle(53.0, plate_carree=False):+.1f}deg")
```

**Regression criterion after P0-1:** the Radon peak near `expected_scan_angle(53.0)` must drop below
**0.3** normalised power.

---

## Task checklist

```
P0  [ ] 1. Exclude L7 from LST composites; add `missions:` to config
P0  [ ] 2. Expose n_scenes on every frame; add `min_scenes:`; add n_obs debug layer
P0  [ ] 3. Derive render.dimensions from native_scale_m (TIRS = 100 m!)
P0  [ ] 4. Render in a metric CRS (EPSG:25833 / 3035)
P0  [ ] 5. Fix the LST viz range (or supersede via P1-1)
P0  [ ] 6. README: ECOSTRESS is in GEE now (LA-only; AOI at coverage edge anyway)
P0  [ ] 7. Rename `ecostress` -> `lst_sharp` everywhere
P0  [ ] 8. README: MODIS decommissioning + orbital drift caveat

P1  [ ] 1. Anomaly rendering mode (climatology z-score / ERA5 reference)   <- biggest win
P1  [ ] 2. Replace _ECOSTRESS_NDVI_SLOPE with fitted DisTrad + residual correction
P1  [ ] 3. Verify the L1(TOA)/L2(SR) join in lst_smw; diff lst vs lst_smw

P2  [ ] 1. Indices: NIRv, kNDVI, PSRI, NDRE, CIre, IRECI, NBR, TCW, MNDWI
P2  [ ] 2. Sensors: HLS, Sentinel-1, VIIRS (day + night LST)
P2  [ ] 3. Phenology configs: MCD12Q2 / VNP22Q2
P2  [ ] 4. scripts/frame_forensics.py as a regression check
```
