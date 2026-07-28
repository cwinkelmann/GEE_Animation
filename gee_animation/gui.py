"""A simple Gradio front-end over the gee_animation pipeline.

The orchestration (`run_animation`, `_region_aoi_from_upload`) is plain Python and
unit-testable; Gradio is imported lazily inside `build_app` so the module (and its
tests) load without the optional `gui` extra.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import types
import zipfile
from pathlib import Path

from . import anomaly, auth, aoi, charts, collection, compositing, render
from .compositing import period_starts
from .config import RunConfig
from .products import INDICES, SENSORS, get_product

# Injectable pipeline seams (overridden in tests).
DEFAULT_DEPS = types.SimpleNamespace(
    init=auth.init,
    parse=aoi.parse,
    frame_bbox=aoi.frame_bbox_from_region,
    build=collection.build,
    monthly_median=compositing.monthly_median,
    anomaly=anomaly.apply,
    render=render.render,
    timeseries=charts.inside_outside_timeseries,
)


def _mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _zip_frames(png_paths, out_dir, name) -> str:
    """Bundle the per-frame PNGs into a single ``{name}_frames.zip`` for download."""
    zip_path = Path(out_dir) / f"{name}_frames.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in png_paths:
            zf.write(p, Path(p).name)
    return str(zip_path)

# MODIS is 500 m; the optical/thermal sensors are 30 m — pick the thumbnail scale.
_SCALE = {"modis": 500}

# Pre-load a default AOI upload: GEE_DEFAULT_AOI if set (e.g. a path mounted into
# the container), else the shipped WNE AOI, else None (docs/ isn't in the image).
_WNE_AOI = Path(__file__).resolve().parent.parent / "docs" / "aoi" / "wne" / "wne.geojson"
DEFAULT_AOI = os.environ.get("GEE_DEFAULT_AOI") or (str(_WNE_AOI) if _WNE_AOI.exists() else None)

# Where previously-rendered runs live (overridable for a mounted output volume).
OUTPUT_DIR = os.environ.get("GEE_OUTPUT_DIR", "out")
_MONTH_RE = re.compile(r"\d{4}-\d{2}")   # a frame stem is "<name>_YYYY-MM"


def _run_frames(run_dir: Path, name: str) -> list:
    """Per-month frame PNGs for animation `name` in `run_dir`, sorted by month.

    A frame is exactly ``<name>_<YYYY-MM>.png`` — the strict suffix match keeps a
    run named ``wne_lst`` from grabbing ``wne_lst_smw``'s frames in a shared folder.
    """
    frames = []
    for p in run_dir.glob(f"{name}_*.png"):
        if _MONTH_RE.fullmatch(p.stem[len(name) + 1:]):
            frames.append(p)
    return sorted(frames)


def list_previous_runs(base_dir=None) -> list:
    """``[(label, media_path)]`` for every rendered animation found under `base_dir`.

    A run is any ``.mp4``/``.gif`` (mp4 preferred) below the base directory, keyed by
    its folder + basename so both the flat layout (``out/wne_lst.mp4``) and per-run
    subdirs (``out/wne_lst_smw_10yr/…mp4``) are discovered. Each label carries the run
    path relative to the base plus its frame count; the value is the media file path,
    which ``load_previous_run`` reads back. Returns ``[]`` if the base is absent.
    """
    base = Path(base_dir or OUTPUT_DIR)
    if not base.exists():
        return []
    media = {}   # (dir, name) -> media Path, mp4 overriding gif
    for p in base.rglob("*.gif"):
        media[(p.parent, p.stem)] = p
    for p in base.rglob("*.mp4"):
        media[(p.parent, p.stem)] = p
    runs = []
    for (d, name), m in sorted(media.items(), key=lambda kv: str(kv[1])):
        n = len(_run_frames(d, name))
        rel = (d / name).relative_to(base)
        runs.append((f"{rel}  ({n} frame{'' if n == 1 else 's'})", str(m)))
    return runs


def _metadata_summary(db_path: Path, name: str):
    """One-line summary of a run's metadata.db rows (frames, span, value range), or None."""
    try:
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "SELECT COUNT(*), MIN(month), MAX(month), AVG(aoi_mean), "
                "MIN(aoi_mean), MAX(aoi_mean), AVG(aoi_cloud_fraction) "
                "FROM frame_clouds WHERE name=?", (name,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    n, m0, m1, tavg, tmin, tmax, cloud = row
    bits = [f"{n} months {m0}→{m1} in metadata"]
    if tavg is not None:
        bits.append(f"mean value {tavg:.1f} (range {tmin:.1f}…{tmax:.1f})")
    if cloud is not None:
        bits.append(f"avg AOI cloud {cloud * 100:.0f}%")
    return "; ".join(bits)


def load_previous_run(media_path):
    """Reload a rendered run for display — ``(mp4, gif, frame_pngs, frames_zip, status)``.

    Reads the media file, its sibling per-month frames, zips them, and derives a status
    line (enriched from ``metadata.db`` when present). No Earth Engine, no network — it
    mirrors ``run_animation``'s output shape so it drives the same UI components.
    """
    if not media_path:
        raise ValueError("select a previous run to load")
    media = Path(media_path)
    run_dir, name = media.parent, media.stem
    mp4 = run_dir / f"{name}.mp4"
    gif = run_dir / f"{name}.gif"
    frames = _run_frames(run_dir, name)
    frame_pngs = [str(p) for p in frames]
    frames_zip = _zip_frames(frame_pngs, run_dir, name) if frame_pngs else None
    status = [f"Loaded **{name}** — {len(frames)} frame(s)"]
    if frames:
        status.append(f"({frames[0].stem[len(name) + 1:]} → {frames[-1].stem[len(name) + 1:]})")
    summary = _metadata_summary(run_dir / "metadata.db", name)
    if summary:
        status.append(f"· {summary}")
    return (str(mp4) if mp4.exists() else None, str(gif) if gif.exists() else None,
            frame_pngs, frames_zip, " ".join(status) + ".")


def indices_for(sensor: str) -> list:
    """Indices a sensor supports (e.g. lst/lst_smw/lst_sharp are Landsat-only)."""
    return [name for name, idx in INDICES.items() if sensor in idx.sensors]


def _region_aoi_from_upload(path: str) -> dict:
    """Turn an uploaded AOI file into a region aoi-config dict.

    Accepts a GeoJSON file or a .zip bundling a shapefile (.shp + sidecars).
    """
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".geojson", ".json"):
        return {"geojson": str(p)}
    if ext == ".zip":
        dest = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(p) as zf:
            zf.extractall(dest)
        shp = next(dest.rglob("*.shp"), None)
        if shp is None:
            raise ValueError("the uploaded .zip does not contain a .shp file")
        return {"shapefile": str(shp)}
    if ext == ".shp":
        return {"shapefile": str(p)}
    raise ValueError(
        f"unsupported AOI file {p.name!r}; upload a .geojson or a zipped shapefile (.zip)"
    )


