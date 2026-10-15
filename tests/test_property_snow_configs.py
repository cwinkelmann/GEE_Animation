"""Per-property snow config generation (scripts/property_snow_configs.py).

Uses a throw-away SQLite DB with the expected `properties` shape and invented
places; never touches a real database.
"""
import math
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import property_snow_configs as H  # noqa: E402
from gee_animation.config import RunConfig  # noqa: E402
from gee_animation.products import get_product  # noqa: E402


def _has_snow_product() -> bool:
    """The `modis_snow` product is not in the package yet (see this module's
    docstring in scripts/). Everything except the RunConfig round-trip is
    independent of it, so only that one test waits for it."""
    try:
        get_product("modis_snow", "snow_cover")
    except Exception:
        return False
    return True


needs_snow_product = pytest.mark.skipif(
    not _has_snow_product(),
    reason="sensor 'modis_snow' is not registered in products.py yet")


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "properties.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE properties (property_id TEXT, address TEXT, "
                "latitude REAL, longitude REAL, status TEXT)")
    con.executemany("INSERT INTO properties VALUES (?,?,?,?,?)", [
        ("1001", "Björkhult 12, Testköpings kommun", 59.18358, 14.63634, "active"),
        ("1002", "Granåsen 7, Provdalens kommun", 63.06844, 14.31958, "active"),
        ("111", "Sold house", 60.0, 13.0, "sold"),
        ("222", "No coordinates", None, None, "active"),
    ])
    con.commit()
    con.close()
    return path


def test_load_properties_reads_active_rows_only(db):
    props = H.load_properties(db)
    assert [p["property_id"] for p in props] == ["1001", "1002"]   # sorted, no sold/no-coords
    assert props[0]["address"].startswith("Björkhult")


def test_load_properties_filters_by_ids_and_reports_missing(db, caplog):
    props = H.load_properties(db, ids=["1001", "999"])
    assert [p["property_id"] for p in props] == ["1001"]
    assert "999" in caplog.text


def test_load_properties_opens_the_db_read_only(db):
    # The script must never be able to write to the source DB.
    con = H._connect_ro(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM properties")
    con.close()


def test_load_properties_honours_a_custom_id_column(tmp_path):
    path = tmp_path / "other.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE properties (listing_id TEXT, address TEXT, "
                "latitude REAL, longitude REAL, status TEXT)")
    con.execute("INSERT INTO properties VALUES ('abc', 'Somewhere', 60.0, 13.0, 'active')")
    con.commit()
    con.close()
    props = H.load_properties(path, id_column="listing_id")
    assert [p["property_id"] for p in props] == ["abc"]


def test_load_properties_rejects_a_non_identifier_id_column(db):
    # The column name is interpolated into SQL, so it must not accept free text.
    with pytest.raises(ValueError):
        H.load_properties(db, id_column="id; DROP TABLE properties")


def test_square_bbox_is_square_in_metres_using_cos_lat():
    lat, lon = 63.0, 14.0
    minlon, minlat, maxlon, maxlat = H.square_bbox(lat, lon, 10_000)
    assert minlat < lat < maxlat and minlon < lon < maxlon
    height_m = (maxlat - minlat) * H.M_PER_DEG_LAT
    width_m = (maxlon - minlon) * H.M_PER_DEG_LAT * math.cos(math.radians(lat))
    assert height_m == pytest.approx(10_000, abs=1.0)   # 6-decimal rounding ~0.1 m
    assert width_m == pytest.approx(10_000, abs=1.0)   # 6-decimal rounding ~0.1 m
    # at 63 N a degree of longitude is ~half a degree of latitude
    assert (maxlon - minlon) / (maxlat - minlat) == pytest.approx(
        1 / math.cos(math.radians(lat)), rel=1e-4)


def test_build_config_content():
    prop = {"property_id": "1001", "address": "Björkhult 12, Testköpings kommun",
            "latitude": 59.18358, "longitude": 14.63634}
    cfg = H.build_config(prop)
    assert cfg["name"] == "snow_1001"
    assert cfg["sensor"] == "modis_snow" and cfg["index"] == "snow_cover"
    assert (cfg["start"], cfg["end"], cfg["cadence"]) == ("2021-10-01", "2026-06-01", "monthly")
    frame, region = cfg["aoi"]["frame"]["bbox"], cfg["aoi"]["region"]["bbox"]
    assert frame == H.square_bbox(59.18358, 14.63634, H.FRAME_M)
    assert region == H.square_bbox(59.18358, 14.63634, H.HOUSE_M)
    assert cfg["draw_region"] is True                     # site marker outline
    assert cfg["render"]["scale"] == 500 and cfg["render"]["upscale"] == "nearest"
    assert "Björkhult" in cfg["title"]


def test_write_configs_writes_one_yaml_per_property(db, tmp_path):
    # Everything about the written file that does not depend on the snow product,
    # so the whole write path stays covered while that product is missing.
    out = tmp_path / "configs"
    paths = H.write_configs(H.load_properties(db), out)
    assert sorted(p.name for p in paths) == ["snow_1001.yaml", "snow_1002.yaml"]
    raw = yaml.safe_load(paths[0].read_text(encoding="utf-8"))
    assert raw["name"] == "snow_1001"
    assert raw["aoi"]["frame"]["bbox"] == H.square_bbox(59.18358, 14.63634, H.FRAME_M)
    text = (out / "snow_1002.yaml").read_text(encoding="utf-8")
    # the provenance header carries the address and coordinates of its property
    assert text.startswith("# Generated by scripts/property_snow_configs.py")
    assert "63.06844 N" in text
    # non-ASCII addresses survive (not \x-escaped)
    assert "Granåsen" in text


@needs_snow_product
def test_written_configs_round_trip_through_runconfig(db, tmp_path):
    out = tmp_path / "configs"
    for p in H.write_configs(H.load_properties(db), out):  # valid for the real pipeline
        cfg = RunConfig.from_yaml(p)
        assert cfg.sensor == "modis_snow" and (cfg.viz_min, cfg.viz_max) == (0.0, 100.0)


def test_main_requires_a_selection_and_passes_it_through(db, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(H, "load_properties",
                        lambda path, ids=None, id_column=H.DEFAULT_ID_COLUMN:
                            seen.update(ids=ids) or [])
    with pytest.raises(SystemExit):                       # neither --ids nor --all
        H.main(["--db", str(db), "--out-dir", str(tmp_path)])

    assert H.main(["--db", str(db), "--out-dir", str(tmp_path),
                   "--ids", "1001"]) == 1                 # nothing found
    assert seen["ids"] == ["1001"]
    H.main(["--db", str(db), "--out-dir", str(tmp_path), "--all"])
    assert seen["ids"] is None


def test_main_without_a_database_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "DEFAULT_DB", None)
    assert H.main(["--all", "--out-dir", str(tmp_path), "--db", ""]) == 1
