from pathlib import Path
import numpy as np
import types
import pytest
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


def test_assemble_skips_the_gif_when_render_gif_is_false(tmp_path):
    # render.gif: false is the opt-out for the slowest encode of a run; the MP4 must
    # still be written, and no stale .gif may be left behind.
    cfg = _cfg(tmp_path)
    cfg.gif = False
    frames = [np.zeros((16, 16, 3), np.uint8), np.full((16, 16, 3), 255, np.uint8)]
    paths = assemble(frames, cfg)
    assert [p.suffix for p in paths] == [".mp4"]
    assert paths[0].exists()
    assert not (tmp_path / f"{cfg.name}.gif").exists()


def test_assemble_fails_fast_when_mp4_fails_and_the_gif_is_disabled(tmp_path, monkeypatch):
    # With the GIF off there is no fallback animation, so silently returning only the
    # PNGs would look like a successful render that produced no video at all.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.gif = False
    monkeypatch.setattr(r, "_write_mp4", _boom)
    with pytest.raises(RuntimeError, match="render.gif"):
        assemble([np.zeros((16, 16, 3), np.uint8)], cfg)


def _boom(*a, **kw):
    raise OSError("no ffmpeg")


def test_render_gif_false_still_returns_mp4_and_pngs(tmp_path):
    # End to end through render(): the return contract cli/api/gui read (an .mp4 and
    # the per-frame .pngs) survives switching the GIF off.
    cfg = _cfg(tmp_path)
    cfg.gif = False

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((16, 16)), np.ones((16, 16), dtype=bool)

    paths = render([Frame("2022-01", object()), Frame("2022-02", object())],
                   cfg, fetch=fake_fetch, geometry=None)
    assert [p.suffix for p in paths] == [".mp4", ".png", ".png"]
    assert all(p.exists() for p in paths)
    assert not any(p.suffix == ".gif" for p in paths)


def test_render_frames_false_skips_the_per_frame_pngs(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.frames = False

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((16, 16)), np.ones((16, 16), dtype=bool)

    paths = render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)
    assert {p.suffix for p in paths} == {".mp4", ".gif"}
    assert not list(tmp_path.glob("*.png"))


def test_render_writes_gif_and_frames_by_default(tmp_path):
    # The defaults must be unchanged — an existing config keeps every output it had.
    cfg = _cfg(tmp_path)
    assert not hasattr(cfg, "gif") and not hasattr(cfg, "frames")

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((16, 16)), np.ones((16, 16), dtype=bool)

    paths = render([Frame("2022-01", object())], cfg, fetch=fake_fetch, geometry=None)
    assert {p.suffix for p in paths} == {".mp4", ".gif", ".png"}


@pytest.mark.parametrize("workers", [1, 4])
def test_write_frames_names_and_orders_the_pngs(tmp_path, workers):
    # Threaded encoding must not reorder the returned paths (callers zip them against
    # the frame list) nor corrupt a file.
    from gee_animation.render import _write_frames
    labels = [f"2022-{m:02d}" for m in range(1, 8)]
    frames = [np.full((12, 10, 3), 10 * i, np.uint8) for i in range(len(labels))]
    paths = _write_frames(tmp_path, "anim", frames, labels, workers=workers)
    assert [p.name for p in paths] == [f"anim_{lb}.png" for lb in labels]
    for i, p in enumerate(paths):
        arr = np.asarray(Image.open(p).convert("RGB"))
        assert arr.shape == (12, 10, 3)
        assert np.array_equal(arr, frames[i])       # readable and byte-for-byte


def test_write_frames_is_identical_threaded_and_serial(tmp_path):
    from gee_animation.render import _write_frames
    labels = ["a", "b", "c"]
    rng = np.random.default_rng(11)
    frames = [rng.integers(0, 256, (20, 24, 3), dtype=np.uint8) for _ in labels]
    serial = (tmp_path / "s")
    threaded = (tmp_path / "t")
    serial.mkdir()
    threaded.mkdir()
    a = _write_frames(serial, "x", frames, labels, workers=1)
    b = _write_frames(threaded, "x", frames, labels, workers=4)
    assert [p.name for p in a] == [p.name for p in b]
    assert [p.read_bytes() for p in a] == [p.read_bytes() for p in b]


