import json
import types
import pytest
from gee_animation.aoi import parse, _load_geojson_geometry


class FakeGeometry:
    def __init__(self, spec):
        self.spec = spec


def _fake_ee():
    def geometry_call(spec):
        return FakeGeometry(("geojson", spec))
    geometry_call.Rectangle = lambda coords: FakeGeometry(("rect", tuple(coords)))
    return types.SimpleNamespace(Geometry=geometry_call)


def test_parse_bbox():
    ee = _fake_ee()
    geom = parse({"bbox": [13.7, 52.8, 13.9, 52.95]}, ee_module=ee)
    assert geom.spec == ("rect", (13.7, 52.8, 13.9, 52.95))


def test_parse_geojson_feature(tmp_path):
    ee = _fake_ee()
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
        ],
    }
    p = tmp_path / "aoi.geojson"
    p.write_text(json.dumps(fc))
    geom = parse({"geojson": str(p)}, ee_module=ee)
    assert geom.spec[0] == "geojson"
    assert geom.spec[1]["type"] == "Polygon"


def test_parse_requires_something():
    ee = _fake_ee()
    with pytest.raises(ValueError, match="aoi"):
        parse({}, ee_module=ee)


def test_load_geojson_unwraps_feature(tmp_path):
    feat = {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1, 2]}}
    p = tmp_path / "f.geojson"
    p.write_text(json.dumps(feat))
    geom = _load_geojson_geometry(str(p))
    assert geom == {"type": "Point", "coordinates": [1, 2]}


def test_geojson_takes_precedence_over_bbox(tmp_path):
    ee = _fake_ee()
    geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    p = tmp_path / "aoi.geojson"
    p.write_text(json.dumps(geom))
    result = parse({"bbox": [13.7, 52.8, 13.9, 52.95], "geojson": str(p)}, ee_module=ee)
    assert result.spec[0] == "geojson"
    assert result.spec[1]["type"] == "Polygon"


def test_load_geojson_returns_bare_geometry(tmp_path):
    geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    p = tmp_path / "g.geojson"
    p.write_text(json.dumps(geom))
    assert _load_geojson_geometry(str(p)) == geom
