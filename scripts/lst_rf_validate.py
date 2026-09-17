#!/usr/bin/env python3
"""Pre-registered validation of random-forest thermal sharpening (lst_rf).

Degrade-and-recover test (no finer truth than 100 m exists): real Landsat LST at
100 m is aggregated to 300 m, then sharpened back to 100 m by three methods on
identical inputs, and each result is scored against the real 100 m field:

  nearest  no sharpening — the 300 m field resampled (floor)
  linear   TsHARP: LST ~ a·NIRv + b at 300 m, applied at 100 m, coarse residual back
  rf       random forest on {ndvi, nirv, ndbi, mndwi, dem}, same residual step

Gate (fixed in docs/superpowers/plans/2026-09-17-lst-rf-sharpening.md before any
run): rf ships only if its frame-wide RMSE is ≥ 0.2 K below linear in ≥ 6 of 8
months and never above nearest. Writes out/lst_rf_validate.csv and prints a verdict.

    python scripts/lst_rf_validate.py [--config config/r12_lst_pretty_10yr.yaml]
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import math
from datetime import date
from pathlib import Path

import ee

from gee_animation import aoi, auth, collection, sharpen
from gee_animation.config import RunConfig
from gee_animation.products import INDEX_BAND

MONTHS = ["2018-05", "2018-08", "2019-10", "2020-03",
          "2022-06", "2023-09", "2025-02", "2026-07"]
COARSE_M, FINE_M = 300, 100          # the same 3x ratio production applies at 100 -> ~30
GATE_MARGIN_K, GATE_MONTHS = 0.2, 6
METHODS = ("nearest", "linear", "rf")


def _month_range(ym: str) -> tuple[str, str]:
    y, m = (int(v) for v in ym.split("-"))
    nxt = date(y + (m == 12), (m % 12) + 1, 1)
    return f"{ym}-01", nxt.isoformat()


def _stats(diff, zone_geom, proj):
    sq_ab = diff.pow(2).rename("sq").addBands(diff.abs().rename("ab"))
    return sq_ab.reduceRegion(ee.Reducer.mean(), geometry=zone_geom, scale=FINE_M,
                              crs=proj, bestEffort=True, maxPixels=int(1e9))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/r12_lst_pretty_10yr.yaml")
    ap.add_argument("--out", default="out/lst_rf_validate.csv")
    a = ap.parse_args()

    cfg = RunConfig.from_yaml(a.config)
    cfg = dataclasses.replace(cfg, index="lst", smooth=None,
                              start="2017-01-01", end="2026-09-01", pool_years=None)
    auth.init(cfg.project)
    frame = aoi.parse(cfg.frame_aoi)
    region = aoi.parse(cfg.region_aoi)
    coll = collection.build(cfg, frame, region)       # LST in °C on INDEX, filtered like a run
    b = cfg.frame_aoi["bbox"]
    proj = ee.Projection(sharpen._utm_epsg((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
    train_region = frame.buffer(sharpen.TRAIN_BUFFER_M)
    pad = sharpen.S2_PAD_DAYS

    rows = []
    for ym in MONTHS:
        s, e = _month_range(ym)
        mc = coll.filterDate(s, e)
        n = mc.size().getInfo()
        if n == 0:
            print(f"{ym}: no Landsat scene, skipped", flush=True)
            continue
        lst = mc.select(INDEX_BAND).median()
        truth = sharpen._on_grid(lst, proj, sharpen.LST_NATIVE_M, FINE_M).rename("lst")
        coarse = sharpen._on_grid(lst, proj, sharpen.LST_NATIVE_M, COARSE_M).rename(INDEX_BAND)
        preds = sharpen.s2_predictors(ee.Date(s).advance(-pad, "day"),
                                      ee.Date(e).advance(pad, "day"), train_region)
        common = dict(proj=proj, coarse_m=COARSE_M, fine_m=FINE_M, region=train_region,
                      lst_native_m=COARSE_M)     # `coarse` is already on the 300 m grid
        outputs = {
            "nearest": coarse,
            "linear": sharpen.linear_sharpen(coarse, preds, **common),
            "rf": sharpen.rf_sharpen(coarse, preds, **common),
        }
        combined = ee.Dictionary({})
        for name, img in outputs.items():
            d = img.select(INDEX_BAND).rename("lst").subtract(truth)
            combined = combined.combine(_stats(d, frame, proj).rename(
                ["sq", "ab"], [f"{name}_frame_sq", f"{name}_frame_ab"]))
            combined = combined.combine(_stats(d, region, proj).rename(
                ["sq", "ab"], [f"{name}_region_sq", f"{name}_region_ab"]))
            intra = (img.select(INDEX_BAND).subtract(coarse.select(INDEX_BAND)).abs()
                     .rename("ic").reduceRegion(ee.Reducer.percentile([99]), geometry=frame,
                                                scale=FINE_M, crs=proj, bestEffort=True,
                                                maxPixels=int(1e9)))
            combined = combined.combine(intra.rename(["ic"], [f"{name}_p99_intracell"]))  # one percentile keeps the band name
        vals = combined.getInfo()
        for name in METHODS:
            rows.append({
                "month": ym, "n_scenes": n, "method": name,
                "rmse_frame": math.sqrt(vals[f"{name}_frame_sq"]),
                "mae_frame": vals[f"{name}_frame_ab"],
                "rmse_region": math.sqrt(vals[f"{name}_region_sq"]),
                "mae_region": vals[f"{name}_region_ab"],
                "p99_intracell": vals[f"{name}_p99_intracell"],
            })
        line = "  ".join(f"{r['method']}: {r['rmse_frame']:.3f} K" for r in rows[-3:])
        print(f"{ym} ({n} scene(s))  frame RMSE  {line}", flush=True)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out}")

    by_month = {}
    for r in rows:
        by_month.setdefault(r["month"], {})[r["method"]] = r["rmse_frame"]
    wins = sum(1 for m in by_month.values() if m["rf"] <= m["linear"] - GATE_MARGIN_K)
    never_worse = all(m["rf"] <= m["nearest"] for m in by_month.values())
    passed = wins >= GATE_MONTHS and never_worse
    print(f"gate: rf beats linear by >= {GATE_MARGIN_K} K in {wins}/{len(by_month)} months "
          f"(need {GATE_MONTHS}); never worse than nearest: {never_worse} -> "
          f"{'PASS' if passed else 'FAIL'}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