def test_write_frames_propagates_a_failure_from_a_worker(tmp_path):
    # A silently-swallowed encode error would leave a run reporting PNGs it never wrote.
    from gee_animation.render import _write_frames
    frames = [np.zeros((4, 4, 3), np.uint8)] * 3
    with pytest.raises(Exception):
        _write_frames(tmp_path / "does-not-exist", "x", frames, ["a", "b", "c"], workers=4)


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


def _full_frame_blend(rgb, casing_mask, core_mask, color, casing=(0, 0, 0)):
    """The original whole-frame `_composite_region`, verbatim — the byte-for-byte
    reference the bounding-box version must reproduce."""
    out = rgb.astype(np.float32)
    for mask, rgb_color in ((casing_mask, casing), (core_mask, color)):
        a = mask[..., None]
        out = out * (1 - a) + np.asarray(rgb_color, dtype=np.float32) * a
    return out.astype(np.uint8)


def test_composite_region_matches_a_full_frame_blend():
    # The bbox optimisation must be byte-identical to blending the whole frame — an
    # "an outline was drawn" assertion would not catch a half-pixel alpha drift.
    from gee_animation.render import _composite_region, _region_masks, REGION_OUTLINE_RGB
    h, w = 96, 130
    rings = [[(0.2, 0.2), (0.8, 0.25), (0.75, 0.8), (0.2, 0.2)]]   # diagonals -> partial alphas
    casing, core = _region_masks((0.0, 0.0, 1.0, 1.0), rings, (h, w), 2)
    assert 0 < float((casing > 0).mean()) < 0.5, "sanity: outline must be a small subset"
    assert np.any((core > 0) & (core < 1)), "sanity: antialiased (fractional) alpha present"

    rng = np.random.default_rng(7)
    frames = [
        rng.integers(0, 256, (h, w, 3), dtype=np.uint8),          # uint8 (post-upscale)
        rng.random((h, w, 3)) * 255.0,                            # float64 (colorize output)
        np.full((h, w, 3), 127.9999999, dtype=np.float64),        # truncation edge
    ]
    for rgb in frames:
        got = _composite_region(rgb, casing, core, color=REGION_OUTLINE_RGB)
        want = _full_frame_blend(rgb, casing, core, REGION_OUTLINE_RGB)
        assert got.dtype == want.dtype == np.uint8
        assert np.array_equal(got, want)
        assert got is not rgb                       # never mutates the caller's frame

    # ...and degenerate masks: all-zero (nothing drawn) and fully-covering.
    zero = np.zeros((h, w), np.float32)
    one = np.ones((h, w), np.float32)
    for masks in ((zero, zero), (one, one), (casing, zero), (zero, core)):
        rgb = frames[0]
        assert np.array_equal(_composite_region(rgb, *masks, color=REGION_OUTLINE_RGB),
                              _full_frame_blend(rgb, *masks, REGION_OUTLINE_RGB))


def test_composite_region_leaves_untouched_pixels_bit_exact():
    # The pixels outside the outline must come through the (uint8) fast path
    # completely unmodified — not merely "close".
    from gee_animation.render import _composite_region, REGION_OUTLINE_RGB
    rng = np.random.default_rng(3)
    rgb = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
    casing = np.zeros((40, 40), np.float32)
    core = np.zeros((40, 40), np.float32)
    casing[10, 10] = core[10, 10] = 1.0
    out = _composite_region(rgb, casing, core)
    untouched = np.ones((40, 40), bool)
    untouched[10, 10] = False
    assert np.array_equal(out[untouched], rgb[untouched])
    assert tuple(out[10, 10]) == tuple(np.asarray(REGION_OUTLINE_RGB, np.uint8))


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


def test_assemble_stream_writes_mp4_from_an_iterator(tmp_path):
    from gee_animation.render import assemble_stream
    cfg = _cfg(tmp_path)
    frames = (np.full((16, 16, 3), v, np.uint8) for v in (0, 128, 255))
    paths = assemble_stream(frames, cfg)
    assert any(p.suffix == ".mp4" for p in paths)
    assert all(p.exists() for p in paths)


