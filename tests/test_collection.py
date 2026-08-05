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
    assert rec["reduce"]["reducer"] == "MEAN"
    assert rec["reduce"]["bestEffort"] is True
    assert rec["reduce"]["maxPixels"] == int(1e9)


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
            lt=lambda name, val: ("lt", name, val),
            inList=lambda prop, vals: ("inList", prop, vals)))
    cfg = types.SimpleNamespace(sensor="landsat", index="lst", missions=None,
                                start="2022-01-01", end="2022-02-01",
                                max_cloud_percent=60, region_max_cloud_percent=10, scale=20)
    C.build(cfg, "FRAME", "REGION", ee_module=ee)
    names = [c[0] for c in calls]
    assert names[0] == "collection"
    assert ("filterBounds", "FRAME") in calls
    assert ("filter", ("inList", "mission", ["L8", "L9"])) in calls  # thermal -> L8/L9 only
    assert ("filter", ("lte", "CLOUD_COVER", 60)) in calls        # sensor's coarse property
    assert ("filter", ("lt", "region_cloud_fraction", 0.1)) in calls
    idx_lt = next(i for i, c in enumerate(calls) if c == ("filter", ("lt", "region_cloud_fraction", 0.1)))
    idx_maps = [i for i, c in enumerate(calls) if c == ("map",)]
    # map order: region-fraction, mask, index -> fraction filter before the mask map
    assert idx_maps[0] < idx_lt < idx_maps[1] < idx_maps[2]


def test_effective_missions_defaults_thermal_to_l8_l9():
    def cfg(sensor, index, missions=None, start="2022-01-01"):
        return types.SimpleNamespace(sensor=sensor, index=index, missions=missions, start=start)
    # thermal indices default to L8/L9; reflectance/non-landsat get no filter
    assert C.effective_missions(cfg("landsat", "lst")) == ["L8", "L9"]
    assert C.effective_missions(cfg("landsat", "lst_smw")) == ["L8", "L9"]
    assert C.effective_missions(cfg("landsat", "lst_sharp")) == ["L8", "L9"]
    assert C.effective_missions(cfg("landsat", "ndvi")) is None
    assert C.effective_missions(cfg("sentinel2", "ndvi")) is None
    # explicit missions always win, even for a thermal index
    assert C.effective_missions(cfg("landsat", "lst", missions=["L7", "L8"])) == ["L7", "L8"]


def test_effective_missions_warns_when_thermal_default_predates_landsat8(caplog):
    import logging
    cfg = types.SimpleNamespace(sensor="landsat", index="lst", missions=None, start="2005-06-01")
    with caplog.at_level(logging.WARNING):
        assert C.effective_missions(cfg) == ["L8", "L9"]
    assert "predates Landsat 8" in caplog.text


def test_build_skips_coarse_filter_when_no_scene_cloud_property(monkeypatch):
    # MODIS has no per-scene cloud metadata (scene_cloud_property is None):
    # the coarse Filter.lte must be skipped; the region fraction filter still runs.
    calls = []
    class FakeColl:
        def filterDate(self, s, e): calls.append(("filterDate",)); return self
        def filterBounds(self, g): calls.append(("filterBounds",)); return self
        def filter(self, f): calls.append(("filter", f)); return self
        def map(self, fn): calls.append(("map",)); return self
    class FakeSensor:
        name = "modis"; scene_cloud_property = None
        def collection(self, ee_module=None): return FakeColl()
        def cloud_band(self, image, ee_module=None): return image
        def mask_clouds(self, image, ee_module=None): return image
    class FakeIndex:
        def compute(self, sensor, image, ee_module=None): return image
    monkeypatch.setattr(C, "get_product", lambda s, i: (FakeSensor(), FakeIndex()))
    ee = types.SimpleNamespace(
        Filter=types.SimpleNamespace(
            lte=lambda name, val: ("lte", name, val),
            lt=lambda name, val: ("lt", name, val)))
    cfg = types.SimpleNamespace(sensor="modis", index="ndmi",
                                start="2022-01-01", end="2022-02-01",
                                max_cloud_percent=60, region_max_cloud_percent=10, scale=500)
    C.build(cfg, "FRAME", "REGION", ee_module=ee)
    filters = [c for c in calls if c[0] == "filter"]
    # only the region-fraction lt filter — no coarse lte filter
    assert filters == [("filter", ("lt", "region_cloud_fraction", 0.1))]


def test_build_without_cloud_filters_keeps_all_scenes(monkeypatch):
    # apply_cloud_filters=False (used by inventory.py to see the unfiltered
    # candidate set): neither the coarse scene-level filter nor the in-region
    # fraction filter should run, but region_cloud_fraction must still be computed
    # (the inventory needs it to judge each scene).
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
            lt=lambda name, val: ("lt", name, val),
            inList=lambda prop, vals: ("inList", prop, vals)))
    cfg = types.SimpleNamespace(sensor="landsat", index="lst", missions=None,
                                start="2022-01-01", end="2022-02-01",
                                max_cloud_percent=60, region_max_cloud_percent=10, scale=20)
    C.build(cfg, "FRAME", "REGION", apply_cloud_filters=False, ee_module=ee)
    filters = [c for c in calls if c[0] == "filter"]
    # only the mission filter survives — no coarse lte, no region-fraction lt
    assert filters == [("filter", ("inList", "mission", ["L8", "L9"]))]
    # region_cloud_fraction is still computed (add_region_cloud_fraction's map runs)
    assert ("map",) in calls


def test_build_widens_the_date_range_to_the_pooled_years(monkeypatch):
    # Cross-year "best month" mode: the candidate scenes live outside [start, end),
    # so build must filterDate over every pooled year (compositing then buckets them
    # by calendar period). Without pool_years the range is untouched.
    def _run(pool_years):
        calls = []
        class FakeColl:
            def filterDate(self, s, e): calls.append(("filterDate", s, e)); return self
            def filterBounds(self, g): return self
            def filter(self, f): return self
            def map(self, fn): return self
        class FakeSensor:
            name = "sentinel2"; scene_cloud_property = None
            def collection(self, ee_module=None): return FakeColl()
            def cloud_band(self, image, ee_module=None): return image
            def mask_clouds(self, image, ee_module=None): return image
        class FakeIndex:
            def compute(self, sensor, image, ee_module=None): return image
        monkeypatch.setattr(C, "get_product", lambda s, i: (FakeSensor(), FakeIndex()))
        ee = types.SimpleNamespace(Filter=types.SimpleNamespace(
            lt=lambda name, val: ("lt", name, val)))
        cfg = types.SimpleNamespace(sensor="sentinel2", index="ndvi", missions=None,
                                    start="2022-05-01", end="2022-08-01",
                                    max_cloud_percent=60, region_max_cloud_percent=10,
                                    scale=20, pool_years=pool_years)
        C.build(cfg, "FRAME", "REGION", ee_module=ee)
        # cfg itself must not be mutated — callers keep using the nominal range
        assert (cfg.start, cfg.end) == ("2022-05-01", "2022-08-01")
        return calls

    assert _run([2019, 2024]) == [("filterDate", "2019-01-01", "2025-01-01")]
    assert _run(None) == [("filterDate", "2022-05-01", "2022-08-01")]
