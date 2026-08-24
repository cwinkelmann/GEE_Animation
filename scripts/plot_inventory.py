#!/usr/bin/env python
"""Plot a Sentinel-2 (or any sensor) scene inventory: how many scenes exist per
month and how cloudy they are over the AOI.

Input is the CSV written by `gee-animation --config ... --inventory`
(gee_animation/inventory.py), which lists EVERY candidate scene per period --
including the ones the cloud filters rejected, with the reason.

    python scripts/plot_inventory.py out/wne_s2_inventory_inventory.csv

Three panels:
  A  stacked bars over time: usable vs rejected scenes per month
  B  year x calendar-month heatmap of usable scene count (the gap map)
  C  per-calendar-month distribution of in-AOI cloud cover
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                       # noqa: E402

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def load(path: Path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["usable"] = str(r["usable"]).strip().lower() in ("true", "1")
        for k in ("scene_cloud_pct", "region_cloud_pct"):
            try:
                r[k] = float(r[k])
            except (TypeError, ValueError):
                r[k] = float("nan")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    rows = load(args.csv)
    if not rows:
        raise SystemExit(f"{args.csv} has no scene records")

    per = defaultdict(lambda: [0, 0])            # label -> [candidates, usable]
    cloud_by_cal = defaultdict(list)             # "01".."12" -> region cloud %
    for r in rows:
        lab = r["period_label"]
        per[lab][0] += 1
        per[lab][1] += int(r["usable"])
        if r["region_cloud_pct"] == r["region_cloud_pct"]:
            cloud_by_cal[lab[5:7]].append(r["region_cloud_pct"])

    # every month in range, so empty months show as real gaps
    ys = sorted({int(l[:4]) for l in per})
    labels = [f"{y:04d}-{m:02d}" for y in range(min(ys), max(ys) + 1)
              for m in range(1, 13)]
    first = min(per); last = max(per)
    labels = [l for l in labels if first <= l <= last]
    cand = np.array([per[l][0] for l in labels])
    good = np.array([per[l][1] for l in labels])
    x = np.arange(len(labels))

    fig = plt.figure(figsize=(15, 11))
    gs = fig.add_gridspec(3, 1, height_ratios=[1.1, 1.3, 1.0], hspace=0.42)

    # --- A: scenes per month over time ------------------------------------
    ax = fig.add_subplot(gs[0])
    ax.bar(x, cand - good, bottom=good, color="#d9d9d9",
           label=f"rejected by cloud filters (n={int((cand-good).sum())})")
    ax.bar(x, good, color="#2b8cbe", label=f"usable (n={int(good.sum())})")
    ax.set_ylabel("Sentinel-2 scenes")
    ax.set_title(args.title or f"Scene inventory - {args.csv.stem}", loc="left",
                 fontsize=13, fontweight="bold")
    ticks = [i for i, l in enumerate(labels) if l.endswith("-01")]
    ax.set_xticks(ticks); ax.set_xticklabels([labels[i][:4] for i in ticks])
    ax.set_xlim(-0.5, len(labels) - 0.5)
    ax.legend(frameon=False, fontsize=9, ncol=2)
    ax.grid(axis="y", alpha=0.25)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    empty = [labels[i] for i in range(len(labels)) if good[i] == 0]
    ax.text(0.995, 0.94, f"{len(empty)} of {len(labels)} months with 0 usable scenes",
            transform=ax.transAxes, ha="right", fontsize=9, color="#b2182b")

    # --- B: year x month gap map ------------------------------------------
    bx = fig.add_subplot(gs[1])
    yrs = list(range(min(ys), max(ys) + 1))
    grid = np.full((len(yrs), 12), np.nan)
    for i, l in enumerate(labels):
        grid[yrs.index(int(l[:4])), int(l[5:7]) - 1] = good[i]
    im = bx.imshow(grid, cmap="YlGnBu", aspect="auto", vmin=0,
                   vmax=max(1, np.nanmax(grid)))
    bx.set_xticks(range(12)); bx.set_xticklabels(MONTHS)
    bx.set_yticks(range(len(yrs))); bx.set_yticklabels(yrs)
    bx.set_title("Usable scenes per month (white = outside range)", loc="left", fontsize=11)
    for i in range(len(yrs)):
        for j in range(12):
            v = grid[i, j]
            if v == v:
                bx.text(j, i, int(v), ha="center", va="center", fontsize=8,
                        color="#b2182b" if v == 0 else
                        ("white" if v > np.nanmax(grid) * 0.6 else "#222"),
                        fontweight="bold" if v == 0 else "normal")
    fig.colorbar(im, ax=bx, pad=0.01, fraction=0.025, label="usable scenes")

    # --- C: cloud cover by calendar month ---------------------------------
    cx = fig.add_subplot(gs[2])
    data = [cloud_by_cal.get(f"{m:02d}", []) for m in range(1, 13)]
    keep = [i for i, d in enumerate(data) if d]
    bp = cx.boxplot([data[i] for i in keep], positions=[i + 1 for i in keep],
                    widths=0.6, patch_artist=True, showfliers=False)
    for p in bp["boxes"]:
        p.set(facecolor="#a6bddb", edgecolor="#3b5c6b")
    for i in keep:
        cx.scatter(np.full(len(data[i]), i + 1) + np.linspace(-.18, .18, len(data[i])),
                   data[i], s=4, alpha=0.25, color="#08519c", zorder=3)
    thr = 20.0
    cx.axhline(thr, color="#b2182b", ls="--", lw=1)
    cx.text(12.4, thr, f" reject >= {thr:.0f}%", color="#b2182b", fontsize=9, va="center")
    cx.set_xticks(range(1, 13)); cx.set_xticklabels(MONTHS)
    cx.set_ylabel("cloud cover over AOI (%)")
    cx.set_title("In-AOI cloud cover of every candidate scene, by calendar month",
                 loc="left", fontsize=11)
    cx.grid(axis="y", alpha=0.25)
    for s in ("top", "right"): cx.spines[s].set_visible(False)

    dest = args.out or args.csv.with_name(args.csv.stem.replace("_inventory", "") + "_inventory.png")
    fig.savefig(dest, dpi=140, bbox_inches="tight")
    print(f"wrote {dest}")
    print(f"  {len(rows)} candidate scenes, {int(good.sum())} usable, "
          f"{len(empty)}/{len(labels)} months empty")


if __name__ == "__main__":
    main()
