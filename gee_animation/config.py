"""Run configuration model: load and validate YAML into a RunConfig."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from .products import INDICES, get_product

SUPPORTED_CADENCES = {"monthly"}


class ConfigError(ValueError):
    """Raised when a run configuration is invalid."""


@dataclass
class RunConfig:
    name: str
    project: str
    frame_aoi: dict
    region_aoi: dict
    start: str
    end: str
    sensor: str
    index: str
    cadence: str
    max_cloud_percent: float
    region_max_cloud_percent: float
    viz_min: float
    viz_max: float
    palette: list[str]
    fps: int
    scale: float
    dimensions: int
    out_dir: str = "out"
    draw_region: bool = True
    # Optional Landsat mission whitelist (e.g. ["L8", "L9"]). None => sensor default
    # (thermal indices default to L8/L9; see collection.build).
    missions: list = None
    # Minimum scenes per monthly median; months with fewer are skipped (default 1 =
    # keep all non-empty months, but every frame is annotated with its scene count).
    min_scenes: int = 1

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RunConfig":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        try:
            render = raw["render"]
            aoi = raw["aoi"] or {}
            sensor = str(raw["sensor"])
            index = str(raw.get("index", "ndvi"))
            viz = dict(raw.get("viz") or {})
            spec = INDICES.get(index)
            d_min, d_max, d_pal = spec.default_viz if spec else (0.0, 1.0, ["#000000", "#ffffff"])
            cfg = cls(
                name=str(raw["name"]),
                project=str(raw["project"]),
                frame_aoi=dict(aoi["frame"] or {}),
                region_aoi=dict(aoi["region"] or {}),
                start=str(raw["start"]),
                end=str(raw["end"]),
                sensor=sensor,
                index=index,
                cadence=str(raw["cadence"]),
                max_cloud_percent=float(raw["max_cloud_percent"]),
                region_max_cloud_percent=float(aoi.get("region_max_cloud_percent", 10)),
                viz_min=float(viz.get("min", d_min)),
                viz_max=float(viz.get("max", d_max)),
                # fall back to the index default only when 'palette' is absent (an
                # explicit [] is preserved so validate() still rejects it); the
                # composite default is None -> [].
                palette=list(_p) if (_p := viz.get("palette", d_pal)) is not None else [],
                fps=int(render["fps"]),
                scale=float(render["scale"]),
                dimensions=int(render["dimensions"]),
                out_dir=str(raw.get("out_dir", "out")),
                draw_region=bool(raw.get("draw_region", True)),
                missions=raw.get("missions"),
                min_scenes=int(raw.get("min_scenes", 1)),
            )
        except KeyError as exc:
            raise ConfigError(f"missing required config key: {exc}") from exc
        cfg.validate()
        return cfg

    def validate(self) -> None:
        try:
            get_product(self.sensor, self.index)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if self.cadence not in SUPPORTED_CADENCES:
            raise ConfigError(
                f"unsupported cadence {self.cadence!r}; supported: {sorted(SUPPORTED_CADENCES)}"
            )
        for label, a in (("frame", self.frame_aoi), ("region", self.region_aoi)):
            if not (a.get("bbox") or a.get("geojson") or a.get("shapefile")):
                raise ConfigError(
                    f"aoi.{label} must define 'bbox', 'geojson', or 'shapefile'"
                )
        if not 0 <= self.region_max_cloud_percent <= 100:
            raise ConfigError("region_max_cloud_percent must be between 0 and 100")
        if self.missions is not None:
            valid = {"L4", "L5", "L7", "L8", "L9"}
            bad = [m for m in self.missions if m not in valid]
            if bad:
                raise ConfigError(
                    f"unknown missions {bad}; valid Landsat missions: {sorted(valid)}")
        if self.min_scenes < 1:
            raise ConfigError("min_scenes must be >= 1")
        try:
            start = date.fromisoformat(self.start)
            end = date.fromisoformat(self.end)
        except ValueError as exc:
            raise ConfigError(f"start/end must be ISO dates: {exc}") from exc
        if end <= start:
            raise ConfigError(f"end ({self.end}) must be after start ({self.start})")
        if self.viz_max <= self.viz_min:
            raise ConfigError("viz.max must be greater than viz.min")
        spec = INDICES.get(self.index)
        if not (spec and spec.composite) and not self.palette:
            # composites (rgb/cir) render 3 real bands, so they need no palette
            raise ConfigError("viz.palette must be non-empty")
