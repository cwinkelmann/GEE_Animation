"""A simple Gradio front-end over the gee_animation pipeline.

The orchestration (`run_animation`, `_region_aoi_from_upload`) is plain Python and
unit-testable; Gradio is imported lazily inside `build_app` so the module (and its
tests) load without the optional `gui` extra.
"""
from __future__ import annotations

import os
import tempfile
import types
import zipfile
from pathlib import Path

from . import auth, aoi, charts, collection, compositing, render
from .compositing import month_starts
from .config import RunConfig
from .products import INDICES, SENSORS, get_product

# Injectable pipeline seams (overridden in tests).
DEFAULT_DEPS = types.SimpleNamespace(
    init=auth.init,
    parse=aoi.parse,
    frame_bbox=aoi.frame_bbox_from_region,
    build=collection.build,
    monthly_median=compositing.monthly_median,
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
        fps=int(fps), scale=_SCALE.get(sensor, 30), dimensions=int(dimensions),
        out_dir=str(out_dir), draw_region=True,
    )

    coll = deps.build(cfg, frame_geom, region_geom)
    frames = deps.monthly_median(coll, cfg)
    if not frames:
        raise RuntimeError(
            "No imagery found for that AOI, date range and cloud filter — "
            "try a wider date range or a higher cloud threshold."
        )
    paths = deps.render(frames, cfg, geometry=frame_geom)
    mp4 = next((str(p) for p in paths if str(p).endswith(".mp4")), None)
    gif = next((str(p) for p in paths if str(p).endswith(".gif")), None)
    frame_pngs = [str(p) for p in paths if str(p).endswith(".png")]
    frames_zip = _zip_frames(frame_pngs, out_dir, cfg.name) if frame_pngs else None
    # [(month, inside, outside)] — index mean inside the AOI vs the surrounding frame.
    # Composites (rgb/cir) have no single INDEX band to reduce, so skip the chart.
    series = [] if composite else deps.timeseries(frames, region_geom, frame_geom, cfg.scale)
    n_months = len(month_starts(str(start), str(end)))
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
    return app


def main():
    build_app().launch()


if __name__ == "__main__":
    main()
