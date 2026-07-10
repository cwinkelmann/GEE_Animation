import types
import pytest
from gee_animation import products as P


_REFL_INDICES = frozenset({"sentinel2", "landsat", "modis"})


def test_registry_contents():
    assert set(P.SENSORS) == {"sentinel2", "landsat", "modis"}
    assert set(P.INDICES) == {"ndvi", "lst", "evi", "ndwi", "ndmi", "ecostress"}
    assert P.INDICES["ecostress"].sensors == frozenset({"landsat"})   # sharpened Landsat LST
    assert P.SENSORS["sentinel2"].scene_cloud_property == "CLOUDY_PIXEL_PERCENTAGE"
    assert P.SENSORS["landsat"].scene_cloud_property == "CLOUD_COVER"
    assert P.SENSORS["modis"].scene_cloud_property is None   # no per-scene cloud metadata
    assert P.SENSORS["modis"].collection_ids == ("MODIS/061/MOD09A1",)
    assert P.SENSORS["landsat"].collection_ids == (
        "LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2")
    assert P.INDICES["lst"].sensors == frozenset({"landsat"})
    for name in ("ndvi", "evi", "ndwi", "ndmi"):
        assert P.INDICES[name].sensors == _REFL_INDICES


def test_get_product_ok():
    sensor, index = P.get_product("modis", "ndmi")
    assert sensor.name == "modis" and index.name == "ndmi"


def test_get_product_rejects_unknown_and_unsupported_pair():
    with pytest.raises(ValueError, match="sensor"):
        P.get_product("viirs", "ndvi")      # viirs not registered
    with pytest.raises(ValueError, match="index"):
        P.get_product("landsat", "savi")    # savi not registered
    with pytest.raises(ValueError, match="not available"):
        P.get_product("modis", "lst")       # LST is Landsat-only


def test_evi_uses_expression_on_scaled_reflectance_and_keeps_time():
    rec = {}
    class FakeResult:
        def set(self, k, v): rec["set"] = (k, v); return "evi_band"
    class FakeRefl:
        def select(self, b): rec.setdefault("selected", []).append(b); return ("band", b)
        def expression(self, expr, bands):
            rec["expr"] = expr; rec["bands"] = tuple(sorted(bands)); return self
        def rename(self, n): rec["rename"] = n; return FakeResult()
    class FakeSensor:
        def reflectance(self, image, ee_module=None): rec["refl"] = True; return FakeRefl()
    class FakeImg:
        def get(self, k): rec["get"] = k; return "TS"
    out = P.INDICES["evi"].compute(FakeSensor(), FakeImg(), ee_module=None)
    assert rec["refl"] and rec["rename"] == "INDEX"
    # standard EVI coefficients present, computed on the aliased nir/red/blue bands
    assert "2.5" in rec["expr"] and "6" in rec["expr"] and "7.5" in rec["expr"]
    assert rec["bands"] == ("blue", "nir", "red")
    assert rec["set"] == ("system:time_start", "TS") and out == "evi_band"


def _normdiff_recorder():
    rec = {}
    class FakeResult:
        def set(self, k, v): rec["set"] = (k, v); return "band"
    class FakeRefl:
        def normalizedDifference(self, bands): rec["nd"] = tuple(bands); return self
        def rename(self, n): rec["rename"] = n; return FakeResult()
    class FakeSensor:
        def reflectance(self, image, ee_module=None): rec["refl"] = True; return FakeRefl()
    class FakeImg:
        def get(self, k): return "TS"
    return rec, FakeSensor(), FakeImg()


def test_ndwi_is_green_nir_normalized_difference():
    rec, sensor, img = _normdiff_recorder()
    out = P.INDICES["ndwi"].compute(sensor, img, ee_module=None)
    assert rec["refl"] and rec["nd"] == ("green", "nir")   # McFeeters open-water NDWI
    assert rec["rename"] == "INDEX" and rec["set"] == ("system:time_start", "TS")
    assert out == "band"


def test_ndmi_is_nir_swir1_normalized_difference():
    rec, sensor, img = _normdiff_recorder()
    out = P.INDICES["ndmi"].compute(sensor, img, ee_module=None)
    assert rec["nd"] == ("nir", "swir1")                   # moisture index
    assert rec["rename"] == "INDEX" and rec["set"] == ("system:time_start", "TS")


