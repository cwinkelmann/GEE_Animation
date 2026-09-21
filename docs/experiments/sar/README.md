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