def test_assemble_stream_does_not_retain_every_frame(tmp_path):
    """Peak retained frames must not scale with the sequence length — this is what
    makes 600-frame interpolated runs possible at all.

    Liveness is measured with weakrefs rather than by counting the producer's own
    references: the producer drops its reference on every iteration, so only the
    encoder can keep a frame alive. An implementation that collects the frames in a
    list before encoding keeps all 40 alive and fails here.
    """
    import gc
    import weakref
    from gee_animation.render import assemble_stream
    cfg = _cfg(tmp_path)
    cfg.gif = False
    refs: list = []
    peak = 0

    def gen():
        nonlocal peak
        for i in range(40):
            a = np.full((16, 16, 3), i % 256, np.uint8)
            refs.append(weakref.ref(a))
            yield a
            del a                     # producer drops its own reference each iteration
            gc.collect()
            peak = max(peak, sum(r() is not None for r in refs))

    assemble_stream(gen(), cfg)
    assert peak <= 2, f"held up to {peak} frames at once"


def test_assemble_still_accepts_a_list(tmp_path):
    cfg = _cfg(tmp_path)
    paths = assemble([np.zeros((16, 16, 3), np.uint8)] * 2, cfg)
    assert any(p.suffix == ".mp4" for p in paths)


def test_assemble_stream_drains_the_iterator_for_the_gif_when_mp4_fails(tmp_path,
                                                                       monkeypatch):
    # The GIF is the fallback for a failed MP4, so it must still contain every frame
    # even though the MP4 pass is what was pulling them off the iterator.
    import imageio.v2 as imageio
    import gee_animation.render as r
    from gee_animation.render import assemble_stream
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(r, "_write_mp4", _boom)
    frames = (np.full((16, 16, 3), v, np.uint8) for v in (0, 80, 160, 240))
    paths = assemble_stream(frames, cfg)
    assert [p.suffix for p in paths] == [".gif"]
    assert len(imageio.mimread(paths[0])) == 4


def test_assemble_stream_removes_a_half_written_mp4(tmp_path, monkeypatch):
    # A truncated .mp4 next to the GIF looks like a successful render; the failed
    # file must not survive the fallback.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)

    def half_write(path, frames, fps):
        Path(path).write_bytes(b"garbage")
        raise RuntimeError("encoder died mid-stream")
    monkeypatch.setattr(r, "_write_mp4", half_write)
    paths = r.assemble_stream(iter([np.zeros((16, 16, 3), np.uint8)]), cfg)
    assert [p.suffix for p in paths] == [".gif"]
    assert not (tmp_path / f"{cfg.name}.mp4").exists()


def test_write_mp4_closes_its_writer_when_a_frame_fails(tmp_path, monkeypatch):
    # A leaked ffmpeg writer hangs or corrupts the output, so the close must happen
    # on the failure path too.
    import gee_animation.render as r
    closed = []

    class FakeWriter:
        def append_data(self, frame):
            raise ValueError("bad frame")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(r.imageio, "get_writer", lambda *a, **k: FakeWriter())
    with pytest.raises(ValueError):
        r._write_mp4(tmp_path / "x.mp4", [np.zeros((16, 16, 3), np.uint8)], 2)
    assert closed == [True]


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
    assert (0.0, "0", "m", True) in spans
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
    # The 0 label is droppable so it can yield when a lopsided range parks it on top
    # of the min label (see test_colorbar_drops_zero_label_when_it_collides_with_min).
    assert zero_ticks[0] == (0.0, "0", "m", True)


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
    # min and max pin the range and never drop; the 0 and mid labels both yield.
    assert [droppable for *_, droppable in ticks] == [False, True, True, False]

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


