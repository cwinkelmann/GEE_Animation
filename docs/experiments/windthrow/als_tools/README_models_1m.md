# 1 m height models from the Berlin ALS 2021 tiles (built 2026-09-22)

Per tile: `<tile>_dtm.tif` (mean ground-class z per 1 m cell, holes filled by linear then
nearest interpolation), `<tile>_dsm.tif` (max z of all non-noise returns), `<tile>_chm.tif`
(DSM − DTM, ≥ 0). Mosaics: `R12_{dtm,dsm,chm}_1m.tif` (17 tiles) and `R13_{dtm,dsm,chm}_1m.tif`
(20 tiles), footprint bbox + 200 m, EPSG:25833, nodata −9999, deflate. Built with
`als_tools/chm_dtm.py` (branch experiment/lst-rf-sharpening). Heights are ellipsoid-free
DHHN2016 as delivered by Berlin; the CHM is the raw first-return canopy height (no
smoothing, no pit filling). Leaf-off flight (Feb/Mar 2021): deciduous crowns are sparse.
