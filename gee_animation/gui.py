"""A simple Gradio front-end over the gee_animation pipeline.

The orchestration (`run_animation`, `_region_aoi_from_upload`) is plain Python and
unit-testable; Gradio is imported lazily inside `build_app` so the module (and its
tests) load without the optional `gui` extra.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import tempfile
import types
import zipfile
from pathlib import Path

from . import anomaly, auth, aoi, charts, collection, compositing, inventory, render
from .compositing import period_starts
from .config import POOL_STRATEGIES, SUPPORTED_CADENCES, ConfigError, RunConfig
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
    inventory=inventory.write_inventory,
)

# Cadence choices, monthly first (the default).
CADENCES = ["monthly"] + sorted(SUPPORTED_CADENCES - {"monthly"})

# Screen-size presets offered in the GUI. NATIVE_PRESET is the "don't upscale" choice
# and maps to cfg.preset=None; everything else is a render.PRESETS key.
NATIVE_PRESET = "native (no upscaling)"
PRESET_CHOICES = ["4k", "1440p", "1080p", "720p", NATIVE_PRESET]
DEFAULT_PRESET = "1080p"
ASPECT_CHOICES = ["match", "16:9", "4:3", "1:1", "21:9"]
DEFAULT_ASPECT = "match"

# MP4 encode quality (render.quality): 1 (smallest/worst) .. 10 (largest/best), or
# "default" => cfg.quality=None (imageio's own default, currently ~5).
QUALITY_CHOICES = ["default"] + [str(n) for n in range(1, 11)]
DEFAULT_QUALITY = "default"

# Shown next to the pooling controls *and* appended to the status of any pooled run —
# the trade-off has to be visible before the user renders. Wording from
# config/pooled.example.yaml.
POOL_WARNING = (
    "**COSMETIC ONLY.** Cross-year pooling keeps each frame's calendar slot but takes "
    "the imagery from whichever year in the range had the clearest scene, so a frame "
    "labelled `2022-05` may show May 2021. The result is a smooth, near cloud-free "
    "seasonal loop — it is **not a time series** and must not be used for quantitative "
    "analysis, trend/change detection, or anything reported as a measurement. Every "
    "borrowed frame is labelled with its source year (`2022-05 ← 2021`) and the info "
    "bar names the pooled range. "
    "**Exception:** `gap_fill` keeps the year you asked for wherever it has usable "
    "data and borrows only for periods that would otherwise be empty, so its unmarked "
    "frames really are from the requested year."
)

# Gradio 6 lays the whole ancestor chain out as a flex column — including <html> — and
# puts `overflow-y: hidden` on .gradio-container. On a tall form like this one that
# leaves the page unscrollable in browsers that don't scroll a flex <html> (Safari in
# particular), so the bottom controls become unreachable. Restore a plain document:
# block layout, auto height, and let the container overflow normally.
PAGE_CSS = """
html, body, gradio-app {
    display: block !important;
    height: auto !important;
    min-height: 100% !important;
    overflow-y: auto !important;
}
.gradio-container {
    overflow-y: visible !important;
    height: auto !important;
    min-height: 0 !important;
}
"""


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
# A frame stem's period suffix: "YYYY-MM" (monthly), "YYYY-MM-DD" (sub-monthly)
# or "YYYY-Qn" (quarterly) — every label format compositing.period_starts emits.
_PERIOD_RE = re.compile(r"\d{4}-(?:\d{2}(?:-\d{2})?|Q[1-4])")


def _run_frames(run_dir: Path, name: str) -> list:
    """Per-period frame PNGs for animation `name` in `run_dir`, sorted by period.

    A frame is exactly ``<name>_<period>.png`` — the strict suffix match keeps a
    run named ``wne_lst`` from grabbing ``wne_lst_smw``'s frames in a shared folder.
    """
    frames = []
    for p in run_dir.glob(f"{name}_*.png"):
        if _PERIOD_RE.fullmatch(p.stem[len(name) + 1:]):
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


def _blank(value) -> bool:
    """True for a Gradio input left empty.

    Handles ``None``, an all-whitespace string, and non-positive numbers. The
    latter matters because ``gr.Number(value=None)`` is rendered by the browser
    as ``0`` (verified against the live DOM), so an *untouched* pooling-year box
    posts ``0``, not ``None``. No calendar year is zero or negative, so treating
    a non-positive number as "not set" keeps the untouched-form default off
    without guessing at a bogus year-0 range.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return float(value) <= 0
    except (TypeError, ValueError):
        return False


