"""Write one MODIS snow-cover config per property in a point database.

Reads coordinates READ-ONLY from any SQLite DB holding a ``properties`` table with
``<id-column>, address, latitude, longitude, status`` and writes
``<out-dir>/snow_<id>.yaml``: a ~10 km square frame centred on the property, the
same dates/cadence as ``config/snow_varmland.example.yaml``, and a small square
region drawn as the site marker.

    export PROPERTY_DB=/path/to/properties.db
    python scripts/property_snow_configs.py --all              # every active row
    python scripts/property_snow_configs.py --ids 123 456
    python scripts/property_snow_configs.py --db other.db --id-column listing_id --all
    gee-animation --config config/property_snow/snow_123.yaml

The database is never written to, and the generated configs land in a gitignored
directory — they carry street addresses and coordinates, which are the property
owner's data, not the repository's.

NOT YET RUNNABLE: the configs name ``sensor: modis_snow`` with ``index:
snow_cover``/``snow_days``, which ``products.py`` does not register yet, so
``gee-animation`` rejects them with a ConfigError. The generator and its tests
are complete and correct on their own; add the MODIS snow product (see
``config/snow_varmland.example.yaml`` for the intended semantics) to make the
emitted configs render.
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import re
import sqlite3
import sys
from pathlib import Path

import yaml

log = logging.getLogger("property_snow_configs")

#: Point the script at a database without repeating ``--db`` on every call.
DEFAULT_DB = os.environ.get("PROPERTY_DB")
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "config" / "property_snow"
DEFAULT_ID_COLUMN = "property_id"

#: metres per degree of latitude — the same constant aoi.fit_bbox_to_aspect uses,
#: so a frame built here is exactly as square as the renderer measures it.
M_PER_DEG_LAT = 110540.0
FRAME_M = 10_000      # frame side: ~20 x 20 MODIS pixels at 500 m
# Site marker side. 1 km rather than 300 m: the region is also what the scene
# cloud filter reduces over (collection.add_region_cloud_fraction at 500 m), and a
# box smaller than one MODIS pixel risks an empty reduction for that scene.
HOUSE_M = 1_000

START, END, CADENCE = "2021-10-01", "2026-06-01", "monthly"

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _connect_ro(db_path) -> sqlite3.Connection:
    """Open the source DB strictly read-only (SQLite URI ``mode=ro``)."""
    return sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)


def load_properties(db_path, ids=None, id_column: str = DEFAULT_ID_COLUMN) -> list[dict]:
    """Active properties with coordinates, sorted by id; optionally only `ids`."""
    if not _IDENT.match(id_column):      # interpolated into SQL, so never free text
        raise ValueError(f"not a column name: {id_column!r}")
    con = _connect_ro(db_path)
    try:
        rows = con.execute(
            f"SELECT {id_column}, address, latitude, longitude FROM properties "
            "WHERE status='active'").fetchall()
    finally:
        con.close()
    props = [{"property_id": str(h), "address": a or "", "latitude": float(la),
              "longitude": float(lo)}
             for h, a, la, lo in rows if la is not None and lo is not None]
    if ids is not None:
        wanted = [str(i) for i in ids]
        found = {p["property_id"] for p in props}
        for missing in (i for i in wanted if i not in found):
            log.warning("id %s not found among active properties with "
                        "coordinates; skipped", missing)
        props = [p for p in props if p["property_id"] in wanted]
    return sorted(props, key=lambda p: p["property_id"])


def square_bbox(lat: float, lon: float, side_m: float) -> list[float]:
    """[minLon, minLat, maxLon, maxLat] of a `side_m` square (in metres) centred
    on (lat, lon); longitude span widened by 1/cos(lat)."""
    half_lat = side_m / 2.0 / M_PER_DEG_LAT
    half_lon = side_m / 2.0 / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return [round(v, 6) for v in
            (lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat)]


def build_config(prop: dict, *, sensor: str = "modis_snow",
                 index: str = "snow_cover", frame_m: float = FRAME_M,
                 house_m: float = HOUSE_M) -> dict:
    lat, lon = prop["latitude"], prop["longitude"]
    place = prop["address"].split(",")[0].strip() or prop["property_id"]
    return {
        "name": f"snow_{prop['property_id']}",
        "project": "hnee-331218",
        "title": f"Snow cover · {place}",
        "subtitle": (f"MODIS daily, 500 m · {frame_m / 1000:g} km around "
                     f"property {prop['property_id']}"),
        "aoi": {
            "frame": {"bbox": square_bbox(lat, lon, frame_m)},
            "region": {"bbox": square_bbox(lat, lon, house_m)},
            # Scenes cloudy over the site box are dropped; with daily data the
            # monthly per-pixel mean still has plenty of clear days.
            "region_max_cloud_percent": 100,
        },
        "sensor": sensor,
        "index": index,
        "start": START,
        "end": END,
        "cadence": CADENCE,
        "max_cloud_percent": 100,
        "draw_region": True,
        # ~20 x 20 native pixels: nearest keeps the 500 m cells honest instead of
        # smoothing them into detail the data does not have.
        "render": {"fps": 2, "scale": 500, "dimensions": 512, "crs": "auto",
                   "preset": "1080p", "upscale": "nearest"},
    }


def write_configs(props, out_dir, **kwargs) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for prop in props:
        cfg = build_config(prop, **kwargs)
        path = out / f"{cfg['name']}.yaml"
        header = (f"# Generated by scripts/property_snow_configs.py — "
                  f"{prop['address']} ({prop['latitude']:.5f} N, "
                  f"{prop['longitude']:.5f} E)\n")
        path.write_text(header + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        paths.append(path)
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB,
                    help="SQLite DB with a `properties` table, read-only "
                         "(default: $PROPERTY_DB)")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--id-column", default=DEFAULT_ID_COLUMN,
                    help=f"id column in `properties` (default: {DEFAULT_ID_COLUMN})")
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--ids", nargs="+", help="only these property ids")
    sel.add_argument("--all", action="store_true", help="every active property")
    ap.add_argument("--index", default="snow_cover", choices=["snow_cover", "snow_days"])
    ap.add_argument("--sensor", default="modis_snow",
                    choices=["modis_snow", "modis_snow_terra_aqua"])
    ap.add_argument("--frame-km", type=float, default=FRAME_M / 1000)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.db:
        log.error("no database: pass --db or set PROPERTY_DB")
        return 1
    props = load_properties(args.db, ids=None if args.all else args.ids,
                            id_column=args.id_column)
    if not props:
        log.error("no matching active properties with coordinates")
        return 1
    for p in write_configs(props, args.out_dir, sensor=args.sensor, index=args.index,
                           frame_m=args.frame_km * 1000):
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
