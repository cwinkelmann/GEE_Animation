# Audience Communication Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the animations legible to a wider, non-technical audience — orientation, attribution, accessible colour, plain language — without weakening a single honesty device.

**Architecture:** All changes are additive overlay/config work in `render.py`, `products.py` (registry metadata), `config.py` (new optional fields), plus a new pure `labels.py` for display-text formatting and a bundled font. Nothing touches fetching, compositing, pooling, interpolation mechanics, or the cache key (all new fields are client-side).

**Tech Stack:** Python 3.11, Pillow, numpy. One new bundled asset: DejaVu Sans TTF (copied from matplotlib's data dir, which is present in the env; DejaVu's licence is free/permissive and must be bundled alongside).

**Source review:** this plan implements the findings of the audience-communication review (2026-08-07). Its "Things NOT to change" section is binding — see Global Constraints.

## Global Constraints

- The unit suite stays network-free; EE is faked at the `ee_module` seam or via injected `fetch`/`deps`.
- `pytest -m "not integration"` passes after every task. Baseline: **396 passed, 1 skipped, 2 deselected**. Many tasks legitimately change drawn text; tests asserting old display strings are updated **within the same task**, but the *property* they pinned (provenance shown, PNGs observed-only, bars in margins, etc.) must survive.
- **Honesty devices are load-bearing and must not be weakened:** no-data grey stays and stays unfilled; borrowed-year provenance stays on-frame; interpolation percentage stays (additions welcome, removals forbidden); cloud gates untouched; the frame stays larger than the region.
- New config fields are **client-side**: they must be added to the cache-key allowlist (`cache.py` `_CLIENT_SIDE`) so restyling stays a cache hit. This is the ONE permitted `cache.py` edit; the key derivation itself is untouched.
- Do not change `compositing.py`, `collection.py`, `inventory.py`, `metadata.py`, `interpolate.py`.
- `interpolate: 0`/absent behaviour, byte-wise, may change ONLY via deliberate overlay redesign in these tasks — never via fetch/composite changes.
- Working tree may contain PRE-EXISTING unrelated files (`.claude/`, `.idea/`); stage only your own files, never `git add -A`.
- Follow the surrounding idiom: docstrings explain *why*; overlay panels copy the `draw_scale_bar` translucent-panel pattern.

## File Structure

| File | Responsibility |
|---|---|
| `gee_animation/fonts/DejaVuSans.ttf` + `LICENSE` | **New.** Bundled typeface: real glyphs (`←`, `–`, `°`), publication look. |
| `gee_animation/labels.py` | **New, pure.** Humanized display text: period labels, scene counts, borrowed/generated wording. No PIL, no I/O. |
| `gee_animation/products.py` | Registry gains `display_name`, `low_label`, `high_label`; NDVI default palette/viz replaced. |
| `gee_animation/config.py` | New optional fields: `title`, `subtitle`, `credit`, `quality`. |
| `gee_animation/render.py` | Header redesign, credit line, north arrow, legend upgrades, observed/generated marker. |
| `gee_animation/cache.py` | New fields added to `_CLIENT_SIDE` allowlist only. |
| `tests/test_labels.py`, existing test files | Mirror modules. |
| `config/*.example.yaml`, `docs/publishing-animations.md` | Delivery guidance. |

Out of scope (deliberate): changing the LST jet ramp. The review itself warned its within-AOI discrimination is why the cool island pops; any replacement needs a live A/B against real July frames, done manually after this plan lands.

---

### Task 1: Bundle DejaVu Sans and retire the ASCII fold

**Files:**
- Create: `gee_animation/fonts/DejaVuSans.ttf`, `gee_animation/fonts/LICENSE`
- Modify: `gee_animation/render.py` (`_font` ~L40, `_DRAWABLE`/`_drawable` ~L52), `pyproject.toml` (package data)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `_font(px)` returning a DejaVu `FreeTypeFont`; `_drawable(text)` becomes the identity (kept as a seam, callers unchanged).

- [ ] **Step 1: Copy the font in and declare it.** Source: `matplotlib.get_data_path()/fonts/ttf/DejaVuSans.ttf` (verified present, 738 KB) plus matplotlib's DejaVu `LICENSE_DEJAVU` (name it `LICENSE` in our fonts dir). Add `[tool.setuptools.package-data] gee_animation = ["fonts/*"]` (verify against existing pyproject structure; `include-package-data` interplay is the usual trap — prove it with a wheel-build check or `importlib.resources` load test).
- [ ] **Step 2: Failing tests.**

