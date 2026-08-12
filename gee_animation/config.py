"""Run configuration model: load and validate YAML into a RunConfig."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from .products import INDICES, get_product

log = logging.getLogger(__name__)

# semimonthly splits at the 1st/16th; 10day splits at the 1st/11th/21st (see
# compositing._SPLIT_DAYS — bins stay aligned to calendar months).
SUPPORTED_CADENCES = {"monthly", "semimonthly", "10day", "quarterly"}

# Cross-year "best month" pooling (see compositing.pooled_composite).
POOL_STRATEGIES = {"least_cloudy", "median", "gap_fill"}

# Frame-interpolation strategies (see render.interpolate / render.interpolate_mode).
INTERPOLATE_MODES = {"auto", "crossfade", "data"}


class ConfigError(ValueError):
    """Raised when a run configuration is invalid."""


#: Optional render flags that must be real YAML booleans (see _flag / validate).
_RENDER_FLAGS = ("gif", "frames")


def _opt_int(render: dict, key: str):
    """A `render.<key>` integer, or None when the key is absent/null.

    `int()` on a YAML string raises a bare `ValueError`, which `from_yaml`'s `except
    KeyError` does not catch — so a typo'd ``quality: abc`` escaped as an unhandled
    exception and `cli.main` printed a traceback instead of the one-line config error
    every other bad key gets. The message names both the key and the offending value,
    because "invalid literal for int()" on its own does not tell you *which* of a
    config's numbers is wrong.
    """
    value = render.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        # YAML `quality: true` would int() to 1 — the *worst* quality — with no
        # diagnostic; a bool here is always a config mistake, never a number.
        raise ConfigError(f"render.{key} must be a whole number, got {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"render.{key} must be a whole number, got {value!r}") from exc


def _flag(render: dict, key: str, default=True, *, scope: str = "render."):
    """A `render.<key>` boolean, or `default` when the key is absent.

    Deliberately not `bool(...)`: YAML turns a *quoted* ``gif: "false"`` into a
    non-empty string, and truthy-coercing it would silently keep writing the very
    output the user asked to skip. Anything that isn't a real boolean is rejected.

    `default` may be ``None`` — "not configured", which `render.gif` needs so that an
    interpolated run can default the GIF off while an explicit ``gif: true`` still
    wins. The absent case returns the default untouched; a value that *is* present is
    held to the same boolean rule either way.
    """
    if key not in render:
        return default
    value = render[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{scope}{key} must be true or false (got {value!r})")
    return value


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
    fps: float          # frames/sec; fractional allowed (e.g. 0.5 = 2 s per frame)
    scale: float
    dimensions: int
    # Render CRS (from render.crs). Default "auto" => UTM zone from the AOI
    # centroid (square pixels; correct scale bar on both axes). Explicit
    # crs: "EPSG:4326" restores the old plate-carrée behaviour (stretched at
    # latitude; not recommended).
    crs: str = "auto"
    # Screen-output controls (from render.*). preset => output long-edge (4k/1440p/
    # 1080p/720p or an int); aspect => canvas aspect (match/16:9/4:3/1:1/21:9);
    # upscale => interpolation used to enlarge the native-resolution frame.
    preset: str = None
    aspect: str = None
    upscale: str = "lanczos"
    # Which outputs render writes (from render.gif / render.frames). The MP4 is always
    # written; the GIF is a preview format and by far the slowest encode (~5 s of a
    # 24 s run), and the per-frame PNGs are ~6 s more. Both effectively default on, so
    # an existing config's deliverables are unchanged. See render.assemble_stream /
    # render._write_frames.
    # `gif` is three-state: None means "not configured", which `render()` resolves —
    # on for a normal run, off for an interpolated one (several hundred quantized
    # frames). Only an explicit True/False here can override that.
    gif: bool | None = None
    frames: bool = True
    # Frame header text (from top-level `title` / `subtitle`). `title` is the header's
    # large first line — what and where this animation is; it defaults to the index's
    # plain-language `products.Index.display_name` when unset, so a frame always says
    # what it shows. `subtitle` is the smaller second line, which it shares with the
    # provenance caveats (pooling / interpolation) — those always win the space (see
    # render._fit_header_line2). Purely client-side: neither reaches Earth Engine, so
    # both are in cache.CLIENT_SIDE_FIELDS and retitling a run is a cache hit.
    title: str = None
    subtitle: str = None
    # Attribution line drawn bottom-right (from top-level `credit`). None (default,
    # no key in YAML) => auto by sensor (see render._default_credit) — this is what
    # makes the Copernicus "Contains modified Copernicus Sentinel data <year>"
    # notice appear with zero configuration, since the licence requires it on
    # published sentinel2 products. A non-empty string overrides verbatim. An
    # *explicit* "" omits the line entirely — a conscious choice, not the default —
    # and validate() warns about it for sentinel2 so that omission is deliberate.
    credit: str = None
    # Anomaly rendering (from top-level `anomaly` / `baseline_years`). "climatology"
    # => per-pixel z-score vs baseline monthly climatology; "reference" => LST minus
    # ERA5 air temp (thermal only). None => raw values.
    anomaly: str = None
    baseline_years: list = None
    # Cross-year "best month" pooling (from top-level `pool_years` / `pool_strategy`).
    # [firstYear, lastYear] inclusive: each period is filled from the same calendar
    # period in ANY of those years, so a "2022-05" frame may show May 2021. Cosmetic
    # only — every frame is labelled with its source year. None => off (default).
    pool_years: list = None
    pool_strategy: str = "least_cloudy"
    # Per-pixel cloud masking (from top-level `mask_clouds`). True (default) masks
    # cloud/shadow pixels via the sensor's QA layer so they render neutral grey —
    # required for palette indices, where a colorized cloud would read as a real
    # low value (e.g. bare soil on NDVI). False keeps clouds in the imagery and is
    # only allowed for composites (rgb/cir), where real white clouds look natural
    # and nothing is painted over. Scene-level cloud filters are unaffected.
    mask_clouds: bool = True
    # Write per-frame AOI cloud fraction to <out_dir>/metadata.db (a reduceRegion per
    # frame, so opt-in).
    metadata: bool = False
    out_dir: str = "out"
    draw_region: bool = True
    # Region outline core width in px (from render.region_line_width). None => the
    # current max(2, h/430) behaviour (see render.draw_region).
    region_line_width: int = None
    # Optional Landsat mission whitelist (e.g. ["L8", "L9"]). None => sensor default
    # (thermal indices default to L8/L9; see collection.build).
    missions: list = None
    # Minimum scenes per monthly median; months with fewer are skipped (default 1 =
    # keep all non-empty months, but every frame is annotated with its scene count).
    min_scenes: int = 1
    # By default render is capped to the product's native resolution (no upsampling);
    # set True to allow a finer render (a warning still names the true native GSD).
    allow_upsample: bool = False
    # Debug: if set to "YYYY-MM", export that month's individual input scenes + the
    # median they collapse into (to <out_dir>/debug/<month>/) instead of the animation.
    debug_month: str = None
    # Concurrent thumbnail fetches (from render.workers). Each frame is an EE
    # compute + stream, so overlapping them dominates runtime; 4 is the measured
    # sweet spot (EE throttles beyond it). 1 => genuinely serial (debugging).
    workers: int = 4
    # On-disk cache of raw thumbnail bytes (from render.cache / render.cache_dir).
    # cache_dir None => $GEE_ANIMATION_CACHE_DIR, else the platform user cache dir
    # (never inside out/, which is the shared deliverable). See cache.py.
    cache: bool = True
    cache_dir: str = None
    # Generated frames inserted between observations so playback reads as motion
    # (from render.interpolate / render.interpolate_mode). 0 = off. "auto" picks
    # data-space interpolation for single-band indices and cross-fade for
    # composites, which arrive from EE already coloured.
    interpolate: int = 0
    interpolate_mode: str = "auto"
    # MP4 encode quality (from render.quality), 1 (smallest/worst) .. 10
    # (largest/best), passed straight through to imageio's ffmpeg writer. None
    # (default) => imageio's own default (currently 5) — the writer call is
    # unchanged from before this knob existed, so an existing config's output is
    # bit-for-bit the same. Untuned MP4s measured ~1 MB/frame in the audience
    # review; this is the deliberate trade-off knob for publishing (see
    # docs/publishing-animations.md's format-picker table).
    quality: int = None

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
            # Unlike title/subtitle, an explicit "" must survive as "" (not collapse
            # to None): "" is a distinct, deliberate "no credit line" choice that
            # render._default_credit and validate() both need to tell apart from
            # "not configured, pick the sensor default".
            if ("credit" in raw and raw["credit"] is not None
                    and not isinstance(raw["credit"], str)):
                # `credit: false` is falsy-but-not-"" and would silently collapse to
                # None — i.e. the automatic Copernicus line the user was trying to
                # turn OFF. Only the exact "" opts out; make the near-miss loud.
                raise ConfigError(
                    f"credit must be a string (got {raw['credit']!r}); use "
                    "credit: \"\" to omit the attribution line, or remove the key "
                    "for the automatic sensor credit")
            credit = (str(raw["credit"]) if raw.get("credit")
                     else ("" if "credit" in raw and raw["credit"] == "" else None))
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
                fps=float(render["fps"]),
                scale=float(render["scale"]),
                dimensions=int(render["dimensions"]),
                crs=render.get("crs") or "auto",
                preset=(str(render["preset"]) if render.get("preset") is not None else None),
                aspect=render.get("aspect"),
                upscale=str(render.get("upscale", "lanczos")),
                gif=_flag(render, "gif", default=None),
                frames=_flag(render, "frames"),
                out_dir=str(raw.get("out_dir", "out")),
                title=(str(raw["title"]) if raw.get("title") else None),
                subtitle=(str(raw["subtitle"]) if raw.get("subtitle") else None),
                credit=credit,
                # Strict bools, not bool(...): a quoted "false" is a non-empty string
                # and truthy-coercing it would silently invert the user's intent
                # (same trap _flag documents for gif/frames).
                draw_region=_flag(raw, "draw_region", scope=""),
                mask_clouds=_flag(raw, "mask_clouds", scope=""),
                region_line_width=(int(render["region_line_width"])
                                   if render.get("region_line_width") is not None else None),
                anomaly=anomaly,
                baseline_years=raw.get("baseline_years"),
                pool_years=raw.get("pool_years"),
                pool_strategy=str(raw.get("pool_strategy") or "least_cloudy"),
                metadata=_flag(raw, "metadata", default=False, scope=""),
                missions=raw.get("missions"),
                min_scenes=int(raw.get("min_scenes", 1)),
                allow_upsample=bool(raw.get("allow_upsample", False)),
                debug_month=(str(raw["debug_month"]) if raw.get("debug_month") else None),
                workers=int(render.get("workers", 4)),
                cache=bool(render.get("cache", True)),
                cache_dir=(str(render["cache_dir"]) if render.get("cache_dir") else None),
                interpolate=int(render.get("interpolate", 0) or 0),
                interpolate_mode=str(render.get("interpolate_mode") or "auto"),
                quality=_opt_int(render, "quality"),
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
        if not self.mask_clouds and not INDICES[self.index].composite:
            # A palette index colorizes every unmasked pixel through the ramp, so a
            # cloud left in the data renders as a plausible-looking real value
            # (bright cloud ~ low NDVI ~ bare soil). Composites show clouds as what
            # they are — white clouds — so only they may opt out of masking.
            raise ConfigError(
                f"mask_clouds: false is only supported for composite indices "
                f"(rgb/cir); {self.index!r} colorizes pixels through a palette, so "
                f"unmasked clouds would render as false data values")
        if self.credit == "" and self.sensor == "sentinel2":
            # An explicit empty credit suppresses the frame's only attribution line.
            # For sentinel2 that line is not decoration: the Copernicus licence
            # requires "Contains modified Copernicus Sentinel data <year>" on
            # published products, so omitting it must be a conscious choice, flagged
            # here, not a silent default.
            log.warning(
                "credit: \"\" omits the attribution line; the Copernicus licence "
                "requires \"Contains modified Copernicus Sentinel data <year>\" on "
                "published Sentinel-2 products — make sure that notice appears "
                "elsewhere if you suppress it here")
        if self.cadence not in SUPPORTED_CADENCES:
            raise ConfigError(
                f"unsupported cadence {self.cadence!r}; supported: {sorted(SUPPORTED_CADENCES)}"
            )
        if self.cadence in ("semimonthly", "10day") and self.sensor == "landsat":
            # Sub-monthly only: quarterly bins are *wider* than monthly, so the
            # 16-day repeat is a reason to prefer quarterly, not a warning case.
            log.warning(
                "cadence %r with sensor 'landsat': Landsat's 16-day repeat leaves most "
                "%s bins empty", self.cadence, self.cadence)
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
        if self.region_line_width is not None and self.region_line_width < 1:
            raise ConfigError("render.region_line_width must be >= 1")
        if self.workers < 1:
            raise ConfigError("render.workers must be >= 1")
        if self.interpolate < 0:
            raise ConfigError("render.interpolate must be >= 0")
        if self.interpolate_mode not in INTERPOLATE_MODES:
            raise ConfigError(
                f"unknown render.interpolate_mode {self.interpolate_mode!r}; "
                f"supported: {sorted(INTERPOLATE_MODES)}")
        if self.interpolate_mode == "data" and INDICES[self.index].composite:
            raise ConfigError(
                f"render.interpolate_mode: data needs a single-band index; "
                f"{self.index!r} is a composite — use crossfade (or auto)")
        if self.quality is not None and not 1 <= self.quality <= 10:
            raise ConfigError(
                f"render.quality must be between 1 and 10 (got {self.quality!r})")
        # Also checked here (not only in from_yaml) because the GUI and api.animate
        # build RunConfig directly and validate() is their only gate.
        for flag in _RENDER_FLAGS:
            value = getattr(self, flag)
            if flag == "gif" and value is None:
                continue          # "not configured" — resolved in render() (see above)
            if not isinstance(value, bool):
                raise ConfigError(
                    f"render.{flag} must be true or false (got {value!r})")
        if self.anomaly is not None:
            from .products import THERMAL_INDICES
            if self.cadence != "monthly":
                # anomaly divides a period mean by a *monthly* climatology σ; a
                # sub-monthly slice would silently produce inflated z-scores.
                raise ConfigError(
                    f"anomaly requires cadence: monthly (got {self.cadence!r}); "
                    "only monthly composites can be scored against a monthly climatology")
            if self.anomaly not in ("climatology", "reference"):
                raise ConfigError(
                    f"unknown anomaly {self.anomaly!r}; use 'climatology' or 'reference'")
            if self.anomaly == "reference" and self.index not in THERMAL_INDICES:
                raise ConfigError("anomaly: reference is thermal-only (lst / lst_smw / lst_sharp)")
            if self.anomaly == "climatology" and not (
                    isinstance(self.baseline_years, (list, tuple)) and len(self.baseline_years) == 2):
                raise ConfigError("anomaly: climatology needs baseline_years: [firstYear, lastYear]")
        if self.pool_strategy not in POOL_STRATEGIES:
            raise ConfigError(
                f"unknown pool_strategy {self.pool_strategy!r}; "
                f"use one of {sorted(POOL_STRATEGIES)}")
        if self.pool_years is not None:
            if self.anomaly is not None:
                # The climatology baseline is itself multi-year, so a pooled frame
                # would score a borrowed year against a mean that already contains it.
                raise ConfigError(
                    "pool_years cannot be combined with anomaly: the anomaly baseline "
                    "is itself multi-year, so a frame borrowed from another year would "
                    "be scored against a climatology that already includes it")
            if not (isinstance(self.pool_years, (list, tuple))
                    and len(self.pool_years) == 2):
                raise ConfigError("pool_years must be [firstYear, lastYear]")
            try:
                y0, y1 = int(self.pool_years[0]), int(self.pool_years[1])
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"pool_years must be two years: {exc}") from exc
            if y1 < y0:
                raise ConfigError(
                    f"pool_years last year ({y1}) must not precede the first ({y0})")
            if self.sensor == "landsat" and not self.missions:
                log.warning(
                    "pool_years with sensor 'landsat' and no `missions` whitelist: "
                    "L7/L8/L9 differ radiometrically and L7 is SLC-off, so pooled "
                    "frames can step between missions; set e.g. missions: [L8, L9]")
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


def pool_span(cfg) -> tuple[str, str] | None:
    """(start, end) covering every year in ``cfg.pool_years``, or None when
    cross-year pooling is off.

    ``collection.build`` widens its ``filterDate`` to this span so the pooled scenes
    exist at all; :func:`compositing.pooled_composite` then buckets them by calendar
    period.
    """
    years = getattr(cfg, "pool_years", None)
    if not years:
        return None
    y0, y1 = int(years[0]), int(years[-1])
    return f"{y0}-01-01", f"{y1 + 1}-01-01"
