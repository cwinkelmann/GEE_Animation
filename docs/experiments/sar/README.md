# Spike: Sentinel-1 backscatter vs predicted windthrow, R12 (2026-09-21)

Throwaway probe on branch `spike/sar-windthrow`. `scripts/sar_windthrow_spike.py`
exports monthly medians of Sentinel-1 GRD (IW, both orbit passes) VV and VH in dB
and the cross-ratio VH − VV at 20 m over the R12 zoom frame, 2017-01 .. 2026-09
(117 months, every month has scenes), then runs the same test as the thermal work:
windthrow cells (>= 20 m predicted stem per 20 m cell) minus stem-free canopy per
month, the post−pre change map (summers after 2025-07 minus 2022–24), and the AUC
of that change for windthrow-dense cells.

| band | windthrow − canopy, before → after | post−pre change, windthrow vs canopy | AUC |
|---|---|---|---|
| VV | +0.19 → −0.64 dB | −0.92 vs −0.03 dB | 0.85 |
| **VH** | +0.08 → **−1.00 dB** | **−0.99 vs +0.05 dB** | **0.88** |
| VH − VV | −0.11 → −0.35 dB | −0.07 vs +0.08 dB | 0.61 |

Reading: the felled stands lose about 1 dB of cross-polarised backscatter (the
canopy's volume scattering) and stay there; VH alone separates windthrow cells
from canopy at AUC 0.88, better than the thermal delta (0.83) and on a par with
the NDVI drop, with no dependence on cloud-free passes. The ratio adds little
because both channels fall together. Caveats: both orbits are mixed (incidence
angles differ), no speckle filter beyond the monthly median, no street mask
was applied inside the frame beyond the footprint mask, and this is one site.
Not merged anywhere; a real product would be a `sentinel1` sensor with VV/VH
indices in `products.py`.

## R13 and Grumsin (2026-09-21)

| site | band | windthrow − canopy, before → after | post−pre change, windthrow vs canopy | AUC |
|---|---|---|---|---|
| R13 | VV | +0.26 → −0.17 dB | −0.36 vs −0.02 dB | 0.69 |
| R13 | **VH** | +0.02 → −0.57 dB | −0.43 vs 0.00 dB | **0.71** |
| R13 | VH − VV | −0.24 → −0.40 dB | −0.07 vs +0.03 dB | 0.57 |
| Grumsin (control) | VV / VH / ratio | footprint mean −9.5 / −15.4 / −5.9 dB | change −0.09 / −0.16 / −0.06 dB | — |

R13 shows the same VH drop at half R12's amplitude (diffuse damage, as in the
thermal and optical tests) — AND a slow decline of the windthrow stands'
backscatter relative to canopy from 2018 onward, before the storm, that the
R12 series does not have. That pre-storm drift is worth a look on its own:
stands losing volume scattering for years before they fell. Grumsin's
footprint-wide backscatter is flat across 2025 (−0.1 to −0.2 dB, within
year-to-year noise).

## Is the R13 pre-storm drift real? (2026-09-21, `scripts/sar_canopy_check.py`)

Checked against the two artefacts that could fake it. Orbit mix: Sentinel-1B
flew until 2021-12 (≈ 23 scenes/month), 2022–24 had S1A alone (≈ 12), S1C/S1D
joined in 2025–26; the same four relative orbits (44, 95, 146, 168) cover every
month. Within a single pass the drift is unchanged:

| VH windthrow − canopy | 2018 | 2024 | slope 2018–24 | after storm |
|---|---|---|---|---|
| ascending only | +0.29 dB | −0.19 dB | −0.07 dB/yr | −0.57 dB |
| descending only | +0.41 dB | −0.16 dB | −0.09 dB/yr | −0.56 dB |
| both (as first reported) | | | −0.08 dB/yr | |

Absolute levels: the windthrow stands fell from −14.96 to −15.43 dB VH between
2018 and 2024 while the stem-free canopy stayed at −15.3; the same in VV. So
the decline is in the stands that later fell, not in the reference, and not
in the sensor. It starts in 2019, i.e. with the 2018–2020 drought years in
Brandenburg. Interpretation stays open with one site: progressive crown loss
before the storm, or a stand type whose backscatter behaves differently.

## Other storms: the WINMOL annotated corpus (2026-09-21, `scripts/sar_multistorm.py`)

