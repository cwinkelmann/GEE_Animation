#!/usr/bin/env python3
"""Charts from out/metadata.db — the numbers behind the animations.

Two panels per run:

  * inside the reserve vs outside it, each with its 10th–90th percentile band,
    so the spread is visible and not just the mean;
  * the difference (inside − outside), which is the quantity the animations are
    really about — a forest cooler than its surroundings shows as a gap that
    widens every summer.

    python scripts/make_plots.py                 # every run with the stats
    python scripts/make_plots.py --light         # white background, for papers
    python scripts/make_plots.py --run NAME ...  # just these

Runs rendered with `smooth: harmonic` never appear here: config.validate forbids
combining a model with metadata, precisely so a fitted curve cannot be plotted
as if it were measured.
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates          # noqa: E402
import matplotlib.pyplot as plt            # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "out" / "metadata.db"
DEST = ROOT / "out" / "plots"

#: A run needs enough paired points for a line to mean anything.
MIN_POINTS = 12

DARK = {"bg": "#101418", "panel": "#1a2027", "ink": "#e8ecef", "muted": "#9aa7b0",
        "grid": "#2c3540", "inside": "#4aa3df", "outside": "#e8834a",
        "diff": "#f5c542"}
LIGHT = {"bg": "#ffffff", "panel": "#ffffff", "ink": "#1a1a1a", "muted": "#555555",
         "grid": "#dddddd", "inside": "#1f77b4", "outside": "#d95f02",
         "diff": "#7a5c00"}


def parse_period(label: str):
    """A period label -> a date, for any format period_starts emits."""
    parts = label.split("-")
    if len(parts) > 1 and parts[1][:1] in ("Q", "q"):
        return date(int(parts[0]), 3 * int(parts[1][1:]) - 2, 1)
    if len(parts) == 2:
        return date(int(parts[0]), int(parts[1]), 1)
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def load(con, name):
    rows = con.execute(
        "SELECT month, aoi_mean, aoi_p10, aoi_p90, outside_mean, outside_p10, "
        "outside_p90, index_name FROM frame_clouds WHERE name=? "
        "AND aoi_mean IS NOT NULL AND outside_mean IS NOT NULL "
        "ORDER BY month", (name,)).fetchall()
    out = []
    for m, a, a10, a90, o, o10, o90, idx in rows:
        try:
            out.append((parse_period(m), a, a10, a90, o, o10, o90, idx))
        except (ValueError, IndexError):
            continue          # a label this script cannot date is skipped, not fatal
    return out


def plot_run(name, rows, colours, dest: Path):
    xs = [r[0] for r in rows]
    inside, in10, in90 = [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows]
    outside, out10, out90 = [r[4] for r in rows], [r[5] for r in rows], [r[6] for r in rows]
    diff = [i - o for i, o in zip(inside, outside)]
    index_name = rows[0][7] or ""
    unit = "°C" if index_name.startswith("lst") else ""

    fig, (ax, bx) = plt.subplots(
        2, 1, figsize=(11, 6.4), sharex=True, height_ratios=[2.2, 1],
        gridspec_kw={"hspace": 0.12})
    fig.patch.set_facecolor(colours["bg"])

    for a in (ax, bx):
        a.set_facecolor(colours["panel"])
        a.grid(True, color=colours["grid"], linewidth=0.6, alpha=0.7)
        a.tick_params(colors=colours["muted"], labelsize=9)
        for spine in a.spines.values():
            spine.set_color(colours["grid"])

    ax.fill_between(xs, out10, out90, color=colours["outside"], alpha=0.16, linewidth=0)
    ax.fill_between(xs, in10, in90, color=colours["inside"], alpha=0.16, linewidth=0)
    ax.plot(xs, outside, color=colours["outside"], linewidth=1.4, label="outside the reserve")
    ax.plot(xs, inside, color=colours["inside"], linewidth=1.4, label="inside the reserve")
    ax.set_ylabel(f"{index_name.upper()} {unit}".strip(), color=colours["ink"], fontsize=10)
    ax.set_title(f"{name} — inside vs outside the AOI", color=colours["ink"],
                 fontsize=12, loc="left", pad=10)
    leg = ax.legend(loc="upper left", framealpha=0, fontsize=9)
    for t in leg.get_texts():
        t.set_color(colours["ink"])

    bx.axhline(0, color=colours["muted"], linewidth=0.8)
    bx.plot(xs, diff, color=colours["diff"], linewidth=1.2)
    bx.fill_between(xs, diff, 0, color=colours["diff"], alpha=0.22, linewidth=0)
    bx.set_ylabel(f"inside − outside {unit}".strip(), color=colours["ink"], fontsize=9)
    bx.xaxis.set_major_locator(mdates.YearLocator(base=max(1, len(xs) // 60 or 1)))
    bx.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    # The shaded bands are percentiles, not confidence intervals — say so, since
    # a reader will otherwise assume error bars.
    fig.text(0.008, 0.015,
             f"shaded: 10th–90th percentile of pixels · {len(xs)} periods · "
             f"means ignore cloud-masked pixels",
             color=colours["muted"], fontsize=7.5)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=140, facecolor=colours["bg"], bbox_inches="tight")
    plt.close(fig)
    return diff


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--light", action="store_true", help="white background, for print")
    ap.add_argument("--run", nargs="*", help="only these runs")
    a = ap.parse_args()
    colours = LIGHT if a.light else DARK

    con = sqlite3.connect(DB)
    names = [r[0] for r in con.execute("SELECT DISTINCT name FROM frame_clouds")]
    if a.run:
        names = [n for n in names if n in set(a.run)]
    names = [n for n in names if "_part" not in n]          # chunk fragments
    made = 0
    for name in sorted(names):
        rows = load(con, name)
        if len(rows) < MIN_POINTS:
            continue
        dest = DEST / f"{name}.png"
        diff = plot_run(name, rows, colours, dest)
        summer = [d for d, r in zip(diff, rows) if r[0].month in (6, 7, 8)]
        note = (f"  summer mean {sum(summer)/len(summer):+.1f}" if summer else "")
        print(f"{dest.relative_to(ROOT)}  ({len(rows)} periods){note}")
        made += 1
    con.close()
    if not made:
        print("no run had both inside and outside statistics — "
              "render with metadata: true and pooling off")


if __name__ == "__main__":
    main()