def _pool_years(first, last) -> list | None:
    """``[firstYear, lastYear]`` from the two GUI year boxes, or ``None`` when off.

    Both boxes empty => pooling off. Exactly one filled is a user mistake, not a
    half-open range, so it is refused rather than guessed at.
    """
    if _blank(first) and _blank(last):
        return None
    if _blank(first) or _blank(last):
        raise ValueError(
            "cross-year pooling needs both a first and a last year "
            "(or leave both empty to switch pooling off)")
    try:
        return [int(first), int(last)]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"pooling years must be whole years: {exc}") from exc


def _preset_value(preset):
    """Map the preset dropdown to ``cfg.preset`` (NATIVE_PRESET / blank => ``None``)."""
    if _blank(preset) or str(preset) == NATIVE_PRESET:
        return None
    return str(preset)


def _text_value(text):
    """Blank/whitespace-only textbox => ``None`` (falls back to the on-frame default);
    anything else is passed through verbatim. Used for title/subtitle, which — unlike
    credit — have no meaningful "explicit empty" state to preserve."""
    return None if _blank(text) else str(text)


def _credit_value(credit, omit_credit):
    """Resolve the credit textbox + "omit" checkbox into ``cfg.credit``'s three states.

    - textbox blank, box unchecked => ``None`` (automatic sensor attribution — the
      default, compliance-safe state; an empty textbox must NOT be read as "omit").
    - textbox non-blank => that text verbatim (the box must be unchecked; see below).
    - box checked, textbox blank   => ``""`` (the conscious opt-out; ``validate()``
      warns for sentinel2).
    - box checked *and* textbox non-blank is contradictory — a friendly error, not a
      silent pick of one over the other.
    """
    text = _text_value(credit)
    if omit_credit and text:
        raise ValueError(
            "credit text and \"Omit the data credit line\" are contradictory — "
            "clear the credit text or uncheck the box, not both")
    return "" if omit_credit else text


def _quality_value(quality):
    """Dropdown value ("default"/blank/None => ``None``) to ``cfg.quality``.

    The dropdown can only offer "default" or "1".."10", but this still coerces
    defensively (e.g. programmatic callers) rather than trusting the caller; out-of-
    range integers are left for ``cfg.validate()`` to reject.
    """
    if quality is None:
        return None
    q = str(quality).strip()
    if not q or q == DEFAULT_QUALITY:
        return None
    try:
        return int(q)
    except ValueError as exc:
        raise ValueError(
            f"quality must be a whole number 1-10 or {DEFAULT_QUALITY!r}, got {q!r}"
        ) from exc


def _validate(cfg) -> list[str]:
    """Run ``cfg.validate()``, returning the warnings it logged.

    The GUI builds :class:`RunConfig` directly, so — unlike ``RunConfig.from_yaml`` —
    nothing would otherwise validate it. ``ConfigError`` becomes a plain ``ValueError``
    with a friendly prefix (the callbacks render it as text, not a traceback), and
    ``config``'s advisory ``log.warning``s are captured so they can be shown to the
    user instead of vanishing into the server log.
    """
    warnings: list[str] = []
    handler = logging.Handler(level=logging.WARNING)
    handler.emit = lambda record: warnings.append(record.getMessage())
    logger = logging.getLogger("gee_animation.config")
    logger.addHandler(handler)
    try:
        cfg.validate()
    except ConfigError as exc:
        raise ValueError(f"Invalid settings: {exc}") from exc
    finally:
        logger.removeHandler(handler)
    return warnings


