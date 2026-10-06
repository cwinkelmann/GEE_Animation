"""Write a PDF guide per site (R12, R13) describing every delivered video.

Usage:  python video_guide.py <videos_dir> <out_dir_R12> <out_dir_R13> <work_dir>

Each entry: filename, a thumbnail taken from the video at a post-storm month,
and a plain-language description derived from the run configs. Markdown is
typeset with pandoc + LuaLaTeX; a short summary table opens each guide.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

VIDEOS, OUT_R12, OUT_R13, WORK = (Path(a) for a in sys.argv[1:5])
OUT_WNE = Path(sys.argv[5]) if len(sys.argv) > 5 else None
PY = sys.executable

SITES = {
    "r12": dict(
        name="Tegeler Forst, Revier 12 (R12)",
        area="515 ha, one polygon; 8,323 predicted fallen stems from the 2025 drone survey",
        zoom="4.7 × 4.7 km, the footprint plus 0.4 km",
        wide="16.3 × 6.9 km, 16:9",
        outline="The footprint is outlined in every video except the true-colour cut.",
    ),
    "wne": dict(
        name="Grumsin beech reserve (WNE), control site",
        area="the UNESCO World Heritage beech reserve in the Schorfheide, Brandenburg, with no known 2025 storm damage; "
             "it is the control that shows what the Tegel signals look like where nothing happened",
        zoom="4.5 × 3.3 km, the reserve plus 0.4 km",
        wide="22.2 × 9.4 km, 16:9",
        outline="The reserve boundary is outlined in both videos.",
    ),
    "r13": dict(
        name="Spandauer Forst, Revier 13 (R13)",
        area="1,033 ha in three parts separated by streets; 25,238 predicted fallen stems",
        zoom="5.7 × 4.9 km, the footprint plus 0.4 km",
        wide="16.8 × 7.1 km, 16:9",
        outline=("Videos ending in `_noaoi` draw no footprint outline; where both versions exist, "
                 "the `_noaoi` one is the cleaner cut and the other keeps the outline for orientation."),
    ),
}

# Shared sentences, so the two guides stay consistent.
PERIOD = "January 2017 to August 2026, one frame per month"
INTERP = ("Between two observed months the video shows 10 generated transition frames; the "
          "observed/generated marker at the bottom left says which is which.")
NDVI = ("Sentinel-2 vegetation greenness (NDVI) at 10 m, cloud-masked, monthly median. "
        "Months without a usable scene are filled from the same month of another year and "
        "labelled as such.")
NBR = ("Sentinel-2 normalised burn ratio (NBR, near-infrared against the longer shortwave "
       "infrared band) at 20 m. NBR responds to the dry wood a windthrow leaves on the ground "
       "as well as to the lost canopy, and separated windthrow cells from intact canopy better "
       "than NDVI in the analysis. Same cloud masking and gap filling as the NDVI cuts.")
RGB = ("Sentinel-2 true colour, monthly, with real clouds left in the picture rather than "
       "masked. The reference view for what the other indices are reacting to.")
LST = ("Landsat 8/9 land surface temperature at its native 100 m thermal resolution, 15 to "
       "45 °C. Every frame is a per-pixel seasonal curve (two harmonics) fitted across the "
       "whole period and evaluated at that month, so the series is hole-free; frames are "
       "labelled 'modelled' on screen.")
GRID = "The 100 m thermal pixels are drawn as a mesh, to show the true size of the thermal cells."
RF = ("Landsat 8/9 land surface temperature sharpened to 20 m with a random forest trained "
      "on each frame: Sentinel-2 reflectance and indices predict the 100 m temperature, the "
      "prediction is applied at 20 m and the residual is added back so every 100 m cell keeps "
      "its observed mean. Absolute temperature, 15 to 45 °C.")
DELTA = ("Same sharpened temperature, shown as each frame's departure from the footprint's "
         "own mean that month (−3 to +3 K, blue cooler, red warmer). This removes the seasonal "
         "cycle and leaves the within-forest contrast; the felled stands appear as warm patches "
         "from July 2025 on.")
FOCUS = "Everything outside the footprint is masked (neutral grey)."
STEMS = ("Overlaid: the density of predicted fallen stems from the drone survey as a continuous "
         "kernel-density surface (40 m bandwidth), filled at 100, 250, 500 and 1000 m of stem "
         "per hectare. The overlay is the ground reference the satellite signal is compared to.")
SAR_ABS = ("Sentinel-1 C-band radar, VH cross-polarised backscatter, monthly median of both "
           "orbit passes, −20 to −8 dB. Radar is weather-independent, so every month is "
           "observed. Felled stands lose about one decibel after the storm.")
SAR_DELTA = ("Same radar series as each month's departure from the footprint mean (−1.5 to "
             "+1.5 dB), footprint only. The storm damage appears as blue (darker) patches.")

# (filename stem, title, description, thumbnail position as fraction of duration)
def entries(site: str):
    s = site
    if s == "wne":
        return [
            ("wne_cinema_lst_rf_2018_2026", "Sharpened surface temperature, wide frame",
             f"{RF.replace(' Absolute temperature, 15 to 45 °C.', '')} Wide frame ({SITES[s]['wide']}) with the surroundings, January 2018 to July 2026, one frame per "
             "month at 2 frames per second. Colour range −3 to 46 °C (the reserve's measured range). The reserve "
             "shows the ordinary seasonal cycle and nothing else across 2025.", 0.89),
            ("wne_focus_lst_rf_delta_2018_2026", "Sharpened temperature, departure from reserve mean, reserve only",
             f"{DELTA.replace('footprint', 'reserve').replace('the felled stands appear as warm patches from July 2025 on', 'in the control nothing appears in 2025')} "
             "Everything outside the reserve is masked. Zoomed to the reserve "
             f"({SITES[s]['zoom']}), January 2018 to July 2026.", 0.89),
        ]
    zoom_noaoi = "_noaoi" if site == "r13" else ""
    E = []
    def add(stem, title, text, pos=0.89):
        E.append((f"{s}_{stem}", title, text, pos))

    add(f"cinema_ndvi_10yr", "NDVI, wide frame",
        f"{NDVI} Wide frame ({SITES[s]['wide']}) with the surroundings for context. {PERIOD}. {INTERP}")
    if s == "r13":
        add("cinema_ndvi_10yr_noaoi", "NDVI, wide frame, no outline",
            "The wide NDVI cut without the footprint outline.")
    add("zoom_ndvi_10yr", "NDVI, zoomed",
        f"{NDVI} Zoomed to the footprint ({SITES[s]['zoom']}). {PERIOD}.")
    if s == "r13":
        add("zoom_ndvi_10yr_noaoi", "NDVI, zoomed, no outline", "The zoomed NDVI cut without the footprint outline.")
    add("zoom_ndvi_10yr_stems", "NDVI, zoomed, with fallen-stem density",
        f"The zoomed NDVI cut with the stem overlay in magenta. {STEMS}")
    add(f"zoom_nbr_10yr{zoom_noaoi}", "NBR, zoomed",
        f"{NBR} Zoomed to the footprint. {PERIOD}.")
    add(f"zoom_nbr_10yr{zoom_noaoi}_stems", "NBR, zoomed, with fallen-stem density",
        f"The zoomed NBR cut with the stem overlay in magenta. {STEMS}")
    add("zoom_rgb_10yr_noaoi", "True colour, zoomed",
        f"{RGB} Zoomed to the footprint, no outline. {PERIOD}.")
    add(f"lst_pretty_10yr{zoom_noaoi}", "Surface temperature, wide frame",
        f"{LST} Wide frame ({SITES[s]['wide']}). {PERIOD}.")
    add(f"lst_pretty_10yr_grid{zoom_noaoi}", "Surface temperature, wide frame, 100 m grid",
        f"The wide temperature cut with the pixel mesh. {GRID}")
    add("zoom_lst_pretty_10yr", "Surface temperature, zoomed",
        f"{LST} Zoomed to the footprint ({SITES[s]['zoom']}).")
    if s == "r13":
        add("zoom_lst_pretty_10yr_noaoi", "Surface temperature, zoomed, no outline", "The zoomed temperature cut without the outline.")
    add("zoom_lst_pretty_10yr_grid", "Surface temperature, zoomed, 100 m grid",
        f"The zoomed temperature cut with the pixel mesh. {GRID}")
    if s == "r13":
        add("zoom_lst_pretty_10yr_grid_noaoi", "Surface temperature, zoomed, 100 m grid, no outline",
            "The zoomed gridded temperature cut without the outline.")
    add(f"zoom_lst_pretty_10yr_grid_delta{zoom_noaoi}", "Surface temperature, 100 m grid, departure from footprint mean",
        "The zoomed 100 m temperature cut with the pixel mesh, shown as each frame's departure from "
        "the footprint's own mean that month (−3 to +3 K, blue cooler, red warmer) at the native thermal "
        "resolution: the unsharpened counterpart of the sharpened departure cuts, for judging what the "
        "sharpening adds. Seasonal cycle removed; the felled stands appear as warm cells from July 2025 on.")
    add("zoom_lst_rf_local_10yr", "Sharpened surface temperature, zoomed",
        f"{RF} Zoomed to the footprint; compare with the 100 m grid cut to see what the sharpening adds. {PERIOD}.")
    if s == "r13":
        add("zoom_lst_rf_local_10yr_noaoi", "Sharpened surface temperature, zoomed, no outline",
            "The sharpened temperature cut without the outline.")
    add("zoom_lst_rf_delta_local_10yr", "Sharpened temperature, departure from footprint mean, surroundings kept",
        f"{DELTA} " + ("The streets between the footprint's three parts stay visible." if s == "r13"
                       else "The surrounding land stays visible, coloured relative to the footprint mean."))
    if s == "r13":
        add("zoom_lst_rf_delta_local_10yr_noaoi", "Sharpened temperature, departure, surroundings kept, no outline",
            "The departure cut with surroundings, without the outline.")
    add("focus_lst_rf_delta_local_10yr", "Sharpened temperature, departure, footprint only",
        f"{DELTA} {FOCUS} The cleanest view of where the forest warmed after the storm. {PERIOD}.")
    if s == "r13":
        add("focus_lst_rf_delta_local_10yr_noaoi", "Sharpened temperature, departure, footprint only, no outline",
            "The footprint-only departure cut without the outline.")
    add("focus_lst_rf_delta_local_10yr_stems", "Sharpened temperature, departure, with fallen-stem density",
        f"The footprint-only departure cut with the stem overlay in purple. {STEMS}")
    add(f"focus_lst_rf_delta_local_2025_2026{zoom_noaoi}", "Sharpened temperature, departure, May 2025 to August 2026",
        f"{DELTA} {FOCUS} Only the sixteen months around the storm, at 3 frames per second so each month can be read; "
        "gap-filled from post-storm months only.", pos=0.5)
    add(f"sar_vh_abs{zoom_noaoi}", "Radar backscatter, VH", f"{SAR_ABS} {PERIOD}.")
    add(f"sar_vh_delta{zoom_noaoi}", "Radar backscatter, VH, departure from footprint mean",
        f"{SAR_DELTA} {PERIOD}.")
    return E


def thumb(mp4: Path, png: Path, pos: float) -> bool:
    if png.exists():
        return True
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
                         capture_output=True, text=True).stdout.strip()
    t = float(dur) * pos
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(mp4), "-frames:v", "1",
                        "-vf", "scale=640:-2", str(png)])
    return r.returncode == 0


def size_mb(p: Path) -> str:
    return f"{p.stat().st_size / 1e6:,.0f}\u00a0MB"


def build(site: str, out_dir: Path):
    S = SITES[site]
    work = WORK / site
    work.mkdir(parents=True, exist_ok=True)
    rows, body, missing = [], [], []
    for stem, title, text, pos in entries(site):
        mp4 = VIDEOS / f"{stem}.mp4"
        if not mp4.exists():
            missing.append(stem)
            continue
        png = work / f"{stem}.png"
        ok = thumb(mp4, png, pos)
        rows.append(f"| `{stem}.mp4` | {title} | {size_mb(mp4)} |")
        pic = f"![]({png.name})\n\n" if ok else ""
        body.append(f"::: entry\n{pic}**{title}**\n\n`{stem}.mp4` · {size_mb(mp4)}\n\n{text}\n:::\n")
    md = [
        f"---\ntitle: \"Videos for {S['name']}\"\ndate: \"HNEE, October 2026\"\n---\n",
        f"These videos accompany the report *What remote sensing can tell us about the 2025 windthrow in the "
        f"Tegeler and Spandauer Forst*. Site: {S['area']}. All videos are 1080p masters; the zoomed cuts use a "
        f"canvas shaped like the footprint. {S['outline']}\n",
        "Every video shares the same anatomy: title and product on the header, a legend with the colour ramp and "
        "a no-data swatch, the month and an observed/generated marker at the bottom left, the data credit at "
        "the bottom right. Colour ramps are fixed over the whole period, so colours are comparable between "
        "months and between videos of the same product.\n",
        "Naming: `cinema` = wide 16:9 frame, `zoom` = footprint plus 0.4 km, `focus` = footprint only with the "
        "outside masked, `lst` = Landsat surface temperature, `lst_rf` = temperature sharpened to 20 m with a "
        "random forest, `delta` = departure from the footprint mean, `stems` = with the fallen-stem overlay, "
        "`noaoi` = without the footprint outline, `sar_vh` = Sentinel-1 radar.\n",
        "# Overview\n", "| file | content | size |", "|---|---|---|", *rows, "",
        "# The videos\n", *body,
    ]
    (work / "guide.md").write_text("\n".join(md))
    pdf = out_dir / f"{site.upper()}_videos_guide.pdf"
    subprocess.run(["pandoc", "guide.md", "-o", str(pdf), "--pdf-engine=lualatex",
                    "-V", "geometry:a4paper,margin=20mm", "-V", "fontsize=10pt",
                    "-V", "mainfont=TeX Gyre Pagella", "-V", "mainfontfallback=DejaVu Sans:",
                    "-V", "monofont=DejaVu Sans Mono", "-V", "monofontoptions=Scale=0.8",
                    "-V", "colorlinks=true", "-V", "linkcolor=black",
                    "-H", str(WORK / "guide_head.tex"), "--lua-filter", str(WORK / "colwidths.lua"), "--lua-filter", str(WORK / "entry.lua")],
                   cwd=work, check=True)
    print(f"{site}: {len(rows)} videos, missing {missing}, -> {pdf}")


(WORK / "guide_head.tex").write_text(r"""
\usepackage{graphicx}
\usepackage{fancyhdr}\pagestyle{fancy}\fancyhf{}\fancyfoot[C]{\small\thepage}
\renewcommand{\headrulewidth}{0pt}
\usepackage{caption}\captionsetup{font=small,skip=3pt}
\usepackage{etoolbox}\AtBeginEnvironment{longtable}{\small}
\usepackage[section]{placeins}

\setlength{\parskip}{4pt plus 1pt}\setlength{\emergencystretch}{3em}
\makeatletter\def\maxheight{0.45\textheight}\makeatother
""")
build("r12", OUT_R12)
build("r13", OUT_R13)
if OUT_WNE is not None:
    build("wne", OUT_WNE)
