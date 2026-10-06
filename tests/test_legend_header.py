"""The legend lives in the header, and frame furniture scales with the 16:9-equivalent height.

Two related fixes for footprint-shaped (portrait) canvases:

* every font, bar and legend size used to follow the frame HEIGHT, so a 1920 x 2236
  focus canvas drew text twice as large (relative to its width) as a 1920 x 1080
  cinema frame, and the legend covered the top of the map;
* the legend sat on the imagery at all. It now sits in the header's right half, on
  the black band that already exists, and the header text keeps the left part.
"""
import types

import numpy as np
from PIL import Image, ImageDraw

from gee_animation.render import (
    _annot_scale, _bar_h, _margins, _two_line_header, add_colorbar, draw_info_bar,
    header_subtitle_fits, legend_width,
)


def _lst_cfg(**over):
    cfg = types.SimpleNamespace(sensor="landsat", index="lst", viz_min=15.0, viz_max=45.0,
                                palette=["#0000ff", "#ffffff", "#ff0000"], title="Revier 13")
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


# --- sizes follow the 16:9-equivalent height --------------------------------------

def test_annotation_scale_is_the_same_for_portrait_and_landscape_at_equal_width():
    """A 1920-wide portrait canvas gets the font a 1920 x 1080 frame gets, not the
    font a 2236 px tall landscape frame would get."""
    font_land, lw_land = _annot_scale(1080, 1920)
    font_port, lw_port = _annot_scale(2236, 1920)
    assert font_port.size == font_land.size and lw_port == lw_land
    # and landscape frames are untouched by the width argument
    assert _annot_scale(1080, 1920)[0].size == _annot_scale(1080)[0].size


def test_bar_height_and_margins_follow_the_width_on_portrait_frames():
    assert _bar_h(2236, 1920) == _bar_h(1080, 1920) == _bar_h(1080)
    ref = 1920 * 9 // 16
    b = _bar_h(ref)
    assert _margins(1900, True, 1920) == (2 * b + 1, b)
    assert _margins(1900, False, 1920) == (b + 1, b)
    # landscape: the width-aware answer equals the old height-only answer
    assert _margins(900, True, 1920) == _margins(900, True)


# --- the header always has room for the legend ------------------------------------

def test_a_colorbar_forces_a_two_line_header_even_without_a_subtitle():
    plain = _lst_cfg(subtitle="", interpolate=0, pool_years=None)
    assert _two_line_header(plain)
    composite = types.SimpleNamespace(sensor="sentinel2", index="rgb", title=None,
                                      subtitle="", interpolate=0, pool_years=None)
    assert not _two_line_header(composite)


# --- the legend is drawn in the header zone, right-aligned, never on the imagery ----

def test_legend_in_the_header_zone_leaves_the_imagery_untouched():
    w, h = 1920, 1080
    top_h, _bottom = _margins(900, True, w)
    canvas = np.zeros((h, w, 3), np.uint8)
    canvas[top_h:top_h + 900] = 77          # "imagery" rows
    out = add_colorbar(canvas.copy(), _lst_cfg(), header_zone=top_h)
    assert np.array_equal(out[top_h:], canvas[top_h:]), "legend ink on the imagery"
    ink = np.argwhere(out[:top_h].sum(axis=2) > 0)
    assert len(ink), "no legend drawn"
    assert ink[:, 1].min() > w // 2, "legend must sit in the right half of the header"
    assert ink[:, 1].max() < w, "legend must not run off the canvas"


def test_legend_width_reserves_the_header_columns_the_legend_will_use():
    w, h = 1920, 1080
    top_h, _bottom = _margins(900, True, w)
    reserved = legend_width(_lst_cfg(), w, h, top_h)
    out = add_colorbar(np.zeros((h, w, 3), np.uint8), _lst_cfg(), header_zone=top_h)
    ink = np.argwhere(out[:top_h].sum(axis=2) > 0)
    assert ink[:, 1].min() >= w - reserved, "legend ink left of the reserved width"
    assert reserved <= w * 0.55, "the legend must leave the header text most of the width"


def test_header_text_stays_left_of_the_reserved_legend_width():
    w, h = 1920, 1080
    top_h, _bottom = _margins(900, True, w)
    reserved = 800
    out = draw_info_bar(np.zeros((h, w, 3), np.uint8),
                        "Spandauer Forst, Revier 13 — a long title that would reach the legend",
                        "Surface temperature · harmonic seasonal model",
                        "10 generated frames between observations · modelled: harmonic fit, 2 harmonics",
                        reserved_w=reserved)
    zone = out[:top_h]
    assert zone[:, w - reserved:].sum() == 0, "header text ran into the legend's columns"
    assert zone[:, :w - reserved].sum() > 0


def test_header_line_two_wraps_into_the_bar_instead_of_dropping_the_subtitle():
    """With the legend taking the right part, line 2 no longer has the full width —
    the caveat line of a modelled run is ~1950 px at 1920 wide. It wraps onto a
    second row inside its bar rather than shrinking past readability or dropping the
    subtitle."""
    w, h = 1920, 1080
    subtitle = "Surface temperature · harmonic seasonal model"
    caveats = "10 generated frames between observations · modelled: harmonic fit, 2 harmonics"
    assert header_subtitle_fits(w, h, subtitle, caveats, reserved_w=800)
    top_h, _bottom = _margins(900, True, w)
    out = draw_info_bar(np.zeros((h, w, 3), np.uint8), "Spandauer Forst, Revier 13",
                        subtitle, caveats, reserved_w=800)
    bar = _bar_h(h, w)
    line2 = out[bar:top_h, :w - 800].sum(axis=(1, 2)) > 0
    rows = np.flatnonzero(line2)
    # two rows of text: a gap of empty rows separates them
    gaps = np.flatnonzero(np.diff(rows) > 1)
    assert len(gaps) >= 1, "line 2 did not wrap onto a second row"
