from pathlib import Path
import numpy as np
import types
from PIL import Image, ImageDraw
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


def test_bar_h_matches_draw_info_bar_and_annotate():
    # draw_info_bar (top) and annotate (bottom) must stay the same height — extracted
    # into one helper so the two call sites can't drift apart.
    from gee_animation.render import _bar_h
    assert _bar_h(120) == max(12, 120 // 12)
    assert _bar_h(12) == 12                      # floor kicks in on tiny frames


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


def test_draw_region_antialiases_diagonal_edges():
    from gee_animation.render import draw_region
    rgb = np.zeros((100, 100, 3), np.uint8)
    bounds = (0.0, 0.0, 10.0, 10.0)
    rings = [[(1, 1), (9, 9), (9, 1), (1, 1)]]   # includes a diagonal edge
    out = draw_region(rgb, bounds, rings, color=(255, 0, 0), width=2)
    red = out[..., 0]
    # PIL's ImageDraw.line has no antialiasing, so a hard-line renderer only ever
    # produces 0 or 255 on this channel; the supersampled-then-downsampled mask must
    # produce in-between values along the diagonal edge.
    assert np.any((red > 0) & (red < 255))


def test_draw_region_respects_configured_line_width():
    from gee_animation.render import draw_region
    rgb = np.zeros((100, 100, 3), np.uint8)
    bounds = (0.0, 0.0, 10.0, 10.0)
    rings = [[(2, 2), (8, 2), (8, 8), (2, 8), (2, 2)]]
    thin = draw_region(rgb.copy(), bounds, rings, color=(255, 0, 0), width=1)
    thick = draw_region(rgb.copy(), bounds, rings, color=(255, 0, 0), width=5)
    thin_px = int(np.sum(thin[..., 0] > 0))
    thick_px = int(np.sum(thick[..., 0] > 0))
    assert thick_px > thin_px


def test_render_applies_region_overlay_when_enabled(tmp_path, monkeypatch):
    # render() builds the overlay masks itself (and composites them via
    # _composite_region) rather than routing through draw_region — see
    # test_render_threads_region_line_width_into_draw_region for why.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.draw_region = True
    cfg.frame_aoi = {"bbox": [0.0, 0.0, 1.0, 1.0]}
    cfg.region_aoi = {"bbox": [0.25, 0.25, 0.75, 0.75]}
    calls = []
    real_region_masks = r._region_masks
    monkeypatch.setattr(r, "_region_masks", lambda bounds, rings, *a, **k: (
        calls.append((bounds, rings)) or real_region_masks(bounds, rings, *a, **k)))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((20, 20)), np.ones((20, 20), dtype=bool)

    render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)
    assert len(calls) == 1
    assert calls[0][0] == (0.0, 0.0, 1.0, 1.0)
    assert calls[0][1] == [[(0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75), (0.25, 0.25)]]


def test_render_builds_region_masks_once_across_frames(tmp_path, monkeypatch):
    # The rings/bounds/frame size are identical every frame, so the (expensive,
    # supersampled) mask construction must be paid once, not once per frame.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.draw_region = True
    cfg.frame_aoi = {"bbox": [0.0, 0.0, 1.0, 1.0]}
    cfg.region_aoi = {"bbox": [0.25, 0.25, 0.75, 0.75]}
    calls = []
    real_region_masks = r._region_masks

    def counting_region_masks(*a, **k):
        calls.append(1)
        return real_region_masks(*a, **k)

    monkeypatch.setattr(r, "_region_masks", counting_region_masks)

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((20, 20)), np.ones((20, 20), dtype=bool)

    frames = [Frame("2022-01", object()), Frame("2022-02", object()), Frame("2022-03", object())]
    render(frames, cfg, fetch=fake_fetch, geometry=None)
    assert len(calls) == 1


