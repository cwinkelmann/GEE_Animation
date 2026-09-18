#!/usr/bin/env python3
"""Regenerate out/wne_cinema_overview.html from what is actually in out/.

Stats (size, frame count, duration) are read off the files, never typed by
hand — an earlier hand-maintained page drifted out of date within a day. The
prose notes live in NOTES below; everything else is derived.

    python scripts/make_overview.py
"""
from __future__ import annotations

import html
import json
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
VID, IMG, THUMB = OUT / "videos", OUT / "images", OUT / "thumbs"

# Scratch runs that are not part of the collection.
SKIP_PREFIX = ("probe_", "wne_landcover_smoke", "wne_showcase")

SECTIONS = [
    ("Full archive · 2018–2026", "Every product over the complete Sentinel-2 and "
     "Landsat 8/9 record, monthly, cross-year gap-filled.",
     ["wne_cinema_ndvi_2018_2026", "wne_cinema_evi_2018_2026",
      "wne_cinema_ndwi_2018_2026", "wne_cinema_ndmi_2018_2026",
      "wne_cinema_rgb_2018_2026", "wne_cinema_cir_2018_2026",
      "wne_cinema_lst_2018_2026", "wne_cinema_lst_smw_2018_2026",
      "wne_cinema_lst_sharp_2018_2026", "wne_cinema_modis_ndvi_2018_2026"]),
    ("Summer-scaled temperature", "The same thermal data on a 15–45 °C ramp "
     "instead of −10…46 °C. On the wide ramp a real 2–3 °C forest/field "
     "difference is about 5% of the width and reads as uniform red; here the "
     "reserve is an obvious cool island. Winter falls below the floor. The first "
     "three are the ones to show — gap-filled, so no month is missing; the "
     "_nopool trio below them is the archive companion for plotting.",
     ["wne_lst_summer_lst", "wne_lst_summer_lst_smw", "wne_lst_summer_lst_sharp",
      "wne_lst_summer_lst_nopool", "wne_lst_summer_lst_smw_nopool",
      "wne_lst_summer_lst_sharp_nopool"]),
    ("Long-term and special", "Wider spans, coarser cadences, and the one "
     "categorical product.",
     ["wne_cinema_lst_smw_max", "wne_cinema_lst_10yr",
      "wne_cinema_lst_5yr_quarterly", "wne_landcover"]),
    ("Five-year set · 2018–2022", "The earlier five-year renders, kept for "
     "comparison.",
     ["wne_cinema_ndvi_5yr", "wne_cinema_evi_5yr", "wne_cinema_ndwi_5yr",
      "wne_cinema_ndmi_5yr", "wne_cinema_rgb_5yr", "wne_cinema_cir_5yr",
      "wne_cinema_lst_5yr", "wne_cinema_lst_smw_5yr", "wne_cinema_lst_sharp_5yr",
      "wne_cinema_modis_ndvi_5yr"]),
    ("Two-year cinema set · 2021–2022", "The original showcase renders.",
     ["wne_cinema_ndvi", "wne_cinema_cir", "wne_cinema_rgb", "wne_cinema_lst"]),
]

TITLES = {
    "ndvi": "Vegetation greenness (NDVI)", "evi": "Vegetation greenness (EVI)",
    "ndwi": "Surface water (NDWI)", "ndmi": "Vegetation moisture (NDMI)",
    "rgb": "True colour", "cir": "Colour infrared",
    "lst": "Land surface temperature", "lst_smw": "Temperature (mono-window)",
    "lst_sharp": "Temperature (sharpened)", "modis_ndvi": "NDVI (MODIS 500 m)",
    "landcover": "Land cover (Dynamic World)",
}

