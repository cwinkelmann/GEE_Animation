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
from dataclasses import dataclass, field
from pathlib import Path

from . import anomaly, aoi, charts, collection, compositing, metadata, render
from .config import RunConfig
from .products import INDICES, get_product


def _render_chart(series, name, index, out_dir) -> str | None:
    """Render an inside-vs-outside-AOI line chart to ``<out_dir>/<name>_chart.png``.

    Returns the path, or ``None`` if there's nothing to plot or matplotlib is absent
    (matplotlib is a lazy import — the ``series`` data is attached to the Animation
    regardless, so a missing chart never loses information).
    """
    if not any(i is not None or o is not None for _, i, o in series):
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    months = [m for m, _, _ in series]
    nan = float("nan")
    inside = [nan if i is None else i for _, i, _ in series]
    outside = [nan if o is None else o for _, _, o in series]
    x = list(range(len(months)))
    fig, ax = plt.subplots(figsize=(9, 3.2), dpi=130)
    ax.plot(x, inside, "-o", ms=3, lw=1.5, color="#2a7a2a", label="inside AOI")
    ax.plot(x, outside, "-s", ms=3, lw=1.5, color="#a1622f", label="outside AOI")
    step = max(1, len(months) // 12)
    ax.set_xticks(x[::step])
    ax.set_xticklabels(months[::step], rotation=45, ha="right", fontsize=7)
    ax.set_ylabel(index.upper())
    ax.set_title(f"{index.upper()} — inside vs outside the AOI", fontsize=11)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    path = str(Path(out_dir) / f"{name}_chart.png")
    fig.savefig(path)
    plt.close(fig)
    return path


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
    timeseries=charts.inside_outside_timeseries,
    render_chart=_render_chart,
)

# Region-cloud / index scale used for the AOI cloud filter + metadata (per sensor GSD).
_SCALE = {"sentinel2": 10, "landsat": 30, "modis": 500, "modis_lst": 1000}


@dataclass
class Animation:
    """Result of :func:`animate`.

    Carries the output paths (`mp4`, `gif`, `frames`, `metadata_db`), the inside-vs-
    outside-AOI `series` and its rendered `chart` PNG, and retrieval helpers. Renders
    inline in Colab/Jupyter (video + chart) via ``_repr_html_``.
    """
    name: str
    mp4: str | None
    gif: str | None
    frames: list
    metadata_db: str | None
    status: str
    series: list = field(default_factory=list)   # [(month, inside_mean, outside_mean)]
    chart: str | None = None                       # path to the inside/outside chart PNG

    def metadata(self) -> list:
        """Per-frame metadata rows from `metadata_db` (month, n_scenes, cloud, mean).

        Returns a list of dicts (empty if no metadata was written). Lets you pull the
        numbers back out of the run later without re-querying Earth Engine.
        """
        if not (self.metadata_db and Path(self.metadata_db).exists()):
            return []
        import sqlite3
        con = sqlite3.connect(self.metadata_db)
        try:
            cur = con.execute(
                "SELECT month, n_scenes, aoi_cloud_fraction, aoi_clear_fraction, aoi_mean "
                "FROM frame_clouds WHERE name=? ORDER BY month", (self.name,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            con.close()

    def _b64(self, path):
        return base64.b64encode(Path(path).read_bytes()).decode()

    def _repr_html_(self) -> str:
        """Inline video + inside/outside chart, both base64-embedded (plays in Colab)."""
        parts = [f'<p style="font:14px system-ui;margin:.2em 0">{self.status}</p>']
        if self.mp4 and Path(self.mp4).exists():
            parts.append(f'<video controls autoplay loop muted playsinline '
                         f'style="max-width:100%;border-radius:8px" '
                         f'src="data:video/mp4;base64,{self._b64(self.mp4)}"></video>')
        elif self.gif and Path(self.gif).exists():
            parts.append(f'<img style="max-width:100%;border-radius:8px" '
                         f'src="data:image/gif;base64,{self._b64(self.gif)}">')
        if self.chart and Path(self.chart).exists():
            parts.append(f'<img style="max-width:100%;margin-top:6px" '
                         f'src="data:image/png;base64,{self._b64(self.chart)}">')
        return "<div>" + "".join(parts) + "</div>"


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
            chart=True, deps=DEFAULT_DEPS) -> Animation:
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
    cfg.validate()   # RunConfig is built directly here, so from_yaml's check is skipped

    frames = deps.monthly_median(deps.build(cfg, frame_geom, region_geom), cfg)
    if not frames:
        raise RuntimeError(
            f"No {sensor} {index} imagery for that AOI, date range and cloud filter — "
            "widen the dates or raise region_max_cloud_percent.")
    frames = deps.anomaly(frames, cfg, frame_geom, region_geom, deps.build)
    db = deps.metadata(frames, cfg, region_geom) if write_metadata else None
    paths = deps.render(frames, cfg, geometry=frame_geom)

    # Inside-vs-outside-AOI series + chart (single-band indices only; composites have
    # no INDEX band to reduce). Attached to the Animation for later retrieval.
    series, chart_path = [], None
    if chart and not INDICES[index].composite:
        series = deps.timeseries(frames, region_geom, frame_geom, cfg.scale)
        chart_path = deps.render_chart(series, name, index, out_dir)
        import csv
        with open(Path(out_dir) / f"{name}_series.csv", "w", newline="") as f:
            csv.writer(f).writerows([("month", "inside", "outside"), *series])

    mp4 = next((str(p) for p in paths if str(p).endswith(".mp4")), None)
    gif = next((str(p) for p in paths if str(p).endswith(".gif")), None)
    frame_pngs = [str(p) for p in paths if str(p).endswith(".png")]
    status = (f"{name}: {len(frames)} frames "
              f"({frames[0].label} → {frames[-1].label}) at {preset or 'native'}.")
    return Animation(name=name, mp4=mp4, gif=gif, frames=frame_pngs,
                     metadata_db=str(db) if db else None, status=status,
                     series=series, chart=chart_path)
