"""Command-line entry point: config.yaml -> NDVI timelapse."""
from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

from . import anomaly, auth, aoi, collection, compositing, render
from .config import RunConfig, ConfigError

DEFAULT_DEPS = types.SimpleNamespace(
    init=auth.init,
    parse=aoi.parse,
    build=collection.build,
    monthly_median=compositing.monthly_median,
    anomaly=anomaly.apply,
    render=render.render,
)


def run(config_path: str, deps=DEFAULT_DEPS) -> list[Path]:
    cfg = RunConfig.from_yaml(config_path)
    deps.init(cfg.project)
    frame_geom = deps.parse(cfg.frame_aoi)
    region_geom = deps.parse(cfg.region_aoi)
    coll = deps.build(cfg, frame_geom, region_geom)
    frames = deps.monthly_median(coll, cfg)
    if not frames:
        raise RuntimeError(
            "No images found for the given AOI/date range/cloud filter."
        )
    frames = deps.anomaly(frames, cfg, frame_geom, region_geom, deps.build)
    return deps.render(frames, cfg, geometry=frame_geom)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gee-animation")
    parser.add_argument("--config", required=True, help="path to config.yaml")
    args = parser.parse_args(argv)
    try:
        paths = run(args.config)
    except (ConfigError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for p in paths:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