def run_animation(*, aoi_path, buffer_m, sensor, index, start, end,
                  region_max_cloud_percent=10.0, max_cloud_percent=60.0,
                  fps=4, dimensions=768, project="hnee-331218",
                  out_dir=None, deps=DEFAULT_DEPS):
    """Build one animation from GUI inputs.

    Returns ``(mp4_path, gif_path, frame_pngs, frames_zip, status, series)`` where
    `frame_pngs` is the list of per-month PNGs and `frames_zip` bundles them for
    download (both ``None``/empty if no frames were rendered).
    """
    if not aoi_path:
        raise ValueError("please upload an AOI (a GeoJSON file or a zipped shapefile)")
    get_product(sensor, index)   # validate the (sensor, index) pair up front
    region_aoi = _region_aoi_from_upload(aoi_path)
    composite = INDICES[index].composite
    viz_min, viz_max, palette = INDICES[index].default_viz
    palette = palette or []      # composites (rgb/cir) carry no palette
    out_dir = out_dir or tempfile.mkdtemp()

    deps.init(project)
    region_geom = deps.parse(region_aoi)
    frame_bbox = deps.frame_bbox(region_geom, float(buffer_m))
    frame_aoi = {"bbox": frame_bbox}
    frame_geom = deps.parse(frame_aoi)

    cfg = RunConfig(
        name=f"{sensor}_{index}", project=project,
        frame_aoi=frame_aoi, region_aoi=region_aoi,
        start=str(start), end=str(end), sensor=sensor, index=index,
        cadence="monthly",
        max_cloud_percent=float(max_cloud_percent),
        region_max_cloud_percent=float(region_max_cloud_percent),
        viz_min=viz_min, viz_max=viz_max, palette=palette,
        fps=float(fps), scale=_SCALE.get(sensor, 30), dimensions=int(dimensions),
        out_dir=str(out_dir), draw_region=True,
    )

    coll = deps.build(cfg, frame_geom, region_geom)
    frames = deps.monthly_median(coll, cfg)
    if not frames:
        raise RuntimeError(
            "No imagery found for that AOI, date range and cloud filter — "
            "try a wider date range or a higher cloud threshold."
        )
    frames = deps.anomaly(frames, cfg, frame_geom, region_geom, deps.build)
    paths = deps.render(frames, cfg, geometry=frame_geom)
    mp4 = next((str(p) for p in paths if str(p).endswith(".mp4")), None)
    gif = next((str(p) for p in paths if str(p).endswith(".gif")), None)
    frame_pngs = [str(p) for p in paths if str(p).endswith(".png")]
    frames_zip = _zip_frames(frame_pngs, out_dir, cfg.name) if frame_pngs else None
    # [(month, inside, outside)] — index mean inside the AOI vs the surrounding frame.
    # Composites (rgb/cir) have no single INDEX band to reduce, so skip the chart.
    series = [] if composite else deps.timeseries(frames, region_geom, frame_geom, cfg.scale)
    n_months = len(period_starts(str(start), str(end), cfg.cadence))
    dropped = n_months - len(frames)
    status = (f"Rendered {len(frames)} of {n_months} months as {sensor} {index.upper()} "
              f"({frames[0].label} → {frames[-1].label}).")
    if dropped > 0:
        status += (f" {dropped} month(s) had no scene under the "
                   f"{float(region_max_cloud_percent):g}% region-cloud filter — "
                   f"raise it for more frames.")
    inside_mean = _mean(row[1] for row in series)
    outside_mean = _mean(row[2] for row in series)
    if inside_mean is not None and outside_mean is not None:
        status += (f" Mean {index.upper()} — inside AOI {inside_mean:.3f}, "
                   f"outside {outside_mean:.3f} (Δ {inside_mean - outside_mean:+.3f}).")
    return mp4, gif, frame_pngs, frames_zip, status, series