```python
def test_font_is_bundled_truetype_with_needed_glyphs():
    from gee_animation.render import _font
    f = _font(24)
    from PIL import ImageFont
    assert isinstance(f, ImageFont.FreeTypeFont)
    for ch in ("←", "–", "°"):
        box = f.getbbox(ch)
        assert box[2] > box[0], f"no glyph for {ch!r}"

def test_drawable_no_longer_mangles_the_arrow():
    from gee_animation.render import _drawable
    assert _drawable("2022-05 ← 2021") == "2022-05 ← 2021"
```

- [ ] **Step 3: Implement.** `_font` loads the bundled TTF via `importlib.resources` (keep `lru_cache`; keep the `load_default` fallback ONLY for a missing/corrupt bundle, logged once). Empty the `_DRAWABLE` table; keep `_drawable` as identity so call sites and the M5 history stay greppable.
- [ ] **Step 4: Update collateral tests.** Any test measuring text with the default font (e.g. narrow-label-drop, info-bar overflow) may shift pixel expectations — re-derive their numbers, do not weaken their assertions.
- [ ] **Step 5: Full suite, commit** (`git add gee_animation/fonts pyproject.toml gee_animation/render.py tests/test_render.py`).

---

### Task 2: Plain-language display text (`labels.py`)

**Files:**
- Create: `gee_animation/labels.py`
- Test: `tests/test_labels.py`

**Interfaces:**
- Consumes: nothing (pure; period keys are the existing `YYYY-MM` / `YYYY-MM-DD` strings).
- Produces (exact contracts Task 3/6 build on):
  - `period_text("2022-05") == "May 2022"`; `period_text("2022-05-11") == "11 May 2022"`
  - `observed_text(label, n_scenes, source)`:
    - `("2022-05", 3, None) → "May 2022 · 3 passes"`
    - `("2022-05", 1, None) → "May 2022 · 1 pass"`
    - `("2022-09", 1, 2018) → "September 2022 · image from 2018 · 1 pass"`
    - `n_scenes=None` omits the passes segment.
  - `generated_text("2022-05", "2022-06", 36) == "between May and June 2022 · 36%"`; when the months share a year, the year appears once; across a year boundary both appear (`"between December 2021 and January 2022 · 50%"`); sub-monthly uses day forms (`"between 11 May and 21 May 2022 · 50%"`).

The percentage STAYS (review §4: "do not remove the interpolation percentage — add to it"); this task only rewords around it.

- [ ] **Step 1: Failing tests** — one test per contract line above, plus year-boundary and sub-monthly cases.
- [ ] **Step 2: Implement.** Month names in English via `calendar.month_name` (no locale dependence). Docstring explains *why* plain language: `n=3` and `← 2018` are correct but jargon to the audience these frames are for.
- [ ] **Step 3: Full suite, commit.**

---

### Task 3: Header redesign — title, subtitle, product name; formula off the frame

