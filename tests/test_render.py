from pathlib import Path
import numpy as np
import types
from gee_animation.render import (
    add_colorbar,
    annotate,
    apply_nodata,
    assemble,
    render,
)
from gee_animation.compositing import Frame


def _cfg(tmp_path, name="anim", fps=2):
    return types.SimpleNamespace(
        name=name, out_dir=str(tmp_path),
        index="ndvi", viz_min=-0.2, viz_max=0.9, palette=["#000000", "#ffffff"],
        fps=fps, scale=20, dimensions=64,
    )


def test_annotate_keeps_shape_and_type():
    rgb = np.zeros((32, 32, 3), dtype=np.uint8)
    out = annotate(rgb, "2022-06")
    assert out.shape == (32, 32, 3)
    assert out.dtype == np.uint8
    # some pixels changed (text/bar drawn)
    assert out.sum() > 0


def test_assemble_writes_gif_and_mp4(tmp_path):
    cfg = _cfg(tmp_path)
    frames = [np.zeros((16, 16, 3), np.uint8), np.full((16, 16, 3), 255, np.uint8)]
    paths = assemble(frames, cfg)
    suffixes = {p.suffix for p in paths}
    assert suffixes == {".mp4", ".gif"}
    assert all(p.exists() for p in paths)


def test_aoi_bounds_from_bbox():
    from gee_animation.render import _aoi_bounds
    assert _aoi_bounds({"bbox": [13.7, 52.8, 13.9, 52.95]}) == (13.7, 52.8, 13.9, 52.95)


def test_region_rings_from_shapefile(tmp_path):
    import pytest
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import Polygon
    from gee_animation.render import _region_rings, _aoi_bounds
    poly = Polygon([(400000, 5860000), (410000, 5860000), (410000, 5870000), (400000, 5870000)])
    p = tmp_path / "r.shp"
    gpd.GeoDataFrame({"id": [0]}, geometry=[poly], crs="EPSG:25833").to_file(p)
    rings = _region_rings({"shapefile": str(p)})
    assert rings and len(rings[0]) >= 4
    assert 12 < rings[0][0][0] < 15                 # reprojected to lon/lat
    minx, miny, maxx, maxy = _aoi_bounds({"shapefile": str(p)})
    assert 12 < minx < maxx < 15 and 52 < miny < maxy < 53


def test_aoi_bounds_from_geojson_polygon():
    from gee_animation.render import _aoi_bounds
    geom = {"type": "Polygon", "coordinates": [[[1, 2], [5, 2], [5, 8], [1, 8], [1, 2]]]}
    assert _aoi_bounds({"geojson": geom}) == (1, 2, 5, 8)


def test_region_rings_from_bbox():
    from gee_animation.render import _region_rings
    assert _region_rings({"bbox": [0, 0, 2, 2]}) == [
        [(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)]
    ]


def test_region_rings_from_geojson_polygon():
    from gee_animation.render import _region_rings
    geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    assert _region_rings({"geojson": geom}) == [[(0, 0), (1, 0), (1, 1), (0, 0)]]


def test_draw_region_maps_coords_and_draws_outline():
    from gee_animation.render import draw_region
    rgb = np.zeros((100, 100, 3), np.uint8)
    bounds = (0.0, 0.0, 10.0, 10.0)                    # 10 deg over 100 px
    rings = [[(2, 2), (8, 2), (8, 8), (2, 8), (2, 2)]]  # -> pixel square (20,80)-(80,20)
    out = draw_region(rgb, bounds, rings, color=(255, 0, 0), width=1)
    assert out.shape == (100, 100, 3) and out.dtype == np.uint8
    assert out[..., 0].max() == 255            # outline drawn (red)
    assert out[50, 50].tolist() == [0, 0, 0]   # interior untouched (outline only)


def test_render_applies_region_overlay_when_enabled(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.draw_region = True
    cfg.frame_aoi = {"bbox": [0.0, 0.0, 1.0, 1.0]}
    cfg.region_aoi = {"bbox": [0.25, 0.25, 0.75, 0.75]}
    calls = []
    monkeypatch.setattr(r, "draw_region", lambda rgb, bounds, rings, **kw: (calls.append((bounds, rings)) or rgb))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((20, 20)), np.ones((20, 20), dtype=bool)

    render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)
    assert len(calls) == 1
    assert calls[0][0] == (0.0, 0.0, 1.0, 1.0)
    assert calls[0][1] == [[(0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75), (0.25, 0.25)]]


