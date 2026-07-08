"""Run configuration model: load and validate YAML into a RunConfig."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

SUPPORTED_SENSORS = {"sentinel2"}
SUPPORTED_CADENCES = {"monthly"}


class ConfigError(ValueError):
    """Raised when a run configuration is invalid."""


@dataclass
class RunConfig:
    name: str
    project: str
    aoi: dict
    start: str
    end: str
    sensor: str
    cadence: str
    max_cloud_percent: float
    ndvi_min: float
    ndvi_max: float
    palette: list[str]
    fps: int
    scale: float
    dimensions: int
    out_dir: str = "out"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RunConfig":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        try:
            ndvi = raw["ndvi"]
            render = raw["render"]
            cfg = cls(
                name=str(raw["name"]),
                project=str(raw["project"]),
                aoi=dict(raw["aoi"] or {}),
                start=str(raw["start"]),
                end=str(raw["end"]),
                sensor=str(raw["sensor"]),
                cadence=str(raw["cadence"]),
                max_cloud_percent=float(raw["max_cloud_percent"]),
                ndvi_min=float(ndvi["min"]),
                ndvi_max=float(ndvi["max"]),
                palette=list(ndvi["palette"]),
                fps=int(render["fps"]),
                scale=float(render["scale"]),
                dimensions=int(render["dimensions"]),
                out_dir=str(raw.get("out_dir", "out")),
            )
        except KeyError as exc:
            raise ConfigError(f"missing required config key: {exc}") from exc
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.sensor not in SUPPORTED_SENSORS:
            raise ConfigError(
                f"unsupported sensor {self.sensor!r}; supported: {sorted(SUPPORTED_SENSORS)}"
            )
        if self.cadence not in SUPPORTED_CADENCES:
            raise ConfigError(
                f"unsupported cadence {self.cadence!r}; supported: {sorted(SUPPORTED_CADENCES)}"
            )
        if not (self.aoi.get("bbox") or self.aoi.get("geojson")):
            raise ConfigError("aoi must define either 'bbox' or 'geojson'")
        try:
            start = date.fromisoformat(self.start)
            end = date.fromisoformat(self.end)
        except ValueError as exc:
            raise ConfigError(f"start/end must be ISO dates: {exc}") from exc
        if end <= start:
            raise ConfigError(f"end ({self.end}) must be after start ({self.start})")
        if self.ndvi_max <= self.ndvi_min:
            raise ConfigError("ndvi.max must be greater than ndvi.min")
        if not self.palette:
            raise ConfigError("ndvi.palette must be non-empty")