def test_colorbar_drops_zero_label_when_it_collides_with_min():
    """A lopsided range puts 0 close to the min end: over -3..46 the zero tick sits at
    6% of the ramp and its label lands on the "-3", which drew as "-30". The min label
    defines the range and must survive; the zero label yields."""
    from gee_animation.render import _colorbar_ticks
    ticks = _colorbar_ticks(-3, 46, "°C")
    zero = [t for t in ticks if t[1] == "0"]
    assert zero and zero[0][3] is True, "the 0 label must be droppable"

    w, h = 300, 80
    cfg = types.SimpleNamespace(index="lst", viz_min=-3.0, viz_max=46.0,
                                palette=["#0000ff", "#ff0000"])
    out = add_colorbar(np.zeros((h, w, 3), np.uint8), cfg)
    # The min label "-3" occupies the left end; nothing may be drawn immediately to its
    # right at label height, which is what the overlapping "0" did.
    bar_w = max(20, int(w * 0.4))
    band = out[:, : bar_w // 4, :]
    assert band.max() > 0, "the min label should still be drawn"


def test_info_text_pooled_note_matches_the_strategy():
    """gap_fill keeps the requested year where it has data, so labelling the whole run
    "cosmetic" overstates it; least_cloudy/median do re-pick every frame, so for those
    the blanket warning is right."""
    from gee_animation.render import _info_text
    base = dict(index="ndvi", pool_years=[2018, 2024])
    gap = _info_text(types.SimpleNamespace(**base, pool_strategy="gap_fill"))
    cosmetic = _info_text(types.SimpleNamespace(**base, pool_strategy="least_cloudy"))
    assert "pooled years 2018-2024" in gap and "gap-filled" in gap
    assert "cosmetic" not in gap
    assert "cosmetic" in cosmetic and "gap-filled" not in cosmetic
    # unpooled runs carry no pooling note at all
    assert "pooled years" not in _info_text(types.SimpleNamespace(index="ndvi"))


def test_colorbar_draws_a_backing_panel_for_contrast():
    """The ramp and its white labels sit on the imagery, which can be any colour —
    white-on-pale-yellow was unreadable. A translucent dark panel must back the whole
    block, as draw_scale_bar already does for its own label."""
    w, h = 400, 120
    bright = np.full((h, w, 3), 255, np.uint8)      # worst case: white imagery
    cfg = types.SimpleNamespace(index="lst", viz_min=-3.0, viz_max=46.0,
                                palette=["#0000ff", "#ff0000"])
    out = add_colorbar(bright, cfg)

    # Sample just under the ramp, where the tick labels are drawn: on a white frame
    # that band must have been darkened, or the labels are invisible.
    bar_h = max(6, h // 20)
    label_band = out[bar_h + 6 : bar_h + 14, : int(w * 0.4)]
    assert label_band.mean() < 200, "no backing panel behind the colorbar labels"
    # Well away from the colorbar the imagery is untouched.
    assert out[h - 4, w - 4].tolist() == [255, 255, 255]


# --- frame interpolation (render.interpolate) -------------------------------------

def _flat_fetch(image, cfg, geometry=None):
    return np.zeros((20, 20)), np.ones((20, 20), dtype=bool)


def test_render_interpolates_between_frames(tmp_path, monkeypatch):
    # Generated frames carry their own "a -> b  NN%" label; the observed ones keep
    # exactly the text they had before.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.interpolate = 2
    drawn = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label: (drawn.append(label) or rgb))
    render([Frame("2022-05", object()), Frame("2022-06", object())], cfg,
           fetch=_flat_fetch, geometry=None)
    assert drawn == ["2022-05", "2022-05 -> 2022-06  33%",
                     "2022-05 -> 2022-06  67%", "2022-06"]


def test_render_scales_generated_frames_with_the_gap(tmp_path, monkeypatch):
    # A three-period absence gets three times the generated frames, so playback speed
    # tracks elapsed time instead of implying the change happened in one step.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.interpolate = 1
    drawn = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label: (drawn.append(label) or rgb))
    render([Frame("2022-05", object()), Frame("2022-08", object())], cfg,
           fetch=_flat_fetch, geometry=None)
    assert len(drawn) == 5                       # 2 observed + 3 * 1 generated
    assert drawn[0] == "2022-05" and drawn[-1] == "2022-08"


def test_render_interpolate_zero_is_untouched(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.interpolate = 0
    drawn = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label: (drawn.append(label) or rgb))
    render([Frame("2022-05", object(), 3), Frame("2022-06", object())], cfg,
           fetch=_flat_fetch, geometry=None)
    assert drawn == ["2022-05  n=3", "2022-06"]


