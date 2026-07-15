"""Export the individual scenes that feed a month's median — a debug aid.

`compositing.monthly_median` collapses every cloud-filtered scene in a month into
one frame via a per-pixel median. To inspect *what went in*, this lists that
month's scenes and renders each one through the exact same styling path as the
final frame, so the N inputs and the 1 median output are directly comparable.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import ee

from .collection import build as _build
from .compositing import Frame, _add_month
from .render import render as _render

log = logging.getLogger(__name__)


def _scene_label(ts_ms, cloud) -> str:
    """`YYYY-MM-DD` acquisition date, suffixed with in-AOI cloud % when known."""
    d = datetime.fromtimestamp((ts_ms or 0) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return d if cloud is None else f"{d}_cloud{round(cloud * 100):02d}pct"


def _aoi_cloud_fraction(image, region_geom, scale, ee_module) -> float | None:
    """Share of the AOI with no valid pixel in this scene (1 − clear), or None.

    Same measure as ``metadata.frame_cloud_fractions``: a pixel is clear only where
    every band is unmasked, so it works for single-band indices and RGB/CIR alike.
    """
    valid = image.mask().reduce(ee_module.Reducer.min())      # 1 where all bands valid
    clear = valid.reduceRegion(
        ee_module.Reducer.mean(), geometry=region_geom, scale=scale,
        bestEffort=True, maxPixels=int(1e9)).values().get(0).getInfo()
    return None if clear is None else round(1.0 - float(clear), 4)


def month_scene_frames(coll, month, region_geom, scale, ee_module=ee) -> list[Frame]:
    """`[Frame]` for the individual scenes in `month` ("YYYY-MM") — the median's inputs.

    Each frame's image is one scene (already carrying the INDEX / R,G,B band from
    ``collection.build``), labelled by acquisition date and its in-AOI cloud fraction.
    ``n_scenes`` is left ``None`` so the per-scene annotation never implies a composite.
    Returns ``[]`` for a month with no scenes.

    Time and cloud are read per scene (not via ``aggregate_array``, which drops nulls
    and would misalign the arrays with the scene list).
    """
    start = date.fromisoformat(f"{month}-01")
    nxt = _add_month(start).isoformat()
    m = coll.filterDate(start.isoformat(), nxt)
    n = int(m.size().getInfo())
    if n == 0:
        return []
    scenes = m.toList(n)
    frames = []
    for i in range(n):
        img = ee_module.Image(scenes.get(i))
        ts = img.get("system:time_start").getInfo()
        cloud = _aoi_cloud_fraction(img, region_geom, scale, ee_module)
        frames.append(Frame(label=_scene_label(ts, cloud), image=img, n_scenes=None))
    return frames


def export_month_scenes(cfg, frame_geom, region_geom, month,
                        build=_build, render=_render, ee_module=ee) -> Path:
    """Render every scene feeding `month`'s median, plus the median they collapse into.

    Writes, under ``<out_dir>/debug/<month>/``:
      * ``scenes/scene_<date>_cloudNNpct.png`` — one per input scene, plus
        ``scene.mp4`` / ``scene.gif`` to flip through the raw inputs;
      * ``median_<month>.png`` — the single frame the median produces (``n=<count>``).

    Returns the debug directory. Raises ``RuntimeError`` if the month has no scenes
    after cloud filtering.
    """
    coll = build(cfg, frame_geom, region_geom)
    scenes = month_scene_frames(coll, month, region_geom, cfg.scale, ee_module)
    if not scenes:
        raise RuntimeError(f"no scenes for {month} after cloud filtering")
    base = Path(cfg.out_dir) / "debug" / month
    render(scenes, replace(cfg, name="scene", out_dir=str(base / "scenes")),
           geometry=frame_geom)
    start = f"{month}-01"
    nxt = _add_month(date.fromisoformat(start)).isoformat()
    median = Frame(label=month, image=coll.filterDate(start, nxt).median(),
                   n_scenes=len(scenes))
    render([median], replace(cfg, name="median", out_dir=str(base)), geometry=frame_geom)
    log.info("exported %d scene(s) + median for %s -> %s", len(scenes), month, base)
    return base
