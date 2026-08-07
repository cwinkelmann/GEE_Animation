import types
import pytest
from gee_animation import products as P


_REFL_INDICES = frozenset({"sentinel2", "landsat", "modis"})


def test_registry_contents():
    assert set(P.SENSORS) == {"sentinel2", "landsat", "modis", "modis_lst"}
    assert set(P.INDICES) == {"ndvi", "lst", "lst_smw", "evi", "ndwi", "ndmi",
                              "rgb", "cir", "lst_sharp", "lst_modis"}
    assert P.INDICES["lst_modis"].sensors == frozenset({"modis_lst"})   # MODIS-only LST
    assert P.SENSORS["modis_lst"].scene_cloud_property is None          # no per-scene cloud
    assert "lst_modis" in P.THERMAL_INDICES
    assert P.INDICES["lst_smw"].sensors == frozenset({"landsat"})   # Ermida 2020 SMW LST
    assert P.INDICES["rgb"].composite and P.INDICES["cir"].composite
    assert P.INDICES["ndvi"].formula == "(NIR - Red) / (NIR + Red)"   # overlay metadata
    assert P.INDICES["rgb"].composite and P.INDICES["rgb"].default_viz[2] is None
    assert P.INDICES["lst_sharp"].sensors == frozenset({"landsat"})   # NDVI-sharpened Landsat LST
    assert P.SENSORS["sentinel2"].scene_cloud_property == "CLOUDY_PIXEL_PERCENTAGE"
    assert P.SENSORS["landsat"].scene_cloud_property == "CLOUD_COVER"
    assert P.SENSORS["modis"].scene_cloud_property is None   # no per-scene cloud metadata
    assert callable(P.SENSORS["landsat"].collection)          # custom harmonizing builder
    assert P.INDICES["lst"].sensors == frozenset({"landsat"})
    for name in ("ndvi", "evi", "ndwi", "ndmi"):
        assert P.INDICES[name].sensors == _REFL_INDICES


def test_native_scale_m_per_product():
    assert P.native_scale_m("landsat", "lst") == 100       # TIRS
    assert P.native_scale_m("landsat", "lst_smw") == 100
    assert P.native_scale_m("landsat", "lst_sharp") == 30  # sharpened to the 30 m NIRv grid
    assert P.native_scale_m("landsat", "ndvi") == 30       # Landsat reflectance
    assert P.native_scale_m("sentinel2", "ndvi") == 10
    assert P.native_scale_m("sentinel2", "ndmi") == 20     # 20 m SWIR band
    assert P.native_scale_m("modis", "ndvi") == 500
    assert P.native_scale_m("modis_lst", "lst_modis") == 1000   # MOD11A1 1 km thermal


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


def test_lst_sharp_uses_distrad_fit_reduceresolution_and_residual():
    # DisTrad: a per-scene fit (linearFit) at the coarse (reduceResolution) grid, not
    # a hardcoded slope; INDEX band + time_start preserved. (Conservation of the math
    # is unit-tested in test_imaging.test_distrad_sharpen_is_conservative.)
    log, reducers = [], []

    class _P:
        def __call__(self, *a, **k): return self
        def __getattr__(self, name):
            def m(*a, **k): log.append((name, a)); return self
            return m
    p = _P()
    ee = types.SimpleNamespace(
        Reducer=types.SimpleNamespace(mean=lambda: reducers.append("mean") or "MEAN",
                                      linearFit=lambda: reducers.append("linearFit") or "FIT"),
        Number=lambda x: p)
    sensor = types.SimpleNamespace(reflectance=lambda img, ee_module=None: p)
    out = P.INDICES["lst_sharp"].compute(sensor, p, ee_module=ee)
    names = [n for n, _ in log]
    assert "reduceResolution" in names and "reduceRegion" in names   # fit at the coarse grid
    assert "linearFit" in reducers                                    # fitted slope, not a constant
    assert ("rename", ("INDEX",)) in log
    assert any(n == "set" and a and a[0] == "system:time_start" for n, a in log)
    assert out is p


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


def test_get_product_modis_lst():
    sensor, index = P.get_product("modis_lst", "lst_modis")
    assert sensor.name == "modis_lst" and index.name == "lst_modis"


def test_lst_modis_kelvin_to_celsius_and_keeps_time():
    rec = {}
    class FakeResult:
        def rename(self, n): rec["rename"] = n; return self
        def set(self, k, v): rec["set"] = (k, v); return "lst_band"
    class FakeBand:
        def multiply(self, v): rec["multiply"] = v; return self
        def subtract(self, v): rec["subtract"] = v; return FakeResult()
    class FakeImg:
        def select(self, b): rec["select"] = b; return FakeBand()
        def get(self, k): rec["get"] = k; return "TS"
    out = P.INDICES["lst_modis"].compute(None, FakeImg(), ee_module=None)
    assert rec["select"] == "LST_Day_1km"
    assert rec["multiply"] == 0.02 and rec["subtract"] == 273.15   # scale then K->C
    assert rec["rename"] == "INDEX" and rec["set"] == ("system:time_start", "TS")
    assert out == "lst_band"


