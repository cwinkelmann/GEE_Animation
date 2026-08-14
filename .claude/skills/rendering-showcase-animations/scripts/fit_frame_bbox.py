#!/usr/bin/env python3
"""Reshape a lon/lat bbox to the aspect a preset needs, so the render fills the
canvas instead of pillarboxing into black bars.

Aspect is measured in METRES, not degrees: a degree of longitude is
cos(latitude) shorter than a degree of latitude, so a bbox that looks wide in
degrees can be square on the ground (at 53°N the factor is 0.6).

Usage:
    fit_frame_bbox.py --region MINLON MINLAT MAXLON MAXLAT [--aspect 2.373]
                      [--pad-km 3]

    --region   the subject bbox (or the bounds of your region polygon)
    --aspect   target frame aspect; 2.373 for a two-line header (title +
               subtitle, or any pooling/interpolation caveat — i.e. almost
               every showcase run), 2.136 for a one-line header
    --pad-km   minimum context margin added around the region before reshaping

Prints the YAML line to paste as `aoi.frame.bbox`, plus the resulting ground
size so you can sanity-check the scale bar.
"""
from __future__ import annotations

import argparse
import math

M_PER_DEG_LAT = 110540.0
M_PER_DEG_LON_EQ = 111320.0


def fit(minlon, minlat, maxlon, maxlat, aspect, pad_km=0.0):
    clat = (minlat + maxlat) / 2.0
    clon = (minlon + maxlon) / 2.0
    m_per_lon = M_PER_DEG_LON_EQ * math.cos(math.radians(clat))

    w_m = (maxlon - minlon) * m_per_lon + 2000.0 * pad_km
    h_m = (maxlat - minlat) * M_PER_DEG_LAT + 2000.0 * pad_km

    # Grow the short side only — never crop the subject out of frame.
    if w_m / h_m < aspect:
        w_m = h_m * aspect
    else:
        h_m = w_m / aspect

    dlon = (w_m / 2.0) / m_per_lon
    dlat = (h_m / 2.0) / M_PER_DEG_LAT
    return (clon - dlon, clat - dlat, clon + dlon, clat + dlat), w_m, h_m


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", nargs=4, type=float, required=True,
                   metavar=("MINLON", "MINLAT", "MAXLON", "MAXLAT"))
    p.add_argument("--aspect", type=float, default=2.373,
                   help="2.373 = two-line header (default), 2.136 = one-line")
    p.add_argument("--pad-km", type=float, default=3.0,
                   help="context margin around the region, km (default 3)")
    a = p.parse_args()

    bbox, w_m, h_m = fit(*a.region, aspect=a.aspect, pad_km=a.pad_km)
    print(f"# {w_m / 1000:.1f} x {h_m / 1000:.1f} km, aspect {w_m / h_m:.4f}")
    print("aoi:")
    print("  frame: {{ bbox: [{:.4f}, {:.4f}, {:.4f}, {:.4f}] }}".format(*bbox))


if __name__ == "__main__":
    main()
