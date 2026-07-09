import types
import pytest
from gee_animation import products as P


def test_registry_contents():
    assert set(P.SENSORS) == {"sentinel2", "landsat"}
    assert set(P.INDICES) == {"ndvi", "lst"}
    assert P.SENSORS["sentinel2"].scene_cloud_property == "CLOUDY_PIXEL_PERCENTAGE"
    assert P.SENSORS["landsat"].scene_cloud_property == "CLOUD_COVER"
    assert P.SENSORS["landsat"].collection_ids == (
        "LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2")
    assert P.INDICES["lst"].sensors == frozenset({"landsat"})
    assert P.INDICES["ndvi"].sensors == frozenset({"sentinel2", "landsat"})


def test_get_product_ok():
    sensor, index = P.get_product("landsat", "lst")
    assert sensor.name == "landsat" and index.name == "lst"


def test_get_product_rejects_unknown_and_unsupported_pair():
    with pytest.raises(ValueError, match="sensor"):
        P.get_product("modis", "ndvi")
    with pytest.raises(ValueError, match="index"):
        P.get_product("landsat", "evi")
    with pytest.raises(ValueError, match="not available"):
        P.get_product("sentinel2", "lst")   # LST is Landsat-only


def test_collection_merges_landsat(ee_recorder=None):
    calls = []
    class FakeColl:
        def __init__(self, cid): self.cid = cid
        def merge(self, other): calls.append(("merge", self.cid, other.cid)); return self
    ee = types.SimpleNamespace(ImageCollection=lambda cid: FakeColl(cid))
    P.SENSORS["landsat"].collection(ee_module=ee)
    assert calls == [("merge", "LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2")]


def test_s2_reflectance_selects_aliases_and_scales():
    rec = {}
    class FakeImg:
        def select(self, bands, names): rec["select"] = (tuple(bands), tuple(names)); return self
        def multiply(self, v): rec["multiply"] = v; return self
        def add(self, v): rec["add"] = v; return self
    P.SENSORS["sentinel2"].reflectance(FakeImg())
    assert rec["select"][1] == ("blue", "green", "red", "nir", "swir1", "swir2")
    assert rec["select"][0] == ("B2", "B3", "B4", "B8", "B11", "B12")
    assert rec["multiply"] == 0.0001
    assert "add" not in rec   # S2 scale is purely multiplicative, no offset


def test_landsat_reflectance_scales_with_offset():
    rec = {}
    class FakeImg:
        def select(self, bands, names): rec["select"] = (tuple(bands), tuple(names)); return self
        def multiply(self, v): rec["multiply"] = v; return self
        def add(self, v): rec["add"] = v; return self
    P.SENSORS["landsat"].reflectance(FakeImg())
    assert rec["select"][0] == ("SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7")
    assert rec["multiply"] == 0.0000275 and rec["add"] == -0.2


def test_ndvi_uses_scaled_reflectance():
    rec = {}
    class FakeRefl:
        def normalizedDifference(self, bands): rec["nd"] = tuple(bands); return self
        def rename(self, n): rec["rename"] = n; return "ndvi_band"
    class FakeSensor:
        def reflectance(self, image, ee_module=None): rec["refl"] = True; return FakeRefl()
    out = P.INDICES["ndvi"].compute(FakeSensor(), object(), ee_module=None)
    assert rec["refl"] and rec["nd"] == ("nir", "red") and rec["rename"] == "INDEX"
    assert out == "ndvi_band"


def test_lst_applies_scale_offset_kelvin_to_celsius():
    rec = {}
    class FakeBand:
        def multiply(self, v): rec["multiply"] = v; return self
        def add(self, v): rec["add"] = v; return self
        def subtract(self, v): rec["subtract"] = v; return self
        def rename(self, n): rec["rename"] = n; return "lst_band"
    class FakeImg:
        def select(self, b): rec["select"] = b; return FakeBand()
    out = P.INDICES["lst"].compute(object(), FakeImg(), ee_module=None)
    assert rec["select"] == "ST_B10"
    assert rec["multiply"] == 0.00341802 and rec["add"] == 149.0 and rec["subtract"] == 273.15
    assert rec["rename"] == "INDEX" and out == "lst_band"


def test_landsat_mask_and_cloud_band_use_qa_bits():
    rec = {}
    class FakeQA:
        def bitwiseAnd(self, bits): rec["bits"] = bits; return self
        def eq(self, v): rec["eq"] = v; return "clearmask"
        def neq(self, v): rec["neq"] = v; return self
        def rename(self, n): rec["rename"] = n; return "cloudband"
    class FakeImg:
        def select(self, b): rec["select"] = b; return FakeQA()
        def updateMask(self, m): rec["masked_with"] = m; return "masked"
    expected_bits = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)
    assert P.SENSORS["landsat"].mask_clouds(FakeImg()) == "masked"
    assert rec["bits"] == expected_bits and rec["eq"] == 0 and rec["masked_with"] == "clearmask"
    rec.clear()
    assert P.SENSORS["landsat"].cloud_band(FakeImg()) == "cloudband"
    assert rec["bits"] == expected_bits and rec["neq"] == 0 and rec["rename"] == "cloud"


def test_s2_mask_and_cloud_band_use_scl_classes():
    rec = {"neq": [], "and_count": 0}

    class FakeMask:
        def And(self, other):
            rec["and_count"] += 1
            return self

    class FakeSCL:
        def neq(self, cls):
            rec["neq"].append(cls)
            return ("neq", cls)
        def remap(self, frm, to, default):
            rec["remap"] = (frm, to, default)
            return self
        def rename(self, n):
            rec["rename"] = n
            return "cloudband"

    class FakeImg:
        def select(self, b):
            rec["select"] = b
            return FakeSCL()
        def updateMask(self, m):
            rec["masked"] = True
            return "masked"

    ee = types.SimpleNamespace(Image=types.SimpleNamespace(constant=lambda v: FakeMask()))
    assert P.SENSORS["sentinel2"].mask_clouds(FakeImg(), ee_module=ee) == "masked"
    assert rec["select"] == "SCL"
    assert rec["neq"] == [3, 8, 9, 10, 11] and rec["and_count"] == 5 and rec["masked"] is True

    rec.clear()
    cb = P.SENSORS["sentinel2"].cloud_band(FakeImg())
    assert rec["remap"][0] == [3, 8, 9, 10, 11] and rec["remap"][2] == 0
    assert rec["rename"] == "cloud" and cb == "cloudband"
