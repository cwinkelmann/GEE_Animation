import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gee_animation import inventory as I


def _ms(day: str) -> int:
    """Epoch milliseconds (UTC) for a YYYY-MM-DD date."""
    return int(datetime.strptime(day, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


class FakeArray:
    """Fakes an ee.List/ee.ComputedObject: a value that isn't resolved until the
    enclosing ee.Dictionary's getInfo() runs."""
    def __init__(self, values):
        self.values = values


class FakeGetInfoDict:
    """Fakes ee.Dictionary({...}): wraps several lazy arrays and resolves ALL of
    them in one getInfo() call — the mechanism scene_inventory relies on to issue
    exactly one round trip regardless of how many properties it needs."""
    def __init__(self, mapping, calls):
        self._mapping = mapping
        self._calls = calls

    def getInfo(self):
        self._calls.append("getInfo")
        return {k: v.values for k, v in self._mapping.items()}


class FakeSceneCollection:
    """Fakes an ImageCollection with EE's REAL aggregate_array semantics: a scene
    whose property is null is DROPPED from the returned array, it does not come
    back as a None element. That drop is what silently misaligned the arrays
    against the timestamps, so the fake must reproduce it rather than pad."""
    def __init__(self, scenes):
        self._scenes = scenes

    def aggregate_array(self, prop):
        return FakeArray([s[prop] for s in self._scenes if s.get(prop) is not None])

    def filter(self, notnull_props):
        return FakeSceneCollection([s for s in self._scenes
                                    if all(s.get(p) is not None for p in notnull_props)])


class FakeCollection(FakeSceneCollection):
    """Back-compat shim for the {ee-property-name: values} tables the tests are
    written against: turns them into per-scene dicts so they exercise the same
    null-dropping aggregate_array as the real thing. A value absent from a
    (deliberately short) column simply means that scene does not carry it."""
    def __init__(self, by_prop):
        times = by_prop["system:time_start"]
        scenes = []
        for i, t in enumerate(times):
            scene = {"system:index": f"s{i}", "system:time_start": t}
            for prop, values in by_prop.items():
                if prop != "system:time_start" and i < len(values):
                    scene[prop] = values[i]
            scenes.append(scene)
        super().__init__(scenes)


def _fake_ee(calls):
    return types.SimpleNamespace(
        Dictionary=lambda mapping: FakeGetInfoDict(mapping, calls),
        Filter=types.SimpleNamespace(notNull=lambda props: props))


class FakeSensor:
    def __init__(self, name, scene_cloud_property):
        self.name = name
        self.scene_cloud_property = scene_cloud_property


def _cfg(**over):
    base = dict(name="t", sensor="sentinel2", index="ndvi", cadence="monthly",
                start="2022-01-01", end="2022-02-01",
                max_cloud_percent=60, region_max_cloud_percent=10, out_dir="out")
    base.update(over)
    return types.SimpleNamespace(**base)


def test_scene_inventory_marks_rejected_scenes_with_reason(monkeypatch):
    # One clear scene (usable) and one cloudy scene (region cloud over threshold).
    sensor = FakeSensor("sentinel2", "CLOUDY_PIXEL_PERCENTAGE")
    monkeypatch.setattr(I, "get_product", lambda s, i: (sensor, None))
    coll = FakeCollection({
        "system:time_start": [_ms("2022-01-05"), _ms("2022-01-20")],
        "region_cloud_fraction": [0.02, 0.34],
        "CLOUDY_PIXEL_PERCENTAGE": [5.0, 12.0],
    })
    calls = []
    records = I.scene_inventory(_cfg(), "FRAME", "REGION",
                                build=lambda cfg, f, r, apply_cloud_filters=True, ee_module=None: coll,
                                ee_module=_fake_ee(calls))
    assert [r.usable for r in records] == [True, False]
    assert records[0].reason == ""
    assert records[1].reason == "region cloud 34% >= 10%"
    assert [r.period_label for r in records] == ["2022-01", "2022-01"]
    assert records[1].region_cloud_pct == 34.0


def test_scene_inventory_reports_scene_cloud_rejection(monkeypatch):
    sensor = FakeSensor("sentinel2", "CLOUDY_PIXEL_PERCENTAGE")
    monkeypatch.setattr(I, "get_product", lambda s, i: (sensor, None))
    coll = FakeCollection({
        "system:time_start": [_ms("2022-01-05")],
        "region_cloud_fraction": [0.01],
        "CLOUDY_PIXEL_PERCENTAGE": [72.0],
    })
    records = I.scene_inventory(_cfg(max_cloud_percent=60), "FRAME", "REGION",
                                build=lambda cfg, f, r, apply_cloud_filters=True, ee_module=None: coll,
                                ee_module=_fake_ee([]))
    assert records[0].usable is False
    assert records[0].reason == "scene cloud 72% > 60%"


def test_scene_inventory_rejects_region_cloud_exactly_at_threshold(monkeypatch):
    # collection.build's real filter is Filter.lt("region_cloud_fraction", threshold)
    # -- KEEPS the scene only when strictly below threshold, so a scene sitting
    # exactly ON the threshold (region_max_cloud_percent=10 -> raw fraction 0.10) is
    # dropped by the real pipeline. A strict `>` comparison here would wrongly call
    # it usable; this pins the `>=` fix.
    sensor = FakeSensor("sentinel2", "CLOUDY_PIXEL_PERCENTAGE")
    monkeypatch.setattr(I, "get_product", lambda s, i: (sensor, None))
    coll = FakeCollection({
        "system:time_start": [_ms("2022-01-05")],
        "region_cloud_fraction": [0.10],
        "CLOUDY_PIXEL_PERCENTAGE": [5.0],
    })
    records = I.scene_inventory(_cfg(region_max_cloud_percent=10), "FRAME", "REGION",
                                build=lambda cfg, f, r, apply_cloud_filters=True, ee_module=None: coll,
                                ee_module=_fake_ee([]))
    assert records[0].usable is False
    assert records[0].reason == "region cloud 10% >= 10%"


def test_scene_inventory_judges_raw_region_fraction_not_the_rounded_display_value(monkeypatch):
    # Both fractions display-round to "10.0%" at one decimal, but only one of them is
    # actually >= the 0.10 threshold in the RAW value the real filter sees. Judging
    # against the rounded display value (rather than the raw fraction) would get one
    # of these two backwards.
    sensor = FakeSensor("sentinel2", "CLOUDY_PIXEL_PERCENTAGE")
    monkeypatch.setattr(I, "get_product", lambda s, i: (sensor, None))
    coll = FakeCollection({
        "system:time_start": [_ms("2022-01-05"), _ms("2022-01-06")],
        "region_cloud_fraction": [0.0996, 0.1004],   # both round to 10.0% for display
        "CLOUDY_PIXEL_PERCENTAGE": [5.0, 5.0],
    })
    records = I.scene_inventory(_cfg(region_max_cloud_percent=10), "FRAME", "REGION",
                                build=lambda cfg, f, r, apply_cloud_filters=True, ee_module=None: coll,
                                ee_module=_fake_ee([]))
    assert [r.region_cloud_pct for r in records] == [10.0, 10.0]   # display value ties
    assert [r.usable for r in records] == [True, False]            # raw value decides


def test_scene_inventory_refuses_an_entirely_null_region_cloud_column(monkeypatch):
    # A scene fully masked over the region (reduceRegion finds no valid pixel) comes
    # back with a null region_cloud_fraction, which EE DROPS from aggregate_array.
    # When that is true of every scene the column arrives empty, which is also what
    # an upstream property drop looks like -- the two are indistinguishable client
    # side, so scene_inventory must fail loudly rather than report a whole inventory
    # as "cloud fraction unavailable". The mixed case (some scenes null, some not) is
    # resolvable and IS reported per scene -- see the id-join test below.
    sensor = FakeSensor("sentinel2", "CLOUDY_PIXEL_PERCENTAGE")
    monkeypatch.setattr(I, "get_product", lambda s, i: (sensor, None))
    coll = FakeCollection({
        "system:time_start": [_ms("2022-01-05")],
        "region_cloud_fraction": [None],
        "CLOUDY_PIXEL_PERCENTAGE": [5.0],
    })
    with pytest.raises(RuntimeError, match="region_cloud_fraction"):
        I.scene_inventory(_cfg(), "FRAME", "REGION",
                          build=lambda cfg, f, r, apply_cloud_filters=True, ee_module=None: coll,
                          ee_module=_fake_ee([]))


def test_scene_inventory_handles_sensor_without_cloud_property(monkeypatch):
    # MODIS-style sensor: scene_cloud_property is None -> no aggregate_array for it,
    # and every record's scene_cloud_pct column is None (not raised).
    sensor = FakeSensor("modis", None)
    monkeypatch.setattr(I, "get_product", lambda s, i: (sensor, None))
    coll = FakeCollection({
        "system:time_start": [_ms("2022-01-05"), _ms("2022-01-20")],
        "region_cloud_fraction": [0.02, 0.5],
    })
    records = I.scene_inventory(_cfg(sensor="modis"), "FRAME", "REGION",
                                build=lambda cfg, f, r, apply_cloud_filters=True, ee_module=None: coll,
                                ee_module=_fake_ee([]))
    assert [r.scene_cloud_pct for r in records] == [None, None]
    assert [r.mission for r in records] == ["modis", "modis"]
    assert [r.usable for r in records] == [True, False]
    assert records[1].reason == "region cloud 50% >= 10%"


def test_scene_inventory_issues_a_single_getinfo(monkeypatch):
    # Global constraint: exactly one getInfo() round trip, however many properties
    # (time, scene cloud, region cloud, mission) are pulled.
    sensor = FakeSensor("landsat", "CLOUD_COVER")
    monkeypatch.setattr(I, "get_product", lambda s, i: (sensor, None))
    coll = FakeCollection({
        "system:time_start": [_ms("2022-01-05"), _ms("2022-01-20"), _ms("2022-01-25")],
        "region_cloud_fraction": [0.02, 0.5, 0.01],
        "CLOUD_COVER": [5.0, 20.0, 90.0],
        "mission": ["L8", "L9", "L8"],
    })
    calls = []
    records = I.scene_inventory(_cfg(sensor="landsat"), "FRAME", "REGION",
                                build=lambda cfg, f, r, apply_cloud_filters=True, ee_module=None: coll,
                                ee_module=_fake_ee(calls))
    assert calls == ["getInfo"]
    assert [r.mission for r in records] == ["L8", "L9", "L8"]


def test_scene_inventory_raises_runtime_error_on_length_mismatch(monkeypatch):
    # Regression for the live-EE bug: index.compute derives a brand-new image and
    # drops every property except system:time_start, so aggregate_array on a
    # dropped property silently returns [] instead of one value per scene.
    # scene_inventory must fail loudly (RuntimeError naming the property), not
    # report every scene as "cloud fraction unavailable" -- which is what a
    # silently empty column would otherwise produce.
    sensor = FakeSensor("sentinel2", "CLOUDY_PIXEL_PERCENTAGE")
    monkeypatch.setattr(I, "get_product", lambda s, i: (sensor, None))
    coll = FakeCollection({
        "system:time_start": [_ms("2022-01-05"), _ms("2022-01-20")],
        "region_cloud_fraction": [],   # dropped -- length mismatch vs. 2 timestamps
        "CLOUDY_PIXEL_PERCENTAGE": [5.0, 12.0],
    })
    with pytest.raises(RuntimeError, match="region_cloud_fraction"):
        I.scene_inventory(_cfg(), "FRAME", "REGION",
                          build=lambda cfg, f, r, apply_cloud_filters=True, ee_module=None: coll,
                          ee_module=_fake_ee([]))


def test_write_inventory_writes_csv_and_logs_period_summary(tmp_path, monkeypatch, caplog):
    import logging
    sensor = FakeSensor("sentinel2", "CLOUDY_PIXEL_PERCENTAGE")
    monkeypatch.setattr(I, "get_product", lambda s, i: (sensor, None))
    coll = FakeCollection({
        "system:time_start": [_ms("2022-01-05"), _ms("2022-01-20")],
        "region_cloud_fraction": [0.02, 0.34],
        "CLOUDY_PIXEL_PERCENTAGE": [5.0, 12.0],
    })
    cfg = _cfg(out_dir=str(tmp_path))
    with caplog.at_level(logging.INFO):
        out = I.write_inventory(cfg, "FRAME", "REGION",
                                build=lambda cfg, f, r, apply_cloud_filters=True, ee_module=None: coll,
                                ee_module=_fake_ee([]))
    assert out == tmp_path / "t_inventory.csv"
    text = out.read_text()
    lines = text.strip().splitlines()
    assert lines[0] == "period_label,date,mission,scene_cloud_pct,region_cloud_pct,usable,reason"
    assert lines[1] == "2022-01,2022-01-05,sentinel2,5.0,2.0,True,"
    assert "region cloud 34%" in lines[2]
    assert "2022-01: 2 scenes, 1 usable" in caplog.text


def test_scene_inventory_aligns_columns_when_ee_drops_a_null_property(monkeypatch):
    # Regression for the live-EE failure on the full Sentinel-2 archive: two
    # edge-of-swath scenes had no valid pixel over the AOI, so reduceRegion
    # returned null and aggregate_array returned 1390 fractions for 1392 scenes.
    # Positional indexing cannot survive that -- every scene after the first drop
    # would be attributed the NEXT scene's cloud cover. Columns are therefore
    # joined on system:index, and the middle scene here must keep its own None.
    sensor = FakeSensor("sentinel2", "CLOUDY_PIXEL_PERCENTAGE")
    monkeypatch.setattr(I, "get_product", lambda s, i: (sensor, None))
    coll = FakeSceneCollection([
        {"system:index": "a", "system:time_start": _ms("2022-01-05"),
         "region_cloud_fraction": 0.02, "CLOUDY_PIXEL_PERCENTAGE": 5.0},
        {"system:index": "b", "system:time_start": _ms("2022-01-12"),
         "region_cloud_fraction": None, "CLOUDY_PIXEL_PERCENTAGE": 9.0},
        {"system:index": "c", "system:time_start": _ms("2022-01-20"),
         "region_cloud_fraction": 0.50, "CLOUDY_PIXEL_PERCENTAGE": 12.0},
    ])
    records = I.scene_inventory(
        _cfg(), "FRAME", "REGION",
        build=lambda cfg, f, r, apply_cloud_filters=True, ee_module=None: coll,
        ee_module=_fake_ee([]))

    assert [r.date for r in records] == ["2022-01-05", "2022-01-12", "2022-01-20"]
    assert [r.region_cloud_pct for r in records] == [2.0, None, 50.0]
    assert [r.usable for r in records] == [True, False, False]
    assert records[1].reason == "region cloud fraction unavailable (scene fully masked)"