@pytest.mark.parametrize("mode", ["auto", "crossfade", "data"])
@pytest.mark.parametrize("index", ["ndvi", "rgb"])
def test_render_observed_frames_are_byte_identical_with_and_without_interpolation(
        tmp_path, mode, index):
    """The central guarantee: interpolation adds frames, it never alters a real one.

    Compared as rendered PNG bytes, over every mode/index combination that resolves —
    `data` on a composite is rejected by config.validate, so it is skipped here rather
    than pinning behaviour no valid run can reach.

    The two observations carry **distinct** imagery and complementary cloud holes, so
    the property has teeth: with one shared shot every generated frame equals the
    observed ones and an implementation that blended a neighbour into a real frame
    would still pass. Here A's hole is where B has data (and vice versa), so any
    bleed-through changes both the imagery and the grey no-data patches.
    """
    if mode == "data" and index == "rgb":
        pytest.skip("config.validate rejects interpolate_mode: data on a composite")
    composite = index == "rgb"
    rng = np.random.default_rng(3)

    def _shot(offset, hole):
        vals = (rng.random((18, 22, 3)) * 255 if composite
                else rng.random((18, 22)) - offset)
        valid = np.ones((18, 22), dtype=bool)
        valid[4:9, hole] = False                     # a cloud hole, different per frame
        return vals, valid

    shots = {"A": _shot(0.2, slice(2, 9)), "B": _shot(0.6, slice(12, 19))}

    def _run(sub, steps):
        cfg = _cfg(tmp_path / sub)
        cfg.index = index
        cfg.palette = [] if composite else cfg.palette
        cfg.interpolate = steps
        cfg.interpolate_mode = mode
        render([Frame("2022-05", "A", 4), Frame("2022-06", "B", 1, 2021)],
               cfg, fetch=lambda image, c, geometry=None: shots[image], geometry=None)

    _run("a", 0)
    _run("b", 3)
    for label in ("2022-05", "2022-06"):
        a = (tmp_path / "a" / f"anim_{label}.png").read_bytes()
        b = (tmp_path / "b" / f"anim_{label}.png").read_bytes()
        assert a == b, f"{label} changed when interpolation was enabled"


def test_render_writes_pngs_for_observed_frames_only(tmp_path):
    # 600 PNGs of which 540 are generated would be noise, and a generated frame has no
    # period key to name a file after.
    cfg = _cfg(tmp_path)
    cfg.interpolate = 5
    paths = render([Frame("2022-05", object()), Frame("2022-06", object())], cfg,
                   fetch=_flat_fetch, geometry=None)
    pngs = [p for p in paths if p.suffix == ".png"]
    assert sorted(p.stem for p in pngs) == ["anim_2022-05", "anim_2022-06"]
    assert sorted(p.stem for p in tmp_path.glob("*.png")) == ["anim_2022-05", "anim_2022-06"]


def test_render_pooled_png_filename_survives_interpolation(tmp_path):
    # The PNG is named from the clean period key, never from the drawn display text
    # (which carries the provenance arrow and the scene count).
    cfg = _cfg(tmp_path)
    cfg.interpolate = 2
    paths = render([Frame("2022-05", object(), 1, 2021), Frame("2022-06", object(), 2)],
                   cfg, fetch=_flat_fetch, geometry=None)
    pngs = [p for p in paths if p.suffix == ".png"]
    assert [p.name for p in pngs] == ["anim_2022-05.png", "anim_2022-06.png"]


