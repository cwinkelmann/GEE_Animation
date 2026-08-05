"""Per-frame AOI metadata — cloud fraction and the mean index value INSIDE the AOI.

Landsat's `CLOUD_COVER` is over the whole scene footprint; what usually matters is
how cloudy the *region of interest* is. For each monthly frame this records the
fraction of the AOI with no cloud-free observation in that month's cloud-masked
median (`aoi_cloud_fraction` = 1 − clear), plus the scene count, plus the mean value
of the index over the AOI's clear pixels (`aoi_mean` — average temperature in °C for
LST, mean NDVI for ndvi, etc.), into a SQLite table.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import ee

from .products import INDICES

log = logging.getLogger(__name__)

_COLUMNS = ("name", "sensor", "index_name", "month", "n_scenes",
            "aoi_cloud_fraction", "aoi_clear_fraction", "aoi_mean")

#: Frames resolved per `getInfo()` round trip. Neither extreme works: one request per
#: frame cost ~5 s each (110 s for a 22-frame run), while ONE request for every frame
#: blows Earth Engine's fixed per-request memory ceiling — a 60-frame (5-year monthly)
#: run died with "User memory limit exceeded" before writing anything. Each entry is
#: not a cheap lookup: it makes EE build that period's median composite over all its
#: cloud-masked scenes and reduce it over the AOI. 22 frames in one request is measured
#: to work (16.4 s), so 16 sits comfortably under the observed ceiling while still
#: cutting a 60-frame run to 4 round trips.
FRAME_STATS_CHUNK = 16

#: Substrings marking a server-side resource failure that a SMALLER request may
#: survive. Deliberately narrow: an ordinary EE error (a bad band name, say) must
#: surface at once rather than be retried four times over.
_LIMIT_HINTS = ("memory limit", "user memory", "out of memory",
                "limit exceeded", "too many", "too large")


def _is_limit_error(exc) -> bool:
    """True for the "User memory limit exceeded" family — the errors worth retrying
    at a smaller chunk size. Anything else propagates untouched."""
    return isinstance(exc, ee.EEException) and any(
        h in str(exc).lower() for h in _LIMIT_HINTS)


def _reduce_expr(image, reducer, region_geom, scale):
    """UNEVALUATED first value of a single-reducer reduceRegion over the AOI.

    Deliberately returns the server-side object rather than calling `.getInfo()` on
    it: `frame_stats` collects these and resolves a whole chunk of frames' worth in
    one round trip (see there). Evaluates to null when the region is empty.
    """
    return image.reduceRegion(
        reducer, geometry=region_geom, scale=scale,
        bestEffort=True, maxPixels=int(1e9)).values().get(0)


def _chunk_exprs(frames, offset, region_geom, scale, mean_band, ee_module):
    """The UNEVALUATED reducers for `frames`, keyed by their GLOBAL frame index
    (`offset + j`) so keys stay unique and unambiguous across chunks.

    Index-keyed (not label-keyed) so two frames sharing a label could never collide
    and silently overwrite each other's stats.
    """
    exprs = {}
    for j, f in enumerate(frames):
        i = offset + j
        valid = f.image.mask().reduce(ee_module.Reducer.min())   # 1 where all bands valid
        exprs[f"clear{i}"] = _reduce_expr(valid, ee_module.Reducer.mean(), region_geom, scale)
        if mean_band is not None:
            exprs[f"mean{i}"] = _reduce_expr(f.image.select(mean_band),
                                             ee_module.Reducer.mean(), region_geom, scale)
    return exprs


def frame_stats(frames, region_geom, scale, mean_band=None, ee_module=ee,
                chunk_size=FRAME_STATS_CHUNK):
    """[(month, n_scenes, aoi_cloud_fraction, aoi_mean)] for each frame.

    aoi_cloud_fraction is the share of AOI pixels with no valid (cloud-free) value in
    the monthly composite — a pixel is valid only where every band is unmasked, so it
    works for single-band indices and RGB/CIR composites alike. aoi_mean is the mean of
    `mean_band` over the AOI's clear pixels (None when `mean_band` is None, e.g. a
    composite with no single meaningful value); reduceRegion's mean ignores masked
    pixels, so cloudy areas don't drag it down.

    Reducers are batched `chunk_size` frames to a `getInfo()`, so a run costs
    ceil(n / chunk) round trips — bounded, and never one per frame (serially that was
    ~5 s per frame, 110 s for a 22-frame run). It is deliberately NOT one request for
    everything: see `FRAME_STATS_CHUNK` for why that fails at 60 frames.

    Graceful degradation: a large AOI or a heavy index can still exceed the ceiling at
    any fixed chunk, so a memory-limit `EEException` halves the chunk and retries the
    same frames, down to a floor of one frame per request. The run gets slower instead
    of producing nothing; at one frame it re-raises rather than spinning. Exactly one
    warning is logged, on the way out, naming the size it settled on.
    """
    frames = list(frames)
    if not frames:                    # no frames: nothing to ask Earth Engine about
        return []

    chunk = requested = max(1, int(chunk_size))
    data: dict = {}
    try:
        i = 0
        while i < len(frames):
            batch = frames[i:i + chunk]
            exprs = _chunk_exprs(batch, i, region_geom, scale, mean_band, ee_module)
            try:
                data.update(ee_module.Dictionary(exprs).getInfo())
            except Exception as exc:
                # Floor reached, or an error a smaller request would not fix.
                if chunk == 1 or not _is_limit_error(exc):
                    raise
                chunk = max(1, chunk // 2)
                continue              # retry the SAME frames, in smaller pieces
            i += len(batch)           # only advance on a resolved chunk
    finally:
        # One warning covering the whole backoff (not one per retry), emitted here so
        # it is logged whether the run recovered or ultimately re-raised.
        if chunk < requested:
            log.warning(
                "Earth Engine memory limit hit while collecting frame metadata; "
                "reduced the batch from %d to %d frame(s) per request "
                "(slower, but the run completes)", requested, chunk)

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
