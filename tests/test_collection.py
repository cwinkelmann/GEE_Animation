import types
from gee_animation import collection as C


def test_add_region_cloud_fraction_uses_cloud_band_and_reduces():
    rec = {}
    class FakeReduced:
        def get(self, k): rec["get"] = k; return "FRAC"
    class FakeCloud:
        def reduceRegion(self, **kw): rec["reduce"] = kw; return FakeReduced()
    class FakeImg:
        def set(self, k, v): rec["set"] = (k, v); return "img+frac"
    def fake_cloud_band(image, ee_module=None):
        rec["cloud_band_called"] = True
        return FakeCloud()
    ee = types.SimpleNamespace(Reducer=types.SimpleNamespace(mean=lambda: "MEAN"))
    out = C.add_region_cloud_fraction(FakeImg(), "REGION", 20, fake_cloud_band, ee_module=ee)
    assert rec["cloud_band_called"] and rec["reduce"]["geometry"] == "REGION"
    assert rec["reduce"]["scale"] == 20 and rec["get"] == "cloud"
    assert rec["set"] == ("region_cloud_fraction", "FRAC") and out == "img+frac"


def test_build_pipeline_order_and_uses_sensor(monkeypatch):
    calls = []
    class FakeColl:
        def filterDate(self, s, e): calls.append(("filterDate", s, e)); return self
        def filterBounds(self, g): calls.append(("filterBounds", g)); return self
        def filter(self, f): calls.append(("filter", f)); return self
        def map(self, fn): calls.append(("map",)); return self
    class FakeSensor:
        name = "landsat"; scene_cloud_property = "CLOUD_COVER"
        def collection(self, ee_module=None): calls.append(("collection",)); return FakeColl()
        def cloud_band(self, image, ee_module=None): return image
        def mask_clouds(self, image, ee_module=None): return image
    class FakeIndex:
        def compute(self, sensor, image, ee_module=None): return image
    monkeypatch.setattr(C, "get_product", lambda s, i: (FakeSensor(), FakeIndex()))
    ee = types.SimpleNamespace(
        Filter=types.SimpleNamespace(
            lte=lambda name, val: ("lte", name, val),
            lt=lambda name, val: ("lt", name, val)))
    cfg = types.SimpleNamespace(sensor="landsat", index="lst",
                                start="2022-01-01", end="2022-02-01",
                                max_cloud_percent=60, region_max_cloud_percent=10, scale=20)
    C.build(cfg, "FRAME", "REGION", ee_module=ee)
    names = [c[0] for c in calls]
    assert names[0] == "collection"
    assert ("filterBounds", "FRAME") in calls
    assert ("filter", ("lte", "CLOUD_COVER", 60)) in calls        # sensor's coarse property
    assert ("filter", ("lt", "region_cloud_fraction", 0.1)) in calls
    idx_lt = next(i for i, c in enumerate(calls) if c == ("filter", ("lt", "region_cloud_fraction", 0.1)))
    idx_maps = [i for i, c in enumerate(calls) if c == ("map",)]
    # map order: region-fraction, mask, index -> fraction filter before the mask map
    assert idx_maps[0] < idx_lt < idx_maps[1] < idx_maps[2]