def test_render_interpolated_frames_reach_the_encoder(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.interpolate = 3
    seen = []
    monkeypatch.setattr(r, "_write_mp4",
                        lambda path, frames, fps: seen.extend(list(frames)))
    render([Frame("2022-05", object()), Frame("2022-06", object())], cfg,
           fetch=_flat_fetch, geometry=None)
    assert len(seen) == 5                        # 2 observed + 3 generated


def test_info_text_names_the_interpolation():
    from gee_animation.render import _info_text
    cfg = types.SimpleNamespace(index="ndvi", interpolate=10)
    assert "interpolated: 10 frames between observations" in _info_text(cfg)
    assert "interpolated" not in _info_text(types.SimpleNamespace(index="ndvi",
                                                                 interpolate=0))


def _seq(cfg, frames, fetch, composite=False):
    from gee_animation.render import _imagery_sequence
    return list(_imagery_sequence(frames, cfg, fetch, None, 1, composite))


def test_imagery_sequence_data_mode_colorizes_the_blended_index(tmp_path):
    """`auto` on a single-band index interpolates index units *before* colouring, so a
    generated frame is coloured with the run's fixed viz range and the colour bar stays
    exactly valid. A three-stop palette makes the two modes distinguishable: blending
    -1 and 1 gives 0 -> the middle stop, while blending their colours gives grey."""
    cfg = _cfg(tmp_path)
    cfg.viz_min, cfg.viz_max = -1.0, 1.0
    cfg.palette = ["#000000", "#ff0000", "#ffffff"]
    cfg.interpolate = 1

    vals = iter([-1.0, 1.0])

    def fetch(image, cfg_, geometry=None):
        return np.full((4, 4), next(vals)), np.ones((4, 4), dtype=bool)

    out = _seq(cfg, [Frame("2022-05", object()), Frame("2022-06", object())], fetch)
    assert [lb for *_x, lb in out] == ["2022-05", None, "2022-06"]
    mid = out[1][0]
    assert np.allclose(mid[0, 0], [255, 0, 0])        # colorize(0.0) — the middle stop


def test_imagery_sequence_crossfade_blends_finished_colour(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.viz_min, cfg.viz_max = -1.0, 1.0
    cfg.palette = ["#000000", "#ff0000", "#ffffff"]
    cfg.interpolate = 1
    cfg.interpolate_mode = "crossfade"

    vals = iter([-1.0, 1.0])

    def fetch(image, cfg_, geometry=None):
        return np.full((4, 4), next(vals)), np.ones((4, 4), dtype=bool)

    out = _seq(cfg, [Frame("2022-05", object()), Frame("2022-06", object())], fetch)
    assert np.allclose(out[1][0][0, 0], [127.5, 127.5, 127.5])   # black/white midpoint


def test_imagery_sequence_auto_crossfades_a_composite(tmp_path):
    """A composite arrives from EE already coloured — there are no index units left to
    interpolate — and where only one endpoint observed a pixel its colour is *held*,
    not faded toward the other frame, so a cloud hole doesn't pulse on every transition.
    """
    cfg = _cfg(tmp_path, name="rgbtest")
    cfg.index = "rgb"
    cfg.palette = []
    cfg.interpolate = 1

    valid_b = np.ones((4, 6), dtype=bool)
    valid_b[:, :3] = False                       # left half unobserved in frame B
    shots = iter([(np.full((4, 6, 3), 100.0), np.ones((4, 6), dtype=bool)),
                  (np.full((4, 6, 3), 200.0), valid_b)])

    def fetch(image, cfg_, geometry=None):
        return next(shots)

    out = _seq(cfg, [Frame("2022-05", object()), Frame("2022-06", object())], fetch,
               composite=True)
    mid, mid_valid, text, label = out[1]
    assert label is None and text == "2022-05 -> 2022-06  50%"
    assert np.allclose(mid[:, :3], 100.0)        # only A observed it -> A's colour held
    assert np.allclose(mid[:, 3:], 150.0)        # both observed -> blended
    assert mid_valid.all()                       # a pixel A saw is not no-data


def test_imagery_sequence_does_not_retain_every_observation(tmp_path):
    """Peak retained *observations* must not scale with the run length.

    Streaming the generated frames is only half the win: an implementation that
    drains every fetch into a list before yielding anything still holds N
    full-resolution observations (a 60-observation 1440p crossfade run is ~2.5 GB).
    `expand` only ever needs a pair, so at most two may be alive at once.

    Liveness is measured with weakrefs, not by counting the producer's own list:
    a test that inspects a list the producer clears would pass against the
    accumulating implementation. Here only `_imagery_sequence` can keep a fetched
    array alive, so listing them all makes `peak` == the observation count.
    """
    import gc
    import weakref
    from gee_animation.render import _imagery_sequence
    cfg = _cfg(tmp_path)
    cfg.interpolate = 2
    refs: list = []

    def fetch(image, cfg_, geometry=None):
        arr = np.zeros((8, 8))
        refs.append(weakref.ref(arr))
        return arr, np.ones((8, 8), dtype=bool)

    frames = [Frame(f"2022-{m:02d}", object()) for m in range(1, 13)]
    peak, at_first_frame = 0, None
    for out in _imagery_sequence(frames, cfg, fetch, None, 1, False):
        if at_first_frame is None:
            at_first_frame = len(refs)
        del out                       # consumer drops its own reference each frame
        gc.collect()
        peak = max(peak, sum(r() is not None for r in refs))
    assert len(refs) == 12, "every observation should have been fetched"
    assert peak <= 2, f"held up to {peak} observations at once"
    # Pipelining: drawing starts on the first frame, not after the last fetch — a
    # run that fetched everything up front would appear to hang, then burst.
    assert at_first_frame == 2, f"{at_first_frame} fetches before the first frame"


def test_render_propagates_a_producer_failure_instead_of_a_truncated_animation(tmp_path):
    """A fetch that fails mid-sequence must fail the run.

    `assemble_stream` catches Exception around the MP4 write so a missing ffmpeg can
    fall back to a GIF — and a frame *producer* raising surfaces at exactly the same
    place. Without a marker the run logs "MP4 write failed", writes a GIF of however
    many frames happened to arrive, and reports that truncation as success.
    """
    cfg = _cfg(tmp_path)
    cfg.workers = 1                              # deterministic: call 3 == frame 3
    seen = []

    def flaky_fetch(image, cfg_, geometry=None):
        seen.append(1)
        if len(seen) == 3:
            raise OSError("Earth Engine hiccup")
        return np.zeros((16, 16)), np.ones((16, 16), dtype=bool)

    frames = [Frame(f"2022-{m:02d}", object()) for m in range(1, 7)]
    with pytest.raises(RuntimeError, match="2022-03"):
        render(frames, cfg, fetch=flaky_fetch, geometry=None)
    assert not (tmp_path / f"{cfg.name}.gif").exists()
    assert not (tmp_path / f"{cfg.name}.mp4").exists()


@pytest.mark.parametrize("workers", [1, 4])
def test_render_propagates_a_producer_failure_while_interpolating(tmp_path, workers):
    """A mid-sequence fetch failure must fail the run on both the serial and the
    threaded fetch path — `expand` streams the fetch generator, so the exception now
    surfaces through the sliding window rather than out of a materialising list."""
    cfg = _cfg(tmp_path)
    cfg.workers = workers
    cfg.interpolate = 4
    # Keyed off the frame's image, not the call count: with workers > 1 the fetches
    # are not issued in frame order, so "the second call" is not "the second frame".
    frames = [Frame(f"2022-{m:02d}", f"img{m}") for m in range(1, 5)]

    def flaky_fetch(image, cfg_, geometry=None):
        if image == "img2":
            raise OSError("Earth Engine hiccup")
        return np.zeros((16, 16)), np.ones((16, 16), dtype=bool)

    with pytest.raises(RuntimeError, match="2022-02"):
        render(frames, cfg, fetch=flaky_fetch, geometry=None)
    assert not (tmp_path / f"{cfg.name}.gif").exists()
    assert not (tmp_path / f"{cfg.name}.mp4").exists()


def test_assemble_stream_reraises_a_producer_failure(tmp_path):
    # The unit-level guarantee behind the two render() tests above.
    from gee_animation.render import _produced, assemble_stream
    cfg = _cfg(tmp_path)

    def gen():
        for i in range(6):
            if i == 3:
                raise ValueError("producer died")
            yield np.full((16, 16, 3), 40 * i, np.uint8)

    with pytest.raises(RuntimeError, match="producer died"):
        assemble_stream(_produced(gen()), cfg)
    assert not (tmp_path / f"{cfg.name}.gif").exists()
    assert not (tmp_path / f"{cfg.name}.mp4").exists()


def test_render_defaults_the_gif_off_when_interpolating(tmp_path):
    # Several hundred quantized frames is an enormous, slow GIF; the MP4 and the
    # observed-frame PNGs are unaffected.
    cfg = _cfg(tmp_path)
    cfg.gif = None                               # unset (RunConfig's default)
    cfg.interpolate = 3
    paths = render([Frame("2022-05", object()), Frame("2022-06", object())], cfg,
                   fetch=_flat_fetch, geometry=None)
    assert not any(p.suffix == ".gif" for p in paths)
    assert any(p.suffix == ".mp4" for p in paths)


def test_render_explicit_gif_true_wins_over_the_interpolation_default(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.gif = True
    cfg.interpolate = 3
    paths = render([Frame("2022-05", object()), Frame("2022-06", object())], cfg,
                   fetch=_flat_fetch, geometry=None)
    assert any(p.suffix == ".gif" and p.exists() for p in paths)


def test_render_unset_gif_still_defaults_on_without_interpolation(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.gif = None
    paths = render([Frame("2022-05", object())], cfg, fetch=_flat_fetch, geometry=None)
    assert any(p.suffix == ".gif" and p.exists() for p in paths)
