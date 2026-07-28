import types
from datetime import datetime, timezone
from pathlib import Path

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


class FakeCollection:
    """Fakes the ImageCollection surface scene_inventory uses: aggregate_array per
    property, keyed off of a {ee-property-name: values} dict."""
    def __init__(self, by_prop):
        self._by_prop = by_prop

    def aggregate_array(self, prop):
        return FakeArray(self._by_prop[prop])


def _fake_ee(calls):
    return types.SimpleNamespace(Dictionary=lambda mapping: FakeGetInfoDict(mapping, calls))


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


def test_scene_inventory_handles_missing_region_cloud_fraction(monkeypatch):
    # A scene fully masked over the region (reduceRegion finds no valid pixel) comes
    # back with region_cloud_fraction=None. Real EE Filter.lt against a null property
    # does not evaluate true, so the real pipeline excludes the scene -- the
    # inventory must report it as rejected, not silently usable.
    sensor = FakeSensor("sentinel2", "CLOUDY_PIXEL_PERCENTAGE")
    monkeypatch.setattr(I, "get_product", lambda s, i: (sensor, None))
    coll = FakeCollection({
        "system:time_start": [_ms("2022-01-05")],
        "region_cloud_fraction": [None],
        "CLOUDY_PIXEL_PERCENTAGE": [5.0],
    })
    records = I.scene_inventory(_cfg(), "FRAME", "REGION",
                                build=lambda cfg, f, r, apply_cloud_filters=True, ee_module=None: coll,
                                ee_module=_fake_ee([]))
    assert records[0].usable is False
    assert records[0].region_cloud_pct is None
    assert records[0].reason == "region cloud fraction unavailable (scene fully masked)"


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
