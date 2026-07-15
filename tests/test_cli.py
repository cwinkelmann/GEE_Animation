import types
from pathlib import Path

import pytest

from gee_animation.cli import run, main


def test_run_orchestrates_pipeline(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "name: t\nproject: p\n"
        "aoi:\n  frame: {bbox: [0,0,1,1]}\n  region: {bbox: [0,0,1,1]}\n"
        "  region_max_cloud_percent: 10\n"
        'start: "2022-01-01"\nend: "2022-03-01"\n'
        "sensor: sentinel2\ncadence: monthly\nmax_cloud_percent: 60\n"
        'ndvi: {min: -0.2, max: 0.9, palette: ["#000000","#ffffff"]}\n'
        "render: {fps: 2, scale: 20, dimensions: 64}\n"
    )
    calls = []
    parsed = {"count": 0}

    def fake_parse(aoi):
        parsed["count"] += 1
        tag = "FRAME" if parsed["count"] == 1 else "REGION"
        calls.append(("parse", tag)); return tag

    deps = types.SimpleNamespace(
        init=lambda project: calls.append(("init", project)),
        parse=fake_parse,
        build=lambda cfg, frame, region: (calls.append(("build", frame, region)) or "COLL"),
        monthly_median=lambda coll, cfg: (calls.append(("monthly_median", coll)) or ["f1", "f2"]),
        anomaly=lambda frames, cfg, f, r, build: (calls.append(("anomaly", frames)) or frames),
        render=lambda frames, cfg, geometry=None: (calls.append(("render", frames, geometry)) or [tmp_path / "t.gif"]),
    )
    out = run(str(cfg_path), deps=deps)
    assert [c[0] for c in calls] == ["init", "parse", "parse", "build", "monthly_median", "anomaly", "render"]
    assert ("build", "FRAME", "REGION") in calls
    assert ("render", ["f1", "f2"], "FRAME") in calls
    assert out == [tmp_path / "t.gif"]


def test_run_debug_month_exports_scenes_and_skips_animation(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "name: t\nproject: p\n"
        "aoi:\n  frame: {bbox: [0,0,1,1]}\n  region: {bbox: [0,0,1,1]}\n"
        "  region_max_cloud_percent: 10\n"
        'start: "2022-01-01"\nend: "2022-03-01"\n'
        "sensor: sentinel2\ncadence: monthly\nmax_cloud_percent: 60\n"
        'ndvi: {min: -0.2, max: 0.9, palette: ["#000000","#ffffff"]}\n'
        "render: {fps: 2, scale: 20, dimensions: 64}\n"
        'debug_month: "2022-02"\n'
    )
    calls = []
    deps = types.SimpleNamespace(
        init=lambda project: calls.append(("init", project)),
        parse=lambda aoi: "FRAME" if not calls or calls[-1][0] != "parse" else "REGION",
        build=lambda *a: calls.append(("build",)),      # must NOT be called
        debug=lambda cfg, f, r, month: (calls.append(("debug", month)) or tmp_path / "debug"),
    )
    out = run(str(cfg_path), deps=deps)
    assert ("debug", "2022-02") in calls
    assert not any(c[0] == "build" for c in calls)      # animation path skipped
    assert out == [tmp_path / "debug"]


def test_run_raises_when_no_frames(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "name: t\nproject: p\n"
        "aoi:\n  frame: {bbox: [0,0,1,1]}\n  region: {bbox: [0,0,1,1]}\n"
        "  region_max_cloud_percent: 10\n"
        'start: "2022-01-01"\nend: "2022-03-01"\n'
        "sensor: sentinel2\ncadence: monthly\nmax_cloud_percent: 60\n"
        'ndvi: {min: -0.2, max: 0.9, palette: ["#000000","#ffffff"]}\n'
        "render: {fps: 2, scale: 20, dimensions: 64}\n"
    )
    deps = types.SimpleNamespace(
        init=lambda project: None,
        parse=lambda aoi: "GEOM",
        build=lambda cfg, frame, region: "COLL",
        monthly_median=lambda coll, cfg: [],
        render=lambda frames, cfg, geometry=None: [tmp_path / "t.gif"],
    )
    with pytest.raises(RuntimeError, match="No images"):
        run(str(cfg_path), deps=deps)


def test_main_returns_zero_on_success(tmp_path, monkeypatch):
    import gee_animation.cli as c
    monkeypatch.setattr(c, "run", lambda path, deps=None: [Path("out/x.gif")])
    rc = main(["--config", "whatever.yaml"])
    assert rc == 0


def test_main_returns_one_on_error(monkeypatch, capsys):
    import gee_animation.cli as c
    def boom(path, deps=None):
        raise RuntimeError("No images found for the given AOI/date range/cloud filter.")
    monkeypatch.setattr(c, "run", boom)
    rc = main(["--config", "whatever.yaml"])
    assert rc == 1
    assert "No images" in capsys.readouterr().err
