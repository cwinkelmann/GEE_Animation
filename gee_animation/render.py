"""Render index frames to annotated PNGs and assemble MP4 + GIF."""
from __future__ import annotations

import io
import logging
import math
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache, partial
from pathlib import Path
from urllib.request import urlopen

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import cache
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


@lru_cache(maxsize=64)
def _font(px: int):
    """A default font at the given pixel size (cached). Falls back for old Pillow."""
    try:
        return ImageFont.load_default(size=max(10, int(px)))
    except TypeError:            # Pillow < 10 has no size argument
        return ImageFont.load_default()


# Pillow's bundled default font has no glyph for "←" (pooled-frame provenance arrow)
# or "–" (en dash, e.g. a pooled year range), and draws an empty notdef box instead —
# which reads as a corrupted frame. `Frame.label`/`.source` keep the real characters
# (they also feed filenames and DB rows); only the composed *display* string that
# `render()` hands to `annotate` is folded down. "2022-05 <- 2021" is still
# unambiguous provenance.
_DRAWABLE = {"←": "<-", "–": "-"}


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


def _info_text(cfg) -> str:
    """One-line 'INDEX = formula   bands: …' describing how the frame was made.

    Under cross-year pooling (`cfg.pool_years`) it also states the pooled year range:
    frames then come from whichever year had the clearest scene, so the provenance
    has to be on the frame itself, not only in the config.
    """
    meta = _index_meta(cfg)
    name = getattr(cfg, "index", "").upper()
    head = f"{name} = {meta.formula}" if (meta and meta.formula) else name
    bands = meta.bands if meta else ""
    text = f"{head}   bands: {bands}" if bands else head
    pool = getattr(cfg, "pool_years", None)
    if pool:
        # Plain ASCII hyphen, not an en dash: this string is drawn straight into
        # `draw_info_bar` with no fold step, and Pillow's default font has no en-dash
        # glyph (see _DRAWABLE).
        # gap_fill keeps the requested year wherever it has data and only borrows for
        # otherwise-empty periods, so its unmarked frames really are that year —
        # calling the whole run "cosmetic" would overstate it. The other strategies
        # re-pick every frame, so for those the blanket warning is correct.
        note = ("gap-filled: frames marked <- YYYY borrow another year"
                if getattr(cfg, "pool_strategy", None) == "gap_fill"
                else "cosmetic: frames may be from different years")
        text += f"   pooled years {int(pool[0])}-{int(pool[-1])} ({note})"
    return text


def _margins(imagery_h: int) -> tuple:
    """(top_h, bottom_h) label margins to pad imagery `imagery_h` px tall with.

    `draw_info_bar`/`annotate` pick their bar height as `max(12, h // 12)` of the
    array they are handed — which is the *padded* array — so the margin has to equal
    that height: ``m == max(12, (imagery_h + top_h + bottom_h) // 12)``.
    ``imagery_h // 10`` is the exact solution, under the same 12 px floor. The top
    margin carries one extra pixel because PIL's `rectangle` includes its bottom
    edge: without it the info bar's last row would tint the imagery's first row.
    """
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


def _fit_bar_text(draw: ImageDraw.ImageDraw, text: str, px: int, avail_w: int) -> tuple:
    """(font, text) that fits `avail_w` — shrink font size down to the `_font` floor
    (10px) before truncating with a trailing ellipsis.

    Started at `px` (whatever the caller would otherwise have used unshrunk), so text
    that already fits is returned completely unchanged — same font, same string.
    """
    font = _font(px)
    tb = draw.textbbox((0, 0), text, font=font)
    while tb[2] - tb[0] > avail_w and px > 10:
        px -= 1
        font = _font(px)
        tb = draw.textbbox((0, 0), text, font=font)
    if tb[2] - tb[0] <= avail_w:
        return font, text
    # Still too wide at the size floor: binary-search the longest prefix (+ "…") that
    # fits. "…" (U+2026) is a real glyph in Pillow's bundled default font — unlike
    # "←"/"–" (see _DRAWABLE), it does not draw as a notdef box.
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        tb = draw.textbbox((0, 0), text[:mid] + "…", font=font)
        if tb[2] - tb[0] <= avail_w:
            lo = mid
        else:
            hi = mid - 1
    return font, (text[:lo] + "…" if lo > 0 else "…")