def test_render_threads_region_line_width_into_draw_region(tmp_path, monkeypatch):
    # render() passes cfg.region_line_width straight into the mask build (_region_masks),
    # not through draw_region's `width=` (which, once masks exist, it never reads —
    # see Fix 6 / render()'s comment above the overlay block).
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.draw_region = True
    cfg.frame_aoi = {"bbox": [0.0, 0.0, 1.0, 1.0]}
    cfg.region_aoi = {"bbox": [0.25, 0.25, 0.75, 0.75]}
    cfg.region_line_width = 7
    widths = []
    real_region_masks = r._region_masks
    monkeypatch.setattr(r, "_region_masks", lambda bounds, rings, shape, width: (
        widths.append(width) or real_region_masks(bounds, rings, shape, width)))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((20, 20)), np.ones((20, 20), dtype=bool)

    render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)
    assert widths == [7]


def test_render_skips_region_overlay_when_disabled(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.draw_region = False
    cfg.frame_aoi = {"bbox": [0, 0, 1, 1]}
    cfg.region_aoi = {"bbox": [0, 0, 1, 1]}

    def boom(*a, **k):
        raise AssertionError("_region_masks should not be called when disabled")

    monkeypatch.setattr(r, "_region_masks", boom)

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((10, 10)), np.ones((10, 10), dtype=bool)

    render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)  # must not raise


def test_draw_info_bar_shrinks_long_text_to_fit_the_frame():
    # Reviewer-verified live overflow: lst_smw's info text measured 2239px wide in a
    # 1920px frame and got cut mid-word. draw_info_bar must shrink the font until the
    # text fits inside the frame instead of letting Pillow draw past the right edge.
    from gee_animation.render import _info_text, draw_info_bar
    cfg = types.SimpleNamespace(index="lst_smw")
    text = _info_text(cfg)
    w, h = 500, 1200          # narrow frame relative to this long formula string
    rgb = np.zeros((h, w, 3), np.uint8)
    out = draw_info_bar(rgb, text)
    bar_h = h // 12
    # no ink in the rightmost columns of the bar row band -> text stayed inside frame
    assert out[:bar_h, -3:].sum() == 0


