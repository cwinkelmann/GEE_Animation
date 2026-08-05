"""Per-frame AOI metadata — cloud fraction and the mean index value INSIDE the AOI.

Landsat's `CLOUD_COVER` is over the whole scene footprint; what usually matters is
how cloudy the *region of interest* is. For each monthly frame this records the
fraction of the AOI with no cloud-free observation in that month's cloud-masked
median (`aoi_cloud_fraction` = 1 − clear), plus the scene count, plus the mean value
of the index over the AOI's clear pixels (`aoi_mean` — average temperature in °C for
LST, mean NDVI for ndvi, etc.), into a SQLite table.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import ee

from .products import INDICES

_COLUMNS = ("name", "sensor", "index_name", "month", "n_scenes",
            "aoi_cloud_fraction", "aoi_clear_fraction", "aoi_mean")


def _reduce_expr(image, reducer, region_geom, scale):
    """UNEVALUATED first value of a single-reducer reduceRegion over the AOI.

    Deliberately returns the server-side object rather than calling `.getInfo()` on
    it: `frame_stats` collects one of these per frame and resolves them all in a
    single round trip (see there). Evaluates to null when the region is empty.
    """
    return image.reduceRegion(
        reducer, geometry=region_geom, scale=scale,
        bestEffort=True, maxPixels=int(1e9)).values().get(0)


def frame_stats(frames, region_geom, scale, mean_band=None, ee_module=ee):
    """[(month, n_scenes, aoi_cloud_fraction, aoi_mean)] for each frame.

    aoi_cloud_fraction is the share of AOI pixels with no valid (cloud-free) value in
    the monthly composite — a pixel is valid only where every band is unmasked, so it
    works for single-band indices and RGB/CIR composites alike. aoi_mean is the mean of
    `mean_band` over the AOI's clear pixels (None when `mean_band` is None, e.g. a
    composite with no single meaningful value); reduceRegion's mean ignores masked
    pixels, so cloudy areas don't drag it down.

    Every frame's reducers are batched into ONE `ee.Dictionary(...).getInfo()` — the
    same idiom `inventory.scene_inventory` uses, and the same global constraint
    `compositing.composite` is pinned to: never one `getInfo()` per frame. Serially
    this cost ~5 s per frame (110 s for a 22-frame run) purely in round trips.
    """
    # Index-keyed (not label-keyed) so two frames sharing a label could never collide
    # and silently overwrite each other's stats.
    exprs = {}
    for i, f in enumerate(frames):
        valid = f.image.mask().reduce(ee_module.Reducer.min())   # 1 where all bands valid
        exprs[f"clear{i}"] = _reduce_expr(valid, ee_module.Reducer.mean(), region_geom, scale)
        if mean_band is not None:
            exprs[f"mean{i}"] = _reduce_expr(f.image.select(mean_band),
                                             ee_module.Reducer.mean(), region_geom, scale)
    if not exprs:                     # no frames: nothing to ask Earth Engine about
        return []
    data = ee_module.Dictionary(exprs).getInfo()

    rows = []
    for i, f in enumerate(frames):
        clear = data.get(f"clear{i}")
        clear = 0.0 if clear is None else float(clear)
        mean = None
        if mean_band is not None:
            mv = data.get(f"mean{i}")
            mean = None if mv is None else round(float(mv), 4)
        rows.append((f.label, getattr(f, "n_scenes", None), round(1.0 - clear, 4), mean))
    return rows


def write_db(db_path, run, rows) -> str:
    """Insert per-frame stat rows into a SQLite DB (created/migrated if needed).

    `run` = dict(name, sensor, index); `rows` from frame_stats. Returns the path.
    """
    db_path = str(db_path)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS frame_clouds ("
            "name TEXT, sensor TEXT, index_name TEXT, month TEXT, n_scenes INTEGER, "
            "aoi_cloud_fraction REAL, aoi_clear_fraction REAL, aoi_mean REAL, "
            "PRIMARY KEY (name, month))")
        # migrate a pre-aoi_mean DB in place so old and new runs share one file
        cols = {r[1] for r in con.execute("PRAGMA table_info(frame_clouds)")}
        if "aoi_mean" not in cols:
            con.execute("ALTER TABLE frame_clouds ADD COLUMN aoi_mean REAL")
        con.executemany(
            f"INSERT OR REPLACE INTO frame_clouds ({','.join(_COLUMNS)}) VALUES (?,?,?,?,?,?,?,?)",
            [(run["name"], run["sensor"], run["index"], month, n, cloud,
              (None if cloud is None else round(1.0 - cloud, 4)), mean)
             for month, n, cloud, mean in rows])
        con.commit()
    finally:
        con.close()
    return db_path


def write_frame_metadata(frames, cfg, region_geom, ee_module=ee) -> str:
    """Compute per-frame AOI stats and write them to <out_dir>/metadata.db.

    The mean is taken over the index band ("INDEX") for single-band indices; composites
    (rgb/cir) have no single meaningful value so their aoi_mean is left null.
    """
    spec = INDICES.get(cfg.index)
    mean_band = None if (spec and spec.composite) else "INDEX"
    rows = frame_stats(frames, region_geom, cfg.scale, mean_band, ee_module)
    run = {"name": cfg.name, "sensor": cfg.sensor, "index": cfg.index}
    return write_db(Path(cfg.out_dir) / "metadata.db", run, rows)
