# ALS point clouds, height models and crown-segmentation checks (moved from experiment/lst-rf-sharpening, 2026-09-22)

Data lives in `/Volumes/2TB/winmol/ALS_Data/` (Berlin 2021 tiles for R12 + R13 with 1 m
DTM/DSM/CHM in `berlin_als_2021/models_1m/`; partial Brandenburg tiles, DGM1 and bDOM for
Grumsin). Each data folder has a README with source, licence and method.

| file | what |
|---|---|
| `als_tools/remote_zip.py`, `fetch_als_tiles.py`, `fetch_r12_tiles.py` | extract single LAS tiles from Berlin's 16–50 GB regional zips by HTTP range |
| `als_tools/fetch_wne_tiles.py`, `fetch_grumsin_all.py` | Brandenburg (LGB) per-tile fetch, small Range chunks (that server drops connections) |
| `als_tools/chm_dtm.py` | 1 m DTM / DSM / CHM per tile + per-area mosaics from classified LAS/LAZ |
| `als_crowns_look.py` → `als_crowns_look.png` | what the WINMOL crown file looks like: stacked crowns, D–H relation, height histogram |
| `als_points_vs_crowns.py` → `als_points_vs_crowns.png` | Berlin 2021 points against the crown polygons (95 % of vegetation points inside a crown) |
| `als_dtm_chm.png`, `wne_dtm_chm.png` | height-model figures for R12/R13 and Grumsin |
| `als_fusion.py` → `als_fusion.png/.csv` | crown structure as detection and PREDICTION feature at R13 (pre-storm AUC 0.77 fused) |

Findings: the crown file `WINDWURF_Tegel/ALS segmentation 2017.gpkg` was segmented from
the Feb/Mar 2021 Berlin scan (canopy heights equal the 2021 point maxima to 0.00 m median);
its stacked understorey crowns and linear crown-width–height relation are consistent with
adaptive mean shift 3D.

`als_fusion.py` depends on the windthrow analysis code of branch `experiment/lst-rf-sharpening`
(`sensor_figures.py`, `detection_upgrade.py`, `windthrow_vs_delta.py`) and on the SAR spike's
monthly GeoTIFFs; run it from that worktree with its `docs/experiments/windthrow` on `PYTHONPATH`.
