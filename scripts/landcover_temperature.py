#!/usr/bin/env python3
"""Surface temperature per Dynamic World land-cover class, over time.

Joins two products that are normally rendered separately: the classified land
cover (Dynamic World, 10 m) and Landsat surface temperature. For each period it
assigns every pixel a class, then takes the mean LST within each class — so the
question "how much cooler is forest than cropland, and does that change across
the years" gets a number rather than an impression.

    python scripts/landcover_temperature.py --config config/wne_lst_summer_lst.yaml
    python scripts/landcover_temperature.py --cadence quarterly --scale 100

Writes out/landcover_temperature.csv and out/plots/landcover_temperature.png.

Caveats worth carrying into any reading of the output:

  * Classes are resampled to the LST grid, so a cell takes the class that
    DOMINATES it. At 100 m a "built" cell still contains gardens and roads, and
    thin features (hedgerows, single streets) disappear into their surroundings.
    Class contrasts are therefore conservative — the real spread is wider.
  * A class present on only a handful of pixels gives a noisy mean; the CSV
    keeps the pixel count so thin classes can be filtered out.
  * Cloud-masked pixels are excluded, so a cloudy period reduces over whatever
    stayed visible, not over the whole class.
"""
from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import ee

from gee_animation import auth, aoi as aoi_mod, collection as coll_mod
from gee_animation.compositing import period_starts
from gee_animation.config import RunConfig
from gee_animation.compositing import _SPLIT_HINTS
from gee_animation.products import _DW_CLASSES, _DW_PROB_BANDS, INDEX_BAND

ROOT = Path(__file__).resolve().parent.parent
CLASS_NAME = {v: n for v, n, _h in _DW_CLASSES}
CLASS_COLOUR = {v: h for v, _n, h in _DW_CLASSES}

#: Periods resolved per getInfo. Same reasoning as metadata.FRAME_STATS_CHUNK:
#: one request per period is needlessly slow, one request for all of them
#: exceeds Earth Engine's budget.
CHUNK = 8


def dw_class_image(dw_collection, start, end, ee_module=ee):
    """The dominant land-cover class per pixel for [start, end)."""
    probs = dw_collection.filterDate(start, end).select(_DW_PROB_BANDS).mean()
    return probs.toArray().arrayArgmax().arrayGet([0]).rename("lc")


def periods_with_thermal(lst_coll, periods, ee_module=ee):
    """Drop periods with no usable thermal scene.

    A median over an empty collection yields an image with NO bands, so stacking
    it with the class layer gives a one-band image and `Reducer.group` fails with
    "Need 2 bands". One round trip up front is cheaper than discovering that per
    period — and it is the same bucket-the-timestamps idiom compositing uses.
    """
    millis = lst_coll.aggregate_array("system:time_start").getInfo()
    stamps = sorted(datetime.fromtimestamp(t / 1000, tz=timezone.utc).date().isoformat()
                    for t in millis)
    kept = []
    for label, start, end in periods:
        if any(start <= d < end for d in stamps):
            kept.append((label, start, end))
    dropped = len(periods) - len(kept)
    if dropped:
        print(f"  {dropped} period(s) have no thermal scene and are skipped")
    return kept