**Files:**
- Modify: `gee_animation/config.py` (fields `title: str = None`, `subtitle: str = None`, read from top-level YAML), `gee_animation/products.py` (add `display_name: str = ""` to `Index`, fill for every index), `gee_animation/render.py` (`_info_text` ~L400, `draw_info_bar` ~L355, `_margins` ~L287, `annotate` ~L380, `render()` composition ~L1130), `gee_animation/cache.py` (allowlist += `title`, `subtitle`)
- Test: `tests/test_render.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: Task 1 font, Task 2 `labels.observed_text`/`generated_text`.
- Produces: header layout all later overlay tasks assume:
  - **Top margin line 1** (larger): `cfg.title` if set, else the index's `display_name`.
  - **Top margin line 2** (smaller, only when non-empty): `cfg.subtitle`, then provenance caveats joined with `" · "` — the existing pooled note and interpolation note, reworded plain (`"gap-filled from 2018–2024"` / `"every frame re-picked from 2018–2024 — not a time series"` / `"10 generated frames between observations"`).
  - **Bands and formula no longer appear on the frame.** They remain in the method doc. (Review M2: no claim is weakened; the coefficient string was costing credibility.)
  - **Bottom bar left**: `labels.observed_text(...)` / `labels.generated_text(...)` output replaces the `2022-05  n=3` / `-> ... 36%` strings.

`display_name` values (exact): ndvi `"Vegetation greenness (NDVI)"`, evi `"Vegetation greenness (EVI)"`, ndwi `"Surface water index (NDWI)"`, ndmi `"Vegetation moisture (NDMI)"`, rgb `"True colour"`, cir `"Colour infrared"`, lst `"Land surface temperature"`, lst_sharp `"Land surface temperature (sharpened)"`, lst_smw `"Land surface temperature (split-window)"`, lst_modis `"Land surface temperature (MODIS)"`.

**Margin arithmetic — the trap in this task.** The top margin doubles when line 2 is non-empty. Line-2 presence is decidable from `cfg` alone before the frame loop (subtitle and caveats are run-level), so margins stay static per run. Everything consuming `_margins` must see the same decision: `add_margins` call, `_fit_margins`/`_output_spec` (Trap 2 from the margins feature: explicit aspects must still come out exact), and the pinning tests (`test_margins_match_the_bar_height_of_the_padded_frame`, `test_output_spec_accounts_for_margins`) — update their expectations deliberately, never loosen them. Recommended shape: `_margins(imagery_h, two_line_header: bool)` and thread one boolean from `render()`.

**Legibility floor (review M1):** header text uses font-fit-with-floor like `draw_info_bar`'s overflow logic, but must NEVER shrink below `_annot_scale`'s size for line 2 — if the caveat line cannot fit at floor size, truncate the *subtitle* with `…`, never the caveat. The caveat outranks decoration.

- [ ] **Step 1: Failing tests.** (a) title set → drawn in top margin, imagery untouched (reuse the imagery-unoccluded test pattern); (b) no title → `display_name` drawn; (c) subtitle + pooled run → line 2 contains both, caveat survives a very long subtitle (assert caveat substring present in drawn text via a draw-spy, subtitle truncated); (d) formula string absent from any drawn text on an `lst` frame; (e) `test_output_spec_accounts_for_margins` extended with the two-line case; (f) bottom label uses `"May 2022 · 3 passes"` wording; (g) borrowed frame shows `"image from 2018"`; interpolated label wording per `labels.generated_text`; (h) config round-trip + cache-hit test: changing `title` must not change the thumb cache key (model on the existing `palette`-is-a-hit test in `tests/test_cache.py`).
- [ ] **Step 2: Implement** (registry first, then config, then render).
- [ ] **Step 3: Update every test asserting the old strings** (`n=`, `<- 2021`, `->  36%`, `bands:` in info bar). The properties they pinned must survive with new wording — e.g. the pooled-provenance test still fails if the source year is dropped.
- [ ] **Step 4: Full suite, commit.**

---

### Task 4: Data attribution + north arrow

**Files:**
- Modify: `gee_animation/config.py` (`credit: str = None`, top-level), `gee_animation/render.py`, `gee_animation/cache.py` (allowlist += `credit`)
- Test: `tests/test_render.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: Task 3 layout (bottom bar right half is free).
- Produces: `_default_credit(cfg) -> str` and a drawn credit; north-arrow glyph beside the scale bar.

Credit resolution: `cfg.credit` set → use verbatim. `None` → auto by sensor:
- sentinel2: `"Contains modified Copernicus Sentinel data {years}"`
- landsat: `"Landsat imagery courtesy of the U.S. Geological Survey"`
- modis / modis_lst: `"MODIS data courtesy of NASA LP DAAC"`

`{years}` spans `start.year` through `(end − 1 day).year`, **widened to the `pool_years` span when pooling is on** — borrowed frames contain data from those years, so the notice must cover them. Single year renders `"2022"`, a span `"2018–2024"` (en-dash; Task 1 makes it drawable). Explicit `credit: ""` omits the line — but `validate()` logs a warning naming the Copernicus licence requirement when the sensor is sentinel2, because omission is a compliance decision someone should make consciously.

Drawn: small text (≈0.8× `_annot_scale` size, floor 10 px), right-aligned in the bottom bar. North arrow: an upward arrow + `N` in a small `draw_scale_bar`-style panel directly above the scale bar; both render CRSs used here (UTM, plate carrée) are north-up, so no rotation logic — state that in the docstring rather than pretending generality.

- [ ] **Step 1: Failing tests.** Auto-credit strings for all three sensors (pooled-widened years case included: start 2021, end 2023, pool [2018, 2024] → `"2018–2024"`); explicit credit verbatim; `credit: ""` omits and warns for sentinel2; credit pixels land in the bottom margin, not imagery; north-arrow panel present above the scale-bar panel (pixel-region assertion, same style as existing scale-bar tests); cache-hit on credit change.
- [ ] **Step 2: Implement; Step 3: full suite, commit.**

