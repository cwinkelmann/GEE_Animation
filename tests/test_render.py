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
        sensor="sentinel2", index="ndvi", viz_min=-0.2, viz_max=0.9, palette=["#000000", "#ffffff"],
        fps=fps, scale=20, dimensions=64, frame_aoi={"bbox": [0, 0, 1, 1]},
    )


# --- bundled font (DejaVu Sans replaces Pillow's bitmap default) ------------------

def test_font_is_bundled_truetype_with_needed_glyphs():
    # Pillow's own bundled default (Aileron, or the pre-10.1 bitmap font on older
    # Pillow) is what we are replacing. getbbox() alone isn't proof of a real glyph
    # -- FreeType renders a nonzero-width notdef box for *missing* characters too
    # (verified: Aileron's cmap has no U+2190/U+2013, yet getbbox still returns a
    # nonzero box for both) -- so pin the font identity via its name table as well.
    from gee_animation.render import _font
    from PIL import ImageFont
    f = _font(24)
    assert isinstance(f, ImageFont.FreeTypeFont)
    assert f.getname()[0] == "DejaVu Sans"
    for ch in ("←", "–", "°"):
        box = f.getbbox(ch)
        assert box[2] > box[0], f"no glyph for {ch!r}"


def test_drawable_no_longer_mangles_the_arrow():
    # _drawable is kept as a seam (call sites unchanged) but the fold table is now
    # empty: DejaVu draws "←"/"–" directly, so nothing needs replacing.
    from gee_animation.render import _drawable
    assert _drawable("2022-05 ← 2021") == "2022-05 ← 2021"


def test_font_loads_via_importlib_resources_from_the_installed_package():
    # Guards against the classic setuptools package-data trap: the font must resolve
    # as installed package data (importlib.resources), not via a path relative to
    # this repo checkout, or it would silently vanish from a built wheel/install.
    from importlib import resources
    ref = resources.files("gee_animation.fonts") / "DejaVuSans.ttf"
    assert ref.is_file()
    with resources.as_file(ref) as path:
        from PIL import ImageFont
        f = ImageFont.truetype(str(path), size=24)
        assert isinstance(f, ImageFont.FreeTypeFont)


def test_font_falls_back_to_pillow_default_when_bundle_is_missing(monkeypatch, caplog):
    # If the bundled TTF is missing/corrupt (e.g. a broken install), _font must not
    # raise -- frame generation should degrade to Pillow's default font, not crash.
    import logging
    import gee_animation.render as r
    monkeypatch.setattr(r, "_FONT_FILENAME", "does-not-exist.ttf")
    r._font.cache_clear()
    try:
        with caplog.at_level(logging.WARNING):
            f = r._font(24)
        assert f is not None
        assert "does-not-exist.ttf" in caplog.text or "font" in caplog.text.lower()
    finally:
        r._font.cache_clear()   # don't leak the monkeypatched miss into other tests


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


def _text_spy(monkeypatch):
    """Every string PIL is actually asked to draw, in order."""
    seen = []
    real = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text",
                        lambda self, xy, text, *a, **k: (seen.append(text)
                                                        or real(self, xy, text, *a, **k)))
    return seen


def test_draw_info_bar_shrinks_a_long_title_to_fit_the_frame():
    # Reviewer-verified live overflow: a header line measured 2239px wide in a 1920px
    # frame and got cut mid-word. draw_info_bar must shrink the font until the line
    # fits inside the frame instead of letting Pillow draw past the right edge.
    from gee_animation.render import draw_info_bar
    title = "Grumsiner Forst — UNESCO World Heritage beech forest, Brandenburg, Germany"
    w, h = 500, 1200          # narrow frame relative to this long title
    rgb = np.zeros((h, w, 3), np.uint8)
    out = draw_info_bar(rgb, title)
    bar_h = h // 12
    # no ink in the rightmost columns of the bar row band -> text stayed inside frame
    assert out[:bar_h, -3:].sum() == 0