def draw_info_bar(rgb: np.ndarray, text: str) -> np.ndarray:
    """Draw a translucent top bar naming the bands used and the formula (if any).

    `text` is drawn as given — callers must pre-fold any character Pillow's default
    font cannot render (see `_DRAWABLE`); `_info_text` itself never emits one. Unlike
    `annotate`'s bottom label (always short), this text can overflow a narrow frame
    for the long-formula indices (lst_smw, lst_sharp); `_fit_bar_text` shrinks the
    font (and, as a last resort, truncates) so it always stays inside the frame. The
    bar height/rectangle and vertical placement are untouched either way — only the
    font size and, in the worst case, the string itself change.
    """
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    bar_h = _bar_h(h)
    pad = max(1, h // 200)
    x = max(4, w // 200)
    px = max(11, h // 40)   # same starting size _annot_scale would pick
    avail_w = max(1, w - 2 * x)
    font, text = _fit_bar_text(draw, text, px, avail_w)
    draw.rectangle([0, 0, w, bar_h], fill=(0, 0, 0, 140))
    draw.text((x, pad), text, fill=(255, 255, 255, 255), font=font)
    return np.asarray(img)


def annotate(rgb: np.ndarray, label: str) -> np.ndarray:
    """Draw a translucent bottom bar with `label`.

    `label` is drawn as given — callers must pre-fold any character Pillow's default
    font cannot render (see `_DRAWABLE`); `render()` does this when composing the
    pooled-frame provenance text.
    """
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    font, _ = _annot_scale(h)
    bar_h = _bar_h(h)
    draw.rectangle([0, h - bar_h, w, h], fill=(0, 0, 0, 140))
    draw.text((max(4, w // 200), h - bar_h + max(1, h // 200)), label,
              fill=(255, 255, 255, 255), font=font)
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


def draw_scale_bar(rgb: np.ndarray, frame_width_m: float, target_frac: float = 0.25,
                   color=(255, 255, 255)) -> np.ndarray:
    """Draw a ground-distance scale bar (bottom-right) onto an RGB frame.

    `frame_width_m` is the frame's x-extent in metres (the image width maps to it),
    so the bar is correct whether the render is plate carrée or a metric CRS. A
    "nice" round distance near `target_frac` of the frame width is chosen.
    """
    img = Image.fromarray(rgb.astype(np.uint8), "RGB")
    h, w = rgb.shape[:2]
    if w < 24 or frame_width_m <= 0:      # too small to annotate meaningfully
        return np.asarray(img)
    nice_m = _nice_distance(frame_width_m * target_frac)
    bar_px = int(round(nice_m / (frame_width_m / w)))
    if bar_px < 1:
        return np.asarray(img)
    label = f"{nice_m / 1000:g} km" if nice_m >= 1000 else f"{nice_m:g} m"

    draw = ImageDraw.Draw(img, "RGBA")
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
    draw.rectangle([x0 - pad, panel_top, x1 + pad, y + pad], fill=(0, 0, 0, 120))
    draw.line([(x0, y), (x1, y)], fill=color, width=lw)
    draw.line([(x0, y - tick), (x0, y)], fill=color, width=lw)   # end ticks
    draw.line([(x1, y - tick), (x1, y)], fill=color, width=lw)
    draw.text(((x0 + x1) / 2 - tw / 2, panel_top + pad // 2), label, fill=color, font=font)
    return np.asarray(img)


def _fit_margins(pw: int, ph: int, ch: int, aoi_aspect: float) -> tuple:
    """Shrink the imagery place box so imagery + both label margins still fits `ch`.

    The margins are part of the output, so they have to come *out of* the canvas the
    preset asked for; adding them afterwards would make an `aspect: "16:9"` render
    taller than 16:9. `_margins` together are ~1/5 of the imagery height, so 10/12 of
    the canvas height is the analytic starting guess and the loop only trims the odd
    pixel — or a little more on the small canvases where the 12 px floor dominates.
    """
    ph = min(ph, max(1, ch * 10 // 12))
    while ph > 1 and ph + sum(_margins(ph)) > ch:
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
    """
    preset = getattr(cfg, "preset", None)
    if not preset:
        return None
    long_edge = PRESETS.get(str(preset).lower()) or int(preset)
    method = UPSCALE_METHODS.get(getattr(cfg, "upscale", "lanczos"), Image.LANCZOS)
    iw, ih = imagery_wh
    aoi_aspect = iw / ih
    aspect = getattr(cfg, "aspect", None)
    if not aspect or aspect == "match":     # canvas == frame aspect; imagery fills it
        if aoi_aspect >= 1:
            pw, ph = long_edge, max(1, round(long_edge / aoi_aspect))
        else:
            ph, pw = long_edge, max(1, round(long_edge * aoi_aspect))
        return pw, ph + sum(_margins(ph)), pw, ph, method   # canvas grows, imagery doesn't
    target = ASPECTS[aspect]
    if target >= 1:
        cw, ch = long_edge, max(1, round(long_edge / target))
    else:
        ch, cw = long_edge, max(1, round(long_edge * target))
    if aoi_aspect > target:                 # frame wider than canvas -> width-limited
        pw, ph = cw, max(1, round(cw / aoi_aspect))
    else:
        ph, pw = ch, max(1, round(ch * aoi_aspect))
    pw, ph = _fit_margins(pw, ph, ch, aoi_aspect)
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


def _write_mp4(path: Path, frames: list[np.ndarray], fps: int) -> None:
    even = [_pad_to_even(f) for f in frames]
    imageio.mimsave(path, even, fps=fps, macro_block_size=1)


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


def assemble(frames_rgb: list[np.ndarray], cfg) -> list[Path]:
    """Encode the frames to MP4 and (unless `render.gif` is false) GIF.

    The GIF is the slowest single step of a render (5.4 s of a 24 s run, half of it
    PIL's colour quantisation) and is only a preview format — `render.gif: false`
    skips it. It defaults to true, so an existing config's outputs are unchanged.
    """
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gif_path = out_dir / f"{cfg.name}.gif"
    mp4_path = out_dir / f"{cfg.name}.mp4"
    want_gif = bool(getattr(cfg, "gif", True))
    paths: list[Path] = []
    try:
        _write_mp4(mp4_path, frames_rgb, cfg.fps)
        paths.append(mp4_path)
    except Exception as exc:  # ffmpeg missing / encode error
        if not want_gif:
            # The GIF is the fallback that makes an MP4 failure survivable; without it
            # there is no animation at all, so fail fast rather than return only PNGs.
            raise RuntimeError(
                f"MP4 encoding failed ({exc}) and render.gif is disabled, so no "
                f"animation could be written; set render.gif: true to fall back to a GIF"
            ) from exc
        log.warning("MP4 write failed (%s); producing GIF only", exc)
    if want_gif:
        _write_gif(gif_path, frames_rgb, cfg.fps)
        paths.append(gif_path)
    return paths


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
    info_text = _info_text(cfg)
    output = None      # (cw, ch, pw, ph, method) screen layout, computed on frame 1
    region_width = getattr(cfg, "region_line_width", None) or 2
    region_masks = None   # (casing_mask, core_mask); built once below, then reused
    rgb_frames: list[np.ndarray] = []
    workers = max(1, int(getattr(cfg, "workers", DEFAULT_WORKERS) or DEFAULT_WORKERS))
    # Fetches run concurrently with a bounded lookahead; everything below stays
    # strictly ordered and single-threaded, so output is identical to workers=1.
    for frame, (arr, valid) in _fetch_in_order(frames, cfg, fetch, geometry, workers):
        # composite fetch already returns colour (H×W×3); an index returns 2-D.
        rgb = arr if arr.ndim == 3 else colorize(arr, cfg.viz_min, cfg.viz_max, cfg.palette)
        rgb = apply_nodata(rgb, valid)
        # Screen output: smoothly upscale the native-resolution imagery, then draw the
        # overlays at the output size so text/lines stay crisp; letterbox at the end.
        if getattr(cfg, "preset", None):
            if output is None:
                output = _output_spec(cfg, (rgb.shape[1], rgb.shape[0]))
            if output:
                rgb = _upscale_rgb(rgb, output[2], output[3], output[4])
        # Georeferenced overlays go on first, while the array is still pure imagery:
        # draw_region maps lon/lat linearly across the *whole* array, so drawing it
        # once the label margins exist would silently slide the outline off its pixels.
        if draw_overlay:
            # The rings, bounds and frame size are identical every frame, so the
            # (comparatively expensive) supersampled masks are built once here on the
            # first frame; every later frame only pays for the cheap alpha composite
            # in draw_region/_composite_region.
            if region_masks is None:
                region_masks = _region_masks(proj_bounds, proj_rings, rgb.shape[:2], region_width)
            # masks is always non-None here, so draw_region would just forward straight
            # to _composite_region without ever reading `width` — call it directly.
            rgb = _composite_region(rgb, *region_masks, color=REGION_OUTLINE_RGB)
        if bounds is not None:
            rgb = draw_scale_bar(rgb, frame_width_m)
        # Now grow the canvas and let the (shape-preserving) bar drawers fill the new
        # top/bottom strips, so the labels sit beside the imagery instead of over it.
        top_h, bottom_h = _margins(rgb.shape[0])
        rgb = add_margins(rgb, top_h, bottom_h)
        rgb = draw_info_bar(rgb, info_text)
        n = getattr(frame, "n_scenes", None)
        source = getattr(frame, "source", None)
        # Compose the *display* string here: `frame.label` stays a clean period key
        # (it is also the PNG filename and the metadata `month` column — see
        # compositing.pooled_composite), so a pooled frame's provenance is appended
        # only for drawing. Pillow's default font can't render "←", so the composed
        # text is folded right here, in one place, before it reaches `annotate`.
        display = f"{frame.label} ← {source}" if source is not None else frame.label
        text = f"{display}  n={n}" if n is not None else display
        rgb = annotate(rgb, _drawable(text))
        if not composite:                            # colorbar needs a palette
            rgb = add_colorbar(rgb, cfg, y_offset=top_h + 4)   # just inside the imagery
        if output:
            rgb = _letterbox(rgb, output[0], output[1])
        rgb_frames.append(rgb)
    paths = assemble(rgb_frames, cfg)
    if getattr(cfg, "frames", True):
        paths += _write_frames(Path(cfg.out_dir), cfg.name, rgb_frames,
                               [f.label for f in frames], workers=workers)
    return paths