def test_ecostress_sharpens_lst_with_ndvi_detail_and_keeps_time():
    rec = {"multiply": [], "add": [], "subtract": [], "select": []}
    class Chain:
        def __init__(self, r): self.r = r
        def select(self, b): self.r["select"].append(b); return self
        def multiply(self, v): self.r["multiply"].append(v); return self
        def add(self, v): self.r["add"].append(v); return self
        def subtract(self, v): self.r["subtract"].append(v); return self
        def normalizedDifference(self, b): self.r["nd"] = tuple(b); return self
        def focal_mean(self, **k): self.r["focal_mean"] = k; return self
        def rename(self, n): self.r["rename"] = n; return self
        def set(self, k, v): self.r["set"] = (k, v); return "ecostress_band"
        def get(self, k): return "TS"
    class FakeSensor:
        def reflectance(self, image, ee_module=None): rec["refl"] = True; return Chain(rec)
    out = P.INDICES["ecostress"].compute(FakeSensor(), Chain(rec), ee_module=None)
    # LST from ST_B10 with the standard scale/offset, in Celsius
    assert "ST_B10" in rec["select"]
    assert 0.00341802 in rec["multiply"] and 149.0 in rec["add"] and 273.15 in rec["subtract"]
    # NDVI high-frequency detail (focal-mean smoothing) injected into the thermal field
    assert rec.get("refl") and rec["nd"] == ("nir", "red")
    assert rec["focal_mean"]["units"] == "meters"
    assert 16.0 in rec["multiply"]                      # NDVI-detail slope
    assert rec["rename"] == "INDEX" and rec["set"] == ("system:time_start", "TS")
    assert out == "ecostress_band"


def test_modis_reflectance_maps_bands_and_scales():
    rec = {}
    class FakeImg:
        def select(self, bands, names): rec["select"] = (tuple(bands), tuple(names)); return self
        def multiply(self, v): rec["multiply"] = v; return self
    P.SENSORS["modis"].reflectance(FakeImg())
    assert rec["select"][1] == ("blue", "green", "red", "nir", "swir1", "swir2")
    assert rec["select"][0] == (
        "sur_refl_b03", "sur_refl_b04", "sur_refl_b01", "sur_refl_b02",
        "sur_refl_b06", "sur_refl_b07")
    assert rec["multiply"] == 0.0001
    assert "add" not in rec   # MOD09 scale is purely multiplicative


def test_modis_mask_and_cloud_band_use_state_bits():
    rec = {"and": 0}
    class FakeState:
        def bitwiseAnd(self, bits): rec.setdefault("bits", []).append(bits); return self
        def eq(self, v): rec.setdefault("eq", []).append(v); return self
        def And(self, other): rec["and"] += 1; return self
        def Not(self): rec["not"] = True; return self
        def rename(self, n): rec["rename"] = n; return "cloudband"
    class FakeImg:
        def select(self, b): rec["select"] = b; return FakeState()
        def updateMask(self, m): rec["masked"] = True; return "masked"
    assert P.SENSORS["modis"].mask_clouds(FakeImg()) == "masked"
    assert rec["select"] == "StateQA"
    assert 3 in rec["bits"] and (1 << 2) in rec["bits"] and (1 << 10) in rec["bits"]
    assert rec["masked"] is True
    rec2 = {"and": 0}
    cb = P.SENSORS["modis"].cloud_band(FakeImg())
    assert cb == "cloudband" and rec["rename"] == "cloud"


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


def test_ndvi_uses_scaled_reflectance_and_keeps_time():
    rec = {}
    class FakeResult:
        def set(self, k, v): rec["set"] = (k, v); return "ndvi_band"
    class FakeRefl:
        def normalizedDifference(self, bands): rec["nd"] = tuple(bands); return self
        def rename(self, n): rec["rename"] = n; return FakeResult()
    class FakeSensor:
        def reflectance(self, image, ee_module=None): rec["refl"] = True; return FakeRefl()
    class FakeImg:
        def get(self, k): rec["get"] = k; return "TS"
    out = P.INDICES["ndvi"].compute(FakeSensor(), FakeImg(), ee_module=None)
    assert rec["refl"] and rec["nd"] == ("nir", "red") and rec["rename"] == "INDEX"
    assert rec["set"] == ("system:time_start", "TS") and rec["get"] == "system:time_start"
    assert out == "ndvi_band"


def test_lst_applies_scale_offset_kelvin_to_celsius_and_keeps_time():
    rec = {}
    class FakeResult:
        def set(self, k, v): rec["set"] = (k, v); return "lst_band"
    class FakeBand:
        def multiply(self, v): rec["multiply"] = v; return self
        def add(self, v): rec["add"] = v; return self
        def subtract(self, v): rec["subtract"] = v; return self
        def rename(self, n): rec["rename"] = n; return FakeResult()
    class FakeImg:
        def select(self, b): rec["select"] = b; return FakeBand()
        def get(self, k): rec["get"] = k; return "TS"
    out = P.INDICES["lst"].compute(object(), FakeImg(), ee_module=None)
    assert rec["select"] == "ST_B10"
    assert rec["multiply"] == 0.00341802 and rec["add"] == 149.0 and rec["subtract"] == 273.15
    assert rec["rename"] == "INDEX" and rec["set"] == ("system:time_start", "TS")
    assert out == "lst_band"


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
