import types
from pathlib import Path
from gee_animation.cli import run, main


def test_run_orchestrates_pipeline(tmp_path, monkeypatch):
    # minimal valid config file
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "name: t\nproject: p\naoi: {bbox: [0,0,1,1]}\n"
        'start: "2022-01-01"\nend: "2022-03-01"\n'
        "sensor: sentinel2\ncadence: monthly\nmax_cloud_percent: 60\n"
        'ndvi: {min: -0.2, max: 0.9, palette: ["#000000","#ffffff"]}\n'
        "render: {fps: 2, scale: 20, dimensions: 64}\n"
    )
    calls = []
    deps = types.SimpleNamespace(
        init=lambda project, ee_module=None: calls.append(("init", project)),
        parse=lambda aoi, ee_module=None: ("geom", aoi),
        build=lambda cfg, geom, ee_module=None: ("coll", geom),
        monthly_median=lambda coll, cfg, ee_module=None: ["f1", "f2"],
        render=lambda frames, cfg, **k: [tmp_path / "t.gif"],
    )
    out = run(str(cfg_path), deps=deps)
    assert ("init", "p") in calls
    assert out == [tmp_path / "t.gif"]


def test_main_returns_zero_on_success(tmp_path, monkeypatch):
    import gee_animation.cli as c
    monkeypatch.setattr(c, "run", lambda path, deps=None: [Path("out/x.gif")])
    rc = main(["--config", "whatever.yaml"])
    assert rc == 0
