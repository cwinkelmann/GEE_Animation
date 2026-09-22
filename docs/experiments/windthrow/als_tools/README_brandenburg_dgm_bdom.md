# Brandenburg DGM1 and bDOM, Grumsin (WNE) tiles

LGB open data, dl-de/by-2.0, "GeoBasis-DE / LGB". 1 km tiles, 1 m grid, EPSG:25833.
- `dgm_33<E>-<N>.tif`: DGM1 laser-based terrain model (14 tiles, complete), from
  https://data.geobasis-bb.de/geobasis/daten/dgm/tif/
- `bdom_33<E>-<N>.tif`: image-based digital surface model (bDOM) from stereo aerial
  photos (14 tiles), from https://data.geobasis-bb.de/geobasis/daten/bdom/tif/ —
  covers the northern row 5872 that the laser scan lacks.
`*_meta.html` / `*.tfw` are the LGB sidecars. Downloaded 2026-09-22 (browser + `../fetch_grumsin_all.py`).