def test_modis_lst_mask_clouds_keeps_good_quality_qc():
    rec = {}
    class FakeQC:
        def bitwiseAnd(self, m): rec["and"] = m; return self
        def lte(self, v): rec["lte"] = v; return "GOODMASK"
    class FakeImg:
        def select(self, b): rec["select"] = b; return FakeQC()
        def updateMask(self, m): rec["mask"] = m; return "masked"
    out = P.SENSORS["modis_lst"].mask_clouds(FakeImg())
    assert rec["select"] == "QC_Day" and rec["and"] == 3 and rec["lte"] == 1
    assert out == "masked"


def test_modis_lst_cloud_band_flags_missing_lst():
    rec = {}
    class FakeMaskChain:
        def Not(self): rec["not"] = True; return self
        def rename(self, n): rec["rename"] = n; return "cloud"
    class FakeBand:
        def mask(self): rec["mask"] = True; return FakeMaskChain()
    class FakeImg:
        def select(self, b): rec["select"] = b; return FakeBand()
    out = P.SENSORS["modis_lst"].cloud_band(FakeImg())
    assert rec["select"] == "LST_Day_1km" and rec["mask"] and rec["not"]
    assert rec["rename"] == "cloud" and out == "cloud"


def test_modis_lst_has_no_reflectance():
    with pytest.raises(NotImplementedError, match="thermal"):
        P.SENSORS["modis_lst"].reflectance("img")


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


def test_landsat_collection_harmonizes_l4_to_l9():
    loaded, selects = [], []
    class FakeColl:
        def __init__(self, cid): self.cid = cid
        def select(self, src, dst): selects.append((self.cid, tuple(src), tuple(dst))); return self
        def map(self, fn): return self          # mission tagging — no-op for this fake
        def merge(self, other): return self
    ee = types.SimpleNamespace(ImageCollection=lambda cid: (loaded.append(cid) or FakeColl(cid)))
    P.SENSORS["landsat"].collection(ee_module=ee)
    # all five Landsat missions loaded
    assert loaded == [
        "LANDSAT/LT04/C02/T1_L2", "LANDSAT/LT05/C02/T1_L2", "LANDSAT/LE07/C02/T1_L2",
        "LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"]
    dst_sets = {s[2] for s in selects}
    # every mission is renamed to the SAME canonical band set
    assert dst_sets == {("blue", "green", "red", "nir", "swir1", "swir2", "thermal", "QA_PIXEL")}
    by_id = {s[0]: s[1] for s in selects}
    # TM/ETM+ thermal is ST_B6, red=SR_B3, nir=SR_B4; OLI thermal is ST_B10, red=SR_B4, nir=SR_B5
    assert by_id["LANDSAT/LT05/C02/T1_L2"] == (
        "SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B7", "ST_B6", "QA_PIXEL")
    assert by_id["LANDSAT/LC08/C02/T1_L2"] == (
        "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7", "ST_B10", "QA_PIXEL")


def test_s2_and_modis_collection_merge():
    loaded = []
    class FakeColl:
        def __init__(self, cid): self.cid = cid
        def merge(self, other): return self
    ee = types.SimpleNamespace(ImageCollection=lambda cid: (loaded.append(cid) or FakeColl(cid)))
    P.SENSORS["sentinel2"].collection(ee_module=ee)
    P.SENSORS["modis"].collection(ee_module=ee)
    assert loaded == ["COPERNICUS/S2_SR_HARMONIZED", "MODIS/061/MOD09A1"]


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


def test_landsat_reflectance_selects_canonical_bands_and_scales():
    # Landsat is harmonized to canonical band names at collection build, so
    # reflectance just selects the aliases and scales.
    rec = {}
    class FakeImg:
        def select(self, names): rec["select"] = tuple(names); return self
        def multiply(self, v): rec["multiply"] = v; return self
        def add(self, v): rec["add"] = v; return self
    P.SENSORS["landsat"].reflectance(FakeImg())
    assert rec["select"] == ("blue", "green", "red", "nir", "swir1", "swir2")
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
    assert rec["select"] == "thermal"   # canonical harmonized thermal band (ST_B6/ST_B10)
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


def test_every_index_has_a_plain_language_display_name():
    # The frame header names the product in words a non-specialist reads; an index
    # added later without one would silently fall back to a bare acronym.
    import gee_animation.products as P
    for name, idx in P.INDICES.items():
        assert idx.display_name, f"{name} has no display_name"
        assert idx.display_name[0].isupper(), f"{name}: {idx.display_name!r}"
    assert P.INDICES["ndvi"].display_name == "Vegetation greenness (NDVI)"
    assert P.INDICES["rgb"].display_name == "True colour"
    assert P.INDICES["lst_modis"].display_name == "Land surface temperature (MODIS)"