def _prepare(*, aoi_path, buffer_m, sensor, index, start, end,
             region_max_cloud_percent, max_cloud_percent, fps, dimensions,
             project, out_dir, cadence, preset, aspect, write_gif,
             pool_start_year, pool_end_year, pool_strategy,
             title=None, subtitle=None, credit=None, omit_credit=False,
             quality=None, show_clouds=False, deps):
    """Authenticate, resolve the AOIs and build a validated RunConfig.

    Returns ``(cfg, frame_geom, region_geom, warnings)``. Shared by
    :func:`run_animation` and :func:`run_inventory` so both entry points get exactly
    the same configuration and the same validation. ``title``/``subtitle``/``credit``/
    ``quality`` are purely client-side (see ``RunConfig``) so they flow into the
    inventory's config too, harmlessly — the CSV writer never reads them.
    """
    if not aoi_path:
        raise ValueError("please upload an AOI (a GeoJSON file or a zipped shapefile)")
    get_product(sensor, index)   # validate the (sensor, index) pair up front
    region_aoi = _region_aoi_from_upload(aoi_path)
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
        cadence=str(cadence),
        max_cloud_percent=float(max_cloud_percent),
        region_max_cloud_percent=float(region_max_cloud_percent),
        viz_min=viz_min, viz_max=viz_max, palette=palette,
        fps=float(fps), scale=_SCALE.get(sensor, 30), dimensions=int(dimensions),
        # `dimensions` is the *fetch* size (capped to the product's native GSD by
        # render._cap_dimensions); `preset` is the output size it is upscaled to.
        # Without a preset a 100 m LST over a ~9 km AOI renders at ~91 px.
        preset=_preset_value(preset), aspect=(aspect or None),
        # The GIF is the slowest encode of a render and only a preview format, so it
        # is opt-out here. The PNG frames are NOT exposed: the gallery and the ZIP
        # download are built from them, so switching them off would empty the GUI's
        # own outputs. (`render.frames` still exists for config/API runs.)
        gif=bool(write_gif),
        pool_years=_pool_years(pool_start_year, pool_end_year),
        pool_strategy=str(pool_strategy or "least_cloudy"),
        out_dir=str(out_dir), draw_region=True,
        # `show_clouds` inverts to the config's mask_clouds; validate() rejects it
        # for palette indices, surfacing the same error the YAML path would give.
        mask_clouds=not bool(show_clouds),
        title=_text_value(title), subtitle=_text_value(subtitle),
        credit=_credit_value(credit, omit_credit),
        quality=_quality_value(quality),
    )
    return cfg, frame_geom, region_geom, _validate(cfg)


def run_inventory(*, aoi_path, buffer_m, sensor, index, start, end,
                  region_max_cloud_percent=10.0, max_cloud_percent=60.0,
                  fps=4, dimensions=768, project="hnee-331218", out_dir=None,
                  cadence="monthly", preset=DEFAULT_PRESET, aspect=DEFAULT_ASPECT,
                  write_gif=True, pool_start_year=None, pool_end_year=None,
                  pool_strategy="least_cloudy",
                  title=None, subtitle=None, credit=None, omit_credit=False,
                  quality=None, show_clouds=False, deps=DEFAULT_DEPS):
    """Write the per-scene usable/rejected inventory CSV. Returns ``(csv_path, status)``.

    Mirrors ``cli.run(..., inventory=True)``, including its refusal to combine the
    inventory with cross-year pooling. ``title``/``subtitle``/``credit``/``quality``
    are accepted for parity with :func:`run_animation` (both share ``_prepare``) but
    are no-ops here — they only affect rendered frames, and the CSV has none.
    """
    if not _blank(pool_start_year) or not _blank(pool_end_year):
        # Same reason as cli.run: the inventory buckets scenes by the nominal date
        # range while pooling draws them from other years, so the CSV would list
        # scenes the run did not use and omit the ones it did.
        raise ValueError(
            "the scene inventory does not support cross-year pooling: it buckets "
            "scenes by the nominal date range, so it cannot describe frames borrowed "
            "from other years. Clear the pooling years to inventory the candidate "
            "scenes.")
    cfg, frame_geom, region_geom, warnings = _prepare(
        aoi_path=aoi_path, buffer_m=buffer_m, sensor=sensor, index=index,
        start=start, end=end, region_max_cloud_percent=region_max_cloud_percent,
        max_cloud_percent=max_cloud_percent, fps=fps, dimensions=dimensions,
        project=project, out_dir=out_dir, cadence=cadence, preset=preset,
        aspect=aspect, write_gif=write_gif, pool_start_year=None, pool_end_year=None,
        pool_strategy=pool_strategy,
        title=title, subtitle=subtitle, credit=credit, omit_credit=omit_credit,
        quality=quality, show_clouds=show_clouds, deps=deps)
    path = deps.inventory(cfg, frame_geom, region_geom)
    status = (f"Wrote the scene inventory for {sensor} {index.upper()} "
              f"({cfg.start} → {cfg.end}, {cfg.cadence}) — one row per candidate scene "
              f"with the reason it was used or rejected.")
    return str(path), _with_warnings(status, warnings)


