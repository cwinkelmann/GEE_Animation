"""SPIKE: animate the Sentinel-1 monthly medians exported by sar_windthrow_spike.py
through the repo's renderer, via LocalImage frames (no Earth Engine at render time).

    WT_SITE=R12 python scripts/sar_video.py abs      # VH in dB over the frame
    WT_SITE=R12 python scripts/sar_video.py delta    # VH minus footprint mean, footprint only
"""
import glob, json, os, re, sys, types
import numpy as np, rasterio
sys.path.insert(0, "scripts")
from sar_windthrow_spike import SITE, CFG, FRAME, OUT, FILL
from gee_animation import focus, products, render
from gee_animation.compositing import Frame
from gee_animation.local_image import LocalImage
from gee_animation.products import Index, INDICES

MODE = sys.argv[1]
TITLES = {"R12": "Tegeler Forst, Revier 12", "R13": "Spandauer Forst, Revier 13", "WNE": "Grumsin Beech Forest"}
# a temporary radar product so the legend, units and header read correctly
INDICES["s1_vh"] = Index("s1_vh", frozenset({"landsat"}), (-20.0, -8.0, None), None, units="dB",
                         display_name="Radar backscatter, VH (Sentinel-1)", low_label="open / smooth",
                         high_label="dense canopy")
products.THERMAL_INDICES  # noqa: touch to keep the import explicit
frames = []
for f in sorted(glob.glob(f"{OUT}/*.tif")):
    lab = re.search(r"(\d{4}-\d{2})\.tif$", f).group(1)
    with rasterio.open(f) as ds:
        vh = ds.read(2).astype("float32"); vh[vh == FILL] = np.nan
        b = ds.bounds; crs = str(ds.crs); res = float(ds.res[0])
    frames.append(Frame(lab, LocalImage(vh, (b.left, b.bottom, b.right, b.top), crs, res)))
region_aoi = {"geojson": CFG["footprint"]} if CFG["footprint"].endswith(".geojson") else {"shapefile": CFG["footprint"]}
delta = MODE == "delta"
cfg = types.SimpleNamespace(
    name=f"{SITE.lower()}_sar_vh_{MODE}", out_dir="out", project="hnee-331218",
    sensor="landsat", index="s1_vh", title=TITLES[SITE],
    subtitle="Sentinel-1 VH backscatter" + (" · Δ vs footprint mean" if delta else ""),
    credit="Contains modified Copernicus Sentinel-1 data 2017–2026",
    frame_aoi={"bbox": FRAME}, region_aoi=region_aoi, region_max_cloud_percent=100,
    start=frames[0].label + "-01", end=frames[-1].label + "-01", cadence="monthly", max_cloud_percent=100,
    draw_region=True, region_line_width=5,
    viz_min=(-1.5 if delta else -20.0), viz_max=(1.5 if delta else -8.0),
    palette=(["#2166ac", "#67a9cf", "#d1e5f0", "#f7f7f7", "#fddbc7", "#ef8a62", "#b2182b"] if delta
             else ["#0b0f2b", "#1e3a8a", "#2f7fb8", "#5ec2c0", "#b7e3a4", "#f1f1a1", "#f5d16b", "#f7f7f7"]),
    fps=6, interpolate=10, interpolate_mode="auto", scale=20, dimensions=236, allow_upsample=True,
    crs="auto", preset="1080p", aspect="match", upscale="lanczos", quality=8,
    gif=False, frames=True, raw_frames=False, geotiffs=False, pixel_grid=False,
    region_only=delta, relative=("region_mean" if delta else None),
    anomaly=None, smooth=None, pool_years=None, pool_strategy="gap_fill", workers=4, metadata=False,
    cache=True, cache_dir=None, missions=None, sharpen=None, harmonics=2, min_scenes=1, debug_month=None,
    allow_slc_off=False, baseline_years=None, mask_clouds=True)
frames = focus.apply(frames, cfg, region_geom=None)
paths = render.render(frames, cfg, geometry=None)
for p in paths[:3]: print("wrote", p)
print(f"{len(frames)} observed frames")
