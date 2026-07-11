"""Unit tests for the Ermida et al. (2020) SMW LST port.

The heavy numerical correctness is covered by the opt-in live test
(`GEE_INTEGRATION`); here we guard the transcribed coefficient tables, the
satellite wiring, and that compute()/build() are plumbed correctly — all
network-free with a permissive fake Earth Engine.
"""
import types

import pytest

from gee_animation import collection as C
from gee_animation import smw_lst
from gee_animation.products import INDEX_BAND, get_product


# --- Reference-value guards (pure Python; catch transcription typos) ----------

def test_coefficient_tables_are_10_bins_for_every_satellite():
    for table in (smw_lst._SMW_A, smw_lst._SMW_B, smw_lst._SMW_C):
        assert set(table) == {"L4", "L5", "L7", "L8", "L9"}
        assert all(len(v) == 10 for v in table.values())


def test_coefficients_match_ermida_reference_values():
    # spot-check first/last bins against SMW_coefficients.js
    assert smw_lst._SMW_A["L4"][0] == 0.9755
    assert smw_lst._SMW_C["L4"][9] == 279.9854
    assert smw_lst._SMW_A["L8"][0] == 0.9751
    assert smw_lst._SMW_B["L8"][7] == -451.0790
    assert smw_lst._SMW_C["L9"][0] == 213.0526
    assert smw_lst._SMW_B["L5"][9] == -600.7079


def test_emissivity_convolution_coeffs_match_reference():
    # compute_emissivity.js: L8 and L9 share the OLI values
    assert smw_lst._EM_COEF["L5"] == (-0.0723, 1.0521, 0.0195)
    assert smw_lst._EM_COEF["L8"] == smw_lst._EM_COEF["L9"] == (0.6820, 0.2578, 0.0584)


def test_satellite_sources_cover_l4_through_l9_with_correct_tir_band():
    sats = {s[0]: (s[2], s[4]) for s in smw_lst._SMW_SATS}   # SATID -> (toa_id, tir)
    assert set(sats) == {"L4", "L5", "L7", "L8", "L9"}
    assert sats["L7"][1] == "B6_VCID_1"          # ETM+ thermal
    assert sats["L8"][1] == "B10" and sats["L9"][1] == "B10"
    assert sats["L4"][1] == "B6" and sats["L5"][1] == "B6"
    assert sats["L8"][0] == "LANDSAT/LC08/C02/T1_TOA"


# --- Permissive fake Earth Engine: every call returns a chainable recorder ----

class _P:
    """Chainable recorder: any attribute/call returns self and logs the call."""
    def __init__(self, log):
        self._log = log

    def __call__(self, *a, **k):
        return self

    def __getattr__(self, name):
        def method(*a, **k):
            self._log.append((name, a))
            return self
        return method


def _fake_ee(log):
    p = _P(log)
    # constructors used by smw_lst; ImageCollection records its id argument
    def image_collection(arg=None, *a, **k):
        log.append(("ImageCollection", arg))
        return p
    ee = types.SimpleNamespace(
        Image=p, ImageCollection=image_collection, Number=p, String=p, List=p,
        Dictionary=p, Date=p, Filter=p, Join=p, Algorithms=p, Reducer=p)
    return ee, p


def test_join_bt_matches_toa_on_system_index_and_adds_bt():
    # Guards the L2<->TOA join contract (P1-3): inner join on system:index via
    # saveFirst, with the TOA thermal band renamed to "bt". Changing this to a
    # position/order-based combine would silently diverge the scene sets.
    rec = {}

    class FakeColl:
        def __init__(self, tag): self.tag = tag
        def select(self, bands, names=None): rec["toa_select"] = (tuple(bands), names); return self
        def map(self, fn): rec["mapped"] = True; return self

    class FakeJoin:
        def apply(self, primary, secondary, filt): rec["apply_filter"] = filt; return "JOINED"

    ee = types.SimpleNamespace(
        Filter=types.SimpleNamespace(equals=lambda **k: ("equals", k)),
        Join=types.SimpleNamespace(
            saveFirst=lambda key: (rec.__setitem__("save_key", key) or FakeJoin())),
        Image=lambda x: x,
        ImageCollection=lambda x: FakeColl("joined"))
    out = smw_lst._join_bt(FakeColl("l2"), FakeColl("toa"), "B10", ee)
    assert rec["save_key"] == "toa"
    assert rec["apply_filter"] == ("equals", {"leftField": "system:index",
                                              "rightField": "system:index"})
    assert rec["toa_select"] == (("B10",), ["bt"])   # TOA thermal -> "bt"
    assert rec["mapped"] is True                      # bt added per image


