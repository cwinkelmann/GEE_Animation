# Publishing these animations

A companion to [`how-the-timeseries-is-made.md`](how-the-timeseries-is-made.md) for the
step after the render: putting an MP4/GIF in front of an audience that did not write
this pipeline. It assumes the frame layout that document describes — title/subtitle
header, auto credit line, legend with word anchors, filled/hollow observed/generated
marker — and answers the questions that come up once you try to actually ship one of
these: what do I write under it, whose logo goes where, what size does the platform
accept, and is anyone going to ask "is this real?"

---

## 1. Caption templates

Every template ends with the credit line (§2) — copy it from the frame, don't
retype it. `{region}` below is the caption-length name of the AOI, not the config's
internal `name:` key.

**Vegetation / water indices** (NDVI, EVI, NDWI, NDMI — single-band, one number per
pixel):

> {display_name} over {region}, {start}–{end} ({cadence}). {credit}

Filled in:

> Vegetation greenness (NDVI) over the Grumsiner Forst — UNESCO World Heritage beech
> forest, Brandenburg, Germany, May–Oct 2022 (monthly). Contains modified Copernicus
> Sentinel data 2022.

**Thermal** (LST, LST split-window, LST sharpened — usually the one worth publishing
alongside the inside-vs-outside chart, §5):

> {display_name} over {region}, {start}–{end}. The forest reads {cooler/warmer} than
> the surrounding land by up to {X} °C in {season} — see the accompanying chart for
> the measured difference. {credit}

Filled in:

> Land surface temperature over the Grumsiner Forst — UNESCO World Heritage beech
> forest, 2021–2022 (monthly, Landsat). The forest reads up to 5.6 °C cooler than the
> surrounding farmland in summer, and is indistinguishable from it in winter — see the
> accompanying chart for the measured difference. Landsat imagery courtesy of the
> U.S. Geological Survey.

**True colour / colour infrared** (RGB, CIR — visual context, no measurement):

> {display_name} of {region}, {start}–{end}. For visual orientation only — no index or
> measurement is shown in this animation. {credit}

Filled in:

> True colour of the Grumsiner Forst — UNESCO World Heritage beech forest, 2022. For
> visual orientation only — no index or measurement is shown in this animation.
> Contains modified Copernicus Sentinel data 2022.

**Two overlay modalities that stack on any of the above** — reword the sentence, don't
drop it, when either is active:

- *Cross-year pooling* (`pool_years` + `pool_strategy: least_cloudy`/`median`): add
  *"a 'typical {season}' loop built from the clearest scene in any of {pool_years} for
  each period — not a single year's actual progression."* (`gap_fill` is different —
  see §4.)
- *Interpolated playback* (`render.interpolate`): add *"played back with generated
  in-between frames for smooth motion; frames marked with a hollow dot on-screen were
  not observed."*

---

## 2. Required credit lines

`render._default_credit` picks one of three strings by `sensor`, with **zero config**
— this is what makes correct attribution the default rather than something to
remember:

| sensor | auto credit line | when it applies |
|---|---|---|
| `sentinel2` | `Contains modified Copernicus Sentinel data {year}` (or `{year0}–{year1}` for a multi-year run; widened to cover `pool_years` when pooling is on) | every Sentinel-2 render |
| `landsat` | `Landsat imagery courtesy of the U.S. Geological Survey` | every Landsat render |
| `modis` / `modis_lst` | `MODIS data courtesy of NASA LP DAAC` | every MODIS render |

An explicit `credit: "..."` in the config overrides this verbatim (use it to add a
project/funder line — it replaces the auto text rather than appending to it, so if you
still need the Copernicus/USGS/NASA wording, include it yourself).

