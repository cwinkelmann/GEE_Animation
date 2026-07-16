import types
from pathlib import Path

import pytest

from gee_animation import api


def _fake_deps(tmp_path, captured, frames=None):
    if frames is None:
        frames = [types.SimpleNamespace(label="2023-06"), types.SimpleNamespace(label="2023-07")]

    def render(frames_, cfg, geometry=None):
        pngs = []
        for f in frames_:
            p = tmp_path / f"{cfg.name}_{f.label}.png"
            p.write_bytes(b"x")
            pngs.append(p)
        (tmp_path / f"{cfg.name}.mp4").write_bytes(b"\x00\x00mp4")
        return [tmp_path / f"{cfg.name}.mp4", tmp_path / f"{cfg.name}.gif", *pngs]

    def render_chart(series, name, index, out_dir):
        p = tmp_path / f"{name}_chart.png"
        p.write_bytes(b"chart")
        return str(p)

    return types.SimpleNamespace(
        parse=lambda a: ("geom", tuple(sorted(a))),
        frame_bbox=lambda region, m: (captured.__setitem__("buffer", m) or [0.0, 0.0, 2.0, 2.0]),
        build=lambda cfg, f, r: captured.update(cfg=cfg) or "COLL",
        monthly_median=lambda coll, cfg: frames,
        anomaly=lambda frames_, cfg, f, r, build: frames_,
        metadata=lambda frames_, cfg, region: str(tmp_path / "metadata.db"),
        render=render,
        timeseries=lambda frames_, region, frame, scale: [(f.label, 0.7, 0.4) for f in frames_],
        render_chart=render_chart,
    )


def test_animate_sets_preset_and_returns_paths(tmp_path):
    captured = {}
    anim = api.animate([0, 0, 1, 1], sensor="landsat", index="lst",
                       start="2023-01-01", end="2023-12-01", out_dir=str(tmp_path),
                       deps=_fake_deps(tmp_path, captured))
    cfg = captured["cfg"]
    # the key fix: a preset is set so LST isn't rendered at its ~57 px native size
    assert cfg.preset == "1080p" and cfg.aspect == "match"
    assert cfg.sensor == "landsat" and cfg.index == "lst" and cfg.scale == 30
    assert anim.mp4.endswith("landsat_lst.mp4") and len(anim.frames) == 2
    assert anim.metadata_db.endswith("metadata.db")
    assert "2 frames" in anim.status and "1080p" in anim.status
    # the object now carries the inside/outside series + a chart path
    assert anim.series == [("2023-06", 0.7, 0.4), ("2023-07", 0.7, 0.4)]
    assert anim.chart.endswith("landsat_lst_chart.png")
    assert (tmp_path / "landsat_lst_series.csv").exists()   # series persisted alongside


def test_animate_composite_skips_chart(tmp_path):
    captured = {}
    anim = api.animate([0, 0, 1, 1], sensor="sentinel2", index="rgb",
                       start="2023-01-01", end="2023-06-01", out_dir=str(tmp_path),
                       deps=_fake_deps(tmp_path, captured))
    # rgb/cir have no INDEX band -> no series/chart
    assert anim.series == [] and anim.chart is None


def test_animation_metadata_reads_db(tmp_path):
    from gee_animation import metadata
    db = tmp_path / "metadata.db"
    metadata.write_db(db, {"name": "landsat_lst", "sensor": "landsat", "index": "lst"},
                      [("2023-06", 3, 0.1, 22.0), ("2023-07", 4, 0.0, 25.0)])
    anim = api.Animation(name="landsat_lst", mp4=None, gif=None, frames=[],
                         metadata_db=str(db), status="")
    rows = anim.metadata()
    assert [r["month"] for r in rows] == ["2023-06", "2023-07"]
    assert rows[0]["aoi_mean"] == 22.0 and rows[1]["n_scenes"] == 4


def test_animate_bbox_region_and_buffer(tmp_path):
    captured = {}
    api.animate((5.0, 50.0, 6.0, 51.0), sensor="sentinel2", index="ndvi",
                start="2022-05-01", end="2022-09-01", buffer_m=500,
                out_dir=str(tmp_path), deps=_fake_deps(tmp_path, captured))
    assert captured["buffer"] == 500
    assert captured["cfg"].region_aoi == {"bbox": [5.0, 50.0, 6.0, 51.0]}
    assert captured["cfg"].scale == 10          # sentinel2 GSD


def test_animate_raises_on_no_frames(tmp_path):
    deps = _fake_deps(tmp_path, {}, frames=[])
    with pytest.raises(RuntimeError, match="No sentinel2 ndvi imagery"):
        api.animate([0, 0, 1, 1], sensor="sentinel2", index="ndvi",
                    start="2022-01-01", end="2022-03-01", out_dir=str(tmp_path), deps=deps)


def test_animate_rejects_unsupported_pair(tmp_path):
    with pytest.raises(ValueError, match="not available"):
        api.animate([0, 0, 1, 1], sensor="sentinel2", index="lst",
                    start="2022-01-01", end="2022-03-01", out_dir=str(tmp_path),
                    deps=_fake_deps(tmp_path, {}))


def test_to_region_aoi_forms():
    assert api._to_region_aoi([0, 1, 2, 3]) == {"bbox": [0, 1, 2, 3]}
    assert api._to_region_aoi("x.geojson") == {"geojson": "x.geojson"}
    assert api._to_region_aoi("x.shp") == {"shapefile": "x.shp"}
    assert api._to_region_aoi({"bbox": [0, 0, 1, 1]}) == {"bbox": [0, 0, 1, 1]}
    # a raw GeoJSON geometry dict is wrapped as a geojson AOI
    geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    assert api._to_region_aoi(geom) == {"geojson": geom}
    with pytest.raises(ValueError, match="bbox"):
        api._to_region_aoi([0, 1, 2])


def test_animation_repr_html_embeds_video(tmp_path):
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"\x00\x01\x02")
    anim = api.Animation(name="a", mp4=str(mp4), gif=None, frames=[],
                         metadata_db=None, status="done")
    html = anim._repr_html_()
    assert "<video" in html and "data:video/mp4;base64," in html and "done" in html
