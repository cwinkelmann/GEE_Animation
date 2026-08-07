"""Render index frames to annotated PNGs and assemble MP4 + GIF."""
from __future__ import annotations

import io
import logging
import math
from collections import deque
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from functools import lru_cache, partial
from importlib import resources
from pathlib import Path
from urllib.request import urlopen

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import cache, interpolate, labels
from .aoi import _load_geojson_geometry, _read_shapefile_geometry
from .imaging import colorize
from .products import INDICES, native_scale_m

log = logging.getLogger(__name__)


NODATA_RGB = (240, 240, 240)
REGION_OUTLINE_RGB = (255, 235, 59)   # amber — high contrast over the index palette
LETTERBOX_RGB = (0, 0, 0)

# Screen-output presets (output long edge, px), aspect ratios, and upscale methods.
PRESETS = {"4k": 3840, "2160p": 3840, "1440p": 2560, "1080p": 1920, "720p": 1280, "480p": 854}
ASPECTS = {"16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0, "3:2": 3 / 2, "21:9": 21 / 9,
           "9:16": 9 / 16, "4:5": 4 / 5}
UPSCALE_METHODS = {"lanczos": Image.LANCZOS, "bicubic": Image.BICUBIC,
                   "bilinear": Image.BILINEAR, "nearest": Image.NEAREST}
_GIF_MAX_EDGE = 1280   # GIFs stay preview-sized even when the MP4 is 4K


_FONT_PACKAGE = "gee_animation.fonts"
_FONT_FILENAME = "DejaVuSans.ttf"


@lru_cache(maxsize=64)
def _font(px: int):
    """The bundled DejaVu Sans at the given pixel size (cached).

    Pillow's own bundled default font (a limited-character-set "Aileron", or on
    Pillow < 10.1 a small fixed-size bitmap font) has no glyph for "←" (pooled-frame
    provenance arrow) or "–" (en dash, e.g. a pooled year range) — Pillow silently
    substitutes a notdef box, which reads as a corrupted frame to a viewer. DejaVu
    Sans is a real, fully-hinted TrueType font that has both, so frames can be drawn
    with the actual characters instead of an ASCII-folded stand-in (see `_drawable`).

    Loaded via `importlib.resources` (not a repo-relative path) so it resolves
    correctly from an installed wheel, not just a source checkout — matplotlib
    ships the same font, but matplotlib is only an optional extra here, so the
    font is bundled into this package rather than borrowed from it at runtime.
    Falls back to Pillow's own default font, logged once, only if the bundled TTF
    is missing or unreadable (e.g. a broken/partial install) — better a plainer
    frame than a crash.
    """
    try:
        with resources.as_file(resources.files(_FONT_PACKAGE) / _FONT_FILENAME) as path:
            return ImageFont.truetype(str(path), size=max(10, int(px)))
    except Exception:
        log.warning(
            "Bundled font %s could not be loaded (missing/corrupt install?); "
            "falling back to Pillow's default font, which lacks some glyphs "
            "('←', '–') used in provenance/unit labels.",
            _FONT_FILENAME, exc_info=True,
        )
        try:
            return ImageFont.load_default(size=max(10, int(px)))
        except TypeError:        # Pillow < 10 has no size argument
            return ImageFont.load_default()


# DejaVu Sans (see _font) has real glyphs for "←"/"–", so no ASCII fold is needed any
# more. `_DRAWABLE` is kept empty and `_drawable` kept as an identity function so its
# call sites — and the M5 history of why they exist — stay greppable.
_DRAWABLE: dict[str, str] = {}


def _drawable(text: str) -> str:
    for src, dst in _DRAWABLE.items():
        text = text.replace(src, dst)
    return text


