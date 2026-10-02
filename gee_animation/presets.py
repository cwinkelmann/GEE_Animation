"""Named presets for the GUI: complete input sets for the showcase renders.

Each preset is a dict of :func:`gee_animation.gui.run_animation` keyword arguments,
so applying one is the same as filling the form by hand, and the config it builds
is the one the corresponding YAML in the experiment branch describes (the Tegel
cuts: ``r12_zoom_lst_pretty_10yr``, ``_grid``, ``r12_zoom_lst_rf_local_10yr`` and
the R13 equivalents). The footprints ship as package data under ``presets/aoi``
so the presets also work inside the container, where ``docs/`` is absent.
"""
from __future__ import annotations

from pathlib import Path

_AOI_DIR = Path(__file__).resolve().parent / "presets_aoi"

# The Tegel showcase frame is the footprint's bounds + 0.4 km on every side.
_TEGEL_BUFFER_M = 400

_TEGEL_COMMON = dict(
    buffer_m=_TEGEL_BUFFER_M, sensor="landsat", start="2017-01-01", end="2026-09-01",
    cadence="monthly", region_max_cloud_percent=20, max_cloud_percent=70,
    fps=6, dimensions=1440, preset="1080p", aspect="match", quality="9", write_gif=False,
    interpolate=10, interpolate_mode="auto", min_scenes=1, raw_frames=True,
    viz_min=15.0, viz_max=45.0, missions=["L8", "L9"], crs_choice="auto (UTM zone of the AOI — square pixels)",
    project="hnee-331218",
)

_SITES = {
    "R12": dict(aoi_path=str(_AOI_DIR / "r12_footprint.geojson"), title="Tegeler Forst, Revier 12"),
    "R13": dict(aoi_path=str(_AOI_DIR / "r13_footprint.geojson"), title="Spandauer Forst, Revier 13"),
}

_EDITIONS = {
    # native 100 m thermal, harmonic seasonal model (hole-free winters), smooth upscale
    "native 100 m": dict(index="lst", smooth="harmonic", harmonics=2,
                         subtitle="Surface temperature · harmonic seasonal model",
                         upscale="lanczos", pixel_grid=False, geotiffs=False),
    # the same run with the fetched 100 m cells drawn as a mesh over flat blocks
    "100 m grid": dict(index="lst", smooth="harmonic", harmonics=2,
                       subtitle="Surface temperature · harmonic seasonal model · 100 m grid",
                       upscale="nearest", pixel_grid=True, geotiffs=False),
    # random-forest sharpening to the 20 m grid, forests trained locally per frame
    "RF-sharpened": dict(index="lst_rf", sharpen_local=True,
                         subtitle="RF-sharpened surface temperature",
                         pool_start_year=2017, pool_end_year=2026, pool_strategy="gap_fill",
                         upscale="lanczos", pixel_grid=False, geotiffs=True),
}

PRESETS: dict[str, dict] = {
    f"Tegel {site} · {edition}": {**_TEGEL_COMMON, **site_values, **edition_values}
    for site, site_values in _SITES.items()
    for edition, edition_values in _EDITIONS.items()
}

NO_PRESET = "— none —"
PRESET_CHOICES = [NO_PRESET, *PRESETS]


def staged_aoi(path: str) -> str:
    """Copy a packaged footprint into a fresh temp dir and return the copy's path.

    Gradio refuses to serve files that it did not create and that are outside its
    ``allowed_paths`` (``InvalidPathError`` at postprocess time), which is exactly
    where package data lives in a container. A copy under the system temp dir is
    "created by the application" and passes.
    """
    import shutil
    import tempfile
    staged = Path(tempfile.mkdtemp(prefix="gee_preset_")) / Path(path).name
    shutil.copyfile(path, staged)
    return str(staged)
