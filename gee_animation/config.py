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
    # Render CRS (from render.crs). None => EPSG:4326 (plate carrée). "auto" => UTM
    # zone from the AOI centroid (square pixels; correct scale bar on both axes).
    crs: str = None
    # Screen-output controls (from render.*). preset => output long-edge (4k/1440p/
    # 1080p/720p or an int); aspect => canvas aspect (match/16:9/4:3/1:1/21:9);
    # upscale => interpolation used to enlarge the native-resolution frame.
    preset: str = None
    aspect: str = None
    upscale: str = "lanczos"
    # Anomaly rendering (from top-level `anomaly` / `baseline_years`). "climatology"
    # => per-pixel z-score vs baseline monthly climatology; "reference" => LST minus
    # ERA5 air temp (thermal only). None => raw values.
    anomaly: str = None
    baseline_years: list = None
    # Write per-frame AOI cloud fraction to <out_dir>/metadata.db (a reduceRegion per
    # frame, so opt-in).
    metadata: bool = False
    out_dir: str = "out"
    draw_region: bool = True
    # Optional Landsat mission whitelist (e.g. ["L8", "L9"]). None => sensor default
    # (thermal indices default to L8/L9; see collection.build).
    missions: list = None
    # Minimum scenes per monthly median; months with fewer are skipped (default 1 =
    # keep all non-empty months, but every frame is annotated with its scene count).
    min_scenes: int = 1
    # By default render is capped to the product's native resolution (no upsampling);
    # set True to allow a finer render (a warning still names the true native GSD).
    allow_upsample: bool = False

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
            anomaly = raw.get("anomaly")
            if anomaly and not raw.get("viz"):        # diverging default (subsumes P0-5)
                from .anomaly import ANOMALY_VIZ
                d_min, d_max, d_pal = ANOMALY_VIZ.get(anomaly, (-3.0, 3.0, ["#000000", "#ffffff"]))
            else:
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
                crs=render.get("crs"),
                preset=(str(render["preset"]) if render.get("preset") is not None else None),
                aspect=render.get("aspect"),
                upscale=str(render.get("upscale", "lanczos")),
                out_dir=str(raw.get("out_dir", "out")),
                draw_region=bool(raw.get("draw_region", True)),
                anomaly=anomaly,
                baseline_years=raw.get("baseline_years"),
                metadata=bool(raw.get("metadata", False)),
                missions=raw.get("missions"),
                min_scenes=int(raw.get("min_scenes", 1)),
                allow_upsample=bool(raw.get("allow_upsample", False)),
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
        if self.anomaly is not None:
            from .products import THERMAL_INDICES
            if self.anomaly not in ("climatology", "reference"):
                raise ConfigError(
                    f"unknown anomaly {self.anomaly!r}; use 'climatology' or 'reference'")
            if self.anomaly == "reference" and self.index not in THERMAL_INDICES:
                raise ConfigError("anomaly: reference is thermal-only (lst / lst_smw / lst_sharp)")
            if self.anomaly == "climatology" and not (
                    isinstance(self.baseline_years, (list, tuple)) and len(self.baseline_years) == 2):
                raise ConfigError("anomaly: climatology needs baseline_years: [firstYear, lastYear]")
        if self.preset or self.aspect or self.upscale != "lanczos":
            from .render import ASPECTS, PRESETS, UPSCALE_METHODS
            if self.preset and self.preset.lower() not in PRESETS and not str(self.preset).isdigit():
                raise ConfigError(
                    f"unknown render.preset {self.preset!r}; use one of {sorted(PRESETS)} or an int")
            if self.aspect and self.aspect != "match" and self.aspect not in ASPECTS:
                raise ConfigError(
                    f"unknown render.aspect {self.aspect!r}; use 'match' or one of {sorted(ASPECTS)}")
            if self.upscale not in UPSCALE_METHODS:
                raise ConfigError(
                    f"unknown render.upscale {self.upscale!r}; use one of {sorted(UPSCALE_METHODS)}")
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
