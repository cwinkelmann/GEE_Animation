"""Lean one-call API: a region + a (sensor, index) + dates -> a playable animation.

Separation of concerns: authenticate once yourself (``auth.init(project)``), then call
``animate(...)`` as many times as you like. ``animate`` runs the rest of the pipeline
(AOI -> build -> monthly median -> render) with notebook-friendly defaults — a screen
preset so coarse products (LST 100 m, MODIS 1 km) aren't rendered at their tiny native
pixel size, and ``aspect="match"`` so there are no letterbox bars. The returned
``Animation`` renders inline in Colab/Jupyter (its ``_repr_html_`` base64-embeds the MP4
so it actually plays).

For fully config-driven runs use ``cli.run()`` with a YAML file instead.
"""
from __future__ import annotations

import base64
import types
from dataclasses import dataclass
from pathlib import Path

from . import anomaly, aoi, collection, compositing, metadata, render
from .config import RunConfig
from .products import INDICES, get_product

# Pipeline seams, injectable for network-free tests. NOTE: no `init` here — auth is a
# separate, explicit step the caller runs once.
DEFAULT_DEPS = types.SimpleNamespace(
    parse=aoi.parse,
    frame_bbox=aoi.frame_bbox_from_region,
    build=collection.build,
    monthly_median=compositing.monthly_median,
    anomaly=anomaly.apply,
    render=render.render,
    metadata=metadata.write_frame_metadata,
)

# Region-cloud / index scale used for the AOI cloud filter + metadata (per sensor GSD).
_SCALE = {"sentinel2": 10, "landsat": 30, "modis": 500, "modis_lst": 1000}


@dataclass
class Animation:
    """Result of :func:`animate` — paths plus inline notebook display."""
    name: str
    mp4: str | None
    gif: str | None
    frames: list
    metadata_db: str | None
    status: str

    def _repr_html_(self) -> str:
        """Inline <video> with the MP4 base64-embedded so it plays in Colab/Jupyter."""
        if self.mp4 and Path(self.mp4).exists():
            b64 = base64.b64encode(Path(self.mp4).read_bytes()).decode()
            media = (f'<video controls autoplay loop muted playsinline '
                     f'style="max-width:100%;border-radius:8px" '
                     f'src="data:video/mp4;base64,{b64}"></video>')
        elif self.gif and Path(self.gif).exists():
            b64 = base64.b64encode(Path(self.gif).read_bytes()).decode()
            media = f'<img style="max-width:100%;border-radius:8px" src="data:image/gif;base64,{b64}">'
        else:
            media = ""
        return f'<div><p style="font:14px system-ui;margin:.2em 0">{self.status}</p>{media}</div>'


def _to_region_aoi(region) -> dict:
    """Normalize `region` to an AOI-config dict.

    Accepts an AOI-config dict (``{"bbox"|"geojson"|"shapefile": ...}``), a raw GeoJSON
    geometry/Feature dict, a path to a ``.geojson``/``.shp`` file, or a bbox
    ``[west, south, east, north]``.
    """
    if isinstance(region, dict):
        if any(k in region for k in ("bbox", "geojson", "shapefile")):
            return region
        if region.get("type"):            # a raw GeoJSON geometry/Feature dict
            return {"geojson": region}
        raise ValueError("region dict must be an AOI config or a GeoJSON object")
    if isinstance(region, (list, tuple)):
        if len(region) != 4:
            raise ValueError("a bbox region must be [west, south, east, north]")
        return {"bbox": list(region)}
    s = str(region)
    low = s.lower()
    if low.endswith((".geojson", ".json")):
        return {"geojson": s}
    if low.endswith((".shp", ".zip")):
        return {"shapefile": s}
    raise ValueError(
        f"unsupported region {region!r}; pass a GeoJSON path/dict, a .shp path, "
        "or a [west, south, east, north] bbox")


def animate(region, *, sensor="landsat", index="lst", start, end,
            buffer_m=1000.0, preset="1080p", aspect="match", fps=2,
            region_max_cloud_percent=60.0, max_cloud_percent=80.0,
            out_dir="out", project="hnee-331218", name=None, write_metadata=True,
            deps=DEFAULT_DEPS) -> Animation:
    """Build one animation in a single call. Authenticate first via ``auth.init``.

    `region` is a GeoJSON path/dict, a ``.shp`` path, or a ``[w, s, e, n]`` bbox. The
    animation frame is the region's bounding box grown by `buffer_m`. `preset` upscales
    the native-resolution imagery to a real screen size (so LST/MODIS aren't tiny);
    pass ``preset=None`` for a raw native-resolution render. Returns an :class:`Animation`
    that plays inline in a notebook and carries the output paths.
    """
    get_product(sensor, index)                      # validate the pair up front
    region_aoi = _to_region_aoi(region)
    viz_min, viz_max, palette = INDICES[index].default_viz
    name = name or f"{sensor}_{index}"

    region_geom = deps.parse(region_aoi)
    frame_bbox = deps.frame_bbox(region_geom, float(buffer_m))
    frame_aoi = {"bbox": frame_bbox}
    frame_geom = deps.parse(frame_aoi)

    cfg = RunConfig(
        name=name, project=project, frame_aoi=frame_aoi, region_aoi=region_aoi,
        start=str(start), end=str(end), sensor=sensor, index=index, cadence="monthly",
        max_cloud_percent=float(max_cloud_percent),
        region_max_cloud_percent=float(region_max_cloud_percent),
        viz_min=viz_min, viz_max=viz_max, palette=list(palette) if palette else [],
        fps=float(fps), scale=_SCALE.get(sensor, 30), dimensions=768,
        preset=preset, aspect=aspect, upscale="lanczos", out_dir=out_dir,
        draw_region=True, metadata=bool(write_metadata))

    frames = deps.monthly_median(deps.build(cfg, frame_geom, region_geom), cfg)
    if not frames:
        raise RuntimeError(
            f"No {sensor} {index} imagery for that AOI, date range and cloud filter — "
            "widen the dates or raise region_max_cloud_percent.")
    frames = deps.anomaly(frames, cfg, frame_geom, region_geom, deps.build)
    db = deps.metadata(frames, cfg, region_geom) if write_metadata else None
    paths = deps.render(frames, cfg, geometry=frame_geom)

    mp4 = next((str(p) for p in paths if str(p).endswith(".mp4")), None)
    gif = next((str(p) for p in paths if str(p).endswith(".gif")), None)
    frame_pngs = [str(p) for p in paths if str(p).endswith(".png")]
    status = (f"{name}: {len(frames)} frames "
              f"({frames[0].label} → {frames[-1].label}) at {preset or 'native'}.")
    return Animation(name=name, mp4=mp4, gif=gif, frames=frame_pngs,
                     metadata_db=str(db) if db else None, status=status)