---

### Task 5: Legend upgrades — word anchors, no-data swatch, honest ticks

**Files:**
- Modify: `gee_animation/products.py` (`low_label: str = ""`, `high_label: str = ""` on `Index`; fill: ndvi/evi `"bare"`→`"dense vegetation"` — ndvi's low label becomes `"water"` in Task 7 when the blue stop lands; lst family `"cooler"`→`"warmer"`; ndwi `"dry"`→`"water"`; ndmi `"dry"`→`"moist"`), `gee_animation/render.py` (`add_colorbar` ~L178, `_colorbar_ticks`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: Task 1 font.
- Produces: colorbar block = ramp + ticks + word anchors below the end ticks + a no-data swatch.

Changes:
1. **Word anchors:** `low_label` under the left end, `high_label` under the right end, drawn on the existing backing panel (extend panel height one text line). Numeric ticks stay — words supplement, never replace numbers.
2. **No-data swatch:** small `NODATA_RGB` square + `"no data"` to the right of the ramp, inside the panel. This is the review's fix for the honest-but-unexplained grey (fix the legend, not the data).
3. **Honest mid tick:** replace the exact midpoint with the *nicest* value strictly inside `(vmin, vmax)` — candidates `{1, 1.5, 2, 2.5, 3, 4, 5} × 10^k`, choose the one nearest the midpoint — drawn at its true proportional position. `0…0.95` gets `0.5`, not `0.475`; `18…43` gets `30`, not `30.5`. Zero-tick and collision-drop logic unchanged. Pure helper `_nice_mid(vmin, vmax) -> float` with direct unit tests including a range where two candidates are equidistant (pin the deterministic choice: smaller absolute value wins). (Plan self-review note: an earlier candidate set `{1,2,2.5,5}` could not produce 30 for 18…43 — its nearest was 25. Worked table below is derived from the seven-candidate set; re-verify by hand before asserting.)

- [ ] **Step 1: Failing tests** — `_nice_mid` table test (`(0, 0.95) → 0.5`, `(18, 43) → 30`, `(-3, 46) → 20`, `(-0.25, 1.0) → 0.4`, tie case); anchors drawn for `lst` (`cooler`/`warmer` present via draw-spy), absent when labels empty; swatch pixels equal `NODATA_RGB` inside the panel; existing collision/zero-tick tests still pass with re-derived positions.
- [ ] **Step 2: Implement; Step 3: full suite, commit.**

---

### Task 6: Observed/generated marker dot

**Files:**
- Modify: `gee_animation/render.py` (bottom-bar composition in `render()`'s drawing loop)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: Task 3's bottom-bar layout; the loop already carries `is_real` per frame.
- Produces: a pre-attentive marker — **filled circle** before the label on observed frames, **hollow circle** (2 px ring, same diameter) on generated frames. Diameter = the label font's cap height; same white as the label text.

This is review H5: at 2 fps nobody reads a label that changes every 0.5 s; the dot reads peripherally at any size. It is an ADDITION — the percentage text stays (Task 2 wording).

- [ ] **Step 1: Failing tests.** Render a 2-observation, `interpolate: 2` sequence with a draw-spy on the marker helper (or pixel assertions in the bar's left region): frames 0 and 3 filled, 1–2 hollow; a non-interpolated run draws filled markers only; marker pixels sit in the margin, never the imagery.
- [ ] **Step 2: Implement (single `_frame_marker(draw, xy, size, filled)` helper); Step 3: full suite, commit.**

---

### Task 7: NDVI palette — CVD-safe, water-blue bottom stop

**Files:**
- Modify: `gee_animation/products.py` (ndvi entry), `config/wne_2yr_ndvi.example.yaml`, `config/wne_interpolated.example.yaml`
- Test: `tests/test_imaging.py` or `tests/test_render.py` (palette maths), `tests/test_gui.py` (default-viz assertion updates)

**Interfaces:**
- Consumes: verified fact — `imaging.colorize` interpolates across **evenly spaced** stops.
- Produces: new ndvi `default_viz`: `(-0.25, 1.0, ["#4575b4", "#8c510a", "#d8b365", "#f6e8c3", "#5ab4ac", "#01665e"])`, and ndvi `low_label` becomes `"water"`.

Why these exact numbers: six stops over −0.25…1.0 put stop boundaries at exactly −0.25, 0, 0.25, 0.5, 0.75, 1.0 — the blue→brown transition completes AT 0, so water (negative NDVI) stays in the blue segment and bare soil (≥ ~0.05) is already brown. The four positive-range colours are ColorBrewer BrBG, whose endpoint separation under deuteranopia the review measured at 98/255 (vs 18/255 for the old ramp). The zero tick (range spans zero) now marks the water/land boundary — a feature, label it so in the example config comment.

**What this does NOT do:** it is not a water *mask*. Turbid/vegetated water with slightly positive NDVI will still render brownish. State this limitation in the example config comment and the method doc — honest about the residual, per project norms. (A true NDWI-based mask needs a second band through the fetch path — deliberately out of scope; noted for a future plan.)

- [ ] **Step 1: Failing tests.** `colorize(-0.25…0, new_viz)` stays in the blue family (R < B for every pixel); `colorize(0.05)` is brown-family (R > B); deuteranope-simulated endpoint separation > 60/255 using the review's Viénot matrix as a test helper; update the gui test asserting NDVI default viz `(-1, 1)`.
- [ ] **Step 2: Implement + update the two example configs** (drop their explicit `viz:` overrides in favour of the new default, or set it explicitly with the why-comment — pick one, consistently, and update the water-clips comment to the new "slightly-positive water renders brownish" caveat).
- [ ] **Step 3: Full suite, commit.**

---

### Task 8: Delivery — MP4 quality knob, 16:9 example, publishing guide

**Files:**
- Modify: `gee_animation/config.py` (`quality: int = None` under render), `gee_animation/render.py` (writer creation in `assemble_stream`/`_write_mp4`), `gee_animation/cache.py` (allowlist += `quality`), `config/wne_summer_pooled.example.yaml` (switch to `aspect: "16:9"` with trade-off comment)
- Create: `docs/publishing-animations.md`
- Test: `tests/test_render.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: everything prior (the doc describes the finished frame).
- Produces: `render.quality` 1–10 passed to `imageio.get_writer(..., quality=...)` when set (imageio's ffmpeg quality scale; verify the kwarg reaches ffmpeg by asserting a large size difference between quality 3 and 9 on a synthetic 40-frame write — a behavioural test, not a mock).

`docs/publishing-animations.md` contents (audience: the analyst publishing these):
1. **Caption templates** — one per product family, with the Grumsin/UNESCO example filled in.
2. **Required credit lines** — the three auto-generated strings, when each applies, and that `credit: ""` is a conscious compliance decision (Copernicus notice is a licence term).
3. **Format picker table** — where it will be shown (slides / YouTube / Instagram / report PDF) → `aspect`, `preset`, `fps`, `quality`, GIF yes/no.
4. **The three sentences for "is this real?"** — plain-language answers on interpolation, gap-filling, and the cosmetic mode, consistent with the frame markings.
5. **The inside-vs-outside chart** — what it shows, that it carries the ecological story, and how to produce it (api/gui today); the review called it the least discoverable, most persuasive artefact.
6. GIF guidance (sizes measured this session: 17–30 MB for 22–24 frames — effectively undeliverable; prefer MP4 + a short clip).

Also: link the new doc from `docs/how-the-timeseries-is-made.md` §10 and clear the relevant TODO items there if now covered.

- [ ] **Step 1: Failing tests** (quality knob validation: rejects 0 and 11, accepts None/1..10; behavioural size test; 16:9 config loads).
- [ ] **Step 2: Implement + write the doc; Step 3: full suite, commit.**

---

## Verification

```bash
conda activate GEE_animation
pytest -m "not integration"          # green after every task
```

End-to-end after all tasks (live EE, ~5 min): render `config/wne_interpolated.example.yaml` and `config/wne_2yr_gapfill.example.yaml`, then check against the review's findings:

- A frame answers *where/what*: title + subtitle drawn; credit line present without any config edit (H1, H4 closed).
- NDVI: lakes blue, soil brown, canopy teal-green; endpoints distinguishable in a deuteranope simulation (H2, H3).
- Legend: word anchors, grey "no data" swatch, mid tick a round number (M4).
- Bottom bar: `May 2022 · 3 passes`, filled dot; generated frames `between May and June 2022 · 36%`, hollow dot (H5, M5).
- No formula/bands text anywhere on the frame; caveat line legible at floor size (M1, M2).
- The `←`, `–`, `°` glyphs render as real glyphs (M6).
- 16:9 example renders 1920×1080 exactly.
- Every honesty device still present: grey no-data, provenance year, interpolation percentage, cosmetic warnings.