`credit: ""` draws **no** line at all. This is a real, supported option — not
every internal draft needs attribution baked into the pixels — but for Sentinel-2 it
is a **conscious compliance decision**, not a cosmetic one: the Copernicus data
licence requires the "Contains modified Copernicus Sentinel data" notice on published
products, so this codebase treats omitting it as something you opt into deliberately.
`RunConfig.validate()` logs a warning naming the licence requirement whenever
`credit: ""` is set on a `sentinel2` run, precisely so that suppressing the line is a
choice you made, not a default you didn't notice. If you do suppress it, put the
notice somewhere else the published animation is anchored to (a video description, a
report's figure caption, a slide footer) — the licence obligation doesn't go away
just because the frame doesn't carry it.

---

## 3. Format picker table

Match the destination, then set `render.aspect`, `render.preset`, `render.fps`, and
`render.quality` accordingly. `fps` here means *seconds per period*, not natural
video motion — these are composited satellite images, not filmed footage, so even the
"cinema pacing" interpolated example (`config/wne_interpolated.example.yaml`) uses
`fps: 2`, not 24 or 30; nothing in this table goes higher.

| Destination | `aspect` | `preset` | `fps` | `quality` | GIF? |
|---|---|---|---|---|---|
| Slide deck (PowerPoint / Keynote), embedded MP4 | `16:9` (matches the deck) | `1080p` | 2–3 | 3–4 (small embed, played once) | No |
| YouTube / hosted video player | `16:9` | `1080p` or `4k` | 2 (or with `interpolate` for smoother motion) | 8–10 (the host re-compresses; don't hand it a low-quality source) | No |
| Instagram / social (feed or Reels/Stories) | `1:1` or `9:16` | `1080p` | 2–3 | 6–8 | No — see §6, Instagram doesn't take true animated GIFs either |
| Report / PDF figure | `match` (keep the full AOI shape — the point of a figure is the area, not a fixed frame) | `720p`–`1080p` | 2 | 4–5 | No — embed a couple of representative PNG frames (`render.frames: true`) alongside or instead of the video |

`quality` is `None` by default, which is imageio's own default (~5) — that untuned
default is what produced the ~1 MB/frame MP4s referenced in §6 (`wne_2yr_ndvi.mp4`:
22.2 MB for 24 frames at 1440p). Setting `render.quality` explicitly is the
publishing-time trade-off knob: lower it for anything you're emailing or attaching to
a slide, raise it for anything a hosting platform will re-encode anyway.

---

## 4. "Is this real?" — three sentences

Someone will ask. These map directly onto the honesty devices actually drawn on the
frame (`docs/how-the-timeseries-is-made.md` §7a, §9) — say them plainly rather than
pointing at the pipeline:

1. **Interpolation.** *"Most frames in a smooth-playback animation were generated
   between two real satellite observations, not photographed — a hollow dot next to
   the caption (filled dots are real observations) and text like 'between May and
   June 2022 · 36%' mark every generated frame on-screen."*
2. **Gap-filling.** *"A frame captioned 'image from 2018' inside an otherwise-2022
   animation is real satellite data — just not from the year its position in the
   sequence suggests. The caption always names the actual source year; an unmarked
   frame really is the year it claims to be."*
3. **Cosmetic pooling.** *"A 'typical summer' loop (captioned 'every frame re-picked
   from 2018–2024 — not a time series') recombines the clearest available scene from
   any of several years into a smooth-looking sequence. It shows what a normal season
   looks like — it is not, and must never be presented as, one year's actual
   progression."*

---

## 5. The inside-vs-outside chart

**What it shows:** the mean index value inside the AOI (the forest polygon) plotted
against the mean over the surrounding frame, both as a time series across the whole
run — e.g. `docs/images/lst-inside-outside.png` in the main doc, which is what makes a
July cool-island frame and a January flat one add up to "the canopy buffers
temperature, seasonally, by up to 5.6 °C."

**Why it's the artefact that carries the story:** no single animated frame can show a
*comparison* — a July frame proves the forest is cool that day, not that it is
*different from its surroundings because of the canopy*. The chart is the only output
that puts "inside" and "outside" on the same axis over time, which is what turns a
striking picture into evidence. In the audience-communication review this was rated
the least discoverable and most persuasive artefact the pipeline produces — it is easy
to render an animation and never see it.

**How to produce it — today, not through the CLI:**

- **Python / notebook (`api.animate`)**: the returned `Animation` carries `.series`
  (the raw `[(period, inside_mean, outside_mean), ...]` rows) and `.chart` (a path to
  a rendered PNG, via `matplotlib` if it's installed — `None` otherwise, though
  `.series` is always populated). `Animation._repr_html_()` embeds both the video and
  the chart inline in Colab/Jupyter.
- **GUI (`gee_animation.gui`)**: the Gradio app plots the same series live as part of
  a run and reports the inside/outside means and their delta in the status text.
- **`gee-animation --config ...` (the CLI)**: does **not** produce this chart. `cli.py`
  calls the render pipeline only; the inside-vs-outside comparison is computed by
  `charts.inside_outside_timeseries`, which only `api.animate` and the GUI call. A
  config-driven CLI run gives you the animation and (with `metadata: true`) a
  per-frame `metadata.db`, from which the same inside/outside series can be
  reconstructed by hand — but the chart itself is an `api`/`gui` output, not a CLI one.

---

## 6. GIF guidance

Sizes measured this session, two-year monthly runs (22–24 frames each — both
Decembers are dropped from the two-year Landsat examples for lack of a usable scene,
see `how-the-timeseries-is-made.md` §9, hence 22 rather than 24):

| file | frames | size |
|---|---|---|
| `wne_2yr_gapfill.gif` (LST) | 22 | 17.7 MB |
| `wne_2yr_rgb.gif` | 24 | 27.7 MB |
| `wne_2yr_ndvi.gif` | 24 | 29.7 MB |

At those sizes a GIF is **effectively undeliverable** through the channels an analyst
actually uses to share these — most chat tools and mail servers cap attachments well
under that, and a GIF that size stutters or refuses to load in a browser tab. It is
not a size a resized/re-encoded GIF can fix without also destroying what makes it
useful: `_write_gif` already caps every frame to `_GIF_MAX_EDGE` (1280 px) before
quantising, so the size difference from the MP4 (§3's table: a few MB at reasonable
`quality`) is the format itself, not a tunable setting.

**Prefer MP4.** It plays natively in every modern browser and every platform in §3's
table accepts it; the GIF path exists as a fallback for players that genuinely cannot
show a video (`render.gif: false` turns it off entirely, which is the right default
for anything meant to be published rather than previewed locally). If a GIF is
unavoidable — an old forum, a README badge, a platform that only takes GIFs — trim the
run to a handful of representative frames (fewer periods, not more `interpolate`) and
accept a materially smaller, materially less complete animation; there is no
"GIF, but comparable to the MP4" option in this pipeline.

---

*Voice note for anyone extending this doc: match `how-the-timeseries-is-made.md` —
mechanisms and candour, not a marketing page. If a claim here can't be traced to a
line the frame actually draws, don't add it.*