def test_render_skips_region_overlay_when_disabled(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.draw_region = False
    cfg.frame_aoi = {"bbox": [0, 0, 1, 1]}
    cfg.region_aoi = {"bbox": [0, 0, 1, 1]}

    def boom(*a, **k):
        raise AssertionError("draw_region should not be called when disabled")

    monkeypatch.setattr(r, "draw_region", boom)

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((10, 10)), np.ones((10, 10), dtype=bool)

    render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)  # must not raise


def test_assemble_encodes_mp4_for_odd_dimension_frames(tmp_path):
    # libx264 requires even width AND height; frames from arbitrary AOIs are
    # often odd (e.g. 768x577). The MP4 must still be produced, not dropped.
    cfg = _cfg(tmp_path)
    frames = [np.zeros((15, 16, 3), np.uint8), np.full((15, 16, 3), 200, np.uint8)]
    paths = assemble(frames, cfg)
    suffixes = {p.suffix for p in paths}
    assert ".mp4" in suffixes, "MP4 should be produced for odd-dimension frames"
    mp4 = next(p for p in paths if p.suffix == ".mp4")
    assert mp4.exists() and mp4.stat().st_size > 0


def test_assemble_falls_back_to_gif_when_mp4_fails(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("no ffmpeg")
    monkeypatch.setattr(r, "_write_mp4", boom)
    frames = [np.zeros((16, 16, 3), np.uint8)]
    paths = assemble(frames, cfg)
    assert all(p.suffix == ".gif" for p in paths)
    assert paths[0].exists()


def test_render_pipeline_with_injected_fetch(tmp_path):
    cfg = _cfg(tmp_path)
    # fetch returns a tiny (ndvi_array, valid_mask) tuple per frame
    def fake_fetch(image, cfg, geometry=None):
        arr = np.array([[0.5, -0.1], [0.9, 0.0]])
        valid = np.ones(arr.shape, dtype=bool)
        return arr, valid
    frames = [Frame("2022-01", object()), Frame("2022-02", object())]
    paths = render(frames, cfg, fetch=fake_fetch, geometry=None)
    assert any(p.suffix == ".gif" and p.exists() for p in paths)
    # one downloadable PNG per frame, named {name}_{label}.png
    pngs = [p for p in paths if p.suffix == ".png"]
    assert [p.name for p in pngs] == ["anim_2022-01.png", "anim_2022-02.png"]
    assert all(p.exists() and p.stat().st_size > 0 for p in pngs)


def _cfg_ns():
    return types.SimpleNamespace(
        index="ndvi", viz_min=-0.2, viz_max=0.9, palette=["#000000", "#ffffff"],
    )


def test_apply_nodata_paints_invalid_pixels():
    rgb = np.zeros((1, 2, 3), np.uint8)
    valid = np.array([[True, False]])
    out = apply_nodata(rgb, valid)
    assert out[0, 0].tolist() == [0, 0, 0]          # valid untouched
    assert out[0, 1].tolist() == list((240, 240, 240))  # invalid -> no-data colour


def test_add_colorbar_preserves_shape_and_draws():
    rgb = np.zeros((40, 60, 3), np.uint8)
    out = add_colorbar(rgb, _cfg_ns())
    assert out.shape == (40, 60, 3) and out.dtype == np.uint8
    assert out.sum() > 0


def test_thumb_params_preserve_aspect_ratio():
    from gee_animation.render import _thumb_params
    cfg = _cfg(Path("."))  # _cfg provides viz_min/max, dimensions
    params = _thumb_params(cfg, "GEOM")
    assert isinstance(params["dimensions"], int)   # single int -> EE preserves aspect
    assert params["region"] == "GEOM"
    assert params["min"] == cfg.viz_min and params["max"] == cfg.viz_max


def test_fetch_thumbnail_selects_index_band(tmp_path):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    assert r._thumb_params(cfg, "GEOM")["min"] == cfg.viz_min
    # selection is exercised by the integration test; unit-assert the band constant is INDEX:
    from gee_animation.products import INDEX_BAND
    assert INDEX_BAND == "INDEX"