def _with_warnings(status: str, warnings) -> str:
    """Append the config's advisory warnings to a status line."""
    return status + "".join(f"\n\n⚠️ {w}" for w in warnings)


def run_animation(*, aoi_path, buffer_m, sensor, index, start, end,
                  region_max_cloud_percent=10.0, max_cloud_percent=60.0,
                  fps=4, dimensions=768, project="hnee-331218", out_dir=None,
                  cadence="monthly", preset=DEFAULT_PRESET, aspect=DEFAULT_ASPECT,
                  write_gif=True, pool_start_year=None, pool_end_year=None,
                  pool_strategy="least_cloudy",
                  title=None, subtitle=None, credit=None, omit_credit=False,
                  quality=None, show_clouds=False, deps=DEFAULT_DEPS):
    """Build one animation from GUI inputs.

    Returns ``(mp4_path, gif_path, frame_pngs, frames_zip, status, series)`` where
    `frame_pngs` is the list of per-period PNGs and `frames_zip` bundles them for
    download (both ``None``/empty if no frames were rendered). `gif_path` is ``None``
    when `write_gif` is off — the MP4 and the frames are unaffected.
    `title`/`subtitle`/`credit`/`quality` mirror the config-file keys of the same
    name (see ``RunConfig``); `omit_credit` is the GUI-only checkbox that resolves to
    `credit=""` (see :func:`_credit_value`).
    """
    composite = INDICES[index].composite if index in INDICES else False
    cfg, frame_geom, region_geom, warnings = _prepare(
        aoi_path=aoi_path, buffer_m=buffer_m, sensor=sensor, index=index,
        start=start, end=end, region_max_cloud_percent=region_max_cloud_percent,
        max_cloud_percent=max_cloud_percent, fps=fps, dimensions=dimensions,
        project=project, out_dir=out_dir, cadence=cadence, preset=preset,
        aspect=aspect, write_gif=write_gif, pool_start_year=pool_start_year,
        pool_end_year=pool_end_year, pool_strategy=pool_strategy,
        title=title, subtitle=subtitle, credit=credit, omit_credit=omit_credit,
        quality=quality, show_clouds=show_clouds, deps=deps)
    out_dir = cfg.out_dir

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
    n_periods = len(period_starts(cfg.start, cfg.end, cfg.cadence))
    dropped = n_periods - len(frames)
    status = (f"Rendered {len(frames)} of {n_periods} {cfg.cadence} periods as "
              f"{sensor} {index.upper()} ({frames[0].label} → {frames[-1].label}).")
    if dropped > 0:
        status += (f" {dropped} period(s) had no scene under the "
                   f"{cfg.region_max_cloud_percent:g}% region-cloud filter — "
                   f"raise it for more frames.")
    inside_mean = _mean(row[1] for row in series)
    outside_mean = _mean(row[2] for row in series)
    if inside_mean is not None and outside_mean is not None:
        status += (f" Mean {index.upper()} — inside AOI {inside_mean:.3f}, "
                   f"outside {outside_mean:.3f} (Δ {inside_mean - outside_mean:+.3f}).")
    if cfg.pool_years:
        status += (f"\n\nPooled over {cfg.pool_years[0]}–{cfg.pool_years[1]} "
                   f"({cfg.pool_strategy}). {POOL_WARNING}")
    return mp4, gif, frame_pngs, frames_zip, _with_warnings(status, warnings), series


