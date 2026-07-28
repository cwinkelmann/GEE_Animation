"""Command-line entry point: config.yaml -> NDVI timelapse."""
from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

from . import anomaly, auth, aoi, collection, compositing, debug, inventory, metadata, render
from .config import RunConfig, ConfigError

DEFAULT_DEPS = types.SimpleNamespace(
    init=auth.init,
    parse=aoi.parse,
    build=collection.build,
    monthly_median=compositing.monthly_median,
    anomaly=anomaly.apply,
    metadata=metadata.write_frame_metadata,
    render=render.render,
    debug=debug.export_month_scenes,
    inventory=inventory.write_inventory,
)


def run(config_path: str, deps=DEFAULT_DEPS, inventory: bool = False) -> list[Path]:
    cfg = RunConfig.from_yaml(config_path)
    deps.init(cfg.project)
    frame_geom = deps.parse(cfg.frame_aoi)
    region_geom = deps.parse(cfg.region_aoi)
    if getattr(cfg, "debug_month", None):
        # Debug mode: export one month's input scenes + median, not the animation.
        return [deps.debug(cfg, frame_geom, region_geom, cfg.debug_month)]
    if inventory:
        # Inventory mode: list every candidate scene + rejection reason, not the
        # animation (request #7 — why smoother transitions aren't possible).
        return [deps.inventory(cfg, frame_geom, region_geom)]
    coll = deps.build(cfg, frame_geom, region_geom)
    frames = deps.monthly_median(coll, cfg)
    if not frames:
        raise RuntimeError(
            "No images found for the given AOI/date range/cloud filter."
        )
    frames = deps.anomaly(frames, cfg, frame_geom, region_geom, deps.build)
    if getattr(cfg, "metadata", False):
        deps.metadata(frames, cfg, region_geom)   # <out_dir>/metadata.db
    return deps.render(frames, cfg, geometry=frame_geom)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gee-animation")
    parser.add_argument("--config", required=True, help="path to config.yaml")
    parser.add_argument("--inventory", action="store_true",
                        help="write a per-scene usable/rejected inventory CSV "
                             "(<out_dir>/<name>_inventory.csv) instead of rendering")
    args = parser.parse_args(argv)
    kwargs = {"inventory": True} if args.inventory else {}
    try:
        paths = run(args.config, **kwargs)
    except (ConfigError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for p in paths:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
