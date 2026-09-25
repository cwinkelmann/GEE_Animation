# Windthrow experiments (Tegel R12 / R13, Grumsin control), September 2026

One-off analysis scripts behind the report `out/report/report.md` and the decision log
`docs/superpowers/plans/2026-09-17-lst-rf-sharpening.md`. They are not part of the package
and have no tests; they read GeoTIFFs produced by `gee-animation` runs (`geotiffs: true`) and
the WINMOL stem predictions. Results (PNG/CSV) are committed next to the scripts so the report
figures can be traced.

## Inputs

| input | where | how it is found |
|---|---|---|
| stem predictions, footprints, R12 street mask | `<WT_DATA_ROOT>/Revier_12/…`, `Revier_13/…` (the WINMOL `WINDWURF_Tegel` folder) | `WT_DATA_ROOT` env var; fallbacks: NAS `/Volumes/storage/…`, `/Volumes/2TB/…`, `~/data/…`; or `WT_STEMS` / `WT_STREETS` directly |
| site | `WT_SITE=R12` (default), `R13`, `WNE` | read at import by `windthrow_vs_delta.py` (scripts that loop over sites re-import it) |
| thermal delta frames | `out/geotiffs/<config name>/*.tif` from `config/r12_focus_lst_rf_delta_local_10yr.yaml`, `r13_zoom_lst_rf_delta_local_10yr.yaml`, `wne_focus_lst_rf_delta_2018_2026.yaml` (on this branch) | passed as a glob |
| vegetation index frames | `out/geotiffs/<site>_data_<index>/*.tif` from `config/<site>_data_{ndvi,ndmi,ndre,evi}.yaml` | passed as a root dir |
| Sentinel-1 monthly tifs | `out/sar_<site>/*.tif` in the `spike/sar-windthrow` worktree | passed as the worktree path |

Run everything from the repo root with the project conda env.

## Scripts → outputs

| script | invocation | writes |
|---|---|---|
| `windthrow_vs_delta.py` | `WT_SITE=R12 python … "<delta tif glob>" <out csv>` | `windthrow_series.csv` / `windthrow_focus.csv` (monthly windthrow − canopy, Spearman, density bins); also the `stem_density()` helper every other script imports |
| `windthrow_plot.py` | `python … <series csv> <out png>` | `windthrow_series.png`, `windthrow_focus_series.png` |
| `windthrow_maps.py` | `python … "<delta tif glob>" <YYYY-MM> <out png>` | `windthrow_focus_maps.png`, `windthrow_maps_post.png` |
| `heat_islands.py` | `python … "<post-storm glob>" "<pre-storm glob>" <out png>` | `heat_islands.png` (≥ 1 K patches vs stems, permutation test) |
| `pre_storm.py` | `python … "<delta tif glob>"` | prints pre-storm AUC (no file) |
| `site_comparison.py` | `python … <wne glob> <r12 glob> <r13 glob> <out png>` | `site_comparison.png` |
| `index_drops.py` | `WT_SITE=R12 python … <geotiff root> "<lst delta glob>" <out png>` | `r12_index_drops.png`, `r13/r13_index_drops.png` |
| `index_means.py` | `WT_SITE=WNE python … <geotiff root> "<lst delta glob>" <out png>` | `wne/wne_index_means.png` (control, reserve-wide means) |
| `compare_noise.py` | `python … <tif A> <tif B>` | prints high-frequency noise of two fields (the pooled-vs-per-frame forest check) |
| `sensor_figures.py` | `python … <sar worktree> <out dir>` | `sensor_comparison_series.png`, `roc_curves.png`, `dose_response.png`, `thermal_seasonality.png` |
| `detection_upgrade.py` | `python … <sar worktree> <out dir>` | `detection_upgrade.png/.csv` (fusion, VH change-point, texture, capture curves; blocked 5-fold CV) |
| `truecolour_before_after.py` | `python … <out png>` (needs Earth Engine, project `hnee-331218`) | `truecolour_before_after.png` |
| `r13/`, `wne/` | outputs of the same scripts for the other sites | `r13_windthrow_series.csv/.png`, `r13_heat_islands.png`, `r13_windthrow_maps.png`, `lst_rf_per_frame_oob*.csv` |

`lst_rf_local_models_loyo.csv` and `lst_rf_per_frame_oob_2025_2026.csv` are the model
scores written by `gee_animation.sharpen_local` for the pooled (rejected) and per-frame runs.

## Known limitations

- Site selection through an environment variable read at import time; `sensor_figures.py`,
  `detection_upgrade.py` and `truecolour_before_after.py` re-import the module per site.
- The footprint mask is re-derived in `index_drops.py`, `index_means.py` and
  `sensor_figures.py` instead of shared; `series`/`mean_of` exist twice.
- `truecolour_before_after.py` hard-codes the Earth Engine project id.
- Nothing here is network-free or unit-tested; treat the numbers as the report's evidence,
  not as a product.