def build_app():
    """Construct the Gradio Blocks app (requires the `gui` extra: gradio)."""
    import gradio as gr

    sensors = list(SENSORS)
    default_sensor, default_index = "landsat", "lst"

    with gr.Blocks(title="GEE Index Timelapse") as app:
        gr.Markdown(
            "# GEE Index Timelapse\n"
            "Upload an area of interest, choose a sensor / index and a date range, "
            "and generate a cloud-masked median-composite animation. The AOI is the "
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
                cadence = gr.Dropdown(CADENCES, value="monthly", label="Cadence",
                                      info="One frame per period. Sub-monthly cadences "
                                           "leave most bins empty on Landsat (16-day "
                                           "repeat) — prefer Sentinel-2.")
                region_cloud = gr.Slider(0, 100, value=10, step=5,
                                         label="Max cloud % over the region")
                with gr.Row():
                    fps = gr.Number(label="Frames per second", value=4)
                    dims = gr.Number(label="Fetch size (px)", value=768,
                                     info="How much data is fetched. Capped to the "
                                          "product's true resolution (no upsampling).")
                with gr.Row():
                    preset = gr.Dropdown(PRESET_CHOICES, value=DEFAULT_PRESET,
                                         label="Output size",
                                         info="Screen size the fetched frame is "
                                              "upscaled to. Without it, 100 m LST over "
                                              "a 9 km AOI is only ~91 px wide.")
                    aspect = gr.Dropdown(ASPECT_CHOICES, value=DEFAULT_ASPECT,
                                         label="Aspect ratio",
                                         info="'match' keeps the AOI's own shape "
                                              "(no letterbox bars).")
                    quality = gr.Dropdown(QUALITY_CHOICES, value=DEFAULT_QUALITY,
                                          label="MP4 quality",
                                          info="1 (smallest/worst) – 10 (largest/"
                                               "best), passed to the ffmpeg writer. "
                                               "'default' ≈ 5, imageio's own default.")
                write_gif = gr.Checkbox(
                    value=True, label="Also write a GIF",
                    info="The GIF is a low-resolution preview and the slowest step of "
                         "a render (~5 s per run). The MP4 and the per-frame PNGs are "
                         "written either way.")
                show_clouds = gr.Checkbox(
                    value=False, label="Show real clouds (true colour / CIR only)",
                    info="Keeps clouds in the imagery instead of masking them to "
                         "grey. Only for the RGB/CIR composites — index products "
                         "(NDVI, LST, …) would colorize a cloud as a false data "
                         "value, so they always mask. The cloudiest scenes are "
                         "still filtered out either way.")
                with gr.Accordion("🖋️ Presentation (title, subtitle, credit)", open=False):
                    title = gr.Textbox(
                        label="Title", value="",
                        placeholder="e.g. Białowieża Forest NDVI 2022",
                        info="Frame header's large first line. Empty = the index's "
                             "own name (e.g. \"Vegetation greenness (NDVI)\").")
                    subtitle = gr.Textbox(
                        label="Subtitle", value="",
                        placeholder="e.g. UNESCO World Heritage site, Brandenburg, "
                                    "Germany",
                        info="Frame header's smaller second line. Empty = none "
                             "(a pooling/interpolation notice still wins that line "
                             "when one applies).")
                    with gr.Row():
                        credit = gr.Textbox(
                            label="Credit / attribution line", value="", scale=3,
                            placeholder="leave blank for automatic Copernicus/USGS/"
                                        "NASA attribution",
                            info="Overrides the bottom-right attribution line "
                                 "verbatim. Leave blank for the automatic "
                                 "sensor-appropriate credit.")
                        omit_credit = gr.Checkbox(
                            value=False, label="Omit the data credit line", scale=1,
                            info="Suppresses the attribution line entirely. For "
                                 "Sentinel-2 this is a licence-relevant choice — "
                                 "the Copernicus notice normally appears here.")
                with gr.Accordion("🔁 Cross-year pooling (cosmetic)", open=False):
                    gr.Markdown(POOL_WARNING)
                    with gr.Row():
                        pool_start = gr.Number(label="Pool from year (0 or empty = off)",
                                               value=None, precision=0)
                        pool_end = gr.Number(label="Pool to year (inclusive)",
                                             value=None, precision=0)
                    pool_strategy = gr.Dropdown(
                        sorted(POOL_STRATEGIES), value="least_cloudy",
                        label="Pooling strategy",
                        info="gap_fill = keep the requested year where it has data, "
                             "borrow another year only for otherwise-empty periods "
                             "(the only strategy that preserves the year you asked "
                             "for); least_cloudy = replace every period with the "
                             "single sharpest scene from any pooled year; "
                             "median = median across the pooled years (smoother, "
                             "but blurs and mixes years).")
                project = gr.Textbox(label="Earth Engine project",
                                     value=os.environ.get("EE_PROJECT", "hnee-331218"))
                go = gr.Button("Generate animation", variant="primary")
                inv = gr.Button("📋 Scene inventory (CSV)", variant="secondary")
            with gr.Column():
                video = gr.Video(label="Animation (MP4)")
                gif = gr.File(label="Animation (GIF, download)")
                gallery = gr.Gallery(label="Frames (click to preview)", columns=4,
                                     height=200, object_fit="contain")
                frames_zip = gr.File(label="Frames (ZIP of PNGs, download)")
                inventory_csv = gr.File(label="Scene inventory (CSV, download)")
                chart = gr.LinePlot(x="period", y="value", color="area",
                                    x_title="Period", y_title="Index (mean)",
                                    title="Inside vs outside the AOI", height=280)
                status = gr.Markdown()

        # Keep the index choices in sync with the selected sensor.
        def _sync_index(s):
            choices = indices_for(s)
            return gr.update(choices=choices, value=choices[0])
        sensor.change(_sync_index, sensor, index)

        # Every run-shaped callback returns the same widget tuple:
        # (video, gif, gallery, frames_zip, inventory_csv, status, chart).
        inputs = [aoi_file, buffer_m, sensor, index, start, end, cadence, region_cloud,
                  fps, dims, preset, aspect, quality, write_gif,
                  title, subtitle, credit, omit_credit,
                  pool_start, pool_end, pool_strategy, project, show_clouds]
        outputs = [video, gif, gallery, frames_zip, inventory_csv, status, chart]

        def _error(exc):
            return None, None, None, None, None, f"**Error:** {exc}", None

        def _go(aoi_file, buffer_m, sensor, index, start, end, cadence, region_cloud,
                fps, dims, preset, aspect, quality, write_gif,
                title, subtitle, credit, omit_credit,
                pool_start, pool_end, pool_strategy, project, show_clouds,
                progress=gr.Progress()):
            import pandas as pd
            try:
                progress(0.05, desc="Filtering imagery and building frames…")
                mp4, gif_path, frame_pngs, zip_path, msg, series = run_animation(
                    aoi_path=aoi_file, buffer_m=buffer_m, sensor=sensor, index=index,
                    start=start, end=end, cadence=cadence,
                    region_max_cloud_percent=region_cloud, fps=fps, dimensions=dims,
                    preset=preset, aspect=aspect, quality=quality, write_gif=write_gif,
                    title=title, subtitle=subtitle, credit=credit,
                    omit_credit=omit_credit,
                    pool_start_year=pool_start, pool_end_year=pool_end,
                    pool_strategy=pool_strategy, project=project,
                    show_clouds=show_clouds)
                rows = []
                for period, inside, outside in series:
                    if inside is not None:
                        rows.append((period, inside, "inside AOI"))
                    if outside is not None:
                        rows.append((period, outside, "outside AOI"))
                df = pd.DataFrame(rows, columns=["period", "value", "area"])
                progress(1.0, desc="Done")
                return mp4, gif_path, frame_pngs, zip_path, None, msg, df
            except Exception as exc:   # surface a friendly message in the UI
                return _error(exc)

        go.click(_go, inputs, outputs)

        def _inventory(aoi_file, buffer_m, sensor, index, start, end, cadence,
                       region_cloud, fps, dims, preset, aspect, quality, write_gif,
                       title, subtitle, credit, omit_credit,
                       pool_start, pool_end, pool_strategy, project, show_clouds,
                       progress=gr.Progress()):
            try:
                progress(0.05, desc="Listing candidate scenes…")
                csv_path, msg = run_inventory(
                    aoi_path=aoi_file, buffer_m=buffer_m, sensor=sensor, index=index,
                    start=start, end=end, cadence=cadence,
                    region_max_cloud_percent=region_cloud, fps=fps, dimensions=dims,
                    preset=preset, aspect=aspect, quality=quality, write_gif=write_gif,
                    title=title, subtitle=subtitle, credit=credit,
                    omit_credit=omit_credit,
                    pool_start_year=pool_start, pool_end_year=pool_end,
                    pool_strategy=pool_strategy, project=project,
                    show_clouds=show_clouds)
                progress(1.0, desc="Done")
                return None, None, None, None, csv_path, msg, None
            except Exception as exc:   # surface a friendly message in the UI
                return _error(exc)

        inv.click(_inventory, inputs, outputs)

        # Load a previously-rendered run into the same output widgets (no Earth Engine).
        def _load(sel):
            try:
                mp4, gif_path, frame_pngs, zip_path, msg = load_previous_run(sel)
                return mp4, gif_path, frame_pngs, zip_path, None, msg, None
            except Exception as exc:   # surface a friendly message in the UI
                return _error(exc)
        load.click(_load, [prev], outputs)
    return app


def main():
    # Gradio 6 moved `css` from the Blocks constructor to launch(); passing it to
    # Blocks still works but warns. See PAGE_CSS for why the override is needed.
    build_app().launch(css=PAGE_CSS)


if __name__ == "__main__":
    main()
