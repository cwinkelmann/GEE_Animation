# Berlin ALS point cloud tiles (flight 24/25 Feb and 2 Mar 2021), Spandau block (fetched 2026-09-22)

20 tiles, LAS 1.4 point format 6, ~9.8 pts/m², classified (2 ground, 3/4/5 low/medium/high
vegetation, 7 low points, 0 default), ETRS89 / UTM 33N (EPSG:25833), 1 km × 1 km each.
These are exactly the tiles referenced in the `layer` column of
`../ALS segmentation 2017.gpkg` (the crown segmentation), so they are its input. NOTE: the crown file is named "2017" but Berlin's only published ALS is the
Feb/Mar 2021 flight (metadata lineage; LAS headers created 2021-07-01 by TerraScan), and the crown
heights match the 2021 point maxima to 0.00 m median for canopy trees — the crowns were segmented
from THIS 2021 scan (leaf-off). Treat "2017" in the crown file name as a misnomer.

Source: Senatsverwaltung für Stadtentwicklung, Bauen und Wohnen Berlin,
"Airborne Laserscanning (ALS) Primäre 3D Laserscan-Daten", INSPIRE ATOM download
service https://gdi.berlin.de/data/a_als/atom/ (regional packages Nordwest.zip and
West.zip). Licence: Datenlizenz Deutschland – Zero – Version 2.0 (dl-de-zero-2.0).
Metadata: https://gdi.berlin.de/geonetwork/srv/api/records/85a97801-36bb-4627-8790-f5e53b1e38b3

Fetched with HTTP range requests into the zip members (no full package download):
`fetch_als_tiles.py` + `remote_zip.py` in this folder. Each file was checked for the
LASF magic and its point count after extraction. Total 13.8 GB.