Eleven hand-digitised windthrow polygons from three storms, each against a ring of
intact tree cover 100–500 m around it (ESA WorldCover, every damage polygon removed).
Monthly VH, 2016–2026. "Step" = polygon−ring mean over the 12 months after the storm
minus the 12 months before; "slope" = pre-storm trend of polygon−ring over 24 months.

| site (storm) | species | step, dB | pre-storm slope, dB/yr |
|---|---|---|---|
| Eberswalde Campus (Xavier 2017-10) | beech | **−1.00** | −0.21 (only ~20 months of S1 before) |
| Eberswalde survey polygon (Xavier) | beech | **−0.72** | −0.24 |
| Bachsee north (Xavier) | beech | −0.34 | −0.14 |
| Kaufland (summer 2021, date guessed) | beech, 0.5 ha | +0.01 | −0.20 |
| Campus Oberheide (Zeynep 2022-02) | beech | −0.09 | +0.03 |
| Eberswalde survey polygon (Zeynep) | beech | +0.06 | 0.00 |
| Barnekow 3 / 5 / survey (Zeynep) | spruce/pine | **−0.97 / −1.13 / −0.95** | +0.01 / −0.03 / −0.01 |
| Bremerhagen 3 / survey (Zeynep) | spruce | **−1.59 / −0.76** | −0.04 / −0.05 |

Detection replicates: 8 of 11 polygons drop by 0.3–1.6 dB at the storm, on beech in
autumn (Xavier) and on spruce/pine in winter (Zeynep). The three that do not are the
0.5 ha Kaufland patch (too small for 20 m cells, storm date uncertain) and the two
Eberswalde beech polygons of Zeynep — leaf-off beech in February, on ground that
already carried Xavier damage in the ring. The **pre-storm decline does not
replicate**: every Zeynep site has a flat pre-storm slope (±0.05 dB/yr); the Xavier
sites show −0.14 to −0.24 dB/yr but with under two years of Sentinel-1 before the
storm that number is not trustworthy. So: VH backscatter is a robust windthrow
*detector* across storms and species; the R12/R13 pre-storm decline stays a Tegel
observation, not a general precursor.

### Damaged vs intact, absolute VH (`scripts/sar_damaged_vs_intact.py`, `sar_damaged_vs_intact.png`)

Same data as the two analyses above, but the two parts of each site plotted as
separate absolute series (dB) instead of their difference: Tegel windthrow cells vs
stem-free cells, corpus polygons vs their ring. What the difference plots hide: the
intact part is flat across every storm (−0.05 to +0.05 dB, Bachsee −0.32 because
its ring includes damage), so the drop is entirely in the damaged part; and the
Barnekow/Bremerhagen conifer polygons sit 0.5–1 dB *below* their ring for years
before Zeynep — a stand-type offset, not a precursor, which is why their pre-storm
slope is flat.

### Does the step scale with how much fell? (`scripts/sar_step_vs_density.py`, `sar_step_vs_density.png`)

Why do some polygons drop and others not? Test: VH step of each polygon against
its annotated fallen-stem density (WINMOL stem outlines / polygon area; Tegel as
whole footprints vs their stem-free cells, with the tegel-unet stems). Barnekow 6
was fetched for this (`MS_ONLY=` subset fetch, appended to the CSV).

| polygon | ha | stems/ha | step, dB |
|---|---|---|---|
| Tegel R12 / R13 footprint | 515 / 1033 | 16 / 24 | −0.10 / −0.07 |
| Bachsee, Campus Oberheide | 3.2, 11.2 | 43, 44 | −0.34, −0.09 |
| Eberswalde Campus | 11.9 | 69 | −1.00 |
| Barnekow 3 / 5 / 6 | 1.6 / 2.8 / 1.0 | 231 / 364 / 717 | −0.97 / −1.13 / −0.84 |
| Bremerhagen 3 | 0.26 | 616 | −1.59 |
| Kaufland (date uncertain, excluded) | 0.54 | 232 | +0.01 |

Spearman r = −0.73 (p = 0.025), Pearson r on log density = −0.86 (p = 0.003),
n = 9 dated storms. So the on/off pattern is mostly damage fraction: below ~50
stems/ha the polygon mean drops by 0.1–0.3 dB, which is inside the noise; above
~200 stems/ha it drops by about 1 dB and saturates (Barnekow 6 at 717/ha is not
deeper than Barnekow 3 at 231/ha). Eberswalde Campus at 69/ha is the one polygon
that drops more than its density predicts (in-leaf beech, October). Kaufland's
polygon was already 1 dB below its ring by 2019, two years before the flight
date used as the storm date, so its damage predates the assumed storm.
