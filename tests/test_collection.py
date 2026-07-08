import types
from gee_animation import collection as C


class FakeSCL:
    def __init__(self, rec):
        self.rec = rec
    def neq(self, cls):
        self.rec["neq_args"].append(cls)
        return ("neq", cls)


class FakeMask:
    def __init__(self, rec):
        self.rec = rec
    def And(self, other):
        self.rec["and_count"] += 1
        return self


class FakeNDVI:
    def __init__(self, rec):
        self.rec = rec
    def rename(self, name):
        self.rec["rename"] = name
        return "ndvi_band"


class FakeImage:
    def __init__(self, rec):
        self.rec = rec
    def select(self, name):
        self.rec["selected"] = name
        return FakeSCL(self.rec)
    def updateMask(self, mask):
        self.rec["masked"] = True
        return "masked_image"
    def normalizedDifference(self, bands):
        self.rec["normdiff"] = bands
        return FakeNDVI(self.rec)
    def addBands(self, band):
        self.rec["addbands"] = band
        return "image_with_ndvi"


def _rec():
    return {"neq_args": [], "and_count": 0, "selected": None, "masked": False,
            "normdiff": None, "rename": None, "addbands": None}


def _fake_ee_for_mask(rec):
    return types.SimpleNamespace(
        Image=types.SimpleNamespace(constant=lambda v: FakeMask(rec)),
    )


def test_mask_s2_clouds_masks_expected_scl_classes():
    rec = _rec()
    result = C.mask_s2_clouds(FakeImage(rec), ee_module=_fake_ee_for_mask(rec))
    assert rec["selected"] == "SCL"
    assert rec["neq_args"] == [3, 8, 9, 10, 11]
    assert rec["and_count"] == 5
    assert rec["masked"] is True
    assert result == "masked_image"


def test_add_ndvi_adds_named_ndvi_band():
    rec = _rec()
    result = C.add_ndvi(FakeImage(rec))
    assert rec["normdiff"] == ["B8", "B4"]
    assert rec["rename"] == "NDVI"
    assert rec["addbands"] == "ndvi_band"
    assert result == "image_with_ndvi"


def test_build_applies_collection_id_filters_and_two_maps_in_order():
    calls = []

    class FakeColl:
        def filterDate(self, s, e):
            calls.append(("filterDate", s, e)); return self
        def filterBounds(self, g):
            calls.append(("filterBounds", g)); return self
        def filter(self, f):
            calls.append(("filter", f)); return self
        def map(self, fn):
            calls.append(("map",)); return self

    ee = types.SimpleNamespace(
        ImageCollection=lambda cid: (calls.append(("ImageCollection", cid)) or FakeColl()),
        Filter=types.SimpleNamespace(lte=lambda name, val: ("lte", name, val)),
    )
    cfg = types.SimpleNamespace(start="2022-01-01", end="2022-02-01", max_cloud_percent=60)
    C.build(cfg, "GEOM", ee_module=ee)
    assert calls[0] == ("ImageCollection", "COPERNICUS/S2_SR_HARMONIZED")
    assert ("filterDate", "2022-01-01", "2022-02-01") in calls
    assert ("filterBounds", "GEOM") in calls
    assert ("filter", ("lte", "CLOUDY_PIXEL_PERCENTAGE", 60)) in calls
    assert [c for c in calls if c[0] == "map"] == [("map",), ("map",)]
