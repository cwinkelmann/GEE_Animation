"""Command-line entry point: config.yaml -> NDVI timelapse."""
from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

from . import auth, aoi, collection, compositing, render
from .config import RunConfig, ConfigError

DEFAULT_DEPS = types.SimpleNamespace(
    init=auth.init,
    parse=aoi.parse,
    build=collection.build,
    monthly_median=compositing.monthly_median,
    render=render.render,
)


def run(config_path: str, deps=DEFAULT_DEPS) -> list[Path]:
    cfg = RunConfig.from_yaml(config_path)
    deps.init(cfg.project)
    geometry = deps.parse(cfg.aoi)
    coll = deps.build(cfg, geometry)
    frames = deps.monthly_median(coll, cfg)
    if not frames:
        raise RuntimeError(
            "No images found for the given AOI/date range/cloud filter."
        )
    return deps.render(frames, cfg, geometry=geometry)


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