def test_draw_info_bar_draws_the_title_larger_than_the_annotation_font():
    # Line 1 is the frame's headline — it must read bigger than the small print, not
    # at the same size as the bottom-bar label.
    from gee_animation.render import draw_info_bar, _annot_scale
    title = "Vegetation greenness (NDVI)"
    w, h = 800, 300
    out = draw_info_bar(np.zeros((h, w, 3), np.uint8), title)

    annot_font, _ = _annot_scale(h)
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    tb = scratch.textbbox((0, 0), title, font=annot_font)
    ink_cols = np.nonzero(out[:h // 12].sum(axis=(0, 2)))[0]
    assert ink_cols.size
    # the title fits (so it was not shrunk) and is wider than _annot_scale would draw it
    assert ink_cols.max() - ink_cols.min() > (tb[2] - tb[0])
    assert ink_cols.max() < w


def test_draw_info_bar_bar_height_is_one_line_or_two(monkeypatch):
    # The bar rectangle feeds the _margins fixed point (see
    # test_margins_match_the_bar_height_of_the_padded_frame): exactly one bar height
    # for a title alone, exactly two when a second line exists, and never more.
    from gee_animation.render import draw_info_bar, _bar_h
    w, h = 500, 300
    bar_h = _bar_h(h)
    one = draw_info_bar(np.zeros((h, w, 3), np.uint8), "Vegetation greenness (NDVI)")
    assert one[:bar_h].sum() > 0 and one[bar_h:].sum() == 0

    two = draw_info_bar(np.zeros((h, w, 3), np.uint8), "Title",
                        "Brandenburg, Germany", "gap-filled from 2018–2024")
    assert two[bar_h:2 * bar_h].sum() > 0        # the second line is drawn...
    assert two[2 * bar_h:].sum() == 0            # ...and nothing below the doubled bar


def test_header_text_uses_the_index_display_name_when_no_title_is_set():
    # (b) No title configured: the frame still has to say WHAT it shows, in words a
    # non-specialist reads — not "NDVI = (NIR - Red) / (NIR + Red)".
    from gee_animation.render import _header_text
    title, subtitle, caveats = _header_text(types.SimpleNamespace(index="ndvi"))
    assert title == "Vegetation greenness (NDVI)"
    assert (subtitle, caveats) == ("", "")
    assert _header_text(types.SimpleNamespace(index="lst_smw"))[0] == \
        "Land surface temperature (mono-window)"


def test_header_text_prefers_an_explicit_title():
    # (a) cfg.title wins over the registry name — the run knows what it is about.
    from gee_animation.render import _header_text
    cfg = types.SimpleNamespace(index="ndvi", title="Grumsiner Forst",
                                subtitle="Brandenburg, Germany")
    assert _header_text(cfg)[:2] == ("Grumsiner Forst", "Brandenburg, Germany")


def test_header_never_carries_the_formula_or_the_band_list():
    # (d) Review M2: the scaling coefficient read as debug output and cost credibility.
    # The formula and band list live in the method doc now, on no frame.
    from gee_animation.render import _header_text
    from gee_animation.products import INDICES
    for index in INDICES:
        cfg = types.SimpleNamespace(index=index, pool_years=[2018, 2024],
                                    pool_strategy="gap_fill", interpolate=4)
        drawn = " ".join(_header_text(cfg))
        assert "bands" not in drawn.lower()
        for fragment in ("0.00341802", "273.15", "(NIR - Red)", "Ermida", "TsHARP"):
            assert fragment not in drawn, f"{index}: {fragment!r} still on the frame"


def test_header_never_draws_the_formula_on_an_lst_frame(monkeypatch, tmp_path):
    # (d) end to end: the strings must not survive anywhere in a real lst render.
    import gee_animation.render as r
    cfg = _cfg(tmp_path, name="lstframe")
    cfg.index, cfg.viz_min, cfg.viz_max = "lst", -10.0, 40.0
    seen = _text_spy(monkeypatch)

    def fake_fetch(image, cfg_, geometry=None):
        return np.zeros((300, 600)), np.ones((300, 600), dtype=bool)

    r.render([Frame("2022-06", object(), 2)], cfg, fetch=fake_fetch, geometry=None)
    joined = " ".join(seen)
    assert "Land surface temperature" in joined          # it still says what it is
    for fragment in ("0.00341802", "149.0", "273.15", "bands", "ST_B"):
        assert fragment not in joined


def test_header_caveats_are_plain_language(monkeypatch):
    # (c) The pooled/interpolated notes must survive the rewrite — reworded, not
    # dropped. Each one still names the year range / frame count it warns about.
    from gee_animation.render import _header_text
    base = dict(index="ndvi", pool_years=[2018, 2024])
    gap = _header_text(types.SimpleNamespace(**base, pool_strategy="gap_fill"))[2]
    pooled = _header_text(types.SimpleNamespace(**base, pool_strategy="least_cloudy"))[2]
    interp = _header_text(types.SimpleNamespace(index="ndvi", interpolate=10))[2]
    assert gap == "gap-filled from 2018–2024"
    assert pooled == "every frame re-picked from 2018–2024 — not a time series"
    assert interp == "10 generated frames between observations"
    # a plain run carries no caveat line at all
    assert _header_text(types.SimpleNamespace(index="ndvi"))[2] == ""
    # ...and both caveats at once are joined, neither dropped
    both = _header_text(types.SimpleNamespace(index="ndvi", pool_years=[2018, 2024],
                                              pool_strategy="gap_fill", interpolate=10))[2]
    assert both == "gap-filled from 2018–2024 · 10 generated frames between observations"


def test_line2_prefix_names_the_product_only_for_a_titled_composite():
    # A titled composite (rgb/cir) is the one case with no colorbar heading AND a
    # title that has displaced line 1's display-name fallback -- the product name
    # would otherwise appear nowhere on the frame. Every other combination is "",
    # because the gap it patches does not exist for them.
    from gee_animation.render import _line2_prefix
    titled_rgb = types.SimpleNamespace(index="rgb", title="Grumsin forest")
    assert _line2_prefix(titled_rgb) == "True colour"
    titled_cir = types.SimpleNamespace(index="cir", title="Grumsin forest")
    assert _line2_prefix(titled_cir) == "Colour infrared"
    # untitled composite: line 1 already falls back to the display name
    assert _line2_prefix(types.SimpleNamespace(index="rgb", title=None)) == ""
    assert _line2_prefix(types.SimpleNamespace(index="rgb")) == ""
    # titled single-band index: the colorbar heading already names it -- no doubling
    assert _line2_prefix(types.SimpleNamespace(index="ndvi", title="Grumsin forest")) == ""


def test_two_line_header_is_forced_by_a_titled_composite_alone():
    # Without a configured subtitle or caveat, a plain single-band+title run stays
    # one line (unchanged). A titled composite must still get its second line, or
    # the product name (the whole point of this fix) has nowhere to be drawn.
    from gee_animation.render import _two_line_header
    assert _two_line_header(types.SimpleNamespace(index="rgb",
                                                   title="Grumsin forest")) is True
    assert _two_line_header(types.SimpleNamespace(index="ndvi",
                                                   title="Grumsin forest")) is False


def test_header_line_two_truncates_the_subtitle_never_the_caveat(monkeypatch):
    # (c) Legibility floor / review M1: line 2 is held at (or near) the _annot_scale
    # size, so when it overflows something must give. The caveat outranks the
    # decoration: the SUBTITLE is what gets the "…".
    #
    # Re-derived after the full-canvas header fix, which lets line 2 give up a bounded
    # few percent (_LINE2_MIN_SCALE) before it starts trimming. The strict equality
    # below still holds for THIS input -- the subtitle here is ~125 chars, four times
    # too long for the row, so no size within the permitted band fits it and the
    # shrink is skipped entirely -- and the band itself is asserted separately.
    from gee_animation.render import draw_info_bar, _annot_scale
    w, h = 640, 300
    caveat = "every frame re-picked from 2018–2024 — not a time series"
    subtitle = ("Brandenburg, Germany, in the Schorfheide-Chorin Biosphere Reserve "
                "north-east of Berlin, mapped every month from Sentinel-2")
    seen = _text_spy(monkeypatch)
    draw_info_bar(np.zeros((h, w, 3), np.uint8), "Grumsiner Forst", subtitle, caveat)

    line2 = seen[1]
    assert caveat in line2, "the caveat must survive whole"
    assert subtitle not in line2 and "…" in line2, "the subtitle must be the one trimmed"
    # ...and it is still drawn at the legibility floor, not shrunk to fit
    annot_font, _ = _annot_scale(h)
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    assert scratch.textbbox((0, 0), line2, font=annot_font)[2] <= w

    from gee_animation.render import _fit_header_line2
    font, _text = _fit_header_line2(scratch, subtitle, caveat, annot_font.size, w - 8)
    assert font.size == annot_font.size, "no permitted size fits this subtitle at all"


def test_header_line_two_truncates_the_subtitle_never_the_composite_name_or_caveat(
        monkeypatch):
    # Extends the priority test above with a titled composite's `prefix`: THREE parts
    # on line 2 now instead of two, and the subtitle is still the only sacrificial one.
    # The product name is data-identity, exactly like the caveat -- neither may be
    # dropped or trimmed while the subtitle still has something left to give.
    from gee_animation.render import draw_info_bar, _annot_scale
    w, h = 640, 300
    prefix = "True colour"
    caveat = "every frame re-picked from 2018–2024 — not a time series"
    subtitle = ("Brandenburg, Germany, in the Schorfheide-Chorin Biosphere Reserve "
                "north-east of Berlin, mapped every month from Sentinel-2")
    seen = _text_spy(monkeypatch)
    draw_info_bar(np.zeros((h, w, 3), np.uint8), "Grumsin forest — vegetation",
                  subtitle, caveat, prefix)

    line2 = seen[1]
    assert line2.startswith(f"{prefix} · "), "the product name must survive whole, leading"
    assert caveat in line2, "the caveat must survive whole too"
    assert subtitle not in line2 and "…" in line2, "the subtitle must be the one trimmed"
    # ...and it is still drawn at the legibility floor, not shrunk to fit
    annot_font, _ = _annot_scale(h)
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    assert scratch.textbbox((0, 0), line2, font=annot_font)[2] <= w


# --- R: the header/bottom bar own the whole canvas width, not the imagery width ----
#
# Root cause behind two critical review findings. `draw_info_bar` and `annotate` lay
# text out against the width of the array they are handed, and `render()` used to hand
# them the bare *imagery* -- which on a letterboxed aspect is a fraction of the output
# (at 16:9 with a near-square AOI, ~890 px of a 1920 px canvas). So a configured
# subtitle and the Copernicus licence notice were truncated for want of room while
# more than half the frame's width sat empty and black beside them. `render()` now
# letterboxes the SIDE bars on before the label bars are drawn.

_CAVEAT_POOLED = "every frame re-picked from 2018–2024 — not a time series"
_SUBTITLE_30 = "Grumsiner Forst, Brandenburg D"          # 29 chars + the run's title


def _audience_cfg(tmp_path, preset, aspect, subtitle=_SUBTITLE_30, interpolate=2):
    """A pooled + interpolated + titled + subtitled run -- the exact shape the
    audience-communication keys were built for, and the one the header used to fail on."""
    cfg = _cfg(tmp_path, name="aud")
    cfg.sensor = "sentinel2"
    cfg.start, cfg.end = "2022-05-01", "2022-08-01"
    cfg.preset, cfg.aspect = preset, aspect
    cfg.title = "Grumsin forest — vegetation greenness"
    cfg.subtitle = subtitle
    cfg.pool_years, cfg.pool_strategy = [2018, 2024], "least_cloudy"
    cfg.interpolate = interpolate
    cfg.gif, cfg.frames = False, False
    return cfg


def _wide_fetch(image, cfg_, geometry=None):
    return np.zeros((100, 200)), np.ones((100, 200), dtype=bool)


def _wide_rgb_fetch(image, cfg_, geometry=None):
    """The composite (rgb/cir) counterpart to `_wide_fetch`: an H x W x 3 colour array,
    already coloured -- composites arrive from EE this way, never as index values."""
    return np.full((100, 200, 3), 123, dtype=float), np.ones((100, 200), dtype=bool)


@pytest.mark.parametrize("preset,aspect", [
    (768, "16:9"), ("480p", "16:9"), ("720p", "16:9"), ("1080p", "16:9"),
    (1920, "16:9"), ("4k", "16:9"),
    ("1080p", "match"),          # no side bars at all -- must still fit
    ("1080p", "4:3"), ("1080p", "1:1"),
])
def test_render_draws_subtitle_and_caveats_whole_on_a_pooled_interpolated_run(
        tmp_path, monkeypatch, preset, aspect):
    """Critical finding 1: with pool_years + interpolate set, the subtitle rendered as
    0 of 30 characters at 768, 890, 1280, 1920 AND 3840 px -- line 2's font scales with
    frame height, so the character budget was resolution-independent and the headline
    config key of this branch only worked on runs with no caveats, i.e. never on the
    pooled/interpolated runs it was written for. Both strings must now draw in full."""
    seen = _text_spy(monkeypatch)
    cfg = _audience_cfg(tmp_path, preset, aspect)
    render([Frame("2022-05", "A"), Frame("2022-06", "B")], cfg,
           fetch=_wide_fetch, geometry=None)
    line2 = next(t for t in seen if _CAVEAT_POOLED in t)
    assert cfg.subtitle in line2, "the configured subtitle must be drawn in full"
    assert "2 generated frames between observations" in line2, "caveats stay whole too"
    assert "…" not in line2


@pytest.mark.parametrize("preset", [768, 1920])
def test_render_draws_the_copernicus_notice_whole_on_every_frame(tmp_path, monkeypatch,
                                                                 preset):
    """Critical finding 2: 'Contains modified Copernicus Sentinel …' truncated mid-word
    at every resolution from 768 px to 4K. That string is a licence term, not a caption
    -- a clipped one is not the notice the Copernicus licence asks for. Checked on the
    generated (hollow-marker) frames as well as the observed ones: interpolation pushes
    the label wider ('between May and June 2022 · 33%'), which squeezes the credit."""
    seen = _text_spy(monkeypatch)
    cfg = _audience_cfg(tmp_path, preset, "16:9")
    render([Frame("2022-05", "A"), Frame("2022-06", "B")], cfg,
           fetch=_wide_fetch, geometry=None)
    credits = [t for t in seen if "Copernicus" in t]
    # 2 observed + 2 generated frames all carry the notice, and all carry it whole.
    assert len(credits) == 4
    assert set(credits) == {"Contains modified Copernicus Sentinel data 2018–2024"}


def test_render_bars_span_the_full_canvas_not_just_the_imagery(tmp_path, monkeypatch):
    """The structural half of the R fix, observed on pixels rather than on strings: the
    header and bottom bars must reach the canvas edges, so the black side bars are part
    of the bar and not a strip of unused width beside a cramped one."""
    import gee_animation.render as r
    from gee_animation.render import _output_spec, _margins
    widths = []

    def spy(name):
        real = getattr(r, name)
        return lambda rgb, *a, **k: (widths.append(rgb.shape[1]) or real(rgb, *a, **k))

    monkeypatch.setattr(r, "draw_info_bar", spy("draw_info_bar"))
    monkeypatch.setattr(r, "annotate", spy("annotate"))
    cfg = _audience_cfg(tmp_path, "720p", "16:9", interpolate=0)
    cfg.frames = True
    paths = render([Frame("2022-05", "A")], cfg, fetch=_wide_fetch, geometry=None)
    cw, ch, pw, ph, _m = _output_spec(cfg, (200, 100))
    assert pw < cw, "this AOI/aspect must actually letterbox, or the test proves nothing"
    assert widths == [cw, cw], "the bars must be laid out on the canvas, not the imagery"
    # ...and the consequence, on pixels: with the bars now spanning the canvas, the
    # header's own text inset (w // 200) puts ink in the columns the side bars occupy.
    # On the old layout those columns were black by construction.
    arr = np.asarray(Image.open(next(p for p in paths if p.suffix == ".png")))
    assert arr.shape[:2] == (ch, cw)
    top_h, bottom_h = _margins(ph, True)
    y_pad = (ch - (ph + top_h + bottom_h)) // 2
    side = (cw - pw) // 2
    assert arr[y_pad:y_pad + top_h, :side].sum() > 0, "no header ink in the side bar"
    bottom = arr[y_pad + top_h + ph:y_pad + top_h + ph + bottom_h]
    assert bottom[:, :side].sum() > 0, "no bottom-bar ink in the side bar"


def test_line_two_gives_up_a_bounded_few_percent_before_it_trims_the_subtitle():
    """The one non-structural half of the fix. Line 2's nominal size comes from frame
    HEIGHT (h // 40) while its room is a WIDTH -- unrelated quantities, so the nominal
    size can overshoot by a few percent for no reason a viewer would recognise. Giving
    those up is invisible; dropping configured text is not. The shrink is bounded by
    _LINE2_MIN_SCALE so a caveat can never end up as fine print."""
    from gee_animation.render import _fit_header_line2, _font, _text_w, _LINE2_MIN_SCALE
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    sub, cav = _SUBTITLE_30, _CAVEAT_POOLED
    joined = f"{sub} · {cav}"
    px = 27
    nominal_w = _text_w(scratch, joined, _font(px))
    avail = nominal_w - 8                      # overshoots by ~1%: shrink, don't trim
    font, text = _fit_header_line2(scratch, sub, cav, px, avail)
    assert text == joined, "a few percent too wide must cost font size, not characters"
    assert font.size < px
    assert font.size >= round(px * _LINE2_MIN_SCALE), "caveats never become fine print"
    # ...and past the band, the subtitle pays as before -- the shrink does not become a
    # licence to trade characters for size indefinitely. Room for the caveats and
    # almost nothing else: the caveats stay whole at the nominal size and the subtitle
    # is the one that goes (trimmed to "…", or dropped when even that will not fit --
    # test_header_line_two_truncates_the_subtitle_never_the_caveat covers the trim).
    avail2 = _text_w(scratch, cav, _font(px)) + 40
    font2, text2 = _fit_header_line2(scratch, sub, cav, px, avail2)
    assert font2.size == px, "with the caveats alone fitting, no shrink is warranted"
    assert cav in text2 and sub not in text2


def test_render_warns_when_a_configured_subtitle_cannot_be_drawn_in_full(tmp_path, caplog):
    """Silently dropping user-configured text is the actual defect behind finding 1.
    Wherever the width genuinely will not hold both (a narrow portrait render, or a
    subtitle several lines long), the caveats still win -- but the run says so, naming
    the subtitle, instead of producing a frame that quietly lacks it."""
    import logging
    cfg = _audience_cfg(tmp_path, "480p", "9:16", subtitle="Schorfheide-Chorin " * 12)
    with caplog.at_level(logging.WARNING, logger="gee_animation.render"):
        render([Frame("2022-05", "A")], cfg, fetch=_wide_fetch, geometry=None)
    warnings = [r for r in caplog.records if "subtitle" in r.getMessage()]
    assert len(warnings) == 1, "run-level geometry: warn once, not once per frame"
    assert "Schorfheide-Chorin" in warnings[0].getMessage()


def test_render_does_not_warn_when_the_subtitle_does_fit(tmp_path, caplog):
    import logging
    cfg = _audience_cfg(tmp_path, "1080p", "16:9")
    with caplog.at_level(logging.WARNING, logger="gee_animation.render"):
        render([Frame("2022-05", "A")], cfg, fetch=_wide_fetch, geometry=None)
    assert not [r for r in caplog.records if "subtitle" in r.getMessage()]


def test_colorbar_stays_on_the_imagery_when_the_canvas_is_letterboxed(tmp_path):
    """The counterpart to the bars spanning the canvas: the legend is an OVERLAY on the
    picture (it explains the picture's colours), so unlike the header it must stay
    anchored to the imagery and not drift out into the side bars."""
    from gee_animation.render import _output_spec
    cfg = _audience_cfg(tmp_path, "720p", "16:9", interpolate=0)
    cfg.frames = True
    paths = render([Frame("2022-05", "A")], cfg, fetch=_wide_fetch, geometry=None)
    cw, ch, pw, ph, _m = _output_spec(cfg, (200, 100))
    arr = np.asarray(Image.open(next(p for p in paths if p.suffix == ".png")))
    side = (cw - pw) // 2
    y_pad = (ch - (ph + sum(_margins_of(ph)))) // 2
    top_h = _margins_of(ph)[0]
    band = arr[y_pad + top_h:y_pad + top_h + ph]          # the imagery rows only
    assert band[:, :side // 2].sum() == 0, "legend must not spill into the side bar"
    assert band[:, side:side + pw // 3].sum() > 0, "legend must be on the imagery"


def _margins_of(ph):
    from gee_animation.render import _margins
    return _margins(ph, True)


@pytest.mark.parametrize("two_line", [True, False])
def test_header_ink_never_reaches_the_imagery_at_any_frame_height(two_line):
    """The invariant the margin arithmetic exists to protect, checked densely.

    Font metrics are not the same quantity as the layout arithmetic: a header line's
    row is `_bar_h` tall (12 px floor) while `_font` bottoms out at 10 px, and 11 px
    DejaVu measures 13 px ascender-to-descender. So on a small frame the text does not
    fit its row — for every imagery height from 1 to 115 px the second line used to
    put ink straight onto imagery row 0. Descenders ("jgpqy") are in every string here
    on purpose: they are what overflows, and a probe without them reports success.

    `_margins(imagery_h, two_line)` is the contract: `draw_info_bar` may write into
    the first `top_h` rows and not one row further, at any height.
    """
    from gee_animation.render import _margins, draw_info_bar
    title = "Grumsiner Forst — UNESCO World Heritage beech forest, jgpqy"
    subtitle = "Brandenburg, Germany, Schorfheide-Chorin, jgpqy"
    caveat = "every frame re-picked from 2018–2024 — not a time series"
    for imagery_h in list(range(1, 140)) + [200, 300, 427, 480, 720, 1080, 2160]:
        top_h, bottom_h = _margins(imagery_h, two_line)
        padded = np.zeros((imagery_h + top_h + bottom_h, 420, 3), np.uint8)
        out = draw_info_bar(padded, title, subtitle if two_line else "",
                            caveat if two_line else "")
        assert out[top_h:].sum() == 0, f"header ink on imagery at height {imagery_h}"
        assert out[:top_h].sum() > 0, f"nothing drawn at all at height {imagery_h}"


@pytest.mark.parametrize("h", [1, 12, 62, 63, 90, 100, 106, 107, 115, 116, 117, 130,
                               240, 400, 1080])
def test_render_draws_a_two_line_header_in_the_margin_not_over_the_imagery(tmp_path, h):
    # (a) The header grows the top margin; it must never grow into the picture. Same
    # property as test_render_draws_label_bars_in_the_margins_not_over_the_imagery,
    # now with the doubled (title + subtitle/caveat) header.
    #
    # Parametrised over the height band where this DID fail, not just a comfortable
    # frame: `_bar_h` floors at 12 px while `_font` floors at 10 px and 11 px DejaVu
    # is 13 px tall ascender-to-descender, so for every imagery height from 1 to 115
    # line 2's glyphs used to land on imagery row 0 (up to 45 lit pixels). A single
    # h=240 case missed the entire band. 106/107 and 115/116/117 are the heights where
    # `_margins`' 12 px floor hands over to its (h+1)//9 branch.
    from gee_animation.render import _margins
    w = 320
    cfg = _cfg(tmp_path, name="hdr")
    cfg.index, cfg.palette = "rgb", []              # composite: no colorbar overlay
    cfg.frame_aoi = None                            # and no scale bar
    cfg.title = "Grumsiner Forst"
    cfg.subtitle = "Brandenburg, Germany"
    cfg.pool_years, cfg.pool_strategy = [2018, 2024], "gap_fill"

    def fake_fetch(image, cfg_, geometry=None):
        return np.full((h, w, 3), 123, dtype=float), np.ones((h, w), dtype=bool)

    paths = render([Frame("2022-05", object(), 1, 2018)], cfg, fetch=fake_fetch,
                   geometry=None)
    arr = np.asarray(Image.open(next(p for p in paths if p.suffix == ".png")))
    top_h, bottom_h = _margins(h, True)
    assert arr.shape[:2] == (h + top_h + bottom_h, w)
    assert np.all(arr[top_h:top_h + h] == 123)      # every imagery pixel untouched
    assert arr[:top_h].sum() > 0 and arr[-bottom_h:].sum() > 0


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


def test_output_spec_accounts_for_a_two_line_header():
    # (e) Same trap, doubled top margin: a subtitle/caveat makes the header two lines
    # tall, and an explicit aspect: "16:9" must STILL come out exactly 16:9 — the
    # extra line comes out of the place box, never off the canvas ratio.
    from gee_animation.render import _output_spec, _margins, _two_line_header
    for aoi_wh in ((200, 100), (100, 200), (160, 90)):
        cfg = types.SimpleNamespace(preset="1080p", aspect="16:9", upscale="lanczos",
                                    title="Grumsiner Forst",
                                    subtitle="Brandenburg, Germany")
        assert _two_line_header(cfg) is True
        cw, ch, pw, ph, _ = _output_spec(cfg, aoi_wh)
        assert (cw, ch) == (1920, 1080) and ch / cw == 9 / 16
        assert ph + sum(_margins(ph, True)) <= ch
        assert pw <= cw
        assert abs(pw / ph - aoi_wh[0] / aoi_wh[1]) < 0.02
    # a caveat alone (no title/subtitle configured) also makes it two lines
    pooled = types.SimpleNamespace(preset="1080p", aspect="16:9", upscale="lanczos",
                                   index="ndvi", pool_years=[2018, 2024])
    assert _two_line_header(pooled) is True
    plain = types.SimpleNamespace(preset="1080p", aspect="16:9", upscale="lanczos",
                                  index="ndvi")
    assert _two_line_header(plain) is False
    # and the two-line canvas really does give the imagery less room than one line
    assert _output_spec(pooled, (200, 100))[3] < _output_spec(plain, (200, 100))[3]


def test_output_spec_match_grows_the_canvas_for_a_two_line_header():
    # "match" promises no ratio, so the second header line grows the canvas instead of
    # shrinking the imagery — exactly as the one-line case does.
    from gee_animation.render import _output_spec, _margins
    cfg = types.SimpleNamespace(preset="720p", aspect="match", upscale="lanczos",
                                index="ndvi", subtitle="Brandenburg, Germany")
    cw, ch, pw, ph, _ = _output_spec(cfg, (200, 100))
    assert (pw, ph) == (1280, 640) and pw == cw
    assert ch == ph + sum(_margins(ph, True))


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


def test_two_line_margins_match_the_doubled_bar_height_of_the_padded_frame():
    # Same fixed point with a two-line header: the TOP margin now has to hold two bar
    # heights (title + subtitle/caveat) of the PADDED frame, the bottom still one.
    # Solving b == (H + 3b + 1)//12 is what keeps the header off the imagery.
    from gee_animation.render import _margins
    for imagery_h in (1, 8, 90, 107, 116, 119, 120, 132, 200, 577, 799, 1080, 1799, 2160):
        top_h, bottom_h = _margins(imagery_h, True)
        bar_h = max(12, (imagery_h + top_h + bottom_h) // 12)
        assert (top_h, bottom_h) == (2 * bar_h + 1, bar_h), imagery_h
        # ...and it really is taller than the one-line header for any real frame
        assert top_h >= _margins(imagery_h)[0]


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


def test_draw_north_arrow_panel_sits_above_scale_bar_panel():
    """The north indicator must share the scale bar's x-span and sit strictly
    above its panel — same pixel-region style as test_draw_scale_bar_labels_and_
    marks_frame, plus the "above" relationship the two panels are required to
    keep (they share `_scale_bar_layout` precisely so this holds)."""
    from gee_animation.render import _scale_bar_layout, draw_north_arrow
    from PIL import ImageDraw

    rgb = np.zeros((240, 480, 3), np.uint8)
    scaled = draw_scale_bar(rgb, 111320.0)
    both = draw_north_arrow(scaled, 111320.0)
    assert both.shape == rgb.shape and both.dtype == np.uint8
    assert both.sum() > scaled.sum(), "the arrow panel must add ink"

    # Where is the scale bar panel, per the shared geometry?
    probe = Image.fromarray(rgb, "RGB")
    geo = _scale_bar_layout(ImageDraw.Draw(probe, "RGBA"), 480, 240, 111320.0, 0.25)
    assert geo is not None
    # Nothing north-arrow-related may land at or below the scale bar panel's top —
    # that band belongs to the scale bar alone.
    assert both[int(geo["panel_top"]):].sum() == scaled[int(geo["panel_top"]):].sum(), \
        "north arrow ink found at/below the scale bar panel's top"
    # And something must have been drawn strictly above it, in roughly the same
    # x-span (bottom-right quadrant, not top-left).
    above = both[:int(geo["panel_top"]), :]
    assert above.sum() > 0, "nothing drawn above the scale bar panel"
    assert both[:60, :120].sum() == 0, "arrow bled into the top-left quadrant"


def test_draw_north_arrow_skips_tiny_frames():
    from gee_animation.render import draw_north_arrow
    rgb = np.zeros((8, 8, 3), np.uint8)
    out = draw_north_arrow(rgb, 111320.0)
    assert out.sum() == 0                                 # too small: no-op, like the scale bar


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


def test_write_mp4_quality_kwarg_reaches_ffmpeg_and_changes_file_size(tmp_path):
    # Behavioural, not mocked: proves render.quality is actually forwarded to
    # imageio's ffmpeg writer and changes the encoded bitrate, rather than just
    # asserting the kwarg is accepted. Needs real (noisy) content -- flat colour
    # frames compress to near-nothing at any quality setting and would hide the
    # effect entirely.
    import gee_animation.render as r
    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 255, (120, 160, 3), dtype=np.uint8) for _ in range(40)]

    low_path, high_path = tmp_path / "low.mp4", tmp_path / "high.mp4"
    r._write_mp4(low_path, frames, 2, quality=3)
    r._write_mp4(high_path, frames, 2, quality=9)

    low_size, high_size = low_path.stat().st_size, high_path.stat().st_size
    assert high_size > low_size * 1.5, (
        f"quality=9 ({high_size}B) should be well over 1.5x quality=3 ({low_size}B) "
        "if the kwarg is really reaching ffmpeg")


def test_write_mp4_quality_none_omits_the_kwarg_entirely(tmp_path, monkeypatch):
    # cfg.quality defaults to None, and the existing writer call (no quality kwarg
    # at all) must be unchanged in that case -- not "quality=None" reaching
    # imageio, which is a different call with potentially different behaviour.
    import gee_animation.render as r
    seen_kwargs = {}

    def fake_get_writer(path, **kwargs):
        seen_kwargs.update(kwargs)
        class W:
            def append_data(self, frame): pass
            def close(self): pass
        return W()

    monkeypatch.setattr(r.imageio, "get_writer", fake_get_writer)
    r._write_mp4(tmp_path / "x.mp4", [np.zeros((16, 16, 3), np.uint8)], 2)
    assert "quality" not in seen_kwargs


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
    monkeypatch.setattr(r, "annotate", lambda rgb, label, credit="", **kw: (labels.append(label) or rgb))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((10, 10)), np.ones((10, 10), dtype=bool)

    render([Frame("2022-06", object(), 7)], cfg, fetch=fake_fetch, geometry=None)
    # (f) the count is still on the frame — as "how much data backs this image", not
    # as a variable dump (see labels.observed_text)
    assert labels == ["June 2022 · 7 passes"]


def test_render_composes_pooled_provenance_into_the_drawn_text(tmp_path, monkeypatch):
    # (g) Frame.label is the clean period key; Frame.source (added for pooled frames)
    # carries where the imagery actually came from. render() must recombine them into
    # the drawn text ("May 2022 · image from 2021 · 1 pass"), routed through
    # `_drawable` (now a no-op identity fold, kept as the seam) where it is composed.
    # The property with teeth: the source year must reach the frame. Dropping it would
    # present borrowed imagery as this year's.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    composed = []
    orig_drawable = r._drawable
    monkeypatch.setattr(r, "_drawable",
                        lambda text: (composed.append(text) or orig_drawable(text)))
    drawn = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label, credit="", **kw: (drawn.append(label) or rgb))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((10, 10)), np.ones((10, 10), dtype=bool)

    render([Frame("2022-05", object(), 1, 2021)], cfg, fetch=fake_fetch, geometry=None)
    # what render composed, before the (now no-op) fold: period, source year, count
    assert composed == ["May 2022 · image from 2021 · 1 pass"]
    assert drawn == ["May 2022 · image from 2021 · 1 pass"]
    assert "2021" in drawn[0], "the borrowed year must never be dropped"


def test_render_leaves_gap_fill_nominal_frames_unmarked(tmp_path, monkeypatch):
    # gap_fill emits a mix: nominal-year frames carry source=None and must draw the
    # PLAIN period label (an arrow there would claim borrowed data that isn't), while
    # the borrowed ones still name their source year — in the same run.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    drawn = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label, credit="", **kw: (drawn.append(label) or rgb))

    def fake_fetch(image, cfg, geometry=None):
        return np.zeros((10, 10)), np.ones((10, 10), dtype=bool)

    render([Frame("2022-05", object(), 3, None), Frame("2022-06", object(), 1, 2019)],
           cfg, fetch=fake_fetch, geometry=None)
    assert drawn == ["May 2022 · 3 passes", "June 2022 · image from 2019 · 1 pass"]
    assert "image from" not in drawn[0], "a nominal-year frame must claim no borrowing"


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


def test_render_titled_rgb_names_true_colour_on_line_two(tmp_path, monkeypatch):
    """The gap this task exists to close: composites (rgb/cir) never draw a colorbar
    heading (`add_colorbar` is `not composite`-gated, see legend-heading-report.md), so
    once cfg.title displaces line 1's display-name fallback, a titled composite frame
    used to state its product name nowhere at all. Line 2 now opens with it, ahead of
    the subtitle; the run's caveats must still survive whole."""
    seen = _text_spy(monkeypatch)
    cfg = _audience_cfg(tmp_path, "1080p", "16:9")
    cfg.index, cfg.palette = "rgb", []          # composite: no colorbar heading
    render([Frame("2022-05", "A"), Frame("2022-06", "B")], cfg,
           fetch=_wide_rgb_fetch, geometry=None)
    line2 = next(t for t in seen if "True colour" in t)
    assert line2.startswith("True colour · "), "the product name must lead line 2"
    assert cfg.subtitle in line2
    assert "generated frames between observations" in line2, "caveats stay whole too"


def test_render_titled_cir_names_colour_infrared_on_line_two(tmp_path, monkeypatch):
    seen = _text_spy(monkeypatch)
    cfg = _audience_cfg(tmp_path, "1080p", "16:9")
    cfg.index, cfg.palette = "cir", []          # composite: no colorbar heading
    render([Frame("2022-05", "A"), Frame("2022-06", "B")], cfg,
           fetch=_wide_rgb_fetch, geometry=None)
    assert any(t.startswith("Colour infrared · ") for t in seen)


def test_render_titled_ndvi_does_not_double_the_product_name_on_line_two(
        tmp_path, monkeypatch):
    """Single-band indices already get the product name for free, on the colorbar
    heading (see legend-heading-report.md) -- doubling it into line 2 would be
    redundant, not helpful. Only composites are missing it."""
    seen = _text_spy(monkeypatch)
    cfg = _audience_cfg(tmp_path, "1080p", "16:9")     # cfg.index stays "ndvi"
    render([Frame("2022-05", "A"), Frame("2022-06", "B")], cfg,
           fetch=_wide_fetch, geometry=None)
    line2 = next(t for t in seen if cfg.subtitle in t)
    assert "Vegetation greenness" not in line2


def test_render_untitled_rgb_names_the_product_on_line_one_only(tmp_path, monkeypatch):
    """Unchanged behaviour for the untitled case: line 1 already falls back to the
    display name (_header_text), so line 2 must not double it."""
    seen = _text_spy(monkeypatch)
    cfg = _audience_cfg(tmp_path, "1080p", "16:9")
    cfg.index, cfg.palette = "rgb", []
    cfg.title = None
    render([Frame("2022-05", "A"), Frame("2022-06", "B")], cfg,
           fetch=_wide_rgb_fetch, geometry=None)
    assert "True colour" in seen, "line 1 falls back to the display name"
    line2 = next(t for t in seen if cfg.subtitle in t)
    assert "True colour" not in line2, "line 1 already carries it; no doubling"


def test_render_titled_composite_without_a_subtitle_still_names_the_product(
        tmp_path, monkeypatch):
    """The plainest shape of the motivating scenario: a titled composite with no
    subtitle/caveats configured at all. Line 2 must still exist to carry the product
    name, or the fix would be invisible on exactly the runs the user is about to make."""
    seen = _text_spy(monkeypatch)
    cfg = _cfg(tmp_path, name="rgbtitle")
    cfg.index, cfg.palette = "rgb", []
    cfg.title = "Grumsin forest"
    render([Frame("2022-05", object())], cfg, fetch=_wide_rgb_fetch, geometry=None)
    assert "True colour" in seen


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
        sensor="sentinel2", index="ndvi", viz_min=-0.2, viz_max=0.9, palette=["#000000", "#ffffff"],
    )


def _colorbar_bar_y0(h, y_offset=4):
    """Re-derive add_colorbar's own bar-top y: the ramp now starts one heading
    line (plus its gap) below y_offset, to make room for the legend heading drawn
    above it. Mirrors add_colorbar's own `y0 = panel_top + line_h + head_gap`
    exactly, so geometry-probing tests don't have to hardcode the heading's height."""
    from gee_animation.render import _annot_scale
    font, lw = _annot_scale(h)
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    line_h = scratch.textbbox((0, 0), "Ag", font=font)[3]
    return y_offset + line_h + max(2, lw)


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


# --- legend heading: the colorbar names the variable it shows --------------------

def test_colorbar_draws_the_index_display_name_as_a_heading():
    """Cartographic convention: the legend names the variable. A heading line -- the
    index's display_name, same font as the tick labels -- must be drawn above the
    ramp on the panel's own top edge. And it must be there *whether or not* cfg.title
    is set: the header shows title OR display_name (never both), but a titled frame
    ("Grumsin Beech Forest") still needs the product name (NDVI, LST, ...) somewhere
    on the frame -- the legend is that somewhere, unconditionally. add_colorbar never
    reads cfg.title at all, so the two renders must come out byte-identical."""
    from gee_animation.render import _annot_scale
    w, h = 400, 120
    rgb = np.zeros((h, w, 3), np.uint8)
    x0 = max(4, w // 200)
    font, _lw = _annot_scale(h)
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    heading_h = scratch.textbbox((0, 0), "Ag", font=font)[3]

    outs = []
    for title in (None, "Grumsin Beech Forest"):
        cfg = types.SimpleNamespace(index="lst", viz_min=-3.0, viz_max=46.0,
                                    palette=["#0000ff", "#ff0000"], title=title)
        out = add_colorbar(rgb.copy(), cfg)
        heading_band = out[4: 4 + heading_h, x0: w]
        assert heading_band.sum() > 0, f"heading not drawn when cfg.title={title!r}"
        outs.append(out)
    assert np.array_equal(outs[0], outs[1]), \
        "the legend heading must not vary with cfg.title -- add_colorbar ignores it"


def test_colorbar_heading_falls_back_to_the_index_key_when_no_display_name():
    """Defensive fallback, exercised through add_colorbar directly (not just the
    _index_display_name unit): an index the registry has no entry for still gets a
    heading -- the bare index key, upper-cased -- rather than a blank line."""
    from gee_animation.render import _annot_scale, _index_display_name
    w, h = 400, 120
    rgb = np.zeros((h, w, 3), np.uint8)
    cfg = types.SimpleNamespace(index="not_a_real_index", viz_min=-3.0, viz_max=46.0,
                                palette=["#0000ff", "#ff0000"])
    assert _index_display_name(cfg) == "NOT_A_REAL_INDEX"

    out = add_colorbar(rgb, cfg)
    x0 = max(4, w // 200)
    font, _lw = _annot_scale(h)
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    heading_h = scratch.textbbox((0, 0), "Ag", font=font)[3]
    heading_band = out[4: 4 + heading_h, x0: w]
    assert heading_band.sum() > 0, "fallback heading text was not drawn"


def test_colorbar_heading_panel_stays_within_the_top_left_quadrant_at_768_and_1920():
    """Scale bar / north arrow are drawn in the bottom-right quadrant (see
    test_draw_scale_bar_labels_and_marks_frame and test_draw_north_arrow_panel_sits_
    above_scale_bar_panel, which assert the same top-left-untouched quadrant on their
    own outputs). The colorbar's legend panel -- now one text line taller for the
    heading -- lives in the opposite (top-left) corner and must stay clear of that
    quadrant at both ends of the supported resolution range. lst_smw is used because
    it has both the longest display_name in the registry and word anchors (cooler/
    warmer), i.e. the panel's tallest/widest real-world case."""
    for w in (768, 1920):
        h = round(w * 9 / 16)
        rgb = np.zeros((h, w, 3), np.uint8)
        cfg = types.SimpleNamespace(index="lst_smw", viz_min=-10.0, viz_max=40.0,
                                    palette=["#000080", "#0000ff", "#00ffff",
                                             "#ffff00", "#ff0000", "#800000"])
        out = add_colorbar(rgb, cfg)
        assert out[h // 2:, w // 2:].sum() == 0, \
            f"colorbar panel bled into the bottom-right quadrant at {w}x{h}"


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
    x0, y0 = max(4, w // 200), _colorbar_bar_y0(h)
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
    x0, y0 = max(4, w // 200), _colorbar_bar_y0(h)
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
    # Genuinely narrow: bar_w == 80px (comparable to the brief's own "dimensions: 256
    # -> ~100px bar" example), with the default LST range — the reviewer-verified
    # real-world case where the mid ("15") label collides with its neighbours. (Width
    # re-derived for the bundled DejaVu font: its glyphs are narrower than Pillow's
    # own default, so the same collision needs more of the ramp to reproduce -- at
    # the old 160px width "15" no longer collides with anything.)
    from gee_animation.render import _annot_scale, _colorbar_ticks
    w, h = 200, 300
    rgb = np.zeros((h, w, 3), np.uint8)
    cfg = types.SimpleNamespace(index="lst", viz_min=-10.0, viz_max=40.0,
                                palette=["#0000ff", "#ff0000"])
    out = add_colorbar(rgb, cfg)

    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = max(4, w // 200), _colorbar_bar_y0(h)
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


def test_header_caveat_states_the_pooled_year_range():
    # Pooled frames come from whichever year was clearest, so the provenance has to be
    # drawn on the frame, not just live in the config. The wording is plain now and the
    # en dash is a real glyph (the bundled DejaVu draws it), but the year range and the
    # "not a time series" warning must both still be there.
    from gee_animation.render import _header_text
    plain = _header_text(types.SimpleNamespace(index="ndvi"))[2]
    pooled = _header_text(types.SimpleNamespace(index="ndvi", pool_years=[2019, 2024]))[2]
    assert plain == ""
    assert "2019–2024" in pooled and "not a time series" in pooled


def test_pooled_label_source_year_is_drawn_not_a_notdef_box():
    # The bundled DejaVu font (see `_font`) has real "←"/"–" glyphs, so the arrow
    # actually renders instead of an empty notdef box. `annotate`/`draw_info_bar` draw
    # whatever text they are given verbatim; `render()` still routes the pooled
    # provenance string through `_drawable` (now identity, kept as a seam) first.
    from gee_animation.render import _drawable, annotate
    assert _drawable("2022-05 ← 2021") == "2022-05 ← 2021"
    assert _drawable("pooled years 2019–2024") == "pooled years 2019–2024"
    rgb = np.zeros((240, 800, 3), np.uint8)
    with_year = annotate(rgb.copy(), _drawable("2022-05 ← 2021"))
    without = annotate(rgb.copy(), "2022-05")
    assert not np.array_equal(with_year, without)      # the source year really lands


# --- data attribution: _default_credit, annotate's credit line -------------------

def test_default_credit_sentinel2_names_the_copernicus_licence_and_year():
    # Zero-config compliance: an unset credit on a sentinel2 run must still carry
    # the exact wording the Copernicus licence requires, plus the year shown.
    from gee_animation.render import _default_credit
    cfg = types.SimpleNamespace(sensor="sentinel2", credit=None,
                                start="2022-01-01", end="2022-06-01", pool_years=None)
    assert _default_credit(cfg) == "Contains modified Copernicus Sentinel data 2022"


@pytest.mark.parametrize("sensor,expected", [
    ("landsat", "Landsat imagery courtesy of the U.S. Geological Survey"),
    ("modis", "MODIS data courtesy of NASA LP DAAC"),
    ("modis_lst", "MODIS data courtesy of NASA LP DAAC"),
])
def test_default_credit_non_sentinel_sensors_get_a_fixed_courtesy_line(sensor, expected):
    from gee_animation.render import _default_credit
    cfg = types.SimpleNamespace(sensor=sensor, credit=None,
                                start="2022-01-01", end="2022-06-01", pool_years=None)
    assert _default_credit(cfg) == expected


def test_default_credit_year_span_widens_to_the_pool_years():
    # Borrowed (pooled) frames contain pixels from any year in pool_years, so the
    # notice must cover the whole borrowed span, not just the nominal run dates.
    # start 2021 / end 2023 alone would be "2021"; pool [2018, 2024] widens it.
    from gee_animation.render import _default_credit
    cfg = types.SimpleNamespace(sensor="sentinel2", credit=None,
                                start="2021-01-01", end="2023-01-01",
                                pool_years=[2018, 2024])
    assert _default_credit(cfg) == "Contains modified Copernicus Sentinel data 2018–2024"


def test_default_credit_explicit_string_wins_verbatim():
    from gee_animation.render import _default_credit
    cfg = types.SimpleNamespace(sensor="sentinel2", credit="My custom credit line",
                                start="2022-01-01", end="2022-06-01", pool_years=None)
    assert _default_credit(cfg) == "My custom credit line"


def test_default_credit_explicit_empty_string_omits_the_line():
    # A conscious compliance decision (config.validate warns about it for
    # sentinel2), not the zero-config default — but _default_credit must still
    # honour it rather than silently falling back to the sensor auto-text.
    from gee_animation.render import _default_credit
    cfg = types.SimpleNamespace(sensor="sentinel2", credit="",
                                start="2022-01-01", end="2022-06-01", pool_years=None)
    assert _default_credit(cfg) == ""


def test_annotate_draws_credit_right_aligned_beside_the_label():
    from gee_animation.render import annotate, _bar_h
    w, h = 800, 240
    rgb = np.zeros((h, w, 3), np.uint8)
    plain = annotate(rgb.copy(), "May 2022")
    credited = annotate(rgb.copy(), "May 2022",
                        "Contains modified Copernicus Sentinel data 2022")
    assert credited.sum() > plain.sum()                    # credit really adds ink
    bar_h = _bar_h(h)
    # right-hand side of the bar (well clear of the left-aligned label) gains ink
    # only in the credited version.
    right_band = slice(h - bar_h, h), slice(600, w)
    assert credited[right_band].sum() > 0
    assert plain[right_band].sum() == 0


def test_annotate_credit_clips_with_ellipsis_rather_than_touch_the_label():
    """The period label is the data (what/when this frame shows); the credit is a
    compliance line. Neither may be silently dropped. At a width too narrow for
    both whole, the credit -- never the label -- degrades: it is clipped with a
    trailing ellipsis at the point the label ends, rather than the label shrinking
    or the credit vanishing outright."""
    from gee_animation.render import (
        annotate, _text_w, _annot_scale, _bar_h, _marker_layout,
    )
    w, h = 260, 120                    # sized so the full credit cannot fit beside it
    label = "May 2022 · image from 2021 · 1 pass"
    credit = "Contains modified Copernicus Sentinel data 2018–2024"
    rgb = np.zeros((h, w, 3), np.uint8)
    label_only = annotate(rgb.copy(), label)
    out = annotate(rgb.copy(), label, credit)

    probe = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(probe)
    font, _ = _annot_scale(h)
    bar_h = _bar_h(h)
    x = max(4, w // 200)
    y = h - bar_h + max(1, h // 200)
    _cx, _cy, _d, label_x = _marker_layout(draw, font, x, y, w)   # marker + gap shift
    label_right = label_x + _text_w(draw, label, font)
    # the label's own pixels are byte-identical whether or not a credit is present
    assert np.array_equal(out[h - bar_h:, :label_right], label_only[h - bar_h:, :label_right])
    # but the credit still draws something (clipped, not dropped) to its right
    assert out[h - bar_h:, label_right:].sum() > 0
    assert not np.array_equal(out, label_only)


def test_annotate_drops_credit_entirely_when_there_is_no_room_at_all():
    # An even narrower frame than the ellipsis case: no width remains for even a
    # single truncated character beside the label. The label still must not move
    # or shrink; here the credit degrades all the way to nothing rather than
    # overlapping it.
    from gee_animation.render import annotate
    w, h = 66, 80
    label = "May 2022"
    credit = "Contains modified Copernicus Sentinel data 2018–2024"
    rgb = np.zeros((h, w, 3), np.uint8)
    label_only = annotate(rgb.copy(), label)
    out = annotate(rgb.copy(), label, credit)
    assert np.array_equal(out, label_only)


def test_render_credit_lands_in_the_bottom_margin_never_the_imagery(tmp_path):
    """Same imagery-untouched discipline as Task 3's header tests, and discriminating
    about the credit specifically (not just "something is drawn"): the period label
    alone already lights up the bottom margin, so a bare `bottom_margin.sum() > 0`
    passes even with the credit forced off — it was proven to pass that way by a
    reviewer sabotage run (see the fix report). This renders the real `render()` ->
    `_default_credit` -> `annotate` path twice, default vs. an explicit `credit=""`,
    and requires the right half of the bar (left of which the label lives,
    left-aligned; see `annotate`) to actually differ: ink with the auto credit, none
    without it.
    """
    from gee_animation.render import _margins
    h, w = 240, 320

    def _run(credit):
        cfg = _cfg(tmp_path, name=f"credit-{credit!r}")
        cfg.index, cfg.palette = "rgb", []       # composite: no colorbar overlay
        cfg.frame_aoi = None                     # and no scale bar/north arrow
        cfg.sensor = "sentinel2"
        cfg.start, cfg.end = "2022-01-01", "2022-06-01"
        cfg.credit = credit

        def fake_fetch(image, cfg_, geometry=None):
            return np.full((h, w, 3), 123, dtype=float), np.ones((h, w), dtype=bool)

        paths = render([Frame("2022-05", object(), 1)], cfg, fetch=fake_fetch, geometry=None)
        arr = np.asarray(Image.open(next(p for p in paths if p.suffix == ".png")))
        top_h, bottom_h = _margins(h, False)
        assert arr.shape[:2] == (h + top_h + bottom_h, w)
        assert np.all(arr[top_h:top_h + h] == 123)     # every imagery pixel untouched
        return arr[top_h + h:]                          # the bottom margin only

    default_margin = _run(None)     # unset -> auto sentinel2 credit
    omitted_margin = _run("")       # explicit "" -> no credit line

    right_half = slice(w // 2, w)
    default_right = int(default_margin[:, right_half].sum())
    omitted_right = int(omitted_margin[:, right_half].sum())
    assert default_right > 0, "the auto credit must draw ink in the bar's right half"
    assert omitted_right == 0, "credit='' must draw nothing in the right half"
    assert default_right > omitted_right


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


def test_header_pooled_caveat_matches_the_strategy():
    """gap_fill keeps the requested year where it has data, so warning that the whole
    run is not a time series overstates it; least_cloudy/median do re-pick every frame,
    so for those the blanket warning is right."""
    from gee_animation.render import _header_text
    base = dict(index="ndvi", pool_years=[2018, 2024])
    gap = _header_text(types.SimpleNamespace(**base, pool_strategy="gap_fill"))[2]
    repicked = _header_text(types.SimpleNamespace(**base, pool_strategy="least_cloudy"))[2]
    assert "gap-filled" in gap and "not a time series" not in gap
    assert "re-picked" in repicked and "not a time series" in repicked
    assert "gap-filled" not in repicked
    # both still name the range they borrow from
    assert "2018–2024" in gap and "2018–2024" in repicked


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


# --- _nice_mid: honest (round) mid tick, not the exact/false-precision midpoint ---

def test_nice_mid_worked_table():
    # Hand-verified: an exact midpoint like 0.475 or 30.5 reads as false precision to
    # a lay viewer; the nice value a human would round to is what's drawn.
    from gee_animation.render import _nice_mid
    assert _nice_mid(0, 0.95) == 0.5
    assert _nice_mid(18, 43) == 30
    assert _nice_mid(-3, 46) == 20
    assert _nice_mid(-0.25, 1.0) == 0.4


def test_nice_mid_tie_breaks_to_the_smaller_absolute_value():
    # Midpoint 1.25 is exactly equidistant from candidates 1 and 1.5 (both 0.25 away)
    # -- the tie is pinned to the smaller absolute value (1), not left to iteration
    # order or the sign of the candidate.
    from gee_animation.render import _nice_mid
    assert _nice_mid(0, 2.5) == 1.0


def test_nice_mid_falls_back_to_the_exact_midpoint_when_nothing_fits_inside():
    # No {1, 1.5, 2, 2.5, 3, 4, 5} x 10**k candidate lies strictly inside (1.0, 1.05):
    # 1.0 itself is the boundary (excluded, not strictly inside) and 1.5 overshoots.
    # The only honest answer left is the exact midpoint.
    from gee_animation.render import _nice_mid
    assert _nice_mid(1.0, 1.05) == pytest.approx(1.025)


def test_colorbar_mid_tick_drawn_at_the_nice_values_true_position_not_centre():
    # 18..43's nice mid is 30, not the exact-midpoint 30.5 -- and 30 sits at 48% of
    # the ramp, not its geometric centre. The tick must land there, not at bar_w // 2.
    from gee_animation.render import _nice_mid
    w, h = 300, 80
    rgb = np.zeros((h, w, 3), np.uint8)
    cfg = types.SimpleNamespace(index="lst", viz_min=18.0, viz_max=43.0,
                                palette=["#0000ff", "#ff0000"])
    out = add_colorbar(rgb, cfg)

    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = max(4, w // 200), _colorbar_bar_y0(h)
    tick_row = y0 + bar_h + 1

    nice = _nice_mid(18.0, 43.0)
    assert nice == 30.0
    x_nice = x0 + (nice - 18.0) / (43.0 - 18.0) * bar_w    # float: 61.6, not an int px
    x_centre = x0 + bar_w // 2
    assert abs(x_nice - x_centre) > 1, "18..43's nice mid (30) is not the ramp's centre"
    # a tick line lands within a couple of px of the nice value's own true position
    # (sub-pixel line rasterisation, not a single exact column)
    window = out[tick_row, int(x_nice) - 1: int(x_nice) + 3]
    assert (window == [255, 255, 255]).all(axis=-1).any(), \
        "no tick near the nice mid's true (non-centred) position"
    # and nothing was drawn at the old exact-midpoint centre instead
    centre_window = out[tick_row, x_centre - 1: x_centre + 2]
    assert not (centre_window == [255, 255, 255]).all(axis=-1).any(), \
        "a tick was drawn at the ramp's centre, not the nice mid's true position"


# --- word anchors ("bare"/"dense vegetation", "cooler"/"warmer", ...) -------------

def test_colorbar_draws_word_anchors_for_the_index():
    # "cooler"/"warmer" (lst's low/high labels) land on their own line under the
    # ramp's ends -- numbers alone don't say what warm/cool means to a lay viewer.
    from gee_animation.render import _annot_scale
    w, h = 400, 120
    rgb = np.zeros((h, w, 3), np.uint8)
    cfg = types.SimpleNamespace(index="lst", viz_min=-3.0, viz_max=46.0,
                                palette=["#0000ff", "#ff0000"])
    out = add_colorbar(rgb.copy(), cfg)

    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = max(4, w // 200), _colorbar_bar_y0(h)
    font, lw = _annot_scale(h)
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text_h = scratch.textbbox((0, 0), "0", font=font)[3]
    line_h = scratch.textbbox((0, 0), "Ag", font=font)[3]
    tick_bot = y0 + bar_h + lw + 2
    text_y = tick_bot + 1
    anchor_y = text_y + text_h + max(2, lw)

    left_band = out[anchor_y: anchor_y + line_h + 1, x0: x0 + 60]
    assert left_band.sum() > 0, "the low-end anchor word ('cooler') was not drawn"
    right_band = out[anchor_y: anchor_y + line_h + 1, x0 + bar_w - 60: x0 + bar_w]
    assert right_band.sum() > 0, "the high-end anchor word ('warmer') was not drawn"


def test_colorbar_omits_word_anchors_when_the_index_has_none():
    # An index the registry has no low/high words for draws no anchor row at all --
    # not blank labels, nothing.
    from gee_animation.render import _annot_scale
    w, h = 400, 120
    rgb = np.zeros((h, w, 3), np.uint8)
    cfg = types.SimpleNamespace(index="not_a_real_index", viz_min=-3.0, viz_max=46.0,
                                palette=["#0000ff", "#ff0000"])
    out = add_colorbar(rgb.copy(), cfg)

    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = max(4, w // 200), _colorbar_bar_y0(h)
    font, lw = _annot_scale(h)
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text_h = scratch.textbbox((0, 0), "0", font=font)[3]
    line_h = scratch.textbbox((0, 0), "Ag", font=font)[3]
    tick_bot = y0 + bar_h + lw + 2
    text_y = tick_bot + 1
    anchor_y = text_y + text_h + max(2, lw)

    anchor_band = out[anchor_y: anchor_y + line_h + 1, :]
    assert anchor_band.sum() == 0, "no anchor row should be drawn for an unlabeled index"


# --- no-data swatch (explains apply_nodata's neutral grey on the frame itself) ----

def test_colorbar_no_data_swatch_matches_nodata_rgb():
    from gee_animation.render import NODATA_RGB, _annot_scale
    w, h = 400, 120
    rgb = np.zeros((h, w, 3), np.uint8)
    cfg = types.SimpleNamespace(index="lst", viz_min=-3.0, viz_max=46.0,
                                palette=["#0000ff", "#ff0000"])
    out = add_colorbar(rgb, cfg)

    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = max(4, w // 200), _colorbar_bar_y0(h)
    _font_, lw = _annot_scale(h)
    swatch_gap = max(6, lw * 4)
    swatch_size = bar_h
    swatch_x0 = x0 + bar_w + swatch_gap
    cy, cx = y0 + swatch_size // 2, swatch_x0 + swatch_size // 2
    assert out[cy, cx].tolist() == list(NODATA_RGB)


def test_colorbar_no_data_swatch_has_a_label():
    from gee_animation.render import _annot_scale
    w, h = 400, 120
    rgb = np.zeros((h, w, 3), np.uint8)
    cfg = types.SimpleNamespace(index="lst", viz_min=-3.0, viz_max=46.0,
                                palette=["#0000ff", "#ff0000"])
    out = add_colorbar(rgb, cfg)

    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = max(4, w // 200), _colorbar_bar_y0(h)
    font, lw = _annot_scale(h)
    swatch_gap = max(6, lw * 4)
    swatch_size = bar_h
    swatch_x0 = x0 + bar_w + swatch_gap
    swatch_text_x = swatch_x0 + swatch_size + max(3, lw * 2)

    band = out[y0: y0 + swatch_size, swatch_text_x: swatch_text_x + 80]
    assert band.sum() > 0, "the 'no data' label was not drawn beside the swatch"


def test_colorbar_composite_has_no_colorbar_and_so_no_swatch(tmp_path):
    # composites (rgb/cir) never call add_colorbar at all (see render()'s "if not
    # composite" gate) -- confirm the no-data swatch can't appear on one either, by
    # checking the composite render path draws no colorbar-shaped ink in that corner.
    cfg = _cfg(tmp_path)
    cfg.index, cfg.palette = "rgb", []
    cfg.frame_aoi = None
    h, w = 120, 400

    def fake_fetch(image, cfg_, geometry=None):
        return np.zeros((h, w, 3), dtype=float), np.ones((h, w), dtype=bool)

    paths = render([Frame("2022-05", object(), 1)], cfg, fetch=fake_fetch, geometry=None)
    from PIL import Image as _Image
    arr = np.asarray(_Image.open(next(p for p in paths if p.suffix == ".png")))
    from gee_animation.render import _margins
    top_h, bottom_h = _margins(h, False)
    # the imagery band, where a colorbar (and its swatch) would have been drawn, is
    # untouched -- still pure black, exactly what fake_fetch returned.
    assert np.all(arr[top_h:top_h + h] == 0)


# --- frame interpolation (render.interpolate) -------------------------------------

def _flat_fetch(image, cfg, geometry=None):
    return np.zeros((20, 20)), np.ones((20, 20), dtype=bool)


def test_render_interpolates_between_frames(tmp_path, monkeypatch):
    # (g) A generated frame says so, in words, and keeps the percentage that marks it
    # as computed rather than photographed (labels.generated_text).
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.interpolate = 2
    drawn = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label, credit="", **kw: (drawn.append(label) or rgb))
    render([Frame("2022-05", object()), Frame("2022-06", object())], cfg,
           fetch=_flat_fetch, geometry=None)
    assert drawn == ["May 2022", "between May and June 2022 · 33%",
                     "between May and June 2022 · 67%", "June 2022"]


def test_render_scales_generated_frames_with_the_gap(tmp_path, monkeypatch):
    # A three-period absence gets three times the generated frames, so playback speed
    # tracks elapsed time instead of implying the change happened in one step.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.interpolate = 1
    drawn = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label, credit="", **kw: (drawn.append(label) or rgb))
    render([Frame("2022-05", object()), Frame("2022-08", object())], cfg,
           fetch=_flat_fetch, geometry=None)
    assert len(drawn) == 5                       # 2 observed + 3 * 1 generated
    assert drawn[0] == "May 2022" and drawn[-1] == "August 2022"


def test_render_interpolate_zero_is_untouched(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.interpolate = 0
    drawn = []
    monkeypatch.setattr(r, "annotate", lambda rgb, label, credit="", **kw: (drawn.append(label) or rgb))
    render([Frame("2022-05", object(), 3), Frame("2022-06", object())], cfg,
           fetch=_flat_fetch, geometry=None)
    assert drawn == ["May 2022 · 3 passes", "June 2022"]


@pytest.mark.parametrize("mode", ["auto", "crossfade", "data"])
@pytest.mark.parametrize("index", ["ndvi", "rgb"])
def test_render_observed_frames_are_byte_identical_with_and_without_interpolation(
        tmp_path, mode, index):
    """The central guarantee: interpolation adds frames, it never alters a real one.

    Compared as the rendered *imagery* — the whole frame minus the label margins —
    over every mode/index combination that resolves; `data` on a composite is rejected
    by config.validate, so it is skipped here rather than pinning behaviour no valid
    run can reach. The header *text* legitimately differs between the two runs: turning
    interpolation on adds the "N generated frames between observations" caveat, which
    is the honesty signal Task 3 exists to make legible. Both runs carry a subtitle, so
    the header is two lines tall either way and the geometry is identical; what must
    not change is a single pixel of the picture.

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
        cfg.subtitle = "Brandenburg, Germany"     # two-line header in BOTH runs
        render([Frame("2022-05", "A", 4), Frame("2022-06", "B", 1, 2021)],
               cfg, fetch=lambda image, c, geometry=None: shots[image], geometry=None)

    from gee_animation.render import _margins

    def _imagery(path):
        arr = np.asarray(Image.open(path))
        top_h, bottom_h = _margins(18, True)      # 18 == the fetched imagery height
        assert arr.shape[0] == 18 + top_h + bottom_h
        return arr[top_h:top_h + 18]

    _run("a", 0)
    _run("b", 3)
    for label in ("2022-05", "2022-06"):
        a = _imagery(tmp_path / "a" / f"anim_{label}.png")
        b = _imagery(tmp_path / "b" / f"anim_{label}.png")
        assert np.array_equal(a, b), f"{label} changed when interpolation was enabled"


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


def test_assemble_stream_forwards_cfg_quality_to_the_writer(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.quality = 8
    seen = {}
    monkeypatch.setattr(r, "_write_mp4",
                        lambda path, frames, fps, quality=None: seen.update(
                            quality=quality, frames=list(frames)))
    r.assemble_stream(iter([np.zeros((16, 16, 3), np.uint8)]), cfg)
    assert seen["quality"] == 8


def test_assemble_stream_omits_quality_kwarg_when_cfg_has_none(tmp_path, monkeypatch):
    # cfg.quality defaults to None (via getattr, since the SimpleNamespace test cfg
    # doesn't set it at all) -- the call must stay the historical 3-positional-arg
    # form, not "quality=None", so a mock/writer that predates this knob still works.
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    assert not hasattr(cfg, "quality")

    def three_arg_only(path, frames, fps):
        list(frames)  # drain, as the real writer would

    monkeypatch.setattr(r, "_write_mp4", three_arg_only)
    r.assemble_stream(iter([np.zeros((16, 16, 3), np.uint8)]), cfg)  # must not raise


# --- observed/generated marker (review H5) -----------------------------------------
#
# At cinema-mode playback nobody reads a label that changes every ~0.5s (91% of
# frames in a heavily-interpolated run are generated); a filled/hollow marker beside
# the label reads peripherally at any speed. This is additive — the percentage text
# stays exactly as Task 3 left it (labels.generated_text wording); only ink is added,
# never removed or reworded.

def test_frame_marker_filled_paints_the_centre():
    from gee_animation.render import _frame_marker
    img = Image.new("RGB", (60, 60))
    draw = ImageDraw.Draw(img, "RGBA")
    _frame_marker(draw, (30, 30), 40, filled=True)
    arr = np.asarray(img)
    assert arr[30, 30].sum() > 0                 # solid disc: centre is painted


def test_frame_marker_hollow_leaves_a_readable_hole():
    # The whole point of the ring is that it reads as *hollow* even at small output
    # sizes -- the centre must stay unpainted with real margin either side of it, not
    # just a single unlit pixel that would vanish under any compression/resize.
    from gee_animation.render import _frame_marker
    size = 40
    img = Image.new("RGB", (60, 60))
    draw = ImageDraw.Draw(img, "RGBA")
    _frame_marker(draw, (30, 30), size, filled=False)
    arr = np.asarray(img)
    assert arr[30, 30].sum() == 0                                    # centre: the hole
    assert arr[30 - 1: 30 + 2, 30 - 1: 30 + 2].sum() == 0             # hole has real margin
    assert arr[30, 30 - size // 2 + 2].sum() > 0                     # the ring itself draws ink


def test_frame_marker_hollow_and_filled_share_the_same_outer_diameter():
    # A hollow ring that reads as *bigger* than the filled dot would look like a
    # different, unrelated shape rather than "the same marker, generated version".
    from gee_animation.render import _frame_marker
    size = 40

    def _outer_extent(filled):
        img = Image.new("RGB", (80, 80))
        draw = ImageDraw.Draw(img, "RGBA")
        _frame_marker(draw, (40, 40), size, filled=filled)
        ys, xs = np.nonzero(np.asarray(img).sum(axis=-1))
        return xs.min(), xs.max(), ys.min(), ys.max()

    fx0, fx1, fy0, fy1 = _outer_extent(True)
    hx0, hx1, hy0, hy1 = _outer_extent(False)
    # allow a 1px antialiasing/rounding tolerance either side
    assert abs(fx0 - hx0) <= 1 and abs(fx1 - hx1) <= 1
    assert abs(fy0 - hy0) <= 1 and abs(fy1 - hy1) <= 1


def test_annotate_draws_a_filled_marker_for_an_observed_frame_by_default():
    from gee_animation.render import annotate, _marker_layout, _annot_scale, _bar_h
    w, h = 320, 240
    rgb = np.zeros((h, w, 3), np.uint8)
    out = annotate(rgb.copy(), "May 2022")               # is_real defaults True

    probe = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(probe)
    font, _ = _annot_scale(h)
    bar_h = _bar_h(h)
    x = max(4, w // 200)
    y = h - bar_h + max(1, h // 200)
    cx, cy, _d, _label_x = _marker_layout(draw, font, x, y, w)
    assert out[round(cy), round(cx)].sum() > 0            # marker centre is painted -> filled


def test_annotate_draws_a_hollow_marker_for_a_generated_frame():
    from gee_animation.render import annotate, _marker_layout, _annot_scale, _bar_h
    w, h = 320, 240
    rgb = np.zeros((h, w, 3), np.uint8)
    out = annotate(rgb.copy(), "between May and June 2022 · 33%", is_real=False)

    probe = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(probe)
    font, _ = _annot_scale(h)
    bar_h = _bar_h(h)
    x = max(4, w // 200)
    y = h - bar_h + max(1, h // 200)
    cx, cy, _d, _label_x = _marker_layout(draw, font, x, y, w)
    assert out[round(cy), round(cx)].sum() == 0            # marker centre unpainted -> hollow


def test_render_marks_observed_and_generated_frames_with_a_draw_spy(tmp_path, monkeypatch):
    """2 observations, interpolate: 2 -> [obs, gen, gen, obs]. Review H5's marker must
    track exactly the same real/generated pattern `_generated_display`/`labels`
    already draw text for -- frames 0 and 3 filled (observed), 1-2 hollow (generated).
    """
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.interpolate = 2
    seen_filled = []
    real_marker = r._frame_marker

    def spy(draw, xy, size, filled):
        seen_filled.append(filled)
        return real_marker(draw, xy, size, filled)

    monkeypatch.setattr(r, "_frame_marker", spy)
    render([Frame("2022-05", object()), Frame("2022-06", object())], cfg,
           fetch=_flat_fetch, geometry=None)
    assert seen_filled == [True, False, False, True]


def test_render_non_interpolated_run_marks_every_frame_filled(tmp_path, monkeypatch):
    # Constraint: a plain run never observes anything but real frames, so "filled"
    # applies universally there too -- it is a harmless, single-valued marker, not
    # something that only sometimes means "observed".
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.interpolate = 0
    seen_filled = []
    real_marker = r._frame_marker

    def spy(draw, xy, size, filled):
        seen_filled.append(filled)
        return real_marker(draw, xy, size, filled)

    monkeypatch.setattr(r, "_frame_marker", spy)
    render([Frame("2022-05", object(), 3), Frame("2022-06", object())], cfg,
           fetch=_flat_fetch, geometry=None)
    assert seen_filled == [True, True]


def test_render_marker_lands_in_the_bottom_margin_never_the_imagery(tmp_path):
    """Same imagery-untouched discipline as the credit/header margin tests: the
    marker (like the label and credit) belongs to the bottom bar, never the picture,
    at every frame of an interpolated run -- including the generated (hollow-marker)
    ones, not just the observed ones the other margin tests exercise."""
    from gee_animation.render import _margins
    h, w = 240, 320
    cfg = _cfg(tmp_path)
    cfg.index, cfg.palette = "rgb", []       # composite: no colorbar overlay
    cfg.frame_aoi = None                     # and no scale bar/north arrow
    cfg.interpolate = 2

    def fake_fetch(image, cfg_, geometry=None):
        return np.full((h, w, 3), 123, dtype=float), np.ones((h, w), dtype=bool)

    paths = render([Frame("2022-05", "A"), Frame("2022-06", "B")], cfg,
                   fetch=fake_fetch, geometry=None)
    top_h, bottom_h = _margins(h, True)     # interpolate>0 adds a caveat -> two-line header
    for p in sorted(pp for pp in paths if pp.suffix == ".png"):
        arr = np.asarray(Image.open(p))
        assert arr.shape[:2] == (h + top_h + bottom_h, w)
        assert np.all(arr[top_h:top_h + h] == 123)      # imagery untouched
        bottom_margin = arr[top_h + h:]
        left_region = bottom_margin[:, :40]              # well inside the marker's zone
        assert left_region.sum() > 0                      # marker(+label) draws ink there


def test_header_names_the_interpolation():
    # Most frames of an interpolated run were never observed; the header has to say so,
    # naming the count. Losing this note would present generated frames as acquisitions.
    from gee_animation.render import _header_text
    cfg = types.SimpleNamespace(index="ndvi", interpolate=10)
    assert "10 generated frames between observations" in _header_text(cfg)[2]
    assert _header_text(types.SimpleNamespace(index="ndvi", interpolate=0))[2] == ""


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
    assert label is None and text == "between May and June 2022 · 50%"
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


def test_resolve_droppables_keeps_the_earlier_of_two_colliding_droppables():
    import gee_animation.render as R
    # viz -1..1.2 puts the 0 tick and nice-mid 0.1 a few percent apart; the old
    # mutual check dropped BOTH labels, leaving bare tick marks. Sequential
    # admission keeps 0 (earlier in the list) and drops only the mid.
    ticks = [(-1.0, "-1", "l", False), (0.0, "0", "m", True),
             (0.1, "0.1", "m", True), (1.2, "1.2", "r", False)]
    bounds = [(0, 20), (100, 110), (105, 120), (300, 330)]   # 0 and 0.1 overlap
    show = R._resolve_droppables(ticks, bounds, gap=4)
    assert show == [True, True, False, True]
    # and a droppable colliding with a range END still drops
    bounds = [(0, 20), (18, 30), (150, 165), (300, 330)]
    assert R._resolve_droppables(ticks, bounds, gap=4) == [True, False, True, True]


def test_header_subtitle_fits_is_not_fooled_by_substring_subtitles(monkeypatch):
    import gee_animation.render as R
    # Subtitle "2021" is a substring of the caveat "gap-filled from 2019-2021";
    # when the drawing path drops the subtitle the fitted text still CONTAINS
    # "2021", and the old `subtitle in text` check reported it as fitting.
    caveats = "gap-filled from 2019–2021"
    monkeypatch.setattr(R, "_fit_header_line2",
                        lambda draw, sub, cav, px, avail, prefix="": (None, cav))
    assert R.header_subtitle_fits(1920, 1080, "2021", caveats) is False
    # untouched composition => genuinely fits
    monkeypatch.setattr(
        R, "_fit_header_line2",
        lambda draw, sub, cav, px, avail, prefix="": (
            None, " · ".join(p for p in (prefix, sub, cav) if p)))
    assert R.header_subtitle_fits(1920, 1080, "2021", caveats) is True


def test_fit_margins_rejects_a_canvas_too_short_for_the_margins():
    import gee_animation.render as R
    # ch=30 with a two-line header cannot hold one imagery row + margin floors;
    # the letterbox would paste at a negative offset and crop the header silently.
    with pytest.raises(RuntimeError, match="too short"):
        R._fit_margins(100, 100, 30, 1.0, two_line_header=True)
    # a canvas just past the floor still works
    w, h = R._fit_margins(100, 100, 200, 1.0, two_line_header=True)
    assert h >= 1 and h + sum(R._margins(h, True)) <= 200


def test_raw_frames_written_byte_exact_to_the_pure_imagery(tmp_path):
    # raw_frames: true writes {name}_raw_{label}.png alongside the annotated frames —
    # the map itself, BEFORE any furniture. With no preset/region/bounds the raw file
    # must be byte-identical to colorize+apply_nodata of the fetched array, and the
    # annotated frame must be taller (margins) — proving the raw copy escaped the
    # overlay pipeline entirely.
    from gee_animation.imaging import colorize
    from gee_animation.render import apply_nodata, render
    cfg = _cfg(tmp_path, name="rawrun")
    cfg.raw_frames = True
    arr = np.linspace(-0.2, 0.9, 64 * 64).reshape(64, 64)
    valid = np.ones((64, 64), bool)
    valid[:4, :4] = False
    def fetch(image, cfg, geometry=None):
        return arr, valid
    paths = render([Frame("2022-06", object())], cfg, fetch=fetch, geometry=None)
    raw = tmp_path / "rawrun_raw_2022-06.png"
    annotated = tmp_path / "rawrun_2022-06.png"
    assert raw.exists() and annotated.exists()
    assert raw in paths                      # offered as an output like other PNGs
    expected = apply_nodata(colorize(arr, cfg.viz_min, cfg.viz_max, cfg.palette), valid)
    got = np.asarray(Image.open(raw))
    assert got.shape == expected.shape and (got == expected).all()
    ann = np.asarray(Image.open(annotated))
    assert ann.shape[0] > got.shape[0]       # margins/furniture on the annotated one


def test_raw_frames_default_off_writes_nothing(tmp_path):
    cfg = _cfg(tmp_path, name="norawrun")
    def fetch(image, cfg, geometry=None):
        return np.zeros((32, 32)), np.ones((32, 32), bool)
    render([Frame("2022-06", object())], cfg, fetch=fetch, geometry=None)
    assert not list(tmp_path.glob("*_raw_*.png"))


def test_export_geotiffs_writes_cached_tifs_per_observed_frame(tmp_path, monkeypatch):
    import gee_animation.render as R
    from gee_animation import cache
    cfg = _cfg(tmp_path, name="tifrun")
    cfg.sensor = "sentinel2"          # native 10 m: the configured scale stands
    cfg.crs = "EPSG:32633"
    cfg.cache = True
    cfg.cache_dir = str(tmp_path / "cache")
    cfg.workers = 2
    urls = []
    monkeypatch.setattr(R, "_fetch_url", lambda url, timeout=0: urls.append(url) or b"TIFBYTES")
    class FakeSel:
        def __init__(self, label): self.label = label
        def getDownloadURL(self, params):
            assert params["format"] == "GEO_TIFF" and params["filePerBand"] is False
            # fixture scale 20 m is coarser than S2 native 10 m -> honoured as-is
            assert params["scale"] == 20.0 and params["crs"] == "EPSG:32633"
            return f"http://dl/{self.label}"
    class FakeImg:
        def __init__(self, label): self.label = label
        def select(self, bands):
            assert bands == "INDEX"          # index run exports index units
            return FakeSel(self.label)
    frames = [Frame("2022-05", FakeImg("2022-05")), Frame("2022-06", FakeImg("2022-06"))]
    paths = R._export_geotiffs(frames, cfg, geometry="GEOM")
    assert [p.name for p in paths] == ["tifrun_2022-05.tif", "tifrun_2022-06.tif"]
    assert all(p.parent == tmp_path / "geotiffs" / "tifrun" for p in paths)
    assert all(p.read_bytes() == b"TIFBYTES" for p in paths)
    assert len(urls) == 2
    # second export: pure cache hit, zero downloads, distinct per-frame entries
    paths2 = R._export_geotiffs(frames, cfg, geometry="GEOM")
    assert len(urls) == 2 and [p.name for p in paths2] == [p.name for p in paths]


def test_render_returns_geotiff_paths_only_when_enabled(tmp_path, monkeypatch):
    import gee_animation.render as R
    cfg = _cfg(tmp_path, name="tifgate")
    def fetch(image, cfg, geometry=None):
        return np.zeros((16, 16)), np.ones((16, 16), bool)
    called = []
    monkeypatch.setattr(R, "_export_geotiffs",
                        lambda frames, cfg, geometry: called.append(1) or [])
    render([Frame("2022-06", object())], cfg, fetch=fetch, geometry=None)
    assert not called                        # default off
    cfg = _cfg(tmp_path, name="tifgate2")
    cfg.geotiffs = True
    render([Frame("2022-06", object())], cfg, fetch=fetch, geometry=None)
    assert called == [1]


def test_geotiff_scale_clamps_to_native_resolution():
    import gee_animation.render as R
    # the cinema LST configs carry scale: 10 (inherited from an NDVI template) against
    # a 100 m thermal product — exporting that would claim 10x false resolution
    cfg = types.SimpleNamespace(sensor="landsat", index="lst", scale=10, crs=None)
    assert R._geotiff_scale(cfg) == 100.0
    # a coarser-than-native request is the user's choice and is honoured
    cfg.scale = 250
    assert R._geotiff_scale(cfg) == 250.0
    # allow_upsample keeps the explicit escape hatch _cap_dimensions honours
    cfg.scale, cfg.allow_upsample = 10, True
    assert R._geotiff_scale(cfg) == 10.0
    # Sentinel-2 NDVI at 10 m is already native: unchanged
    s2 = types.SimpleNamespace(sensor="sentinel2", index="ndvi", scale=10, crs=None)
    assert R._geotiff_scale(s2) == 10.0
    # 20 m SWIR index is not honestly 10 m
    s2b = types.SimpleNamespace(sensor="sentinel2", index="ndmi", scale=10, crs=None)
    assert R._geotiff_scale(s2b) == 20.0


def test_geotiffs_land_in_their_own_subfolder(tmp_path, monkeypatch):
    import gee_animation.render as R
    cfg = _cfg(tmp_path, name="tifdir")
    cfg.sensor, cfg.crs, cfg.cache = "sentinel2", None, False
    monkeypatch.setattr(R, "_fetch_url", lambda url, timeout=0: b"TIF")
    class FakeImg:
        def select(self, bands): return types.SimpleNamespace(
            getDownloadURL=lambda params: "http://dl")
    paths = R._export_geotiffs([Frame("2022-05", FakeImg())], cfg, geometry="GEOM")
    assert paths[0] == tmp_path / "geotiffs" / "tifdir" / "tifdir_2022-05.tif"
    assert paths[0].read_bytes() == b"TIF"
    assert not list(tmp_path.glob("*.tif"))     # not loose in out/


def test_geotiff_export_skips_frames_ee_refuses_and_keeps_the_rest(tmp_path, monkeypatch, caplog):
    # An rgb/cir composite at 10 m over a wide frame can answer "Computation timed
    # out". That must not abort a nine-minute render that already produced the
    # video: retry once, then log and skip, returning the frames that did export.
    import logging
    import gee_animation.render as R
    cfg = _cfg(tmp_path, name="tifskip")
    cfg.sensor, cfg.crs, cfg.cache, cfg.workers = "sentinel2", None, False, 1
    attempts = {}
    def fake_fetch(url, timeout=0):
        label = url.rsplit("/", 1)[-1]
        attempts[label] = attempts.get(label, 0) + 1
        if label == "2022-06":
            raise RuntimeError("Computation timed out.")
        return b"TIF"
    monkeypatch.setattr(R, "_fetch_url", fake_fetch)
    class FakeImg:
        def __init__(self, label): self.label = label
        def select(self, bands):
            return types.SimpleNamespace(
                getDownloadURL=lambda params: f"http://dl/{self.label}")
    frames = [Frame(l, FakeImg(l)) for l in ("2022-05", "2022-06", "2022-07")]
    with caplog.at_level(logging.WARNING):
        paths = R._export_geotiffs(frames, cfg, geometry="GEOM")
    assert [p.name for p in paths] == ["tifskip_2022-05.tif", "tifskip_2022-07.tif"]
    assert attempts["2022-06"] == 2                 # tried, retried once, gave up
    assert "2022-06" in caplog.text and "incomplete" in caplog.text
    # the good frames really were written
    assert all(p.read_bytes() == b"TIF" for p in paths)
