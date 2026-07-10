import json
import types
import zipfile

import pytest

from gee_animation import gui


def _write_geojson(tmp_path):
    p = tmp_path / "region.geojson"
    p.write_text(json.dumps({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}))
    return p


def _fake_deps(tmp_path, captured, frames=None):
    if frames is None:
        frames = [types.SimpleNamespace(label="2022-05"), types.SimpleNamespace(label="2022-06")]

    def frame_bbox(region, m):
        captured["buffer"] = m
        return [0.0, 0.0, 2.0, 2.0]

    return types.SimpleNamespace(
        init=lambda project: captured.__setitem__("project", project),
        parse=lambda a: ("geom", tuple(sorted(a))),
        frame_bbox=frame_bbox,
        build=lambda cfg, f, r: (captured.update(cfg=cfg, frame=f, region=r) or "COLL"),
        monthly_median=lambda coll, cfg: frames,
        render=lambda frames_, cfg, geometry=None: [tmp_path / "o.mp4", tmp_path / "o.gif"],
        timeseries=lambda frames_, region, scale: [(f.label, 0.5) for f in frames_],
    )


def test_default_aoi_is_the_shipped_wne_geojson():
    from pathlib import Path
    assert gui.DEFAULT_AOI is not None
    assert gui.DEFAULT_AOI.replace("\\", "/").endswith("docs/aoi/wne/wne.geojson")
    assert Path(gui.DEFAULT_AOI).exists()


def test_indices_for_filters_by_sensor():
    assert "lst" in gui.indices_for("landsat") and "ecostress" in gui.indices_for("landsat")
    assert "lst" not in gui.indices_for("sentinel2")
    assert set(gui.indices_for("modis")) == {"ndvi", "evi", "ndwi", "ndmi"}


def test_region_aoi_from_upload_geojson(tmp_path):
    p = _write_geojson(tmp_path)
    assert gui._region_aoi_from_upload(str(p)) == {"geojson": str(p)}


def test_region_aoi_from_upload_zipped_shapefile(tmp_path):
    (tmp_path / "x.shp").write_text("")
    (tmp_path / "x.dbf").write_text("")
    z = tmp_path / "aoi.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(tmp_path / "x.shp", "x.shp")
        zf.write(tmp_path / "x.dbf", "x.dbf")
    out = gui._region_aoi_from_upload(str(z))
    assert "shapefile" in out and out["shapefile"].endswith("x.shp")


def test_region_aoi_from_upload_rejects_unknown(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("nope")
    with pytest.raises(ValueError, match="unsupported"):
        gui._region_aoi_from_upload(str(p))


def test_run_animation_builds_config_and_threads_geometry(tmp_path):
    aoi = _write_geojson(tmp_path)
    captured = {}
    mp4, gif, status, series = gui.run_animation(
        aoi_path=str(aoi), buffer_m=1500, sensor="sentinel2", index="ndvi",
        start="2022-05-01", end="2022-07-01", region_max_cloud_percent=15,
        fps=5, dimensions=512, out_dir=str(tmp_path), deps=_fake_deps(tmp_path, captured))
    cfg = captured["cfg"]
    assert cfg.sensor == "sentinel2" and cfg.index == "ndvi"
    assert cfg.frame_aoi == {"bbox": [0.0, 0.0, 2.0, 2.0]}     # from frame_bbox(region, buffer)
    assert cfg.region_aoi == {"geojson": str(aoi)}
    assert captured["buffer"] == 1500.0
    assert cfg.viz_min == -0.2 and cfg.viz_max == 0.9          # NDVI default viz
    assert cfg.scale == 30 and cfg.region_max_cloud_percent == 15
    assert mp4.endswith("o.mp4") and gif.endswith("o.gif")
    assert "Rendered 2 of 2 months" in status                 # May + June both rendered
    assert series == [("2022-05", 0.5), ("2022-06", 0.5)]     # region time-series


def test_run_animation_status_reports_dropped_months(tmp_path):
    # 4-month range but only 2 frames -> status flags the 2 dropped (cloud-filtered) months
    aoi = _write_geojson(tmp_path)
    frames = [types.SimpleNamespace(label="2022-05"), types.SimpleNamespace(label="2022-08")]
    _, _, status, _ = gui.run_animation(
        aoi_path=str(aoi), buffer_m=1000, sensor="sentinel2", index="ndvi",
        start="2022-05-01", end="2022-09-01", region_max_cloud_percent=10,
        out_dir=str(tmp_path), deps=_fake_deps(tmp_path, {}, frames=frames))
    assert "Rendered 2 of 4 months" in status
    assert "2 month(s) had no scene" in status and "10%" in status


def test_run_animation_uses_500m_scale_for_modis(tmp_path):
    aoi = _write_geojson(tmp_path)
    captured = {}
    gui.run_animation(aoi_path=str(aoi), buffer_m=1000, sensor="modis", index="ndmi",
                      start="2022-05-01", end="2022-07-01", out_dir=str(tmp_path),
                      deps=_fake_deps(tmp_path, captured))
    assert captured["cfg"].scale == 500


def test_run_animation_raises_on_no_frames(tmp_path):
    aoi = _write_geojson(tmp_path)
    deps = _fake_deps(tmp_path, {}, frames=[])
    with pytest.raises(RuntimeError, match="No imagery"):
        gui.run_animation(aoi_path=str(aoi), buffer_m=1000, sensor="sentinel2", index="ndvi",
                          start="2022-05-01", end="2022-07-01", out_dir=str(tmp_path), deps=deps)


def test_run_animation_rejects_unsupported_pair(tmp_path):
    aoi = _write_geojson(tmp_path)
    with pytest.raises(ValueError, match="not available"):
        gui.run_animation(aoi_path=str(aoi), buffer_m=1000, sensor="sentinel2", index="lst",
                          start="2022-05-01", end="2022-07-01", out_dir=str(tmp_path),
                          deps=_fake_deps(tmp_path, {}))


def test_run_animation_requires_aoi(tmp_path):
    with pytest.raises(ValueError, match="upload an AOI"):
        gui.run_animation(aoi_path=None, buffer_m=1000, sensor="sentinel2", index="ndvi",
                          start="2022-05-01", end="2022-07-01", deps=_fake_deps(tmp_path, {}))


def test_build_app_constructs():
    pytest.importorskip("gradio")
    app = gui.build_app()
    assert app is not None
