"""Per-candidate-scene inventory: which scenes fed each period, and why the rest
were rejected.

`compositing.composite` only surfaces the scenes that survive cloud filtering
(`Frame.n_scenes`) — the rejected scenes, and why each was rejected, are invisible.
This answers the stakeholder-facing "why can't the animation be smoother" question by
listing *every* candidate scene per period, alongside a human-readable rejection
reason (or "" if it was used).
"""
from __future__ import annotations

import csv
import logging
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

import ee

from .collection import build as _build
from .compositing import period_starts
from .products import get_product

log = logging.getLogger(__name__)

SceneRecord = namedtuple(
    "SceneRecord",
    "period_label date mission scene_cloud_pct region_cloud_pct usable reason")


def _period_label(iso_date: str, periods) -> str | None:
    """The label of the (label, start, end) period `iso_date` falls in, or None if
    it's outside every period (shouldn't happen — scenes are already date-filtered
    to [cfg.start, cfg.end) by `collection.build`)."""
    for label, p_start, p_end in periods:
        if p_start <= iso_date < p_end:
            return label
    return None


def _judge(sensor, cfg, scene_cloud_pct, region_cloud_pct) -> tuple[bool, str]:
    """(usable, reason) for one scene, in the same order `collection.build` filters:
    the coarse scene-level cloud property first, then the in-region cloud fraction.
    Mirrors real Earth Engine `Filter.lt` semantics, where a missing (null) property
    fails the filter rather than passing it."""
    if (sensor.scene_cloud_property is not None and scene_cloud_pct is not None
            and scene_cloud_pct > cfg.max_cloud_percent):
        return False, f"scene cloud {scene_cloud_pct:.0f}% > {cfg.max_cloud_percent:.0f}%"
    if region_cloud_pct is None:
        return False, "region cloud fraction unavailable (scene fully masked)"
    if region_cloud_pct > cfg.region_max_cloud_percent:
        return False, f"region cloud {region_cloud_pct:.0f}% > {cfg.region_max_cloud_percent:.0f}%"
    return True, ""


def scene_inventory(cfg, frame_geom, region_geom, build=_build, ee_module=ee) -> list[SceneRecord]:
    """Every candidate scene in [cfg.start, cfg.end), unfiltered, judged against
    cfg.max_cloud_percent / cfg.region_max_cloud_percent.

    Builds the candidate collection with `apply_cloud_filters=False` (so rejected
    scenes survive to be listed) and pulls every scene's timestamp, scene-level cloud
    property and in-region cloud fraction in ONE `aggregate_array(...).getInfo()`
    round trip (via `ee.Dictionary(...).getInfo()`) — never one getInfo() per scene,
    which would not scale to a multi-year inventory.
    """
    sensor, _ = get_product(cfg.sensor, cfg.index)
    coll = build(cfg, frame_geom, region_geom, apply_cloud_filters=False, ee_module=ee_module)
    periods = period_starts(cfg.start, cfg.end, getattr(cfg, "cadence", "monthly"))

    # Batch every per-scene array we need into one ee.Dictionary so a single
    # getInfo() resolves all of them. scene_cloud/mission are only requested when
    # the sensor actually carries that property (MODIS has no scene_cloud_property;
    # only landsat's collection() tags a "mission" property per scene).
    props = {
        "time": coll.aggregate_array("system:time_start"),
        "region_cloud": coll.aggregate_array("region_cloud_fraction"),
    }
    if sensor.scene_cloud_property is not None:
        props["scene_cloud"] = coll.aggregate_array(sensor.scene_cloud_property)
    if sensor.name == "landsat":
        props["mission"] = coll.aggregate_array("mission")
    data = ee_module.Dictionary(props).getInfo()

    times = data["time"]
    region_clouds = data["region_cloud"]
    scene_clouds = data.get("scene_cloud")
    missions = data.get("mission")

    records: list[SceneRecord] = []
    for i, t in enumerate(times):
        iso = datetime.fromtimestamp(t / 1000, tz=timezone.utc).date().isoformat()
        label = _period_label(iso, periods)
        if label is None:
            continue
        scene_cloud = None if scene_clouds is None else scene_clouds[i]
        region_frac = region_clouds[i]
        region_cloud_pct = None if region_frac is None else round(region_frac * 100, 1)
        mission = missions[i] if missions is not None else sensor.name
        usable, reason = _judge(sensor, cfg, scene_cloud, region_cloud_pct)
        records.append(SceneRecord(
            period_label=label, date=iso, mission=mission,
            scene_cloud_pct=(None if scene_cloud is None else round(scene_cloud, 1)),
            region_cloud_pct=region_cloud_pct, usable=usable, reason=reason))
    return records


def write_inventory(cfg, frame_geom, region_geom, build=_build, ee_module=ee) -> Path:
    """`scene_inventory(...)` written to `<out_dir>/<name>_inventory.csv`, plus a
    per-period usable-scene summary logged at INFO, e.g. "2022-05: 7 scenes, 2 usable".

    Every period in [cfg.start, cfg.end) is logged, including ones with zero
    candidate scenes — those gaps are exactly what makes an animation choppy.
    """
    records = scene_inventory(cfg, frame_geom, region_geom, build=build, ee_module=ee_module)
    cadence = getattr(cfg, "cadence", "monthly")
    counts: dict[str, list[int]] = {}
    for r in records:
        n_usable = counts.setdefault(r.period_label, [0, 0])
        n_usable[0] += 1
        n_usable[1] += int(r.usable)
    for label, _p_start, _p_end in period_starts(cfg.start, cfg.end, cadence):
        n, usable = counts.get(label, [0, 0])
        log.info("%s: %d scenes, %d usable", label, n, usable)

    out_path = Path(cfg.out_dir) / f"{cfg.name}_inventory.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SceneRecord._fields)
        writer.writerows(records)
    return out_path