NOTES = {
    "wne_lst_summer_lst": "Primary. Gap-filled, so every month is present; "
        "borrowed frames say so on-frame.",
    "wne_lst_summer_lst_smw": "Primary. Mono-window retrieval — runs 1–3 K below "
        "the USGS product, so don't mix figures between the two.",
    "wne_lst_summer_lst_sharp": "Primary. Sharpened to 30 m; the extra detail "
        "comes from NDVI structure, not a finer thermal measurement.",
    "wne_cinema_rgb_2018_2026": "Real clouds, not grey mask cutouts.",
    "wne_cinema_cir_2018_2026": "Near-infrared as red; real clouds.",
    "wne_cinema_lst_smw_max": "The whole Landsat archive, 1984 onward — 314 of "
        "511 possible months actually observed. No pooling: every frame is real "
        "data for its own period.",
    "wne_cinema_lst_5yr_quarterly": "One frame per calendar quarter, pooling 3–7 "
        "passes each, so winter composites are nearly hole-free.",
    "wne_landcover": "Categorical: nine classes, swatch legend, no interpolation "
        "(blending class colours would invent categories).",
    "wne_lst_summer_lst_nopool": "Archive companion: no gap-filling, so 69 of 103 "
        "months survive. Every frame is real data for its period — plot from this, "
        "show the pooled one.",
    "wne_lst_summer_lst_smw_nopool": "Archive companion, no gap-filling.",
    "wne_lst_summer_lst_sharp_nopool": "Archive companion, no gap-filling.",
}


def probe(path: Path):
    """(frames, seconds) via ffprobe, or (None, None)."""
    def q(entries):
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", entries, "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True)
        return r.stdout.strip().split(",")[0] if r.returncode == 0 else ""
    dur = q("format=duration") or q("stream=duration")
    nb = q("stream=nb_frames")
    try:
        secs = float(dur)
    except ValueError:
        secs = None
    return (int(nb) if nb.isdigit() else None), secs