def test_draw_info_bar_leaves_short_text_at_the_original_font_size():
    # NDVI-style short text must render identically to before the fitting change: same
    # font size (and therefore identical glyph rendering) as _annot_scale would pick.
    from gee_animation.render import _info_text, draw_info_bar, _annot_scale
    cfg = types.SimpleNamespace(index="ndvi")
    text = _info_text(cfg)
    w, h = 800, 300
    rgb = np.zeros((h, w, 3), np.uint8)
    out = draw_info_bar(rgb, text)

    expected_font, _ = _annot_scale(h)
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    tb = scratch.textbbox((0, 0), text, font=expected_font)
    ink_cols = np.nonzero(out[:h // 12].sum(axis=(0, 2)))[0]
    assert ink_cols.size
    # drawn text width matches what the unshrunk (_annot_scale) font would measure
    assert ink_cols.max() - ink_cols.min() <= (tb[2] - tb[0]) + 2


def test_draw_info_bar_keeps_bar_height_fixed_for_long_and_short_text():
    # Font-fitting must never touch the bar rectangle itself — _bar_h feeds the
    # _margins fixed point (see test_margins_match_the_bar_height_of_the_padded_frame).
    from gee_animation.render import _info_text, draw_info_bar, _bar_h
    w, h = 500, 300
    long_text = _info_text(types.SimpleNamespace(index="lst_smw"))
    short_text = _info_text(types.SimpleNamespace(index="ndvi"))
    for text in (long_text, short_text):
        rgb = np.zeros((h, w, 3), np.uint8)
        out = draw_info_bar(rgb, text)
        bar_h = _bar_h(h)
        # bar tint fills exactly [0, bar_h) and nothing below it
        assert out[:bar_h].sum() > 0
        assert out[bar_h:].sum() == 0


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
    real_region_masks = r._region_masks
    monkeypatch.setattr(r, "_region_masks", lambda bounds, rings, *a, **k: (
        captured.update(bounds=bounds) or real_region_masks(bounds, rings, *a, **k)))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((20, 20)), np.ones((20, 20), dtype=bool)

    render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)
    assert cfg.crs == "EPSG:32633"                    # "auto" resolved to UTM 33N
    assert captured["bounds"][0] > 100_000            # overlay bounds are UTM metres, not degrees


def test_output_spec_match_and_aspect():
    from gee_animation.render import _output_spec
    assert _output_spec(types.SimpleNamespace(preset=None), (200, 100)) is None
    # match: canvas takes the frame aspect, long edge = preset, imagery fills it —
    # the label margins grow the canvas (960 + 97 + 96) rather than shrink the imagery
    cfg = types.SimpleNamespace(preset="1080p", aspect="match", upscale="lanczos")
    assert _output_spec(cfg, (200, 100))[:4] == (1920, 1153, 1920, 960)
    # 16:9 canvas with a wider (2.0) frame -> width-limited, letterboxed top/bottom
    cfg = types.SimpleNamespace(preset="4k", aspect="16:9", upscale="lanczos")
    cw, ch, pw, ph, _ = _output_spec(cfg, (200, 100))
    assert (cw, ch, pw, ph) == (3840, 2160, 3598, 1799)   # 1799+180+179 <= 2160


def test_output_spec_accounts_for_margins():
    # Trap: _output_spec sizes the canvas from the imagery aspect. If the label
    # margins were padded on afterwards, an aspect: "16:9" request would yield
    # something taller than 16:9 — they must come out of the place box instead.
    from gee_animation.render import _output_spec, _margins
    for aoi_wh in ((200, 100), (100, 200), (160, 90)):
        cfg = types.SimpleNamespace(preset="1080p", aspect="16:9", upscale="lanczos")
        cw, ch, pw, ph, _ = _output_spec(cfg, aoi_wh)
        assert (cw, ch) == (1920, 1080)                    # canvas is exactly 16:9
        assert ch / cw == 9 / 16
        # imagery + both margins fits the canvas, and keeps the imagery aspect
        assert ph + sum(_margins(ph)) <= ch
        assert pw <= cw
        assert abs(pw / ph - aoi_wh[0] / aoi_wh[1]) < 0.02


def test_output_spec_match_grows_canvas_instead_of_shrinking_imagery(tmp_path):
    # "match" (and the unset default, config.aspect is None) promises no ratio, so
    # Trap 2 does not apply to it: the margins must grow the canvas rather than eat
    # into the place box. Shrinking the imagery and adding side bars would work
    # directly against the feedback this margin work exists to fix ("the framing is a
    # little too tight") — and a "match" that letterboxes is not matching.
    from gee_animation.render import _output_spec, _margins
    for aspect in (None, "match"):
        cfg = types.SimpleNamespace(preset="720p", aspect=aspect, upscale="lanczos")
        cw, ch, pw, ph, _ = _output_spec(cfg, (200, 100))
        assert (pw, ph) == (1280, 640)              # imagery at full preset size...
        assert pw == cw                             # ...filling the width: no side bars
        assert ch == ph + sum(_margins(ph))         # canvas grew by exactly the margins

    # and end to end: a uniform frame reaches both canvas edges, margins outside it
    cfg = _cfg(tmp_path, name="matchfill")
    cfg.index, cfg.palette = "rgb", []              # composite: no colorbar overlay
    cfg.frame_aoi = None                            # and no scale bar
    cfg.preset, cfg.aspect, cfg.upscale = "480p", None, "lanczos"

    def fake_fetch(image, cfg, geometry=None):
        return np.full((50, 100, 3), 77, dtype=float), np.ones((50, 100), dtype=bool)

    paths = render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)
    arr = np.asarray(Image.open(next(p for p in paths if p.suffix == ".png")))
    top_h, bottom_h = _margins(427)                 # 480p long edge 854, aspect 2.0
    assert arr.shape[:2] == (427 + top_h + bottom_h, 854)
    assert np.all(arr[top_h:top_h + 427] == 77)     # full-width imagery, nothing on it


