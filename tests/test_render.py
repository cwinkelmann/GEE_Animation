from pathlib import Path
import numpy as np
import types
from PIL import Image
from gee_animation.render import (
    add_colorbar,
    annotate,
    apply_nodata,
    assemble,
    draw_scale_bar,
    render,
    _nice_distance,
)
from gee_animation.compositing import Frame


def _cfg(tmp_path, name="anim", fps=2):
    return types.SimpleNamespace(
        name=name, out_dir=str(tmp_path),
        index="ndvi", viz_min=-0.2, viz_max=0.9, palette=["#000000", "#ffffff"],
        fps=fps, scale=20, dimensions=64, frame_aoi={"bbox": [0, 0, 1, 1]},
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


def test_info_text_shows_formula_and_bands():
    from gee_animation.render import _info_text, draw_info_bar
    cfg = types.SimpleNamespace(index="ndvi")
    txt = _info_text(cfg)
    assert txt.startswith("NDVI = (NIR - Red)") and "bands: NIR, Red" in txt
    # composite: bands but no formula
    assert _info_text(types.SimpleNamespace(index="cir")) == "CIR   bands: R<-NIR, G<-Red, B<-Green"
    # and the bar draws onto the top strip
    rgb = np.zeros((60, 200, 3), np.uint8)
    out = draw_info_bar(rgb, txt)
    assert out[:12, :].sum() > 0 and out[30:, :].sum() == 0


def test_render_projects_overlay_and_resolves_crs_when_auto(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.crs = "auto"
    cfg.draw_region = True
    cfg.frame_aoi = {"bbox": [13.90, 52.99, 13.92, 53.00]}   # Brandenburg
    cfg.region_aoi = {"bbox": [13.905, 52.993, 13.915, 52.998]}
    captured = {}
    monkeypatch.setattr(r, "draw_region",
                        lambda rgb, bounds, rings, **k: (captured.update(bounds=bounds) or rgb))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((20, 20)), np.ones((20, 20), dtype=bool)

    render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)
    assert cfg.crs == "EPSG:32633"                    # "auto" resolved to UTM 33N
    assert captured["bounds"][0] > 100_000            # overlay bounds are UTM metres, not degrees


def test_nice_distance_rounds_to_1_2_5_decades():
    assert _nice_distance(2500) == 2000      # 2 km
    assert _nice_distance(800) == 500        # 500 m
    assert _nice_distance(140) == 100        # 100 m
    assert _nice_distance(9000) == 5000      # 5 km


def test_draw_scale_bar_labels_and_marks_frame():
    # ~111 km wide frame; a quarter of that -> a 20 km "nice" bar.
    rgb = np.zeros((120, 240, 3), np.uint8)
    out = draw_scale_bar(rgb, 111320.0)
    assert out.shape == rgb.shape and out.dtype == np.uint8
    assert out.sum() > 0                                  # bar/label drawn
    # drawn in the bottom-right quadrant, not the top-left
    assert out[:60, :120].sum() == 0
    assert out[60:, 120:].sum() > 0


def test_draw_scale_bar_skips_tiny_frames():
    rgb = np.zeros((8, 8, 3), np.uint8)
    out = draw_scale_bar(rgb, 111320.0)
    assert out.sum() == 0                                 # too small: no-op


def test_utm_epsg_and_resolve_crs():
    from gee_animation.render import _utm_epsg, _resolve_crs
    assert _utm_epsg(13.9, 53.0) == "EPSG:32633"          # Brandenburg -> UTM 33N
    assert _utm_epsg(-122.4, 37.8) == "EPSG:32610"        # San Francisco -> UTM 10N
    assert _utm_epsg(13.9, -53.0) == "EPSG:32733"         # southern hemisphere
    cfg = types.SimpleNamespace(crs="auto")
    assert _resolve_crs(cfg, (13.0, 52.9, 14.0, 53.1)) == "EPSG:32633"
    assert _resolve_crs(types.SimpleNamespace(crs=None), (13, 52, 14, 53)) is None
    assert _resolve_crs(types.SimpleNamespace(crs="EPSG:3035"), None) == "EPSG:3035"


def test_project_gives_metric_bounds_and_square_pixels():
    from gee_animation.render import _project, _frame_width_m
    # a ~1 km square AOI at 53N: in EPSG:4326 the lon span is compressed by cos(53),
    # but in UTM the ground width and height should be ~equal (square pixels).
    b = (13.900, 52.995, 13.910, 53.005)
    pb, pr = _project(b, [[(13.9, 53.0), (13.91, 53.0)]], "EPSG:32633")
    w_m = pb[2] - pb[0]; h_m = pb[3] - pb[1]
    assert 600 < w_m < 800 and 1050 < h_m < 1200          # metres, not degrees
    assert len(pr) == 1 and len(pr[0]) == 2               # rings projected too
    assert _frame_width_m(b, pb, "EPSG:32633") == pb[2] - pb[0]


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


def _thermal_cfg(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.sensor, cfg.index = "landsat", "lst"
    cfg.frame_aoi = {"bbox": [0.0, 0.0, 0.01, 0.01]}   # ~1.11 km frame
    cfg.dimensions = 768
    cfg.allow_upsample = False
    return cfg


def _tiny_fetch(image, cfg, geometry=None):
    return np.zeros((8, 8)), np.ones((8, 8), dtype=bool)


def test_render_caps_dimensions_to_native_resolution(tmp_path, caplog):
    import logging
    cfg = _thermal_cfg(tmp_path)
    with caplog.at_level(logging.WARNING):
        render([Frame("2022-06", object(), 4)], cfg, fetch=_tiny_fetch, geometry=None)
    # ~1113 m / 100 m native -> 11 px; the 768 request is capped (no silent upsample)
    assert cfg.dimensions == 11
    assert "native resolution" in caplog.text


def test_render_allow_upsample_keeps_dimensions_but_warns(tmp_path, caplog):
    import logging
    cfg = _thermal_cfg(tmp_path)
    cfg.allow_upsample = True
    with caplog.at_level(logging.WARNING):
        render([Frame("2022-06", object(), 4)], cfg, fetch=_tiny_fetch, geometry=None)
    assert cfg.dimensions == 768                        # honoured, not capped
    assert "upsamples" in caplog.text


def test_render_annotates_scene_count_when_present(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    labels = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label: (labels.append(label) or rgb))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((10, 10)), np.ones((10, 10), dtype=bool)

    render([Frame("2022-06", object(), 7)], cfg, fetch=fake_fetch, geometry=None)
    assert labels == ["2022-06  n=7"]                  # scene count shown on the frame


def test_render_composite_passes_rgb_through_without_colorbar(tmp_path):
    # rgb/cir fetch returns an H×W×3 colour array; render must NOT colorize it,
    # and must not draw a palette colorbar (composites have no palette).
    cfg = _cfg(tmp_path, name="rgbtest")
    cfg.index = "rgb"
    cfg.palette = []                                   # composite: no palette

    def fake_fetch(image, cfg, geometry=None):
        rgb = np.full((90, 140, 3), 123, dtype=float)
        return rgb, np.ones((90, 140), dtype=bool)

    paths = render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)
    png = next(p for p in paths if p.suffix == ".png")
    arr = np.asarray(Image.open(png))
    # a central pixel (clear of the top info bar, bottom label bar and scale bar)
    # keeps the exact composite value — proof it was passed through, not palettized
    assert tuple(arr[45, 70]) == (123, 123, 123)
    assert any(p.suffix == ".gif" and p.exists() for p in paths)


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