def build_app():
    """Construct the Gradio Blocks app (requires the `gui` extra: gradio)."""
    import gradio as gr

    sensors = list(SENSORS)
    default_sensor, default_index = "landsat", "lst"

    with gr.Blocks(title="GEE Index Timelapse") as app:
        gr.Markdown(
            "# GEE Index Timelapse\n"
            "Upload an area of interest, choose a sensor / index and a date range, "
            "and generate a cloud-masked monthly-median animation. The AOI is the "
            "cloud-filtered region; the animation frame is its bounding box expanded "
            "by the buffer below."
        )
        with gr.Accordion("📂 Load a previous animation", open=False):
            with gr.Row():
                prev = gr.Dropdown(list_previous_runs(), label="Previously rendered runs "
                                   f"(from {OUTPUT_DIR}/)", scale=4)
                refresh = gr.Button("🔄 Refresh", scale=1)
                load = gr.Button("Load", variant="secondary", scale=1)
            refresh.click(lambda: gr.update(choices=list_previous_runs()), None, prev)
        with gr.Row():
            with gr.Column():
                aoi_file = gr.File(
                    label="AOI — GeoJSON or zipped shapefile",
                    file_types=[".geojson", ".json", ".zip"], type="filepath",
                    value=DEFAULT_AOI)
                buffer_m = gr.Number(label="Frame buffer around AOI (metres)", value=1000)
                sensor = gr.Dropdown(sensors, value=default_sensor, label="Sensor")
                index = gr.Dropdown(indices_for(default_sensor), value=default_index, label="Index")
                with gr.Row():
                    start = gr.Textbox(label="Start (YYYY-MM-DD)", value="2022-05-01")
                    end = gr.Textbox(label="End (YYYY-MM-DD, exclusive)", value="2022-09-01")
                region_cloud = gr.Slider(0, 100, value=10, step=5,
                                         label="Max cloud % over the region")
                with gr.Row():
                    fps = gr.Number(label="Frames per second", value=4)
                    dims = gr.Number(label="Frame size (px)", value=768)
                project = gr.Textbox(label="Earth Engine project",
                                     value=os.environ.get("EE_PROJECT", "hnee-331218"))
                go = gr.Button("Generate animation", variant="primary")
            with gr.Column():
                video = gr.Video(label="Animation (MP4)")
                gif = gr.File(label="Animation (GIF, download)")
                gallery = gr.Gallery(label="Frames (click to preview)", columns=4,
                                     height=200, object_fit="contain")
                frames_zip = gr.File(label="Frames (ZIP of PNGs, download)")
                chart = gr.LinePlot(x="month", y="value", color="area", x_title="Month",
                                    y_title="Index (mean)",
                                    title="Inside vs outside the AOI", height=280)
                status = gr.Markdown()

        # Keep the index choices in sync with the selected sensor.
        def _sync_index(s):
            choices = indices_for(s)
            return gr.update(choices=choices, value=choices[0])
        sensor.change(_sync_index, sensor, index)

        def _go(aoi_file, buffer_m, sensor, index, start, end, region_cloud, fps, dims,
                project, progress=gr.Progress()):
            import pandas as pd
            try:
                progress(0.05, desc="Filtering imagery and building frames…")
                mp4, gif_path, frame_pngs, zip_path, msg, series = run_animation(
                    aoi_path=aoi_file, buffer_m=buffer_m, sensor=sensor, index=index,
                    start=start, end=end, region_max_cloud_percent=region_cloud,
                    fps=fps, dimensions=dims, project=project)
                rows = []
                for month, inside, outside in series:
                    if inside is not None:
                        rows.append((month, inside, "inside AOI"))
                    if outside is not None:
                        rows.append((month, outside, "outside AOI"))
                df = pd.DataFrame(rows, columns=["month", "value", "area"])
                progress(1.0, desc="Done")
                return mp4, gif_path, frame_pngs, zip_path, msg, df
            except Exception as exc:   # surface a friendly message in the UI
                return None, None, None, None, f"**Error:** {exc}", None

        go.click(_go,
                 [aoi_file, buffer_m, sensor, index, start, end, region_cloud, fps, dims, project],
                 [video, gif, gallery, frames_zip, status, chart])

        # Load a previously-rendered run into the same output widgets (no Earth Engine).
        def _load(sel):
            try:
                mp4, gif_path, frame_pngs, zip_path, msg = load_previous_run(sel)
                return mp4, gif_path, frame_pngs, zip_path, msg, None
            except Exception as exc:   # surface a friendly message in the UI
                return None, None, None, None, f"**Error:** {exc}", None
        load.click(_load, [prev], [video, gif, gallery, frames_zip, status, chart])
    return app


def main():
    build_app().launch()


if __name__ == "__main__":
    main()
