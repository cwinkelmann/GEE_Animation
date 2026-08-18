#!/usr/bin/env python3
"""Write out/manifest.json — an index of every rendered artifact per run.

Catalogues the four artifact kinds the pipeline produces (annotated frames, raw
map frames, GeoTIFFs, videos) alongside the exact server-side parameters that
produced them, so a run can be reproduced or extended without re-deriving
anything. Run after a render sweep:

    python scripts/make_manifest.py
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import yaml

from gee_animation.config import RunConfig

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
PERIOD = re.compile(r"_(\d{4}-(?:\d{2}(?:-\d{2})?|Q[1-4]))\.(?:png|tif)$")


def periods_in(directory: Path, pattern: str) -> list[str]:
    if not directory.is_dir():
        return []
    found = {m.group(1) for p in directory.glob(pattern)
             if (m := PERIOD.search(p.name))}
    return sorted(found)


def main() -> None:
    runs = []
    for cfg_path in sorted(ROOT.glob("config/wne_cinema_*.yaml")):
        cfg = RunConfig.from_yaml(cfg_path)
        raw = yaml.safe_load(cfg_path.read_text())
        img_dir = OUT / "images" / cfg.name
        raw_dir = OUT / "images" / f"{cfg.name}_raw"
        tif_dir = OUT / "geotiffs" / cfg.name
        video = OUT / "videos" / f"{cfg.name}.mp4"

        runs.append({
            "name": cfg.name,
            "config": str(cfg_path.relative_to(ROOT)),
            "artifacts": {
                "video": str(video.relative_to(ROOT)) if video.exists() else None,
                "video_bytes": video.stat().st_size if video.exists() else None,
                "annotated_frames": str(img_dir.relative_to(ROOT)) if img_dir.is_dir() else None,
                "raw_frames": str(raw_dir.relative_to(ROOT)) if raw_dir.is_dir() else None,
                "geotiffs": str(tif_dir.relative_to(ROOT)) if tif_dir.is_dir() else None,
            },
            "counts": {
                "annotated_frames": len(periods_in(img_dir, "*.png")),
                "raw_frames": len(periods_in(raw_dir, "*.png")),
                "geotiffs": len(periods_in(tif_dir, "*.tif")),
            },
            "periods": periods_in(img_dir, "*.png"),
            # Everything below changes what Earth Engine computes: identical
            # values mean a re-render is a pure cache hit.
            "server_side": {
                "sensor": cfg.sensor, "index": cfg.index, "cadence": cfg.cadence,
                "start": cfg.start, "end": cfg.end,
                "frame_bbox": (raw.get("aoi", {}).get("frame") or {}).get("bbox"),
                "region": raw.get("aoi", {}).get("region"),
                "viz": {"min": cfg.viz_min, "max": cfg.viz_max},
                "crs": cfg.crs, "scale": cfg.scale, "dimensions": cfg.dimensions,
                "mask_clouds": cfg.mask_clouds,
                "max_cloud_percent": cfg.max_cloud_percent,
                "region_max_cloud_percent": cfg.region_max_cloud_percent,
                "min_scenes": cfg.min_scenes,
                "pool_years": cfg.pool_years, "pool_strategy": cfg.pool_strategy,
                "missions": cfg.missions,
            },
            # Presentation only: changing these re-renders from cache, for free.
            "client_side": {
                "title": cfg.title, "subtitle": cfg.subtitle, "credit": cfg.credit,
                "palette": cfg.palette or None, "fps": cfg.fps,
                "interpolate": cfg.interpolate,
                "interpolate_mode": cfg.interpolate_mode,
                "preset": cfg.preset, "aspect": cfg.aspect, "quality": cfg.quality,
            },
        })

    manifest = {
        "generated": str(date.today()),
        "layout": {
            "out/videos/": "MP4 masters and 720p previews, flat",
            "out/images/<run>/": "annotated frames; <run>_raw/ holds map-only frames",
            "out/geotiffs/<run>/": "georeferenced float exports, native grid, run CRS",
        },
        "note": ("raw_frames are the map alone — no header, legend, scale bar, "
                 "credit or marker. GeoTIFFs hold real values, not colours."),
        "runs": runs,
    }
    path = OUT / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2))
    tot = sum(r["counts"]["geotiffs"] for r in runs)
    print(f"{path.relative_to(ROOT)}: {len(runs)} runs, {tot} geotiffs")


if __name__ == "__main__":
    main()