def period_stats(lst_coll, dw_coll, periods, region, scale, ee_module=ee):
    """{label: {class_value: (mean, count)}} for each period, batched."""
    out = {}
    i = 0
    chunk = CHUNK
    while i < len(periods):
        batch = periods[i:i + chunk]
        exprs = {}
        for label, start, end in batch:
            lst = lst_coll.filterDate(start, end).select(INDEX_BAND).median()
            stacked = lst.addBands(dw_class_image(dw_coll, start, end, ee_module))
            exprs[label] = stacked.reduceRegion(
                reducer=(ee_module.Reducer.mean()
                         .combine(ee_module.Reducer.count(), sharedInputs=True)
                         .group(groupField=1, groupName="lc")),
                geometry=region, scale=scale, bestEffort=True, maxPixels=int(1e9))
        try:
            data = ee_module.Dictionary(exprs).getInfo()
        except Exception as exc:                       # noqa: BLE001
            splittable = any(h in str(exc).lower() for h in _SPLIT_HINTS)
            if chunk == 1 or not splittable:
                # A structural error (a malformed image, a missing band) repeats
                # identically at every batch size; only size/timeout failures are
                # worth retrying smaller.
                raise
            chunk = max(1, chunk // 2)
            print(f"  (batch too heavy: {exc}; retrying {chunk} period(s) at a time)")
            continue
        for label, payload in data.items():
            groups = (payload or {}).get("groups") or []
            out[label] = {int(g["lc"]): (g.get("mean"), g.get("count"))
                          for g in groups if g.get("lc") is not None}
        print(f"  {i + len(batch)}/{len(periods)} periods", flush=True)
        i += len(batch)
    return out


def write_csv(stats, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["period", "class_value", "class_name", "mean_lst_c", "pixels"])
        for label in sorted(stats):
            for value, (mean, count) in sorted(stats[label].items()):
                w.writerow([label, value, CLASS_NAME.get(value, f"class{value}"),
                            "" if mean is None else round(float(mean), 3),
                            int(count or 0)])
    return dest


def plot(stats, dest: Path, min_pixels: int):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = sorted(stats)
    series = defaultdict(lambda: ([], []))
    for x, label in enumerate(labels):
        for value, (mean, count) in stats[label].items():
            if mean is None or (count or 0) < min_pixels:
                continue
            series[value][0].append(x)
            series[value][1].append(float(mean))

    bg, ink, muted, grid = "#101418", "#e8ecef", "#9aa7b0", "#2c3540"
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor(bg)
    ax.set_facecolor("#1a2027")
    ax.grid(True, color=grid, linewidth=0.6, alpha=0.7)
    for value, (xs, ys) in sorted(series.items()):
        ax.plot(xs, ys, label=CLASS_NAME.get(value, str(value)),
                color=CLASS_COLOUR.get(value, "#888888"), linewidth=1.5)
    ax.set_title("Surface temperature by Dynamic World land-cover class",
                 color=ink, fontsize=13, loc="left", pad=10)
    ax.set_ylabel("mean LST °C", color=ink)
    step = max(1, len(labels) // 12)
    ax.set_xticks(range(0, len(labels), step))
    ax.set_xticklabels([labels[i] for i in range(0, len(labels), step)],
                       rotation=45, ha="right")
    ax.tick_params(colors=muted, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(grid)
    leg = ax.legend(loc="upper left", framealpha=0, fontsize=9, ncol=2)
    for t in leg.get_texts():
        t.set_color(ink)
    fig.text(0.008, 0.015,
             f"classes resampled to the LST grid (dominant class per cell) · "
             f"classes with <{min_pixels} pixels omitted · cloud-masked pixels excluded",
             color=muted, fontsize=7.5)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=140, facecolor=bg, bbox_inches="tight")
    plt.close(fig)
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/wne_lst_summer_lst.yaml",
                    help="supplies the AOI, dates and thermal index")
    ap.add_argument("--cadence", default=None, help="override the config cadence")
    ap.add_argument("--scale", type=float, default=100.0,
                    help="metres; default 100 = Landsat thermal native")
    ap.add_argument("--min-pixels", type=int, default=25,
                    help="omit a class from the chart below this pixel count")
    a = ap.parse_args()

    cfg = RunConfig.from_yaml(a.config)
    cadence = a.cadence or cfg.cadence
    auth.init(cfg.project)
    frame_geom = aoi_mod.parse(cfg.frame_aoi)
    region_geom = aoi_mod.parse(cfg.region_aoi)

    # The thermal series, exactly as the animation builds it (same masking).
    lst_coll = coll_mod.build(cfg, frame_geom, region_geom)
    # Dynamic World over the same AOI and dates.
    dw_cfg = RunConfig.from_yaml(a.config)
    dw_cfg.sensor, dw_cfg.index = "dynamicworld", "landcover"
    dw_cfg.max_cloud_percent, dw_cfg.region_max_cloud_percent = 100.0, 100.0
    dw_cfg.pool_years = None
    dw_raw = ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1") \
        .filterDate(cfg.start, cfg.end).filterBounds(frame_geom)

    periods = period_starts(cfg.start, cfg.end, cadence)
    print(f"{cfg.name}: {len(periods)} {cadence} periods, {a.scale:g} m grid")
    periods = periods_with_thermal(lst_coll, periods)
    stats = period_stats(lst_coll, dw_raw, periods, frame_geom, a.scale)

    csv_path = write_csv(stats, ROOT / "out" / "landcover_temperature.csv")
    png_path = plot(stats, ROOT / "out" / "plots" / "landcover_temperature.png",
                    a.min_pixels)
    print(f"wrote {csv_path.relative_to(ROOT)}")
    print(f"wrote {png_path.relative_to(ROOT)}")

    # A one-line summary of the headline contrast, if both classes are present.
    warm = defaultdict(list)
    for label, per_class in stats.items():
        if not label[5:7].isdigit() or int(label[5:7]) not in (6, 7, 8):
            continue
        for value, (mean, count) in per_class.items():
            if mean is not None and (count or 0) >= a.min_pixels:
                warm[value].append(float(mean))
    if warm:
        print("\nsummer means by class:")
        for value in sorted(warm, key=lambda v: -sum(warm[v]) / len(warm[v])):
            vals = warm[value]
            print(f"  {CLASS_NAME.get(value, value):22s} "
                  f"{sum(vals)/len(vals):5.1f} °C  ({len(vals)} periods)")


if __name__ == "__main__":
    main()