def _annot_scale(h: int):
    """(font, line_width) proportional to frame height so overlays read at any size.

    Calibrated so a ~512 px frame matches the previous fixed look (~12 px font, 1 px
    lines) and a 4K frame gets legible ~54 px text and ~5 px lines.
    """
    return _font(max(11, h // 40)), max(1, round(h / 430))


def _index_meta(cfg):
    """Registry entry for cfg.index (bands/formula/composite), or None if unknown."""
    return INDICES.get(getattr(cfg, "index", None))


def _is_composite(cfg) -> bool:
    meta = _index_meta(cfg)
    return bool(meta and meta.composite)


def _thumb_params(cfg, geometry) -> dict:
    """Build getThumbURL parameters with single-int dimensions to preserve aspect ratio.

    EE fits the largest side to `dimensions` and scales the other proportionally,
    preserving the frame's aspect ratio.
    """
    # single-int `dimensions` -> EE fits the largest side and preserves aspect ratio
    params = {
        "min": cfg.viz_min,
        "max": cfg.viz_max,
        "dimensions": cfg.dimensions,
        "region": geometry,
        "format": "png",
    }
    crs = getattr(cfg, "crs", None)   # metric CRS (e.g. EPSG:32633) -> square pixels
    if crs:
        params["crs"] = crs
    return params


def _decode_thumbnail(data: bytes, cfg, composite: bool):
    """Decode raw PNG bytes from EE into ``(array, valid)`` — see _fetch_thumbnail."""
    if composite:
        arr = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"), dtype=float)
        return arr[..., :3], arr[..., 3] > 0

    img = Image.open(io.BytesIO(data)).convert("LA")   # grayscale + alpha
    arr = np.asarray(img, dtype=float)
    gray = arr[..., 0] / 255.0
    valid = arr[..., 1] > 0                              # True where data present
    # EE scales min..max into 0..255; map back to INDEX units.
    index_arr = cfg.viz_min + gray * (cfg.viz_max - cfg.viz_min)
    return index_arr, valid


def _fetch_thumbnail(image, cfg, geometry):
    """Download the frame via EE getThumbURL (memoised on disk by `cache`).

    For a normal index returns ``(index_arr, valid)`` — a 2-D array in INDEX units
    plus a validity mask. For a composite (rgb/cir) returns ``(rgb_arr, valid)``
    where ``rgb_arr`` is H×W×3 (0..255, already colour). ``valid`` is False where
    EE returned no data (masked/cloud pixels are transparent).

    The *raw bytes* are cached before decoding, so a cache hit is byte-identical
    to a fresh fetch. Anything wrong with a cached entry — unreadable file,
    truncated PNG — is downgraded to a miss; the cache can never break a run.
    """
    composite = _is_composite(cfg)
    params = _thumb_params(cfg, geometry)
    key = cache.thumb_key(cfg, params, composite)

    data = cache.load(cfg, key)
    if data is not None:
        try:
            return _decode_thumbnail(data, cfg, composite)
        except Exception as exc:                    # noqa: BLE001 (any decode failure = miss)
            log.warning("discarding corrupt thumbnail cache entry %s (%s); refetching",
                        key[:12], exc)
            cache.discard(cfg, key)

    bands = ["R", "G", "B"] if composite else "INDEX"
    url = image.select(bands).getThumbURL(params)
    with urlopen(url, timeout=180) as resp:  # noqa: S310 (trusted EE URL; timeout avoids hangs)
        data = resp.read()
    cache.store(cfg, key, data)
    return _decode_thumbnail(data, cfg, composite)


def apply_nodata(rgb: np.ndarray, valid: np.ndarray, color=NODATA_RGB) -> np.ndarray:
    out = rgb.copy()
    out[~valid] = color
    return out


def _colorbar_ticks(vmin: float, vmax: float, units: str) -> list:
    """Ordered tick specs ``(value, label, anchor, droppable)`` for the colorbar.

    min (left-anchored) and max (right-anchored, with units appended) always show —
    they define the range, so they are never dropped. An exact 0 is added only when
    the range straddles it. Both the 0 and the midpoint are always *ticked* but their
    labels are droppable: the caller omits a label that would collide with another.
    A lopsided range makes this necessary — over -3..46 the 0 tick sits at 6% of the
    ramp and its label lands on top of the "-3", drawing as "-30".
    """
    ticks = [(vmin, f"{vmin:g}", "l", False)]
    zero_shown = vmin < 0 < vmax
    if zero_shown:
        ticks.append((0.0, "0", "m", True))
    vmid = (vmin + vmax) / 2.0
    if not (zero_shown and vmid == 0.0):   # avoid a duplicate "0" tick (e.g. -3..3)
        ticks.append((vmid, f"{vmid:g}", "m", True))
    max_label = f"{vmax:g} {units}" if units else f"{vmax:g}"
    ticks.append((vmax, max_label, "r", False))
    return ticks


def add_colorbar(rgb: np.ndarray, cfg, y_offset: int = 4) -> np.ndarray:
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    font, lw = _annot_scale(h)
    bar_w = max(20, int(w * 0.4))
    bar_h = max(6, h // 20)
    x0, y0 = max(4, w // 200), y_offset
    vmin, vmax = cfg.viz_min, cfg.viz_max
    ramp = colorize(
        np.linspace(vmin, vmax, bar_w)[None, :],
        vmin, vmax, cfg.palette,
    )[0]  # (bar_w, 3)
    # Units: the index's own (e.g. "°C" for a thermal index); a climatology anomaly
    # renders z-scores instead, so its unit overrides whatever the index carries.
    meta = _index_meta(cfg)
    units = "σ" if getattr(cfg, "anomaly", None) == "climatology" else (meta.units if meta else "")
    ticks = _colorbar_ticks(vmin, vmax, units)

    def _x(v: float) -> float:
        span = (vmax - vmin) or 1.0
        return x0 + (v - vmin) / span * bar_w

    def _label_bounds(text: str, x: float, anchor: str) -> tuple:
        tb = draw.textbbox((0, 0), text, font=font)
        tw = tb[2] - tb[0]
        if anchor == "l":
            return x, x + tw
        if anchor == "r":
            return x - tw, x
        return x - tw / 2, x + tw / 2

    # A droppable (mid) label is measured against every other label, as
    # draw_scale_bar measures its label with textbbox; skipped if it would overlap.
    gap = max(2, lw * 2)
    show_label = [True] * len(ticks)
    for i, (v, label, anchor, droppable) in enumerate(ticks):
        if not droppable:
            continue
        lo, hi = _label_bounds(label, _x(v), anchor)
        show_label[i] = all(
            hi + gap <= _label_bounds(o_label, _x(o_v), o_anchor)[0]
            or lo - gap >= _label_bounds(o_label, _x(o_v), o_anchor)[1]
            for j, (o_v, o_label, o_anchor, _od) in enumerate(ticks) if j != i
        )

    tick_top, tick_bot = y0 + bar_h, y0 + bar_h + lw + 2
    text_y = tick_bot + 1

    # Translucent panel behind the whole block. The ramp and its white labels sit on
    # top of the imagery, which can be any colour — white-on-pale-yellow was
    # unreadable. draw_scale_bar already backs its label the same way; without this
    # the legend's legibility depends on whatever the scene happens to look like.
    pad = max(3, lw * 2)
    text_h = draw.textbbox((0, 0), "0", font=font)[3]
    shown = [(_label_bounds(lb, _x(v), a)) for i, (v, lb, a, _d) in enumerate(ticks)
             if show_label[i]]
    right = max([x0 + bar_w] + [hi for _lo, hi in shown])
    left = min([x0] + [lo for lo, _hi in shown])
    draw.rectangle([left - pad, y0 - pad, right + pad, text_y + text_h + pad],
                   fill=(0, 0, 0, 130))

    for i in range(bar_w):
        c = tuple(int(v) for v in ramp[i])
        draw.line([(x0 + i, y0), (x0 + i, y0 + bar_h)], fill=c)
    draw.rectangle([x0, y0, x0 + bar_w, y0 + bar_h], outline=(255, 255, 255, 255), width=lw)

    for i, (v, label, anchor, _droppable) in enumerate(ticks):
        x = _x(v)
        draw.line([(x, tick_top), (x, tick_bot)], fill=(255, 255, 255, 255), width=lw)
        if show_label[i]:
            lo, _hi = _label_bounds(label, x, anchor)
            draw.text((lo, text_y), label, fill=(255, 255, 255, 255), font=font)
    return np.asarray(img)


def _index_display_name(cfg) -> str:
    """Plain-language name of the product being shown ("Vegetation greenness (NDVI)").

    Falls back to the bare index key upper-cased if the registry has no
    `display_name` — a frame with a terse title still beats a frame with none.
    """
    meta = _index_meta(cfg)
    name = getattr(meta, "display_name", "") if meta else ""
    return name or str(getattr(cfg, "index", "") or "").upper()


def _caveats(cfg) -> str:
    """Provenance caveats for header line 2, joined with " · " — "" when there are none.

    These are the honesty devices, reworded plain (they used to be parenthetical asides
    inside a formula string, which is not where a viewer looks):

    - **pooling** (`cfg.pool_years`): frames come from whichever year had the clearest
      scene, so the year range has to be on the frame, not only in the config.
      `gap_fill` keeps the requested year wherever it has data and only borrows for
      otherwise-empty periods, so its unmarked frames really are that year — warning
      that the whole run is "not a time series" would overstate it. `least_cloudy` and
      `median` re-pick *every* frame, so for those the blanket warning is correct.
    - **interpolation** (`cfg.interpolate`): most frames of an interpolated run were
      never observed; naming the count keeps the animation from reading as that many
      real acquisitions.
    """
    parts = []
    pool = getattr(cfg, "pool_years", None)
    if pool:
        span = f"{int(pool[0])}–{int(pool[-1])}"
        parts.append(f"gap-filled from {span}"
                     if getattr(cfg, "pool_strategy", None) == "gap_fill"
                     else f"every frame re-picked from {span} — not a time series")
    steps = int(getattr(cfg, "interpolate", 0) or 0)
    if steps:
        parts.append(f"{steps} generated frames between observations")
    return " · ".join(parts)


def _header_text(cfg) -> tuple:
    """``(title, subtitle, caveats)`` for the top margin — the whole header.

    Line 1 is the title (`cfg.title`, else the product's `display_name`); line 2 is
    the subtitle and the caveats, and exists only when at least one of them is
    non-empty. The formula and band list that used to fill this space are gone from
    the frame entirely — they live in the method doc (see `products.Index.bands`).
    """
    return (getattr(cfg, "title", None) or _index_display_name(cfg),
            getattr(cfg, "subtitle", None) or "",
            _caveats(cfg))


#: Sensor-specific attribution lines that carry no vintage (unlike Sentinel-2's,
#: whose exact Copernicus wording needs the year(s) actually shown — see
#: `_default_credit`). Landsat/MODIS courtesy lines are sensor-wide, not licence
#: text tied to a particular acquisition year.
_SENSOR_CREDITS = {
    "landsat": "Landsat imagery courtesy of the U.S. Geological Survey",
    "modis": "MODIS data courtesy of NASA LP DAAC",
    "modis_lst": "MODIS data courtesy of NASA LP DAAC",
}


def _credit_years(cfg) -> str:
    """Year or year-span for the Sentinel-2 auto-credit line ("2022" / "2018–2024").

    Spans `cfg.start`'s year through the year before `cfg.end` (end is exclusive,
    same convention as everywhere else this codebase reads a date range). Widened
    to cover `cfg.pool_years` when cross-year pooling is on: a pooled frame's
    pixels can come from any year in that range, so the notice has to cover it
    too, not just the nominal run span. A single year renders bare; a real span
    uses an en dash, which the bundled DejaVu font (see `_font`) actually draws.
    Returns "" if `cfg.start`/`cfg.end` are missing or unparseable (defensive only
    — a real `RunConfig` always has valid dates by the time this runs).
    """
    try:
        y0 = date.fromisoformat(str(cfg.start)).year
        y1 = (date.fromisoformat(str(cfg.end)) - timedelta(days=1)).year
    except (TypeError, ValueError, AttributeError):
        return ""
    pool = getattr(cfg, "pool_years", None)
    if pool:
        y0 = min(y0, int(pool[0]))
        y1 = max(y1, int(pool[-1]))
    return str(y0) if y0 == y1 else f"{y0}–{y1}"


def _default_credit(cfg) -> str:
    """The attribution line drawn bottom-right, or "" to draw none.

    `cfg.credit` wins verbatim whenever it is *set* — including an explicit `""`,
    which is a conscious "no credit line" decision (see `config.RunConfig.validate`,
    which warns about it for sentinel2, since the Copernicus licence requires this
    notice on published products) rather than an unset field. `None` (the default —
    nobody wrote a `credit:` key) falls back to a sensor-appropriate default, so a
    completely default config still carries correct attribution: that zero-config
    behaviour is the whole point, not a nice-to-have.

    Sentinel-2's notice is the exact required Copernicus wording plus the year(s)
    actually shown (`_credit_years`); Landsat/MODIS get a fixed courtesy line
    (`_SENSOR_CREDITS`). An unrecognised/absent sensor gets no auto-credit ("").
    """
    explicit = getattr(cfg, "credit", None)
    if explicit is not None:
        return explicit
    sensor = getattr(cfg, "sensor", None)
    if sensor == "sentinel2":
        years = _credit_years(cfg)
        return f"Contains modified Copernicus Sentinel data {years}".rstrip() \
            if years else "Contains modified Copernicus Sentinel data"
    return _SENSOR_CREDITS.get(sensor, "")


def _two_line_header(cfg) -> bool:
    """Whether the header needs a second line — decidable from `cfg` alone.

    This matters more than it looks: the second line **doubles the top margin**, and
    every consumer of `_margins` (`add_margins`, `_fit_margins`, `_output_spec`) has
    to agree on the same answer or the bars and the imagery disagree about where the
    picture starts. Because subtitle, pooling and interpolation are all run-level
    settings, the answer is the same for every frame of a run and is computed once,
    before the frame loop.
    """
    _title, subtitle, caveats = _header_text(cfg)
    return bool(subtitle or caveats)


def _margins(imagery_h: int, two_line_header: bool = False) -> tuple:
    """(top_h, bottom_h) label margins to pad imagery `imagery_h` px tall with.

    `draw_info_bar`/`annotate` pick their bar height as `max(12, h // 12)` of the
    array they are handed — which is the *padded* array — so the margins have to be
    whole multiples of that height. With a one-line header the top holds one bar and
    the bottom one: ``m == max(12, (imagery_h + top_h + bottom_h) // 12)``, whose
    exact solution is ``imagery_h // 10`` under a 12 px floor. With a two-line header
    the top holds two, so the fixed point becomes
    ``b == (imagery_h + 3b + 1) // 12`` — i.e. ``b == (imagery_h + 1) // 9``, again
    under the 12 px floor (below which `_bar_h`'s own floor keeps both sides at 12).
    The top margin carries one extra pixel because PIL's `rectangle` includes its
    bottom edge: without it the header's last row would tint the imagery's first row.
    """
    if two_line_header:
        b = max(12, (imagery_h + 1) // 9)
        return 2 * b + 1, b
    m = max(12, imagery_h // 10)
    return m + 1, m


def add_margins(rgb: np.ndarray, top_h: int, bottom_h: int, bg=LETTERBOX_RGB) -> np.ndarray:
    """Grow the frame by blank margins above and below, imagery unchanged in between.

    The label bars used to be painted *over* the imagery (~17 % of every frame
    hidden). They are now drawn into these margins instead, so nothing is occluded —
    `draw_info_bar` and `annotate` stay shape-preserving and simply receive the
    already-padded array.
    """
    if top_h <= 0 and bottom_h <= 0:
        return rgb
    h, w = rgb.shape[:2]
    out = np.empty((h + top_h + bottom_h, w, 3), dtype=np.uint8)
    out[:] = np.asarray(bg, dtype=np.uint8)
    out[top_h:top_h + h] = rgb.astype(np.uint8)
    return out


def _bar_h(h: int) -> int:
    """Info/label bar height for a frame `h` px tall — shared by draw_info_bar and
    annotate so the two bars stay the same height (see also _margins, which sizes the
    padding around them to match; that is a related but distinct computation, see
    _margins' docstring)."""
    return max(12, h // 12)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    tb = draw.textbbox((0, 0), text, font=font)
    return tb[2] - tb[0]


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, avail_w: int) -> str:
    """Longest prefix of `text` (+ "…") that fits `avail_w` at `font`; "" if none does.

    "…" (U+2026) is a real glyph in the bundled font (see `_font`), so it draws as an
    ellipsis rather than a notdef box.
    """
    if _text_w(draw, text, font) <= avail_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_w(draw, text[:mid] + "…", font) <= avail_w:
            lo = mid
        else:
            hi = mid - 1
    if lo:
        return text[:lo] + "…"
    return "…" if _text_w(draw, "…", font) <= avail_w else ""


def _fit_bar_text(draw: ImageDraw.ImageDraw, text: str, px: int, avail_w: int) -> tuple:
    """(font, text) that fits `avail_w` — shrink font size down to the `_font` floor
    (10px) before truncating with a trailing ellipsis.

    Started at `px` (whatever the caller would otherwise have used unshrunk), so text
    that already fits is returned completely unchanged — same font, same string.
    """
    font = _font(px)
    while _text_w(draw, text, font) > avail_w and px > 10:
        px -= 1
        font = _font(px)
    return font, (_truncate(draw, text, font, avail_w) or "…")


def _fit_line_height(draw: ImageDraw.ImageDraw, px: int, max_h: int, floor_px: int) -> int:
    """Largest size <= `px` (never below `floor_px`) whose glyphs are <= `max_h` tall.

    Each header line gets one `_bar_h` of vertical room, and `_bar_h` has a 12 px
    floor while `_font` has a 10 px one — so on a small frame the nominal size does
    not fit its row and the glyphs would spill out of the margin and back over the
    imagery, which is the one thing the margins exist to prevent. "Ag" is the probe
    because it spans ascender to descender; `draw_info_bar` additionally *clips* the
    header to its zone, so this is the "keep the text whole" half of the guarantee,
    not the guarantee itself.
    """
    while px > floor_px and draw.textbbox((0, 0), "Ag", font=_font(px))[3] > max_h:
        px -= 1
    return px


#: How much larger the header's title line is than the body text (`_annot_scale`).
#: Enough to read as a headline; the bar height caps it on small frames.
_TITLE_SCALE = 1.5


def _fit_header_line2(draw: ImageDraw.ImageDraw, subtitle: str, caveats: str,
                      px: int, avail_w: int) -> tuple:
    """(font, text) for header line 2, at `px` — never shrunk below it to gain width.

    Line 2 carries the provenance caveats, which is why it does *not* shrink the way
    `_fit_bar_text` does for the title: a caveat set at 10 px on a 4K frame is a
    caveat nobody reads. When the line is too wide, the **subtitle** pays — trimmed
    with "…", and dropped entirely if even that will not fit. The caveat outranks the
    decoration; that ordering is an integrity requirement, not a styling preference.

    Only when the caveats alone overflow with no subtitle left to give — a narrow
    frame; measurably below ~524 px wide for the pooled caveat, and portrait aspects
    reach that sooner — does the shrink-then-truncate fallback apply to them, because
    at that point there is nothing left to trade.

    `px` is a *width* floor, which is a separate question from whether the line fits
    its row vertically: the caller passes a size already capped by `_fit_line_height`
    so the glyphs cannot spill out of the header zone. On any frame whose bar is tall
    enough for it — every output at or above roughly 480 px — that cap is inert and
    `px` is exactly `_annot_scale`'s size.
    """
    font = _font(px)
    joined = " · ".join(p for p in (subtitle, caveats) if p)
    if _text_w(draw, joined, font) <= avail_w:
        return font, joined
    if not caveats:                       # decoration only: trim it at the floor size
        return font, _truncate(draw, subtitle, font, avail_w)
    if subtitle:
        budget = avail_w - _text_w(draw, f" · {caveats}", font)
        trimmed = _truncate(draw, subtitle, font, budget) if budget > 0 else ""
        if trimmed:
            return font, f"{trimmed} · {caveats}"
        if _text_w(draw, caveats, font) <= avail_w:
            return font, caveats          # subtitle dropped; the caveat stays whole
    return _fit_bar_text(draw, caveats, px, avail_w)      # last resort (see docstring)


def draw_info_bar(rgb: np.ndarray, title: str, subtitle: str = "",
                  caveats: str = "") -> np.ndarray:
    """Draw the translucent header into the top margin: title, then subtitle+caveats.

    Line 1 is the title, set larger than the body text — it is what tells a viewer
    what and where this is, which no frame used to say at all. Line 2 exists only when
    `subtitle` or `caveats` is non-empty, and when it does the bar is **two**
    `_bar_h`s tall; `_margins`/`_two_line_header` size the margin to match, so the
    header never touches the imagery either way.

    Text is drawn as given — the bundled font (see `_font`) renders it directly, no
    fold step required. A long title is shrunk (and, in the worst case, truncated) by
    `_fit_bar_text` so it stays inside the frame; line 2 is fitted by
    `_fit_header_line2`, which protects the caveat instead. The bar rectangle and the
    vertical placement never depend on the text.

    **The header is drawn into its own zone and clipped to it**, rather than onto the
    full frame. Font metrics are not the same quantity as the layout arithmetic: a
    line's row is `_bar_h` tall (12 px floor) while `_font` only shrinks to 10 px, and
    at 11 px DejaVu's ascender-to-descender box is 13 px — so on a small frame the
    text physically does not fit its row and used to spill straight onto imagery row 0
    (measured: every imagery height from 1 to 115 px, up to 45 lit pixels on the
    picture). `_fit_line_height` keeps the text whole wherever the row can hold it;
    the clip is what makes "no header ink on the imagery" true unconditionally, at
    every height, for any string and any future font. Cropping first also keeps the
    blend byte-identical to drawing on the whole frame — same RGBA compositing, same
    pixels, just a smaller canvas.
    """
    out = rgb.astype(np.uint8, copy=True)   # our own buffer — safe to write the zone into
    h, w = out.shape[:2]
    bar_h = _bar_h(h)                 # sized from the FULL frame, not the crop
    two_line = bool(subtitle or caveats)
    # The zone this header owns: exactly the top margin `_margins` reserved for it
    # (`bar_h` per line, +1 because PIL's `rectangle` includes its bottom edge).
    zone_h = min(h, bar_h * (2 if two_line else 1) + 1)
    img = Image.fromarray(out[:zone_h], "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    pad = max(1, h // 200)
    x = max(4, w // 200)
    avail_w = max(1, w - 2 * x)
    avail_line_h = bar_h - pad
    # `_annot_scale`'s size is what line 2 wants; `_fit_line_height` only lowers it on
    # frames whose bar is too short to hold it (see `_fit_header_line2`). The title is
    # larger, and never smaller than line 2.
    base_px = max(11, h // 40)
    line2_px = _fit_line_height(draw, base_px, avail_line_h, 10)
    title_px = _fit_line_height(draw, round(base_px * _TITLE_SCALE), avail_line_h, line2_px)
    title_font, title = _fit_bar_text(draw, title, title_px, avail_w)
    draw.rectangle([0, 0, w, bar_h * (2 if two_line else 1)], fill=(0, 0, 0, 140))
    draw.text((x, pad), title, fill=(255, 255, 255, 255), font=title_font)
    if two_line:
        font, line2 = _fit_header_line2(draw, subtitle, caveats, line2_px, avail_w)
        draw.text((x, bar_h + pad), line2, fill=(255, 255, 255, 255), font=font)
    out[:zone_h] = np.asarray(img)
    return out


#: How much smaller the bottom-bar credit line is than the body text
#: (`_annot_scale`) — small enough to read as a footnote, not compete with the
#: period label. `_font`'s own 10 px floor (see `_font`) is applied on top.
_CREDIT_SCALE = 0.8


def annotate(rgb: np.ndarray, label: str, credit: str = "") -> np.ndarray:
    """Draw a translucent bottom bar with `label` (left) and `credit` (right).

    `label` is drawn as given — the bundled font (see `_font`) renders it directly.
    `render()` still routes the pooled-frame provenance text through `_drawable`
    before calling this (now a no-op fold, kept as a seam) when composing it.

    `credit` is the attribution/licence line (see `_default_credit`) — small text,
    `_CREDIT_SCALE` of the body size with a 10 px floor, right-aligned in the same
    bar. It is drawn only when non-empty and never at the cost of `label`: the two
    are different kinds of text — `label` is the data (what period/source this
    frame shows), `credit` is a compliance line (e.g. the Copernicus licence
    notice) — and neither may be silently dropped to make room for the other. If
    they would collide even at the credit's floor size (an extremely narrow
    frame), `credit` is the one that gives: it is clipped with a trailing "…" at
    the point where `label` ends, rather than shrinking/truncating `label` or
    omitting `credit` outright. This is a last-resort degradation, not a routine
    truncation — on any realistic output width both draw whole.
    """
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    font, _ = _annot_scale(h)
    bar_h = _bar_h(h)
    x = max(4, w // 200)
    y = h - bar_h + max(1, h // 200)
    # No crop-and-write-back needed here (unlike draw_info_bar): text is anchored at
    # the TOP of this bottom bar and grows downward, toward the frame's bottom edge
    # and away from the imagery above — the direction draw_info_bar's header text
    # could spill into imagery in is not available to this bar at all.
    draw.rectangle([0, h - bar_h, w, h], fill=(0, 0, 0, 140))
    draw.text((x, y), label, fill=(255, 255, 255, 255), font=font)
    if credit:
        base_px = max(11, h // 40)              # same size _annot_scale would use
        credit_px = max(10, round(base_px * _CREDIT_SCALE))
        credit_font = _font(credit_px)
        gap = max(4, w // 200)
        label_right = x + _text_w(draw, label, font)
        right_margin = w - x
        avail = right_margin - label_right - gap
        if avail > 0:
            if _text_w(draw, credit, credit_font) > avail:
                credit = _truncate(draw, credit, credit_font, avail)
        else:
            credit = ""                          # no room at all: drop, don't overlap
        if credit:
            credit_w = _text_w(draw, credit, credit_font)
            credit_x = right_margin - credit_w
            draw.text((credit_x, y), credit, fill=(255, 255, 255, 255), font=credit_font)
    return np.asarray(img)


def _geom_rings(geom: dict) -> list:
    """Rings (each a list of (lon, lat)) for a GeoJSON Polygon/MultiPolygon/LineString."""
    t = geom["type"]
    if t == "Polygon":
        return [[(x, y) for x, y in ring] for ring in geom["coordinates"]]
    if t == "MultiPolygon":
        return [[(x, y) for x, y in ring]
                for poly in geom["coordinates"] for ring in poly]
    if t == "LineString":
        return [[(x, y) for x, y in geom["coordinates"]]]
    raise ValueError(f"unsupported region geometry type for overlay: {t}")


def _aoi_geom_dict(aoi_cfg: dict) -> dict:
    """GeoJSON geometry (EPSG:4326) for a non-bbox AOI: shapefile or GeoJSON."""
    if aoi_cfg.get("shapefile"):
        return _read_shapefile_geometry(aoi_cfg["shapefile"])
    return _load_geojson_geometry(aoi_cfg["geojson"])


def _aoi_bounds(aoi_cfg: dict) -> tuple:
    """(minLon, minLat, maxLon, maxLat) for an AOI (bbox, GeoJSON, or shapefile)."""
    if aoi_cfg.get("bbox"):
        b = aoi_cfg["bbox"]
        return (b[0], b[1], b[2], b[3])
    rings = _geom_rings(_aoi_geom_dict(aoi_cfg))
    xs = [x for ring in rings for x, _ in ring]
    ys = [y for ring in rings for _, y in ring]
    return (min(xs), min(ys), max(xs), max(ys))


def _region_rings(aoi_cfg: dict) -> list:
    """Rings (list of (lon, lat)) for a region AOI (bbox rectangle, GeoJSON, or shapefile)."""
    if aoi_cfg.get("bbox"):
        mnx, mny, mxx, mxy = aoi_cfg["bbox"]
        return [[(mnx, mny), (mxx, mny), (mxx, mxy), (mnx, mxy), (mnx, mny)]]
    return _geom_rings(_aoi_geom_dict(aoi_cfg))


def _region_ss(h: int) -> int:
    """Supersampling factor for `_region_masks`: 4x up to ~1200 px tall frames, 2x
    above — a 4K frame at 4x would allocate a ~130 MB single-channel mask."""
    return 4 if h <= 1200 else 2


def _region_masks(bounds: tuple, rings: list, shape: tuple, width: int) -> tuple:
    """Antialiased (casing_mask, core_mask) — float32 arrays in `shape`, 0..1 alpha.

    PIL's `ImageDraw.line` has no antialiasing, so every diagonal ring segment drawn
    directly at frame resolution is a hard staircase. Instead each ring is drawn
    hard-edged (using the existing lon/lat -> pixel mapping) into an `L`-mode canvas
    at `_region_ss(h)`x the frame size, then LANCZOS-downsampled back down — the
    downsample is what turns the staircase into a smooth, antialiased edge. The
    supersampled line width carries a small fixed pad: LANCZOS attenuates a thin
    line's peak value, and without the pad a nominal width=1 core would downsample to
    a translucent (<255) centreline instead of a fully opaque one.
    """
    h, w = shape
    width = max(width, round(h / 430))        # scale the outline for high-res output
    cw = width + 2 * max(1, width // 2 + 1)   # dark casing is wider than the core
    ss = _region_ss(h)
    pad = max(1, ss // 2)
    minx, miny, maxx, maxy = bounds
    dx = (maxx - minx) or 1.0
    dy = (maxy - miny) or 1.0

    def _mask(line_width: int) -> np.ndarray:
        big = Image.new("L", (w * ss, h * ss), 0)
        draw = ImageDraw.Draw(big)
        for ring in rings:
            pts = [((lon - minx) / dx * w * ss, (maxy - lat) / dy * h * ss)
                   for lon, lat in ring]
            if len(pts) >= 2:
                draw.line(pts, fill=255, width=line_width * ss + pad, joint="curve")
        small = big.resize((w, h), Image.LANCZOS)
        return np.asarray(small, dtype=np.float32) / 255.0

    return _mask(cw), _mask(width)


def _composite_region(rgb: np.ndarray, casing_mask: np.ndarray, core_mask: np.ndarray,
                      color=REGION_OUTLINE_RGB, casing=(0, 0, 0)) -> np.ndarray:
    """Alpha-composite the dark casing then the bright core through their masks.

    Cheap (no drawing) — meant to be called once per frame against masks built once
    by `_region_masks`, since the rings/bounds/frame size are identical every frame.

    Only the pixels the outline actually touches are blended. A region outline covers
    ~2 % of a frame, yet the whole frame used to be converted to float32 and blended
    twice (each blend allocating frame-sized temporaries) — ~0.19 s per 1920x2118
    frame, 4.2 s of a 22-frame run. Everywhere else both alphas are exactly 0, so the
    full-frame blend degenerates to ``rgb * 1.0 + colour * 0.0`` and the only thing it
    did was the float32 -> uint8 round trip, which `out` reproduces exactly (uint8 ->
    float32 -> uint8 is lossless, so a uint8 frame is simply copied; a float frame —
    `colorize`'s output — still makes the round trip, so its truncation matches).
    Output is therefore byte-identical to blending the whole frame, pinned by
    tests/test_render.py::test_composite_region_matches_a_full_frame_blend.
    """
    out = rgb.copy() if rgb.dtype == np.uint8 else rgb.astype(np.float32).astype(np.uint8)
    touched = (casing_mask > 0) | (core_mask > 0)      # ~2% of the frame
    patch = rgb[touched].astype(np.float32)            # (n_touched, 3)
    for mask, rgb_color in ((casing_mask, casing), (core_mask, color)):
        a = mask[touched][:, None]
        patch = patch * (1 - a) + np.asarray(rgb_color, dtype=np.float32) * a
    out[touched] = patch.astype(np.uint8)
    return out


def draw_region(rgb: np.ndarray, bounds: tuple, rings: list,
                color=REGION_OUTLINE_RGB, width: int = 2, casing=(0, 0, 0),
                masks: tuple | None = None) -> np.ndarray:
    """Draw region polygon outlines onto an RGB frame.

    `bounds` is the frame extent (minLon, minLat, maxLon, maxLat); the EE thumbnail
    is rendered in linear EPSG:4326 over this extent, so lon/lat map to pixels
    linearly (top row = maxLat). Each ring is drawn as a dark `casing` under the bright
    `color` core, so the outline stays visible on any background — including the amber
    core over hot (yellow/red) LST pixels, where it would otherwise vanish. Edges are
    antialiased via `_region_masks` (see there).

    `masks`, if given, is a precomputed `(casing_mask, core_mask)` pair (from
    `_region_masks`) to composite directly, skipping the (comparatively expensive)
    supersampled draw — callers that draw many frames over the same rings/bounds/size
    (e.g. `render()`) build the masks once and pass them to every call.
    """
    if masks is None:
        masks = _region_masks(bounds, rings, rgb.shape[:2], width)
    return _composite_region(rgb, *masks, color=color, casing=casing)


def _frame_span_m(bounds: tuple) -> float:
    """Larger ground dimension (metres) of the frame — EE fits it to `dimensions`."""
    minx, miny, maxx, maxy = bounds
    mid = math.radians((miny + maxy) / 2.0)
    w = (maxx - minx) * 111320.0 * math.cos(mid)
    h = (maxy - miny) * 111320.0
    return max(w, h)


def _cap_dimensions(cfg, bounds) -> None:
    """Cap cfg.dimensions so the render is no finer than the product's native GSD.

    Mutates cfg.dimensions (render is the terminal step). With cfg.allow_upsample the
    request is honoured but a warning names the true native resolution.
    """
    native = native_scale_m(getattr(cfg, "sensor", None), getattr(cfg, "index", None))
    dims = getattr(cfg, "dimensions", None)
    if not (bounds and native and dims):
        return
    max_dim = max(1, int(_frame_span_m(bounds) / native))
    if dims <= max_dim:
        return
    # With a screen preset the imagery is upscaled client-side (smooth Lanczos), so the
    # fetch must stay at native — fetching finer here would force Earth Engine to
    # nearest-neighbour upsample the thumbnail (blocky). Without a preset, allow_upsample
    # lets the user render directly finer than native.
    preset = getattr(cfg, "preset", None)
    if getattr(cfg, "allow_upsample", False) and not preset:
        log.warning("rendering %s at %d px upsamples the ~%dm-native data %.1fx; "
                    "pixels finer than %dm are interpolated.",
                    getattr(cfg, "index", "?"), dims, native, dims / max_dim, native)
        return
    log.warning("capping fetch dimensions %d -> %d to native (~%dm/px for %s)%s.",
                dims, max_dim, native, getattr(cfg, "index", "?"),
                "; the preset upscales it smoothly to the output size" if preset
                else "; set allow_upsample: true to override")
    cfg.dimensions = max_dim


def _nice_distance(meters: float) -> float:
    """Round a distance down to a cartographer-friendly 1/2/5 × 10ᵏ value."""
    exp = math.floor(math.log10(meters))
    base = 10 ** exp
    for mult in (5, 2, 1):
        if meters >= mult * base:
            return mult * base
    return base


def _utm_epsg(lon: float, lat: float) -> str:
    """WGS84 UTM EPSG code for a lon/lat (e.g. Brandenburg 13.9E/53N -> EPSG:32633)."""
    zone = int((lon + 180) / 6) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def _resolve_crs(cfg, bounds):
    """Concrete render CRS: None (EPSG:4326), an explicit code, or UTM for "auto"."""
    crs = getattr(cfg, "crs", None)
    if not crs:
        return None
    if crs == "auto":
        if bounds is None:
            return None
        minx, miny, maxx, maxy = bounds
        return _utm_epsg((minx + maxx) / 2.0, (miny + maxy) / 2.0)
    return crs


def _project(bounds, rings, crs):
    """Project a lon/lat bbox + rings into `crs` (metres). Returns (bounds, rings)."""
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    minx, miny, maxx, maxy = bounds
    xs, ys = zip(*(t.transform(lon, lat) for lon, lat
                   in [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]))
    pbounds = (min(xs), min(ys), max(xs), max(ys))
    prings = [[t.transform(lon, lat) for lon, lat in ring] for ring in rings]
    return pbounds, prings


def _frame_width_m(bounds, proj_bounds, crs) -> float:
    """Ground width (metres) of the frame's x-extent, in whatever CRS is rendered."""
    if crs:
        return proj_bounds[2] - proj_bounds[0]
    minx, miny, maxx, maxy = bounds
    return (maxx - minx) * 111320.0 * math.cos(math.radians((miny + maxy) / 2.0))


def _scale_bar_layout(draw: ImageDraw.ImageDraw, w: int, h: int, frame_width_m: float,
                      target_frac: float) -> dict | None:
    """Geometry for `draw_scale_bar`'s panel, or None where it would be a no-op.

    Factored out of `draw_scale_bar` so `draw_north_arrow` can find out exactly
    where the scale bar's panel sits (to stack directly above it) without
    duplicating — and risking drifting from — the arithmetic that decides it. Both
    callers therefore always agree on the scale bar's position, including the case
    where there is no scale bar at all (frame too narrow, or no usable
    `frame_width_m`), which is mirrored here via the same guard.
    """
    if w < 24 or frame_width_m <= 0:      # too small to annotate meaningfully
        return None
    nice_m = _nice_distance(frame_width_m * target_frac)
    bar_px = int(round(nice_m / (frame_width_m / w)))
    if bar_px < 1:
        return None
    label = f"{nice_m / 1000:g} km" if nice_m >= 1000 else f"{nice_m:g} m"
    font, lw = _annot_scale(h)
    margin, tick = max(6, w // 100), max(4, h // 80)
    x1 = w - margin
    x0 = x1 - bar_px
    y = h - margin                    # `rgb` is the imagery rect; the month-label bar
                                      # lives in the margin added below it, not here
    tb = draw.textbbox((0, 0), label, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = max(4, h // 200)
    panel_top = y - tick - th - pad
    panel_bottom = y + pad
    return dict(x0=x0, x1=x1, y=y, tick=tick, label=label, tw=tw, th=th, pad=pad,
               panel_top=panel_top, panel_bottom=panel_bottom, font=font, lw=lw)


def draw_scale_bar(rgb: np.ndarray, frame_width_m: float, target_frac: float = 0.25,
                   color=(255, 255, 255)) -> np.ndarray:
    """Draw a ground-distance scale bar (bottom-right) onto an RGB frame.

    `frame_width_m` is the frame's x-extent in metres (the image width maps to it),
    so the bar is correct whether the render is plate carrée or a metric CRS. A
    "nice" round distance near `target_frac` of the frame width is chosen.
    """
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    h, w = rgb.shape[:2]
    draw = ImageDraw.Draw(img, "RGBA")
    geo = _scale_bar_layout(draw, w, h, frame_width_m, target_frac)
    if geo is None:
        return np.asarray(img)
    x0, x1, y, tick = geo["x0"], geo["x1"], geo["y"], geo["tick"]
    label, tw = geo["label"], geo["tw"]
    pad, panel_top, panel_bottom = geo["pad"], geo["panel_top"], geo["panel_bottom"]
    font, lw = geo["font"], geo["lw"]
    draw.rectangle([x0 - pad, panel_top, x1 + pad, panel_bottom], fill=(0, 0, 0, 120))
    draw.line([(x0, y), (x1, y)], fill=color, width=lw)
    draw.line([(x0, y - tick), (x0, y)], fill=color, width=lw)   # end ticks
    draw.line([(x1, y - tick), (x1, y)], fill=color, width=lw)
    draw.text(((x0 + x1) / 2 - tw / 2, panel_top + pad // 2), label, fill=color, font=font)
    return np.asarray(img)


def draw_north_arrow(rgb: np.ndarray, frame_width_m: float, target_frac: float = 0.25,
                     color=(255, 255, 255)) -> np.ndarray:
    """Draw a north indicator (upward arrow + "N") directly above the scale bar panel.

    Both CRSs this renderer ever produces pixels in are north-up by construction:
    plate carrée (EPSG:4326, unprojected — rows are lines of latitude, "up" is
    increasing latitude) and the UTM zones `_resolve_crs`/`_utm_epsg` pick for
    `crs: "auto"` (a projected, north-aligned grid). So "up" on the frame is always
    geographic north, unconditionally, and this draws a fixed vertical glyph rather
    than computing (or pretending to support) a rotation for an oblique CRS this
    codebase never produces.

    Shares `_scale_bar_layout` with `draw_scale_bar`, so the two panels always
    agree on where the scale bar sits: this one is centred on the same x-span, its
    panel bottom sitting a small gap above the scale bar panel's top. It is a
    no-op (frame unchanged) in exactly the cases `draw_scale_bar` itself would be
    — too narrow a frame, or no usable `frame_width_m` — since there would be
    nothing to stack it above.

    `target_frac` must match whatever `draw_scale_bar` was (or will be) called
    with for the same frame — `render()` uses the shared default for both — or the
    two panels' geometry would be computed against different scale bars.
    """
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    h, w = rgb.shape[:2]
    draw = ImageDraw.Draw(img, "RGBA")
    geo = _scale_bar_layout(draw, w, h, frame_width_m, target_frac)
    if geo is None:
        return np.asarray(img)
    font, lw, pad = geo["font"], geo["lw"], geo["pad"]
    gap = max(4, h // 100)                    # visual gap between the two stacked panels
    tb = draw.textbbox((0, 0), "N", font=font)
    n_w, n_h = tb[2] - tb[0], tb[3] - tb[1]
    arrow_h = max(10, h // 30)
    arrow_w = max(8, round(arrow_h * 0.7))
    panel_w = max(arrow_w, n_w) + 2 * pad
    panel_h = arrow_h + n_h + 3 * pad
    cx = (geo["x0"] + geo["x1"]) / 2.0
    panel_bottom = geo["panel_top"] - gap
    panel_top = panel_bottom - panel_h
    x0, x1 = cx - panel_w / 2.0, cx + panel_w / 2.0
    draw.rectangle([x0, panel_top, x1, panel_bottom], fill=(0, 0, 0, 120))
    # Upward arrow: a triangular head over a short shaft, both centred on cx.
    head_top = panel_top + pad
    shaft_top = head_top + arrow_h * 0.35
    shaft_bottom = head_top + arrow_h
    hw = arrow_w / 2.0
    draw.polygon([(cx, head_top), (cx - hw, shaft_top), (cx + hw, shaft_top)], fill=color)
    draw.line([(cx, shaft_bottom), (cx, shaft_top)], fill=color, width=lw)
    draw.text((cx - n_w / 2.0, shaft_bottom + pad // 2), "N", fill=color, font=font)
    return np.asarray(img)


def _fit_margins(pw: int, ph: int, ch: int, aoi_aspect: float,
                 two_line_header: bool = False) -> tuple:
    """Shrink the imagery place box so imagery + both label margins still fits `ch`.

    The margins are part of the output, so they have to come *out of* the canvas the
    preset asked for; adding them afterwards would make an `aspect: "16:9"` render
    taller than 16:9. With a one-line header the margins together are ~1/5 of the
    imagery height, so 10/12 of the canvas height is the analytic starting guess; a
    two-line header's are ~1/3, so it starts at 9/12. Either way the loop only trims
    the odd pixel — or a little more on small canvases where the 12 px floor dominates.
    """
    ph = min(ph, max(1, ch * (9 if two_line_header else 10) // 12))
    while ph > 1 and ph + sum(_margins(ph, two_line_header)) > ch:
        ph -= 1
    return max(1, min(pw, round(ph * aoi_aspect))), ph


def _output_spec(cfg, imagery_wh):
    """Screen-output layout, or None if no render.preset.

    Returns (canvas_w, canvas_h, place_w, place_h, method): the frame (native aspect)
    is upscaled to place_*, gains a label margin above and below, then is letterboxed
    onto the canvas.

    Where the label margins come from depends on whether an aspect was *requested*.
    For an explicit aspect the canvas ratio is contractual, so the margins are taken
    out of the place box (`_fit_margins`) — padding on top of a sized canvas would
    make a "16:9" render taller than 16:9. For "match" (and the unset default) no
    ratio was promised, so the canvas grows by the margins instead and the imagery
    keeps its full preset size: a "match" that letterboxed would not be matching.

    How tall those margins are depends on whether the run's header has a second line,
    which `_two_line_header` decides from `cfg` — the same decision `render()` makes
    for `add_margins`, so the layout computed here is the layout that gets drawn.
    """
    preset = getattr(cfg, "preset", None)
    if not preset:
        return None
    long_edge = PRESETS.get(str(preset).lower()) or int(preset)
    method = UPSCALE_METHODS.get(getattr(cfg, "upscale", "lanczos"), Image.LANCZOS)
    iw, ih = imagery_wh
    aoi_aspect = iw / ih
    aspect = getattr(cfg, "aspect", None)
    two_line = _two_line_header(cfg)
    if not aspect or aspect == "match":     # canvas == frame aspect; imagery fills it
        if aoi_aspect >= 1:
            pw, ph = long_edge, max(1, round(long_edge / aoi_aspect))
        else:
            ph, pw = long_edge, max(1, round(long_edge * aoi_aspect))
        # canvas grows, imagery doesn't
        return pw, ph + sum(_margins(ph, two_line)), pw, ph, method
    target = ASPECTS[aspect]
    if target >= 1:
        cw, ch = long_edge, max(1, round(long_edge / target))
    else:
        ch, cw = long_edge, max(1, round(long_edge * target))
    if aoi_aspect > target:                 # frame wider than canvas -> width-limited
        pw, ph = cw, max(1, round(cw / aoi_aspect))
    else:
        ph, pw = ch, max(1, round(ch * aoi_aspect))
    pw, ph = _fit_margins(pw, ph, ch, aoi_aspect, two_line)
    return cw, ch, pw, ph, method


def _upscale_rgb(rgb, pw, ph, method):
    if (rgb.shape[1], rgb.shape[0]) == (pw, ph):
        return rgb
    return np.asarray(Image.fromarray(rgb.astype(np.uint8), "RGB").resize((pw, ph), method))


def _letterbox(rgb, cw, ch, bg=LETTERBOX_RGB):
    h, w = rgb.shape[:2]
    if (w, h) == (cw, ch):
        return rgb
    canvas = Image.new("RGB", (cw, ch), bg)
    canvas.paste(Image.fromarray(rgb.astype(np.uint8), "RGB"), ((cw - w) // 2, (ch - h) // 2))
    return np.asarray(canvas)


def _cap_edge(frame: np.ndarray, max_edge: int) -> np.ndarray:
    """Downscale a frame so its longer side is <= max_edge (keeps GIFs preview-sized)."""
    h, w = frame.shape[:2]
    m = max(h, w)
    if m <= max_edge:
        return frame
    s = max_edge / m
    return np.asarray(Image.fromarray(frame.astype(np.uint8), "RGB")
                      .resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS))


def _pad_to_even(frame: np.ndarray) -> np.ndarray:
    """Pad a frame's width/height up to the next even number (edge-replicated).

    libx264 requires both dimensions to be even; AOIs frequently yield an odd
    width or height (e.g. 768x577). Padding by at most 1px avoids rescaling the
    whole frame (and imageio's resize warning).
    """
    h, w = frame.shape[:2]
    if h % 2 or w % 2:
        frame = np.pad(frame, ((0, h % 2), (0, w % 2), (0, 0)), mode="edge")
    return frame


def _write_mp4(path: Path, frames: Iterable[np.ndarray], fps: int) -> None:
    """Encode `frames` to MP4, appending each one as it arrives.

    `frames` may be any iterable, including a generator: an interpolated run reaches
    several hundred frames and at 4K that is tens of gigabytes if they are collected
    first. The writer is closed on every path — a leaked ffmpeg writer leaves the
    subprocess running and the file truncated.
    """
    writer = imageio.get_writer(path, fps=fps, macro_block_size=1)
    try:
        for frame in frames:
            writer.append_data(_pad_to_even(frame))
    finally:
        writer.close()


def _write_gif(path: Path, frames: list[np.ndarray], fps: int) -> None:
    # imageio>=2.28: GIF per-frame duration is in milliseconds. GIFs are capped to a
    # preview size so a 4K MP4 doesn't yield a hundreds-of-MB GIF.
    frames = [_cap_edge(f, _GIF_MAX_EDGE) for f in frames]
    imageio.mimsave(path, frames, format="GIF", duration=1000.0 / fps, loop=0)


#: PNG deflate level for the per-frame images. Left at Pillow's default 6 on purpose:
#: measured on 22 1920x2118 frames, level 3 saved 0.8 s of a 3.6 s serial write but
#: doubled the deliverable from 12.9 MB to 25.2 MB, and level 1 tripled it to 44 MB.
#: Writing the frames concurrently instead (below) is the bigger win and costs nothing
#: in file size — threaded, levels 3 and 6 are within noise of each other (1.36 s vs
#: 1.32 s). PNG is lossless, so this only ever trades size against time, never pixels.
_PNG_COMPRESS_LEVEL = 6


def _write_frames(out_dir: Path, name: str, frames_rgb: list[np.ndarray],
                  labels: list[str], workers: int = 1) -> list[Path]:
    """Save each annotated frame as its own PNG, named ``{name}_{label}.png``.

    Returns the frame paths in order so callers can offer the single images for
    download alongside the assembled MP4/GIF. The paths are computed up front, so the
    returned order is the frame order regardless of which thread finished first.

    Encoding is spread over `workers` threads (`cfg.workers`, the same knob that
    bounds the fetch concurrency — one dial, not two): zlib releases the GIL, so this
    is a real ~2.8x speedup on a multi-core machine, and each frame writes its own
    file so the threads never touch shared state.
    """
    paths = [out_dir / f"{name}_{label}.png" for label in labels]

    def _save(item):
        rgb, path = item
        Image.fromarray(rgb.astype(np.uint8), "RGB").save(
            path, compress_level=_PNG_COMPRESS_LEVEL)

    items = list(zip(frames_rgb, paths))
    if workers <= 1 or len(items) <= 1:
        for item in items:
            _save(item)
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(items)),
                                thread_name_prefix="gee-png") as pool:
            list(pool.map(_save, items))     # list() so any failure is re-raised here
    return paths


class _ProducerError(RuntimeError):
    """A failure raised by whatever *produces* frames, not by the encoder consuming them.

    `assemble_stream` wraps the MP4 write in `except Exception` so a missing ffmpeg can
    fall back to a GIF — but the producer is pulled from inside that write, so a fetch
    that fails halfway through surfaces at exactly the same place. Undistinguished, it
    is logged as "MP4 write failed" and the run returns a GIF holding only the frames
    that happened to arrive first: a silently truncated animation reported as success.
    This marker is what lets `assemble_stream` tell the two apart. A `RuntimeError` so
    `cli.main` still turns it into a clean exit code 1.
    """


def _produced(frames_iter: Iterable[np.ndarray]) -> Iterator[np.ndarray]:
    """Tag every failure of `frames_iter` as producer-side (see `_ProducerError`)."""
    try:
        yield from frames_iter
    except _ProducerError:
        raise
    except Exception as exc:            # noqa: BLE001 (re-raised, tagged, not swallowed)
        raise _ProducerError(f"rendering frames failed: {exc}") from exc


def assemble_stream(frames_iter: Iterable[np.ndarray], cfg) -> list[Path]:
    """Encode an *iterator* of frames to MP4 and (unless `render.gif` is false) GIF.

    Frames are written as they arrive rather than collected first: an interpolated run
    can reach several hundred frames, and at 4K that would be tens of gigabytes held at
    once. Only the GIF's frames are retained — a GIF genuinely needs them all at
    quantisation time — and those are capped to `_GIF_MAX_EDGE` here, as they pass, so
    the full-size originals can be released immediately. `assemble` wraps this for
    callers that already hold a list.

    The GIF is the slowest single step of a render (5.4 s of a 24 s run, half of it
    PIL's colour quantisation) and is only a preview format — `render.gif: false`
    skips it. It defaults to true, so an existing config's outputs are unchanged.
    """
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gif_path = out_dir / f"{cfg.name}.gif"
    mp4_path = out_dir / f"{cfg.name}.mp4"
    gif_flag = getattr(cfg, "gif", True)
    # None means "not configured" — `render()` resolves it (an interpolated run turns
    # the GIF off); anything reaching here unresolved keeps the historical default.
    want_gif = True if gif_flag is None else bool(gif_flag)
    paths: list[Path] = []
    frames_iter = iter(frames_iter)
    gif_frames: list[np.ndarray] = []

    def _tee(source: Iterator[np.ndarray]) -> Iterator[np.ndarray]:
        """Pass frames through to the encoder, keeping a preview-sized GIF copy."""
        for frame in source:
            if want_gif:
                gif_frames.append(_cap_edge(frame, _GIF_MAX_EDGE))
            yield frame

    try:
        _write_mp4(mp4_path, _tee(frames_iter), cfg.fps)
        paths.append(mp4_path)
    except _ProducerError:
        # Not an encode failure: the frames themselves could not be produced. There is
        # nothing to fall back to — a GIF of the frames that did arrive would be a
        # truncated animation returned as a successful run (see `_ProducerError`).
        mp4_path.unlink(missing_ok=True)
        raise
    except Exception as exc:  # ffmpeg missing / encode error
        # A partial .mp4 beside the GIF reads as a successful render, so drop it.
        mp4_path.unlink(missing_ok=True)
        if not want_gif:
            # The GIF is the fallback that makes an MP4 failure survivable; without it
            # there is no animation at all, so fail fast rather than return only PNGs.
            raise RuntimeError(
                f"MP4 encoding failed ({exc}) and render.gif is disabled, so no "
                f"animation could be written; set render.gif: true to fall back to a GIF"
            ) from exc
        log.warning("MP4 write failed (%s); producing GIF only", exc)
        # The MP4 pass was what pulled frames off the iterator, and it stopped where it
        # failed — possibly at frame 0. Drain the rest so the fallback GIF is complete.
        for _ in _tee(frames_iter):
            pass
    if want_gif:
        # Already capped above; _cap_edge is a no-op the second time, and leaving the
        # cap in _write_gif keeps that function correct for its own callers.
        _write_gif(gif_path, gif_frames, cfg.fps)
        paths.append(gif_path)
    return paths


def assemble(frames_rgb: list[np.ndarray], cfg) -> list[Path]:
    """Encode an already-collected list of frames — thin wrapper over `assemble_stream`.

    Unchanged contract: MP4 path first, then the GIF path if one was written.
    """
    return assemble_stream(iter(frames_rgb), cfg)


#: Default fetch concurrency. Measured on a 22-frame Landsat LST run: serial
#: 2.65 s/frame, 4 workers 0.81 s/frame (3.3x), 8 workers 1.04 s/frame (2.5x) —
#: Earth Engine throttles beyond ~4 concurrent computes, so more is slower.
DEFAULT_WORKERS = 4

#: How many frames may be fetched ahead of the one being drawn, as a multiple of
#: the worker count. Bounding the *lookahead* (not just the worker count) is what
#: bounds memory: each in-flight frame holds an H×W×3 float64 array (~41 MB for a
#: Sentinel-2 RGB frame), so prefetching a whole 24-frame run would cost ~1 GB.
#: Peak in-flight arrays = workers, plus the one currently being drawn.
_LOOKAHEAD = 1


def _fetch_one(frame, cfg, fetch, geometry):
    """Fetch one frame, publishing its identity for the on-disk cache key.

    Runs on the worker thread, so `cache.frame_identity` (a thread-local) tags the
    right fetch. The injected `fetch` still sees exactly `(image, cfg, geometry)`.
    """
    with cache.frame_identity(getattr(frame, "label", None),
                              getattr(frame, "source", None)):
        return fetch(frame.image, cfg, geometry)


def _fetch_in_order(frames, cfg, fetch, geometry, workers):
    """Yield ``(frame, (arr, valid))`` in frame order, fetching up to `workers` ahead.

    Only the fetch is parallel — it is pure I/O wait on `urlopen`, which releases
    the GIL while Earth Engine computes — and results are consumed strictly in
    order, so the drawing loop downstream is unchanged and produces byte-identical
    frames. `workers=1` takes a genuinely serial path (no pool, no threads).
    """
    if workers <= 1:
        for frame in frames:
            yield frame, _resolve(frame, partial(_fetch_one, frame, cfg, fetch, geometry))
        return

    window = workers * _LOOKAHEAD          # bounded lookahead == bounded memory
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gee-fetch")
    pending: deque = deque()
    try:
        for frame in frames:
            pending.append((frame, pool.submit(_fetch_one, frame, cfg, fetch, geometry)))
            if len(pending) >= window:
                done, fut = pending.popleft()
                yield done, _resolve(done, fut.result)
        while pending:
            done, fut = pending.popleft()
            yield done, _resolve(done, fut.result)
    finally:
        # cancel_futures so an error (or an abandoned generator) never waits for
        # frames that have not started; the few already running finish quickly.
        pool.shutdown(wait=True, cancel_futures=True)


def _resolve(frame, call):
    """Run `call`, re-raising any failure with the offending frame named."""
    try:
        return call()
    except Exception as exc:                 # noqa: BLE001 (re-raised with context)
        label = getattr(frame, "label", "?")
        source = getattr(frame, "source", None)
        where = f"{label} (source {source})" if source is not None else label
        raise RuntimeError(f"fetching frame {where} failed: {exc}") from exc


def _generated_display(marker: str) -> str:
    """Plain-language caption for `interpolate.expand`'s "A -> B  NN%" frame marker.

    `interpolate` stays pure and keyed on period labels — it has no business knowing
    how a frame is captioned — so the marker it emits is parsed back into its three
    facts here and reworded by `labels.generated_text`. The percentage is carried
    through unchanged: it is the signal that this frame was computed, not observed.
    Anything unparseable is drawn as-is rather than dropped; a caption nobody planned
    for is still better than a generated frame that looks observed.
    """
    try:
        span, pct = marker.rsplit("  ", 1)
        label_a, label_b = span.split(" -> ")
        return labels.generated_text(label_a, label_b, int(pct.rstrip("%")))
    except (ValueError, IndexError, KeyError):
        log.debug("un-reworded generated-frame marker %r", marker)
        return marker


def _imagery_sequence(frames, cfg, fetch, geometry, workers, composite):
    """Yield ``(rgb, valid, display_text, label)`` — the imagery for every frame of the
    animation, generated ones included, before any overlay is drawn.

    `label` is the clean period key for an observed frame and None for a generated one:
    a frame that was never observed names no file and belongs in no metadata row, and
    carrying the key here is what keeps the PNG filenames off the *display* text (which
    holds the provenance arrow and the scene count).

    Interpolating here rather than on finished frames is deliberate: blending completed
    frames would cross-fade the period label, the n= count and the colour bar into
    illegible ghosting.
    """
    steps = int(getattr(cfg, "interpolate", 0) or 0)
    mode = getattr(cfg, "interpolate_mode", "auto") or "auto"
    if mode == "auto":
        mode = "crossfade" if composite else "data"

    def display_for(frame):
        # `frame.label` stays a clean period key (it is also the PNG filename and the
        # metadata `month` column — see compositing.pooled_composite), so a pooled
        # frame's provenance is composed in only for drawing. `labels` owns the
        # wording — the same facts (period, borrowed year, scene count) in plain
        # language instead of "2022-05 ← 2021  n=1".
        return labels.observed_text(frame.label, getattr(frame, "n_scenes", None),
                                    getattr(frame, "source", None))

    def as_rgb(arr):
        # composite fetch already returns colour (H×W×3); an index returns 2-D.
        return arr if arr.ndim == 3 else colorize(arr, cfg.viz_min, cfg.viz_max,
                                                  cfg.palette)

    fetched = _fetch_in_order(frames, cfg, fetch, geometry, workers)
    if steps <= 0:
        # Interpolation off: fetch one, draw one, retain nothing.
        for frame, (arr, valid) in fetched:
            yield as_rgb(arr), valid, display_for(frame), frame.label
        return

    # `data` interpolates index units *before* colouring, so every generated frame is
    # coloured with the run's fixed viz range and the colour bar stays exactly valid.
    # `crossfade` blends finished colour — the only option for a composite, which
    # arrives from EE already coloured with no index units left to blend.
    data_mode = mode == "data" and not composite
    post = as_rgb if data_mode else (lambda values: values)
    # Display text is derived as each observation arrives and queued, rather than
    # collected into a label-keyed map up front: the source is a lazy generator, so
    # there is no complete list to key off — and a map would silently collapse two
    # frames that share a label (a pooled run can repeat one). `expand` yields every
    # input item exactly once and in order, so the queue head is always the text for
    # the observed frame being yielded. It never holds more than the two items
    # `expand`'s sliding window has in flight.
    texts: deque = deque()

    def observations():
        for frame, (arr, valid) in fetched:
            texts.append(display_for(frame))
            yield (arr if data_mode else as_rgb(arr)), valid, frame.label

    # `expand` streams: it holds a two-observation window, never the whole run. The
    # generated frames — the several hundred that made streaming necessary — are
    # produced and encoded one at a time, and the fetch lookahead stays busy because
    # `expand` pulls its right endpoint before yielding the left one.
    #
    # `gap_for(cadence)`, not the bare `period_gap`: a "YYYY-MM-01" label starts a
    # period under both `semimonthly` and `10day`, and only the cadence says how many
    # periods fill the rest of the month. Guessing monthly would silently double the
    # generated frames across every month boundary of a sub-monthly run.
    gap = interpolate.gap_for(getattr(cfg, "cadence", None) or "monthly")
    for values, valid, label, is_real in interpolate.expand(observations(), steps, gap):
        # An observed frame keeps its full display text; a generated one is labelled
        # with the transition it sits in ("between May and June 2022 · 30%").
        text = texts.popleft() if is_real else _generated_display(label)
        yield post(values), valid, text, (label if is_real else None)


def render(frames, cfg, fetch=_fetch_thumbnail, geometry=None) -> list[Path]:
    frame_aoi = getattr(cfg, "frame_aoi", None)
    bounds = _aoi_bounds(frame_aoi) if frame_aoi else None
    _cap_dimensions(cfg, bounds)      # honest native resolution (no silent upsampling)
    cfg.crs = _resolve_crs(cfg, bounds)   # concrete EPSG (or None) for getThumbURL + overlays
    draw_overlay = bool(getattr(cfg, "draw_region", False) and getattr(cfg, "region_aoi", None)
                        and bounds is not None)
    rings = _region_rings(cfg.region_aoi) if draw_overlay else []
    # Project the overlay bounds/rings into the render CRS so the region outline and
    # scale bar match the (possibly metric) pixel grid; identity for EPSG:4326.
    if bounds is not None:
        proj_bounds, proj_rings = (_project(bounds, rings, cfg.crs) if cfg.crs
                                   else (bounds, rings))
        frame_width_m = _frame_width_m(bounds, proj_bounds, cfg.crs)
    composite = _is_composite(cfg)
    # The header is run-level: same three strings on every frame, and — crucially —
    # the same number of lines, so the margins are constant for the whole run (see
    # `_two_line_header`). Computed once, before the loop, and used both for drawing
    # and for the `_margins`/`_output_spec` geometry.
    header_title, header_subtitle, header_caveats = _header_text(cfg)
    two_line_header = bool(header_subtitle or header_caveats)
    # Attribution is also run-level (same line on every frame) and, like the
    # header, resolved once rather than per frame — see `_default_credit`.
    credit_text = _default_credit(cfg)
    output = None      # (cw, ch, pw, ph, method) screen layout, computed on frame 1
    region_width = getattr(cfg, "region_line_width", None) or 2
    region_masks = None   # (casing_mask, core_mask); built once below, then reused
    workers = max(1, int(getattr(cfg, "workers", DEFAULT_WORKERS) or DEFAULT_WORKERS))
    if getattr(cfg, "gif", None) is None:
        # `render.gif` unconfigured: on for a normal run (unchanged), but an
        # interpolated one is several hundred frames and quantising them into a GIF is
        # enormous and slow. An explicit `gif: true` is left alone and still wins.
        cfg.gif = not int(getattr(cfg, "interpolate", 0) or 0)
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    want_frames = bool(getattr(cfg, "frames", True))
    png_paths: list[Path] = []

    def _finished():
        """Draw every frame, streaming them to the encoder as they are finished.

        A generator, not a list: an interpolated run reaches several hundred frames and
        at 4K holding them all is tens of gigabytes. Fetches still run concurrently with
        a bounded lookahead; everything here stays strictly ordered and single-threaded,
        so output is identical to workers=1.
        """
        nonlocal output, region_masks
        # Observed frames are written out in small batches rather than one at a time
        # (`_write_frames` encodes a batch across `workers` threads — a real ~2.8x, zlib
        # releases the GIL) or all at the end (which would retain every frame, the very
        # thing streaming avoids). Peak retention is `workers` frames, the same order as
        # the fetch lookahead already holds.
        batch_rgb: list[np.ndarray] = []
        batch_labels: list[str] = []

        def flush():
            if batch_rgb:
                png_paths.extend(
                    _write_frames(out_dir, cfg.name, batch_rgb, batch_labels, workers))
                batch_rgb.clear()
                batch_labels.clear()

        for rgb, valid, text, label in _imagery_sequence(frames, cfg, fetch, geometry,
                                                         workers, composite):
            rgb = apply_nodata(rgb, valid)
            # Screen output: smoothly upscale the native-resolution imagery, then draw
            # the overlays at the output size so text/lines stay crisp; letterbox last.
            if getattr(cfg, "preset", None):
                if output is None:
                    output = _output_spec(cfg, (rgb.shape[1], rgb.shape[0]))
                if output:
                    rgb = _upscale_rgb(rgb, output[2], output[3], output[4])
            # Georeferenced overlays go on first, while the array is still pure imagery:
            # draw_region maps lon/lat linearly across the *whole* array, so drawing it
            # once the label margins exist would slide the outline off its pixels.
            if draw_overlay:
                # The rings, bounds and frame size are identical every frame, so the
                # (comparatively expensive) supersampled masks are built once here on
                # the first frame; every later frame only pays for the cheap alpha
                # composite in draw_region/_composite_region.
                if region_masks is None:
                    region_masks = _region_masks(proj_bounds, proj_rings,
                                                 rgb.shape[:2], region_width)
                # masks is always non-None here, so draw_region would just forward
                # straight to _composite_region without reading `width` — call it direct.
                rgb = _composite_region(rgb, *region_masks, color=REGION_OUTLINE_RGB)
            if bounds is not None:
                rgb = draw_scale_bar(rgb, frame_width_m)
                # Georeferenced-adjacent, same as the scale bar it stacks above: drawn
                # on pure imagery, before the margins exist (Trap 1 — see draw_region).
                rgb = draw_north_arrow(rgb, frame_width_m)
            # Now grow the canvas and let the (shape-preserving) bar drawers fill the new
            # top/bottom strips, so the labels sit beside the imagery instead of over it.
            top_h, bottom_h = _margins(rgb.shape[0], two_line_header)
            rgb = add_margins(rgb, top_h, bottom_h)
            rgb = draw_info_bar(rgb, header_title, header_subtitle, header_caveats)
            # `text` is already composed (see `_imagery_sequence`). `_drawable` is a
            # no-op now (the bundled font draws "←" directly) but stays as the single
            # seam this composed text passes through on its way to `annotate`.
            rgb = annotate(rgb, _drawable(text), credit_text)
            if not composite:                            # colorbar needs a palette
                rgb = add_colorbar(rgb, cfg, y_offset=top_h + 4)  # just inside the imagery
            if output:
                rgb = _letterbox(rgb, output[0], output[1])
            if want_frames and label is not None:
                # Observed frames only: 600 PNGs of which 540 were never observed would
                # be noise, and a generated frame has no period key to name a file with.
                batch_rgb.append(rgb)
                batch_labels.append(label)
                if len(batch_rgb) >= workers:
                    flush()
            yield rgb
        flush()

    # `_produced` marks anything raised while drawing as producer-side, so a fetch that
    # fails mid-run cannot be mistaken for an encode failure and quietly demoted to a
    # truncated GIF (see `_ProducerError`).
    paths = assemble_stream(_produced(_finished()), cfg)
    return paths + png_paths