def pick_still(run: str) -> Path | None:
    """A representative frame: a July if there is one, else the middle."""
    d = IMG / run
    if not d.is_dir():
        return None
    frames = sorted(p for p in d.glob(f"{run}_*.png") if "_raw_" not in p.name)
    if not frames:
        return None
    july = [p for p in frames if "-07" in p.stem or "-Q3" in p.stem]
    return (july or frames)[len(july or frames) // 2]


def thumb_for(run: str) -> str | None:
    """A 480px-wide thumbnail, so the gallery stays light."""
    src = pick_still(run)
    if src is None:
        return None
    THUMB.mkdir(exist_ok=True)
    dst = THUMB / f"{run}.jpg"
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        try:
            from PIL import Image
            im = Image.open(src).convert("RGB")
            im = im.resize((480, round(im.height * 480 / im.width)), Image.LANCZOS)
            im.save(dst, quality=82, optimize=True)
        except Exception:
            return None
    return f"thumbs/{dst.name}"


def title_for(run: str) -> str:
    for key in sorted(TITLES, key=len, reverse=True):
        if key in run:
            return TITLES[key]
    return run


def card(run: str) -> str | None:
    mp4 = VID / f"{run}.mp4"
    if not mp4.exists():
        return None
    frames, secs = probe(mp4)
    mb = mp4.stat().st_size // 1048576
    obs = len([p for p in (IMG / run).glob(f"{run}_*.png")
               if "_raw_" not in p.name]) if (IMG / run).is_dir() else 0
    stats = [f"{obs} observed"] if obs else []
    if frames:
        stats.append(f"{frames} frames")
    if secs:
        stats.append(f"{int(secs // 60)}:{int(secs % 60):02d}")
    stats.append(f"{mb}&nbsp;MB")
    preview = VID / f"{run}_720p.mp4"
    src = f"videos/{preview.name}" if preview.exists() else None
    thumb = thumb_for(run)
    media = (f'<video controls preload="none" poster="{thumb or ""}" '
             f'src="{src}"></video>' if src else
             (f'<img src="{thumb}" alt="{html.escape(run)}">' if thumb else ""))
    note = NOTES.get(run, "")
    links = f'<a href="videos/{mp4.name}">master</a>'
    cfg = ROOT / "config" / f"{run}.yaml"
    if cfg.exists():
        links += f'<a href="../config/{cfg.name}">config</a>'
    return (f'<div class="card">{media}<div class="body">'
            f'<h3>{html.escape(title_for(run))}</h3>'
            f'<div class="run">{html.escape(run)}</div>'
            f'<div class="stats">{" · ".join(stats)}</div>'
            + (f'<p class="note">{html.escape(note)}</p>' if note else "")
            + f'<div class="links">{links}</div></div></div>')


def main() -> None:
    parts, total = [], 0
    for heading, blurb, runs in SECTIONS:
        cards = [c for c in (card(r) for r in runs) if c]
        if not cards:
            continue
        total += len(cards)
        parts.append(f'<h2>{html.escape(heading)}</h2>'
                     f'<p class="blurb">{html.escape(blurb)}</p>'
                     f'<div class="grid">{"".join(cards)}</div>')
    page = TEMPLATE.format(count=total, sections="".join(parts),
                           generated=date.today().isoformat())
    dest = OUT / "wne_cinema_overview.html"
    dest.write_text(page)
    print(f"{dest.relative_to(ROOT)}: {total} videos")


TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grumsin Beech Forest — Satellite Timelapse Collection</title>
<style>
:root {{ --bg:#101418; --card:#1a2027; --ink:#e8ecef; --muted:#9aa7b0;
         --accent:#f5c542; --line:#2c3540; }}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--ink);padding:2.5rem 1.5rem 4rem;
 font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
main{{max-width:1180px;margin:0 auto}}
h1{{font-size:1.9rem;font-weight:650}}
.sub{{color:var(--muted);margin:.4rem 0 2rem;max-width:64ch}}
h2{{font-size:1.05rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;
 color:var(--accent);margin:2.6rem 0 .3rem;padding-bottom:.4rem;
 border-bottom:1px solid var(--line)}}
.blurb{{color:var(--muted);font-size:.9rem;margin:.5rem 0 1.1rem;max-width:70ch}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1.3rem}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;
 overflow:hidden;display:flex;flex-direction:column}}
.card video,.card img{{width:100%;display:block;aspect-ratio:16/9;background:#000;
 object-fit:cover}}
.card .body{{padding:.85rem 1rem 1rem;display:flex;flex-direction:column;flex:1}}
.card h3{{font-size:1.02rem;font-weight:600}}
.run{{color:var(--muted);font-size:.74rem;font-family:ui-monospace,monospace;
 margin:.15rem 0 .4rem}}
.stats{{color:var(--accent);font-size:.8rem;margin-bottom:.5rem}}
.note{{font-size:.86rem;opacity:.92;flex:1}}
.links{{margin-top:.7rem;font-size:.82rem}}
.links a{{color:var(--accent);text-decoration:none;margin-right:1em}}
.links a:hover{{text-decoration:underline}}
.method{{background:var(--card);border:1px solid var(--line);border-radius:10px;
 padding:1.2rem 1.4rem;margin-top:1rem;font-size:.9rem}}
.method li{{margin:.35rem 0}} .method ul{{margin-left:1.1rem}}
.credits{{color:var(--muted);font-size:.82rem;margin-top:2rem}}
code{{background:#0c1013;border:1px solid var(--line);border-radius:4px;
 padding:.08em .4em;font-size:.86em}}
</style></head><body><main>
<h1>Grumsin Beech Forest — Satellite Timelapse Collection</h1>
<p class="sub">UNESCO World Heritage beech forest, Brandenburg, Germany.
{count} animations over a 22.2&nbsp;×&nbsp;9.35&nbsp;km frame, from Sentinel-2,
Landsat&nbsp;4–9, MODIS and Dynamic World. Cards with a player use a 720p
preview; the rest show a still. Every card links its full-resolution master and,
where one exists, the config that reproduces it exactly.</p>
{sections}
<h2>How to read these</h2>
<div class="method"><ul>
<li><strong>Filled dot = observed, hollow = generated.</strong> Frames between
observations are interpolated for smooth playback and are never data.</li>
<li><strong>Borrowed months say so.</strong> The videos here are gap-filled so
that no month is missing, and every borrowed frame states its source year
on-frame (&ldquo;image from &lt;year&gt;&rdquo;) — the picture is smooth, the
caption stays honest. Runs ending <code>_nopool</code> contain no borrowed
frames and are the ones to compute from.</li>
<li><strong>Grey is absence, not zero.</strong> Cloud, shadow and failed thermal
retrievals are painted neutral grey and never filled in.</li>
<li><strong>Colour scales are fixed per video</strong>, so a colour means the same
thing in every frame. The same data on two different ramps can look completely
different — compare only within a video.</li>
<li><strong>Thermal methods differ.</strong> The USGS product and the mono-window
retrieval typically sit 1–3&nbsp;K apart; do not mix figures from the two.</li>
</ul></div>
<p class="credits">Contains modified Copernicus Sentinel data · Landsat imagery
courtesy of the U.S. Geological Survey · MODIS data courtesy NASA LP DAAC ·
Dynamic World by Google/WRI. Generated {generated} by
<code>scripts/make_overview.py</code>.</p>
</main></body></html>
"""

if __name__ == "__main__":
    main()
