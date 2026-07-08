import json
import types
import pytest
from gee_animation.aoi import parse, _load_geojson_geometry


class FakeGeometry:
    def __init__(self, spec):
        self.spec = spec


def _fake_ee():
    m = types.SimpleNamespace()
    m.Geometry = types.SimpleNamespace(
        Rectangle=lambda coords: FakeGeometry(("rect", tuple(coords))),
    )
    # ee.Geometry(...) is also callable to wrap raw geojson:
    def geometry_call(spec):
        return FakeGeometry(("geojson", spec))
    m.Geometry = types.SimpleNamespace(
        Rectangle=lambda coords: FakeGeometry(("rect", tuple(coords))),
    )
    # make Geometry itself callable
    callable_geom = geometry_call
    callable_geom.Rectangle = lambda coords: FakeGeometry(("rect", tuple(coords)))
    m.Geometry = callable_geom
    return m


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
