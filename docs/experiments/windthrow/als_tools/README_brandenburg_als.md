# Brandenburg ALS point clouds, Grumsin (WNE) tiles

Source: Landesvermessung und Geobasisinformation Brandenburg (LGB), open data
https://data.geobasis-bb.de/geobasis/daten/als/laz/als_33<E>-<N>.zip, licence
Datenlizenz Deutschland – Namensnennung 2.0 (dl-de/by-2.0), "GeoBasis-DE / LGB".
LAZ 1.4 point format 6, ~21 pts/m², 1 km tiles, EPSG:25833. Classes as delivered:
0 unclassified (all vegetation/buildings), 1, 2 ground, 20 synthetic water/wet surfaces —
there are NO vegetation classes, so canopy = non-ground. Flight date is not in the
headers (files re-written by las2las in 2020-06); see LGB's currency map
(bb_laserscandaten_aktualitaet.pdf). The northing row 5872 (four tiles along the
north edge of the reserve) is not published yet.
Tiles 33423-5871, 33424-5869/70/71, 33425-5870/71, 33426-5870 were downloaded by hand
(browser) on 2026-09-22; the remaining 33426-5871, 33427-5870/71 by `../fetch_grumsin_all.py`.
`models_1m/`: DTM/DSM/CHM per tile and `WNE_*_1m.tif` mosaics from `als_tools/chm_dtm.py`.
