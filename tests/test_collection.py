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


def test_add_region_cloud_fraction_sets_property():
    rec = {"selected": None, "remap": None, "reduce": None, "prop": None}

    class FakeReduced:
        def get(self, k): rec["reduce_get"] = k; return "FRAC"

    class FakeCloud:
        def rename(self, n): return self
        def reduceRegion(self, **kw): rec["reduce"] = kw; return FakeReduced()

    class FakeSCL:
        def remap(self, frm, to, default):
            rec["remap"] = (frm, to, default); return FakeCloud()

    class FakeImg:
        def select(self, n): rec["selected"] = n; return FakeSCL()
        def set(self, k, v): rec["prop"] = (k, v); return "img+frac"

    ee_fake = types.SimpleNamespace(Reducer=types.SimpleNamespace(mean=lambda: "MEAN"))
    out = C.add_region_cloud_fraction(FakeImg(), "REGION", 20, ee_module=ee_fake)
    assert rec["selected"] == "SCL"
    assert rec["remap"][0] == [3, 8, 9, 10, 11] and rec["remap"][2] == 0
    assert rec["reduce"]["geometry"] == "REGION" and rec["reduce"]["scale"] == 20
    assert rec["prop"] == ("region_cloud_fraction", "FRAC")
    assert out == "img+frac"


def test_build_filters_region_cloud_before_masking():
    calls = []

    class FakeColl:
        def filterDate(self, s, e): calls.append(("filterDate", s, e)); return self
        def filterBounds(self, g): calls.append(("filterBounds", g)); return self
        def filter(self, f): calls.append(("filter", f)); return self
        def map(self, fn): calls.append(("map",)); return self

    ee_fake = types.SimpleNamespace(
        ImageCollection=lambda cid: (calls.append(("ImageCollection", cid)) or FakeColl()),
        Filter=types.SimpleNamespace(
            lte=lambda name, val: ("lte", name, val),
            lt=lambda name, val: ("lt", name, val),
        ),
    )
    cfg = types.SimpleNamespace(
        start="2022-01-01", end="2022-02-01",
        max_cloud_percent=60, region_max_cloud_percent=10, scale=20,
    )
    C.build(cfg, "FRAME", "REGION", ee_module=ee_fake)
    names = [c[0] for c in calls]
    # ImageCollection, filterDate, filterBounds, filter(lte), map(frac), filter(lt), map(mask), map(ndvi)
    assert calls[0] == ("ImageCollection", "COPERNICUS/S2_SR_HARMONIZED")
    assert ("filterBounds", "FRAME") in calls
    assert ("filter", ("lte", "CLOUDY_PIXEL_PERCENTAGE", 60)) in calls
    assert ("filter", ("lt", "region_cloud_fraction", 0.1)) in calls
    # region-fraction filter (lt) must come before the mask map:
    assert names.count("map") == 3
    # order: the lt filter precedes the 2nd map (mask)
    idx_lt = next(i for i, c in enumerate(calls) if c == ("filter", ("lt", "region_cloud_fraction", 0.1)))
    idx_maps = [i for i, c in enumerate(calls) if c == ("map",)]
    assert idx_maps[0] < idx_lt < idx_maps[1] < idx_maps[2]
