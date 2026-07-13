"""Per-frame cloud metadata — cloud fraction INSIDE the AOI (not the whole scene).

Landsat's `CLOUD_COVER` is over the whole scene footprint; what usually matters is
how cloudy the *region of interest* is. For each monthly frame this records the
fraction of the AOI with no cloud-free observation in that month's cloud-masked
median (`aoi_cloud_fraction` = 1 − clear), plus the scene count, into a SQLite table.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import ee


def frame_cloud_fractions(frames, region_geom, scale, ee_module=ee):
    """[(month, n_scenes, aoi_cloud_fraction)] for each frame.

    aoi_cloud_fraction is the share of AOI pixels with no valid (cloud-free) value in
    the monthly composite — a pixel is valid only where every band is unmasked, so
    this works for single-band indices and RGB/CIR composites alike.
    """
    rows = []
    for f in frames:
        valid = f.image.mask().reduce(ee_module.Reducer.min())   # 1 where all bands valid
        clear = valid.reduceRegion(
            ee_module.Reducer.mean(), geometry=region_geom, scale=scale,
            bestEffort=True, maxPixels=int(1e9)).values().get(0).getInfo()
        clear = 0.0 if clear is None else float(clear)
        rows.append((f.label, getattr(f, "n_scenes", None), round(1.0 - clear, 4)))
    return rows


def write_db(db_path, run, rows) -> str:
    """Insert per-frame cloud rows into a SQLite DB (created if needed). Returns the path.

    `run` = dict(name, sensor, index); `rows` from frame_cloud_fractions.
    """
    db_path = str(db_path)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS frame_clouds ("
            "name TEXT, sensor TEXT, index_name TEXT, month TEXT, n_scenes INTEGER, "
            "aoi_cloud_fraction REAL, aoi_clear_fraction REAL, "
            "PRIMARY KEY (name, month))")
        con.executemany(
            "INSERT OR REPLACE INTO frame_clouds VALUES (?,?,?,?,?,?,?)",
            [(run["name"], run["sensor"], run["index"], month, n, cloud,
              (None if cloud is None else round(1.0 - cloud, 4)))
             for month, n, cloud in rows])
        con.commit()
    finally:
        con.close()
    return db_path


def write_frame_metadata(frames, cfg, region_geom, ee_module=ee) -> str:
    """Compute per-frame AOI cloud fractions and write them to <out_dir>/metadata.db."""
    rows = frame_cloud_fractions(frames, region_geom, cfg.scale, ee_module)
    run = {"name": cfg.name, "sensor": cfg.sensor, "index": cfg.index}
    return write_db(Path(cfg.out_dir) / "metadata.db", run, rows)