def test_landsat_collection_references_all_five_missions_and_merges():
    log = []
    ee, _ = _fake_ee(log)
    cfg = types.SimpleNamespace(start="2022-05-01", end="2022-09-01")
    smw_lst.landsat_collection(cfg, "FRAME", ee_module=ee)
    ic_ids = [a for (name, a) in log if name == "ImageCollection" and isinstance(a, str)]
    # 5 L2 + 5 TOA collections referenced
    assert "LANDSAT/LC08/C02/T1_L2" in ic_ids and "LANDSAT/LC08/C02/T1_TOA" in ic_ids
    assert sum("T1_L2" in i for i in ic_ids) == 5
    assert sum("T1_TOA" in i for i in ic_ids) == 5
    # 5 sub-collections merged -> 4 merge() calls
    assert sum(1 for (name, _a) in log if name == "merge") == 4


def test_compute_outputs_index_band_and_preserves_time_start():
    log = []
    ee, _ = _fake_ee(log)
    sensor = types.SimpleNamespace(reflectance=lambda img, ee_module: img)
    image = _P(log)
    smw_lst.compute(sensor, image, ee_module=ee)
    calls = [name for (name, _a) in log]
    assert ("rename", (INDEX_BAND,)) in log            # final band is INDEX
    assert any(name == "set" and a and a[0] == "system:time_start" for (name, a) in log)
    assert "normalizedDifference" in calls             # NDVI computed for FVC
    assert "updateMask" in calls                       # TPW no-data masked


# --- build() routing ----------------------------------------------------------

def test_build_uses_index_build_collection_hook(monkeypatch):
    """When an index provides build_collection, build() must use it (not sensor.collection)."""
    used = {"filters": []}

    class FakeColl:
        def filter(self, f): used["filters"].append(("filter", f)); return self
        def map(self, fn): return self

    def fake_build_collection(cfg, frame_geom, ee_module):
        used["hook"] = (cfg.index, frame_geom)
        return FakeColl()

    class FakeSensor:
        name = "landsat"; scene_cloud_property = "CLOUD_COVER"
        def collection(self, ee_module=None): used["sensor_collection"] = True; return FakeColl()
        def cloud_band(self, image, ee_module=None): return image
        def mask_clouds(self, image, ee_module=None): return image

    fake_index = types.SimpleNamespace(
        compute=lambda sensor, image, ee_module=None: image,
        build_collection=fake_build_collection)
    monkeypatch.setattr(C, "get_product", lambda s, i: (FakeSensor(), fake_index))
    ee = types.SimpleNamespace(Filter=types.SimpleNamespace(
        lte=lambda n, v: ("lte", n, v), lt=lambda n, v: ("lt", n, v),
        inList=lambda p, v: ("inList", p, v)))
    cfg = types.SimpleNamespace(sensor="landsat", index="lst_smw", missions=None,
                                start="2022-05-01", end="2022-09-01",
                                max_cloud_percent=60, region_max_cloud_percent=10, scale=30)
    C.build(cfg, "FRAME", "REGION", ee_module=ee)
    assert used["hook"] == ("lst_smw", "FRAME")        # hook used with frame geometry
    assert "sensor_collection" not in used             # default path skipped
    # thermal index also gets the L8/L9 mission filter on the hook's collection
    assert ("filter", ("inList", "mission", ["L8", "L9"])) in used["filters"]


def test_lst_smw_registered_landsat_only():
    sensor, index = get_product("landsat", "lst_smw")
    assert index.name == "lst_smw" and index.build_collection is not None
    with pytest.raises(ValueError, match="not available"):
        get_product("sentinel2", "lst_smw")