def test_margins_match_the_bar_height_of_the_padded_frame():
    # The margin must be exactly the bar height draw_info_bar/annotate derive from the
    # PADDED height, or the bars leave a bare strip / creep back over the imagery.
    from gee_animation.render import _margins
    for imagery_h in (1, 8, 90, 119, 120, 131, 132, 200, 577, 799, 1080, 1799, 2160):
        top_h, bottom_h = _margins(imagery_h)
        bar_h = max(12, (imagery_h + top_h + bottom_h) // 12)
        assert (top_h, bottom_h) == (bar_h + 1, bar_h), imagery_h


def test_add_margins_keeps_imagery_unoccluded():
    from gee_animation.render import add_margins
    rgb = np.random.default_rng(0).integers(0, 255, (30, 40, 3), dtype=np.uint8)
    out = add_margins(rgb, 7, 11)
    assert out.shape == (30 + 7 + 11, 40, 3) and out.dtype == np.uint8
    assert np.array_equal(out[7:7 + 30], rgb)      # imagery byte-identical, not covered
    assert out[:7].sum() == 0 and out[-11:].sum() == 0   # margins are blank background
    assert np.array_equal(add_margins(rgb, 0, 0), rgb)   # nothing to add -> untouched


def test_render_draws_label_bars_in_the_margins_not_over_the_imagery(tmp_path):
    # The whole point of the margins: a uniform frame must survive the info bar and
    # the month label untouched, with both bars confined to the added strips.
    from gee_animation.render import _margins
    h, w = 200, 200
    cfg = _cfg(tmp_path, name="bars")
    cfg.index = "rgb"                                  # composite: no colorbar overlay
    cfg.palette = []
    cfg.frame_aoi = None                               # and no scale bar

    def fake_fetch(image, cfg, geometry=None):
        return np.full((h, w, 3), 123, dtype=float), np.ones((h, w), dtype=bool)

    paths = render([Frame("2022-01", object(), 5)], cfg, fetch=fake_fetch, geometry=None)
    arr = np.asarray(Image.open(next(p for p in paths if p.suffix == ".png")))
    top_h, bottom_h = _margins(h)
    assert arr.shape[:2] == (h + top_h + bottom_h, w)
    assert np.all(arr[top_h:top_h + h] == 123)         # every imagery pixel untouched
    assert arr[:top_h].sum() > 0 and arr[-bottom_h:].sum() > 0   # both labels drawn


def test_render_keeps_region_outline_on_imagery_when_margins_added(tmp_path):
    # Trap: draw_region maps lon/lat linearly across the WHOLE array. Run after the
    # margins are added, the outline silently slides *up* by half a margin inside the
    # imagery — still a plausible-looking outline, just over the wrong pixels. It must
    # therefore be drawn on pure imagery, before any padding exists.
    from gee_animation.render import _margins, REGION_OUTLINE_RGB
    h = w = 200
    cfg = _cfg(tmp_path, name="ring")
    cfg.draw_region = True
    cfg.frame_aoi = {"bbox": [0.0, 0.0, 1.0, 1.0]}
    cfg.region_aoi = {"bbox": [0.25, 0.25, 0.75, 0.75]}

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((h, w)), np.ones((h, w), dtype=bool)

    paths = render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)
    arr = np.asarray(Image.open(next(p for p in paths if p.suffix == ".png")))
    top_h, bottom_h = _margins(h)
    assert arr.shape[:2] == (h + top_h + bottom_h, w)   # margins added, imagery whole

    core = np.all(arr == np.asarray(REGION_OUTLINE_RGB, np.uint8), axis=-1)
    rows = np.nonzero(core.any(axis=1))[0]
    assert rows.size, "sanity: the amber outline core must be on the frame"
    # lat 0.75 is a quarter down the *imagery*, which starts at row top_h
    assert abs(int(rows.min()) - (top_h + h // 4)) <= 1
    assert abs(int(rows.max()) - (top_h + 3 * h // 4)) <= 1
    # ...and nowhere near where mapping across the padded array would have put it
    wrong_top = round(0.25 * (h + top_h + bottom_h))
    assert not np.any(np.abs(rows - wrong_top) <= 2)


def test_letterbox_centers_on_canvas():
    from gee_animation.render import _letterbox
    rgb = np.full((50, 100, 3), 200, np.uint8)
    out = _letterbox(rgb, 120, 80)
    assert out.shape == (80, 120, 3)
    assert tuple(out[40, 60]) == (200, 200, 200)   # centre = imagery
    assert tuple(out[2, 2]) == (0, 0, 0)           # corner = letterbox background


def test_render_preset_outputs_target_resolution(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.preset, cfg.aspect, cfg.upscale = "720p", "16:9", "lanczos"

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((30, 40)), np.ones((30, 40), dtype=bool)

    paths = render([Frame("2022-01", object(), 3)], cfg, fetch=fake_fetch, geometry=None)
    png = next(p for p in paths if p.suffix == ".png")
    assert np.asarray(Image.open(png)).shape[:2] == (720, 1280)   # 720p 16:9 canvas


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
    assert "capping fetch dimensions" in caplog.text


def test_render_allow_upsample_keeps_dimensions_but_warns(tmp_path, caplog):
    import logging
    cfg = _thermal_cfg(tmp_path)
    cfg.allow_upsample = True
    with caplog.at_level(logging.WARNING):
        render([Frame("2022-06", object(), 4)], cfg, fetch=_tiny_fetch, geometry=None)
    assert cfg.dimensions == 768                        # honoured, not capped
    assert "upsamples" in caplog.text


def test_render_preset_caps_fetch_to_native_despite_allow_upsample(tmp_path):
    # with a screen preset, the fetch stays native (avoids blocky server upsampling) —
    # the preset does the smooth client-side upscale — even if allow_upsample is set
    cfg = _thermal_cfg(tmp_path)
    cfg.allow_upsample = True
    cfg.preset, cfg.aspect, cfg.upscale = "1080p", "match", "lanczos"
    render([Frame("2022-06", object(), 4)], cfg, fetch=_tiny_fetch, geometry=None)
    assert cfg.dimensions == 11                         # capped to native, not 768


def test_render_annotates_scene_count_when_present(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    labels = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label: (labels.append(label) or rgb))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((10, 10)), np.ones((10, 10), dtype=bool)

    render([Frame("2022-06", object(), 7)], cfg, fetch=fake_fetch, geometry=None)
    assert labels == ["2022-06  n=7"]                  # scene count shown on the frame


def test_render_composes_pooled_provenance_into_the_drawn_text(tmp_path, monkeypatch):
    # Frame.label is the clean period key; Frame.source (added for pooled frames)
    # carries where the imagery actually came from. render() must recombine them into
    # the same drawn text as before ("2022-05 ← 2021  n=1"), folded down to a glyph
    # Pillow's default font can draw, right where the string is composed.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    composed = []
    orig_drawable = r._drawable
    monkeypatch.setattr(r, "_drawable",
                        lambda text: (composed.append(text) or orig_drawable(text)))
    drawn = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label: (drawn.append(label) or rgb))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((10, 10)), np.ones((10, 10), dtype=bool)

    render([Frame("2022-05", object(), 1, 2021)], cfg, fetch=fake_fetch, geometry=None)
    # what render composed, before folding: the real arrow, the source year, n=1
    assert composed == ["2022-05 ← 2021  n=1"]
    # what actually reaches Pillow: folded to a glyph the default font has
    assert drawn == ["2022-05 <- 2021  n=1"]
    assert "←" not in drawn[0]


def test_render_leaves_gap_fill_nominal_frames_unmarked(tmp_path, monkeypatch):
    # gap_fill emits a mix: nominal-year frames carry source=None and must draw the
    # PLAIN period label (an arrow there would claim borrowed data that isn't), while
    # the borrowed ones still name their source year — in the same run.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    drawn = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label: (drawn.append(label) or rgb))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((10, 10)), np.ones((10, 10), dtype=bool)

    render([Frame("2022-05", object(), 3, None), Frame("2022-06", object(), 1, 2019)],
           cfg, fetch=fake_fetch, geometry=None)
    assert drawn == ["2022-05  n=3", "2022-06 <- 2019  n=1"]


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


def test_render_pooled_frame_png_filename_is_the_clean_period_key(tmp_path):
    # Frame.label (not the drawn provenance text) names the PNG file; a pooled frame's
    # source year must not leak into it as "anim_2022-05 ← 2021.png" — that would be a
    # filename with a space and U+2190, and it also has to match the DB `month` key
    # (see test_metadata.py) so pooled and non-pooled runs collide correctly.
    cfg = _cfg(tmp_path)

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((10, 10)), np.ones((10, 10), dtype=bool)

    paths = render([Frame("2022-05", object(), 1, 2021)], cfg, fetch=fake_fetch, geometry=None)
    pngs = [p for p in paths if p.suffix == ".png"]
    assert [p.name for p in pngs] == [f"{cfg.name}_2022-05.png"]


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


def test_colorbar_draws_min_mid_max_ticks():
    # Wide enough frame that min/mid/max labels don't collide, so all three tick
    # marks (short vertical lines just below the ramp) are drawn distinctly.
    w, h = 300, 80
    rgb = np.zeros((h, w, 3), np.uint8)
    cfg = types.SimpleNamespace(index="ndvi", viz_min=0.0, viz_max=10.0,
                                palette=["#000000", "#ffffff"])
    out = add_colorbar(rgb, cfg)

    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = max(4, w // 200), 4
    x_min, x_mid, x_max = x0, x0 + bar_w // 2, x0 + bar_w
    tick_row = y0 + bar_h + 1        # inside the tick zone, below the ramp's own border

    assert out[tick_row, x_min].tolist() == [255, 255, 255]
    assert out[tick_row, x_mid].tolist() == [255, 255, 255]
    assert out[tick_row, x_max].tolist() == [255, 255, 255]
    # a point strictly between ticks stays untouched background
    assert out[tick_row, x0 + bar_w // 4].tolist() == [0, 0, 0]


def test_colorbar_shows_units_for_thermal_index():
    # Same numeric range/palette for both indices; only "lst" carries units, so
    # the only pixel difference between the two renders is the appended "°C" glyphs.
    def _cfg(index):
        return types.SimpleNamespace(index=index, viz_min=15.0, viz_max=40.0,
                                     palette=["#0000ff", "#ff0000"])
    rgb = np.zeros((80, 300, 3), np.uint8)
    lst_out = add_colorbar(rgb.copy(), _cfg("lst"))
    ndvi_out = add_colorbar(rgb.copy(), _cfg("ndvi"))
    assert not np.array_equal(lst_out, ndvi_out)
    assert lst_out.sum() > ndvi_out.sum()          # extra "°C" ink on the thermal bar


def test_colorbar_adds_zero_tick_only_when_range_spans_zero():
    from gee_animation.render import _colorbar_ticks
    spans = _colorbar_ticks(-3, 3, "")
    absolute = _colorbar_ticks(15, 40, "")
    assert (0.0, "0", "m", False) in spans
    assert not any(label == "0" for _v, label, _a, _d in absolute)

    # and the rendered (-3, 3) bar actually carries a tick at the 0 position
    w, h = 300, 80
    rgb = np.zeros((h, w, 3), np.uint8)
    cfg = types.SimpleNamespace(index="lst", viz_min=-3.0, viz_max=3.0,
                                palette=["#0000ff", "#ff0000"])
    out = add_colorbar(rgb, cfg)
    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = max(4, w // 200), 4
    x_zero = x0 + round((0.0 - (-3.0)) / (3.0 - (-3.0)) * bar_w)   # -3..3 -> 0 at centre
    tick_row = y0 + bar_h + 1
    assert out[tick_row, x_zero].tolist() == [255, 255, 255]


def test_colorbar_ticks_zero_dedupes_with_midpoint():
    # -3..3 is the default climatology-anomaly range (anomaly.py ANOMALY_VIZ), where
    # the midpoint IS zero: without deduping, _colorbar_ticks would emit two (0.0,
    # "0", "m", ...) entries — a genuine duplicate, not a contrived corner case.
    from gee_animation.render import _colorbar_ticks
    ticks = _colorbar_ticks(-3, 3, "")
    zero_ticks = [t for t in ticks if t[0] == 0.0]
    assert len(zero_ticks) == 1
    assert zero_ticks[0] == (0.0, "0", "m", False)   # kept as the non-droppable zero tick


def test_colorbar_drops_mid_label_on_narrow_ramp():
    # Genuinely narrow: bar_w == 64px (comparable to the brief's own "dimensions: 256
    # -> ~100px bar" example), with the default LST range — the reviewer-verified
    # real-world case where the mid ("15") label collides with its neighbours.
    from gee_animation.render import _annot_scale, _colorbar_ticks
    w, h = 160, 300
    rgb = np.zeros((h, w, 3), np.uint8)
    cfg = types.SimpleNamespace(index="lst", viz_min=-10.0, viz_max=40.0,
                                palette=["#0000ff", "#ff0000"])
    out = add_colorbar(rgb, cfg)

    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = max(4, w // 200), 4
    font, lw = _annot_scale(h)
    text_y = y0 + bar_h + lw + 2 + 1   # matches add_colorbar's tick_bot + 1

    ticks = _colorbar_ticks(-10.0, 40.0, "°C")
    assert [droppable for *_, droppable in ticks] == [False, False, True, False]

    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    def _bounds(text, x, anchor):
        tb = scratch.textbbox((0, 0), text, font=font)
        tw = tb[2] - tb[0]
        if anchor == "l":
            return x, x + tw
        if anchor == "r":
            return x - tw, x
        return x - tw / 2, x + tw / 2

    def _x(v):
        return x0 + (v - (-10.0)) / (40.0 - (-10.0)) * bar_w

    min_lo, min_hi = _bounds("-10", _x(-10.0), "l")
    mid_lo, mid_hi = _bounds("15", _x(15.0), "m")
    max_lo, max_hi = _bounds("40 °C", _x(40.0), "r")

    band = out[text_y:text_y + 40]
    # min and max labels survive...
    assert band[:, int(min_lo):int(min_hi) + 1].sum() > 0
    assert band[:, int(max_lo):int(max_hi) + 1].sum() > 0
    # ...but the mid label was dropped: its own (inset, to dodge rounding at the
    # boundary with the neighbouring label) columns are untouched.
    assert band[:, int(mid_lo) + 1:int(mid_hi)].sum() == 0


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


def test_info_text_states_the_pooled_year_range():
    # Pooled frames come from whichever year was clearest, so the provenance has to
    # be drawn on the frame, not just live in the config. An ASCII hyphen, not an en
    # dash: _info_text feeds draw_info_bar directly with no fold step (Pillow's
    # default font has no en-dash glyph), so the fix has to be at the source.
    from gee_animation.render import _info_text
    plain = _info_text(types.SimpleNamespace(index="ndvi"))
    pooled = _info_text(types.SimpleNamespace(index="ndvi", pool_years=[2019, 2024]))
    assert "pooled years" not in plain
    assert "pooled years 2019-2024" in pooled and "cosmetic" in pooled
    assert "–" not in pooled


def test_pooled_label_source_year_is_drawn_not_a_notdef_box():
    # The bundled default font has no U+2190 glyph, so a raw "←" draws as an empty
    # box. `_drawable` folds it to "<-"; `annotate`/`draw_info_bar` draw whatever text
    # they are given verbatim, so callers (render(), for the pooled provenance string)
    # must fold before calling them.
    from gee_animation.render import _drawable, annotate
    assert _drawable("2022-05 ← 2021") == "2022-05 <- 2021"
    assert _drawable("pooled years 2019–2024") == "pooled years 2019-2024"
    rgb = np.zeros((240, 800, 3), np.uint8)
    with_year = annotate(rgb.copy(), _drawable("2022-05 ← 2021"))
    without = annotate(rgb.copy(), "2022-05")
    assert not np.array_equal(with_year, without)      # the source year really lands
