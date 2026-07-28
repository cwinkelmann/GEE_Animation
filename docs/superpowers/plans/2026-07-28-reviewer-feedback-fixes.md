# Fix reviewer feedback on the WNE timelapse

## Context

A reviewer watched the WNE (Grumsin) animations and reported six issues. All six were
verified against the code and are valid — none rest on a misunderstanding of the tool:

| # | Report | Root cause |
|---|---|---|
| 1 | "Projektion verzerrt, breitgezogen" | Default render CRS is plate carrée. At 53°N that is a **1.66× horizontal stretch** (`1/cos 53°`) |
| 2 | "Grenze hebt sich schwach ab, pixelig" | `draw_region` uses PIL `ImageDraw.line` — PIL has **no antialiasing**; core width only ~2 px |
| 3 | "Nur ein Bild je Monat, Apr→Mai abrupt" | `monthly_median` takes a per-calendar-month `.median()`; `SUPPORTED_CADENCES = {"monthly"}` |
| 4 | "Optische Kollage bräuchte feinere Auflösung" | Same as #3. Sentinel-2 revisits every 5 days, so the data is there |
| 5 | "T-Skala braucht Max/0/Min" | `add_colorbar` prints one string `"LST 15..40"`; no ticks aligned to the ramp, no units |
| 6 | "Ausschnitt zu knapp; Streifen sollen zum Bild gehören" | `draw_info_bar` + `annotate` each paint an `h//12` bar **over** the imagery — ~17 % of frame height occluded |

Two further requirements were added after the initial triage:

| # | Request | Becomes |
|---|---|---|
| 7 | "Liste der nutzbaren Szenen pro Monat" — evidence for the stakeholder on *why* smooth transitions are hard | Task 6: a scene inventory report (CSV + summary) listing every candidate scene and why each was rejected |
| 8 | "Ein Modus, der ein wolkenfreies Bild aus irgendeinem Jahr nimmt — nur der Monat muss stimmen. Optisch schön, wissenschaftlich natürlich nicht." | Task 7: cross-year pooling by calendar period, picking the least-cloudy scene from any pooled year |

Outcome: geometrically correct, fully-visible frames with a readable temperature scale
(Tasks 1–4); a finer time step so leaf-out reads as a transition rather than a jump
(Task 5); a defensible answer to "why is it not smoother?" (Task 6); and an
explicitly-labelled cosmetic mode that trades scientific validity for a clean
animation (Task 7).

Decisions taken: label bars move into **added margins**; sub-monthly cadence is
**within-year** (Task 5) and cross-year pooling is a **separate, opt-in mode** (Task 7)
rather than a change to the default compositing.

---

## Global Constraints

These bind every task. Reviewers should treat a violation as a defect.

1. **The unit suite must stay network-free.** Earth Engine is faked at the `ee_module`
   seam or via an injected `fetch`/`deps` (see `tests/test_collection.py`,
   `tests/test_render.py`). Anything needing live EE goes behind the `integration` marker
   and the `GEE_INTEGRATION` env guard.
2. **`pytest -m "not integration"` must pass after every task.** Run it with the
   `GEE_animation` conda env active, not system Python.
3. **Earth Engine is an injected dependency.** Keep the `ee_module=ee` /
   `fetch=…` / `deps=…` parameter style; never import-and-call EE at module scope in a
   code path the unit tests touch.
4. **Never issue one `getInfo()` per scene or per frame.** Server-side `map` +
   a single `aggregate_array(...).getInfo()` is the established pattern
   (`compositing.monthly_median`). A per-item round trip is a defect.
5. **Backwards compatibility for existing callers.** `cli.py`, `api.py` and `gui.py` each
   hold a `deps` table naming `monthly_median`; `gui.py:18` imports `month_starts`. Renames
   must keep working aliases rather than updating every call site to a new name only.
6. **Fixed visualization across frames.** `cfg.viz_min`/`viz_max`/`palette` are applied
   identically to every frame so colour stays comparable. Do not make viz per-frame.
7. **Test files mirror modules** (`tests/test_<module>.py`), matching the existing style.
8. **Follow the surrounding code's idiom** — comment density, naming, and the explanatory
   docstring style already used in `render.py` and `compositing.py`.
9. Do not reformat, restructure, or "improve" code outside the task's stated scope.

---

## Appendix: problems this will cause — evaluated

**Will happen, accept:**

- **Every existing output changes size.** Task 1 alters the pixel grid and aspect of all
  NDVI/CIR/RGB/EVI/MODIS renders; Task 4 adds ~17 % height. Old and new frames are not
  comparable side by side. Re-render everything shared so far.
- **Cloud holes multiply at 10-day cadence.** Many bins hold one scene, so `median()` of a
  single masked scene leaves grey no-data patches that flicker between frames. This is the
  real cost of Task 5 and it hits the optical collage hardest — the exact product the
  feedback wants it for. The `n=` annotation already surfaces it.
- **Uneven time spacing.** Bins below `min_scenes` are skipped, so a constant-fps animation
  represents unequal time steps. Sub-monthly date labels make it legible but not fixed.
- **Task 7 makes agricultural fields incoherent.** Crop rotation differs year to year, so
  pooled frames show fields jumping between crops while the forest stays coherent.
  Explicitly accepted by the requester; the forest is the subject.

**Real risk, handled inside the tasks:**

- **Outline shifts off the forest.** `draw_region` maps rings across the *full* array
  `w×h`. If Task 4's padding lands before it, the WNE boundary silently moves up by ~8 %
  of the frame. Guarded by Task 4's draw order and its regression test.
- **Aspect presets break.** `_output_spec` computing the canvas from pre-margin imagery
  aspect yields a non-16:9 "16:9" output. Guarded by computing margins first.
- **Landsat at 10-day cadence is mostly empty** (16-day repeat) — Task 5 warns.
- **Anomaly z-scores get inflated sub-monthly.** `anomaly.py` divides a period mean by a
  *monthly* climatology σ. Labels survive, the statistics do not — Task 5 rejects the
  combination.
- **An unlabelled Task 7 render is a misleading artefact.** A viewer sees "2022" and
  believes every frame is 2022. The source-year label is a correctness requirement.
- **Sentinel-2 radiometry shifted in Jan 2022** (processing baseline 04.00 introduced
  `BOA_ADD_OFFSET`). Pooling across that boundary needs `S2_SR_HARMONIZED` — Task 7
  verifies this first.
- **Landsat missions change across pooled years** (L7/L8/L9 differ radiometrically; L7 has
  the SLC-off gap) — Task 7 warns.

**Watch, low impact:**

- EE resamples to UTM; `getThumbURL` defaults to nearest neighbour, so NDVI may look
  marginally blockier along one axis. The LST configs already run this path.
- `_project` approximates the reprojected extent by the bbox of four corners, while EE uses
  the true bbox of the bowed region polygon — sub-pixel to ~1 px at this AOI size.
  Pre-existing, unchanged.
- ~3× more `getThumbURL` downloads at 10-day cadence (~15 frames for May–Sep: fine; a
  multi-year run would be ~180).

---

## Appendix: end-to-end verification (after all tasks, needs EE auth)

```bash
gee-animation --config config/example.yaml        # NDVI  — checks #1, #2, #5, #6
gee-animation --config config/cir.example.yaml    # CIR   — checks #1, #6
```

Visual acceptance, comparing `out/*.png` against the current frames:

- Frame is no longer ~1.66× too wide; the scale bar reads the same distance on both axes.
- WNE boundary edges are smooth, not stair-stepped, and clearly visible over both dark
  forest and bright fields.
- No imagery is hidden behind the top/bottom bars.
- Colorbar shows numeric min/mid/max with `°C` on the LST run.
- The April→May NDVI step is split across ~3 frames at `cadence: 10day`.

---

## Task 1 — Default to a metric render CRS

**Fixes feedback #1** (image stretched 1.66× horizontally at 53°N).

`crs: auto` already exists and works — `render._resolve_crs`, `render._utm_epsg`,
`render._project` — and the LST configs already use it. It is simply off everywhere else,
so every NDVI/CIR/RGB/EVI/MODIS render comes out in plate carrée.

Changes in `gee_animation/config.py`:

- **`crs=render.get("crs")` must become `render.get("crs") or "auto"`.** This is the
  non-obvious line and the whole point of the task: an absent `render.crs` currently
  passes an explicit `None` into the constructor, which *overrides* the dataclass default.
  Changing the default alone silently does nothing for YAML runs.
- Change the `RunConfig` dataclass default from `crs: str = None` to `crs: str = "auto"`.
  This covers `gui.py` and `api.py`, neither of which passes `crs` when constructing a
  `RunConfig`.
- Update the field's explanatory comment to state that `"auto"` is now the default and
  that an explicit `crs: "EPSG:4326"` restores the old plate-carrée behaviour.

Changes in `config/`: add an explicit `crs: auto` line to the `render:` block of
`example.yaml`, `cir.example.yaml`, `evi.example.yaml`, and the `modis*.example.yaml`
files, for documentation value. The LST/anomaly configs already have it.

Do **not** change `_resolve_crs` — it already returns `None` when `bounds is None`, so
bbox-less runs degrade safely. No new dependency: `pyproj>=3.4` is already required.

**Tests** (add to `tests/test_config.py`):

- `test_crs_defaults_to_auto_when_yaml_omits_it` — round-trip a minimal YAML with a
  `render:` block that has no `crs` key through `RunConfig.from_yaml`; assert
  `cfg.crs == "auto"`. This is the regression test for the trap above.
- `test_explicit_crs_in_yaml_is_preserved` — `crs: "EPSG:4326"` in the YAML survives.

---

## Task 2 — Colorbar ticks and units

**Fixes feedback #5** (temperature scale unreadable for a lay audience).

`render.add_colorbar` currently draws the ramp and one string, `f"{index} {min}..{max}"`,
underneath it. Nothing is aligned to a position on the ramp, and there are no units.

Changes in `gee_animation/products.py`:

- Add `units: str = ""` to the `Index` dataclass (it already carries `bands`, `formula`,
  `composite` as optional metadata fields — follow that pattern).
- Set `units="°C"` on the `lst`, `lst_smw`, `lst_sharp` and `lst_modis` entries. Leave the
  reflectance indices (`ndvi`, `evi`, `ndwi`, `ndmi`) with the default `""`.

Changes in `gee_animation/render.py`, `add_colorbar`:

- Draw tick marks with numeric labels aligned to their positions on the ramp: **min at the
  left end, mid at the centre, max at the right end**.
- Append the units to the max-end label (or the index caption) when the index has units, so
  an LST bar reads `15 … 27.5 … 40 °C`.
- Add a `0` tick **only when `viz_min < 0 < viz_max`** — true for anomaly renders, false
  for absolute °C. The request said "Max, 0, Min", but 0 is usually outside an absolute
  temperature range, so min/mid/max is the honest generalisation.
- When `cfg.anomaly == "climatology"` the units are `"σ"` (z-scores), overriding the
  index's own units.
- Drop the mid label when the ramp is too narrow for three non-overlapping labels (at
  `dimensions: 256` the bar is ~100 px). Measure with `draw.textbbox`, as
  `draw_scale_bar` already does.

Keep the function shape-preserving — it returns an array the same size it received.
`test_add_colorbar_preserves_shape_and_draws` must stay green.

**Tests** (add to `tests/test_render.py`):

- `test_colorbar_draws_min_mid_max_ticks` — assert three tick positions are marked.
- `test_colorbar_shows_units_for_thermal_index` — an `lst` cfg produces a bar carrying
  `°C`; an `ndvi` cfg does not.
- `test_colorbar_adds_zero_tick_only_when_range_spans_zero` — `(-3, 3)` gets a zero tick,
  `(15, 40)` does not.

---

## Task 3 — Antialiased, more visible region outline

**Fixes feedback #2** (WNE boundary faint and pixelated).

`render.draw_region` draws with PIL `ImageDraw.line`, which does **no antialiasing** — so
every diagonal segment of the Grumsin polygon is a hard staircase. The core is also only
`max(2, h/430)` px wide, which is why it reads as faint.

Changes in `gee_animation/render.py`:

- Rework `draw_region` to build an `L`-mode mask at `ss×` the frame size, draw the rings
  into it with the existing lon/lat → pixel mapping, LANCZOS-downsample the mask to frame
  size, then alpha-composite the dark casing and the amber core through their masks. Keep
  the existing casing-under-core scheme — it is what keeps the outline visible over both
  dark forest and hot LST pixels.
- Choose `ss` by frame size: `4` for frames up to ~1200 px tall, `2` above, so a 4K render
  does not allocate a ~130 MB mask.
- **Hoist the mask construction out of the per-frame loop in `render()`.** The rings,
  bounds and frame size are identical for every frame, so build the masks once before the
  loop and only composite per frame. Without this the supersampling cost is paid 5–15×
  for an identical result.
- Add a `region_line_width` knob: `RunConfig` field read from `render.region_line_width`,
  default `None` meaning the current `max(2, h/430)` behaviour. Thread it into
  `draw_region`'s existing `width` parameter so the reviewer's "hebt sich schwach ab" can
  be tuned from config without a code change.

Keep `draw_region`'s signature backwards-compatible — `tests/test_render.py:82`
(`test_draw_region_maps_coords_and_draws_outline`) calls it directly with
`(rgb, bounds, rings, color=…, width=1)`. That test asserts `out[..., 0].max() == 255`;
keep the core centreline fully opaque so a `width=1` line still reaches full colour, rather
than relaxing the assertion.

**Tests** (add to `tests/test_render.py`):

- `test_draw_region_antialiases_diagonal_edges` — draw a diagonal ring and assert
  intermediate (non-0, non-255) values appear along the edge, which the old hard-line
  renderer could never produce.
- `test_draw_region_respects_configured_line_width` — a larger width marks more pixels.
- Existing `test_draw_region_maps_coords_and_draws_outline` must stay green unmodified.

---

## Task 4 — Move label bars into added margins

**Fixes feedback #6** ("Ausschnitt zu knapp; die halbtransparenten Streifen sollten mit
zum sichtbaren Bild gehören").

`draw_info_bar` and `annotate` each paint a translucent bar of height `h//12` **over** the
imagery — together occluding ~17 % of every frame. The fix is to grow the canvas and put
the bars in the new margin, so no imagery is hidden.

This is the most delicate task in the plan: two ordering traps, both silent.

Changes in `gee_animation/render.py`:

- Add `add_margins(rgb, top_h, bottom_h)` returning a taller array with the imagery
  unchanged in the middle. **Keep `annotate` and `draw_info_bar` shape-preserving** — they
  will simply be called on the padded array and draw into the margin. This keeps
  `test_annotate_keeps_shape_and_type` green and makes the change additive.
- **Reorder the `render()` loop** so georeferenced overlays are drawn on pure imagery,
  before any padding exists:

```
fetch → colorize → apply_nodata → upscale
      → draw_region → draw_scale_bar      # georeferenced: on the imagery only
      → add_margins                       # canvas grows
      → draw_info_bar (top margin) → annotate (bottom margin) → add_colorbar
      → letterbox
```

  **Trap 1:** `draw_region` maps lon/lat rings linearly across the *full* array `w×h`. If
  it runs after padding, the WNE outline silently shifts up by ~8 % of the frame — it will
  still look like a plausible outline, just in the wrong place. This ordering is the fix.

- **Trap 2:** `_output_spec` derives the canvas from the imagery aspect ratio. If margins
  are added afterwards, an `aspect: "16:9"` request yields something taller than 16:9.
  Compute the margin heights from the intended output height first, subtract them from the
  place box, upscale the imagery into the reduced box, then pad.
- Rebase `add_colorbar`'s `y_offset` and `draw_scale_bar`'s internal `bar_h = h // 12`
  (which currently exists purely to dodge the bottom label bar) onto the imagery rect
  rather than the full array.
- `_pad_to_even` already guards libx264's even-dimension requirement, so odd margin heights
  are safe.

**Tests** (add to `tests/test_render.py`):

- `test_render_keeps_region_outline_on_imagery_when_margins_added` — a synthetic frame with
  a known ring; assert the outline lands at the same *imagery-relative* pixel position with
  and without margins. This is the regression test for Trap 1.
- `test_add_margins_keeps_imagery_unoccluded` — the imagery sub-rect of the padded array is
  byte-identical to the input.
- `test_output_spec_accounts_for_margins` — `aspect: "16:9"` plus margins still yields a
  16:9 canvas. Regression test for Trap 2.

---

## Task 5 — Finer cadence

**Fixes feedback #3 and #4** (one frame per month makes leaf-out a jump, not a transition).

`compositing.monthly_median` buckets by calendar month and `config.SUPPORTED_CADENCES` is
hard-restricted to `{"monthly"}`. Sentinel-2 revisits every 5 days, so finer bins are
available in the data.

Changes in `gee_animation/compositing.py`:

- Generalize `month_starts` → `period_starts(start, end, cadence)` returning
  `[(label, period_start_iso, period_end_iso)]`. **Keep `month_starts` as a thin wrapper**
  returning just the start strings — `tests/test_compositing.py` and `gui.py:18` both use
  it.
- Generalize `monthly_median` → `composite(collection, cfg, ee_module=ee)`, keeping
  `monthly_median` as an alias: `cli.py`, `api.py` and `gui.py` all name it in their `deps`
  tables.
- Replace the `format("YYYY-MM")` + `aggregate_array("ym")` counting with a single
  `aggregate_array("system:time_start").getInfo()`, bucketed client-side. One round trip as
  today, but cadence-agnostic. (Global constraint 4: still exactly one `getInfo`.)
- Labels: keep `YYYY-MM` for monthly; use `YYYY-MM-DD` for sub-monthly cadences. Verified
  safe for `anomaly.py` (`_month` reads `label[5:7]`, which still yields the month) and for
  `render._write_frames` filenames.

Changes in `gee_animation/config.py`:

- `SUPPORTED_CADENCES = {"monthly", "semimonthly", "10day"}`. `semimonthly` splits at the
  1st and 16th; `10day` splits at the 1st, 11th and 21st (so the last bin of a month is
  8–11 days — this keeps bins aligned to months, which matters for the anomaly baseline).
- In `validate()`, **reject `anomaly` combined with a sub-monthly cadence**: `anomaly.py`
  divides a period mean by a *monthly* climatology σ, so a 10-day slice would produce
  inflated z-scores that still look plausible.
- Warn (do not reject) when a sub-monthly cadence is combined with `sensor: landsat` —
  Landsat's 16-day repeat leaves most 10-day bins empty.

Update `gui.py`'s `n_months = len(month_starts(...))` progress estimate to use
`period_starts`. Leave `debug_month` monthly-only — it parses `YYYY-MM`.

**Tests** (add to `tests/test_compositing.py` and `tests/test_config.py`):

- `test_period_starts_semimonthly_splits_at_1st_and_16th`
- `test_period_starts_10day_splits_at_1_11_21`
- `test_month_starts_wrapper_still_returns_start_strings` — the back-compat alias.
- `test_composite_labels_sub_monthly_periods_with_dates`
- `test_validate_rejects_anomaly_with_sub_monthly_cadence`
- Existing `test_month_starts_*` and `test_monthly_median_*` must stay green unmodified.

---

## Task 6 — Usable-scene inventory

**Implements request #7**: a list of usable scenes per period, so the stakeholder can be
shown *why* smooth transitions are hard.

Today the pipeline surfaces only the surviving count (`Frame.n_scenes`, drawn as `n=` on
each frame). The rejected scenes — and the reason each was rejected — are invisible.

New module `gee_animation/inventory.py`:

- `scene_inventory(cfg, frame_geom, region_geom, ee_module=ee) -> list[SceneRecord]`, where
  `SceneRecord` is a `namedtuple` of
  `(period_label, date, mission, scene_cloud_pct, region_cloud_pct, usable, reason)`.
  Follow the `Frame` namedtuple style already in `compositing.py`.
- Build the candidate set **unfiltered**, then evaluate each scene against
  `cfg.max_cloud_percent` and `cfg.region_max_cloud_percent` to fill `usable` and a
  human-readable `reason` (e.g. `"scene cloud 72% > 60%"`, `"region cloud 34% > 10%"`,
  `""` when usable). `collection.build` applies both filters inline today, so add an
  `apply_cloud_filters: bool = True` parameter — the default keeps every existing caller
  unchanged.
- Region cloud fraction comes from the sensor's existing `cloud_band` callable
  (`products.Sensor.cloud_band`) reduced over `region_geom`. Map it server-side and pull
  everything with **one** `aggregate_array(...).getInfo()` (global constraint 4). Give
  `reduceRegion` `scale=cfg.scale`, `bestEffort=True` and an explicit `maxPixels`, or a
  multi-year inventory will exceed EE's request limits.
- `Sensor.scene_cloud_property` is `None` for MODIS — emit `None` for that column rather
  than raising.
- Bucket with `compositing.period_starts` so the inventory always matches the cadence
  actually rendered.

Output: write `out/<name>_inventory.csv` (use the stdlib `csv` module) and log a per-period
summary line, e.g. `2022-05: 7 scenes, 2 usable`.

CLI: add an `--inventory` flag to `gee_animation/cli.py` that short-circuits before
rendering — mirror how `run()` already short-circuits for `cfg.debug_month`, and route it
through the same `deps` table so it stays testable without EE.

**Tests** (new `tests/test_inventory.py`, plus one in `tests/test_collection.py`):

- `test_scene_inventory_marks_rejected_scenes_with_reason` — a faked collection with one
  clear and one cloudy scene yields `usable=True`/`False` and a populated `reason`.
- `test_scene_inventory_handles_sensor_without_cloud_property` — MODIS-style sensor with
  `scene_cloud_property=None` produces records rather than raising.
- `test_scene_inventory_issues_a_single_getinfo` — assert the fake's `getInfo` call count
  is 1, locking in global constraint 4.
- `test_build_without_cloud_filters_keeps_all_scenes` — the new `build()` parameter.

---

## Task 7 — Cross-year "best month" mode

**Implements request #8**: pull a cloud-free image from any year, as long as the month is
right. Optically nice, scientifically not — so the output must say what it is doing.

Opt-in and off by default. For each period, pool scenes from the **same calendar period
across several years** and pick the least-cloudy one.

```yaml
pool_years: [2019, 2024]      # inclusive range of years to draw from
pool_strategy: least_cloudy   # least_cloudy (default) | median
```

**Before implementing, verify** that `collection.py`'s Sentinel-2 builder uses
`COPERNICUS/S2_SR_HARMONIZED` and not plain `S2_SR`. Sentinel-2's processing baseline 04.00
(January 2022) introduced `BOA_ADD_OFFSET`, which shifts reflectance; pooling 2019–2024
crosses that boundary. The harmonized collection corrects it. If the code uses the
un-harmonized collection, report this as a blocking finding rather than proceeding — pooled
frames would step in brightness at the 2022 boundary in a way that mimics a real signal.

Changes in `gee_animation/compositing.py`:

- When `cfg.pool_years` is set, widen the collection's date range to span those years, then
  bucket by calendar period **ignoring the year**. Reuse the
  `ee.Filter.calendarRange(m, m, "month")` pattern already used in `anomaly.py`.
- `least_cloudy` — select the single scene with the lowest region cloud fraction across all
  pooled years. This is what was asked for: sharpest, no averaging.
- `median` — median across all pooled years for that period. Smoother and fills holes
  better, but blurs and mixes years. Keep as the documented alternative.
- **Labelling is mandatory, not optional.** A frame showing May 2021 inside a nominally
  2022 animation must say so: the frame label becomes `2022-05 ← 2021`, and `_info_text`
  gains a `pooled years 2019–2024` note so the provenance is on every frame. Treat a
  missing source-year label as a failing test, not a cosmetic gap — an unlabelled pooled
  render is a misleading artefact, and that is the exact failure the requester flagged.

Changes in `gee_animation/config.py`:

- Add `pool_years: list = None` and `pool_strategy: str = "least_cloudy"` to `RunConfig`,
  read from the top-level YAML keys.
- In `validate()`: reject `anomaly` combined with `pool_years` (the climatology baseline is
  itself multi-year, so pooling would compare a borrowed year against it); reject an
  unknown `pool_strategy`.
- Warn when `pool_years` is combined with `sensor: landsat` without a `missions` whitelist —
  L7/L8/L9 differ radiometrically and L7 has the SLC-off gap, so pooled Landsat frames can
  step between missions.

Add a documented `config/pooled.example.yaml` following the style of the existing example
configs, with comments stating plainly that the mode is cosmetic and not suitable for
quantitative analysis.

**Tests** (add to `tests/test_compositing.py` and `tests/test_config.py`):

- `test_pool_years_picks_least_cloudy_across_years` — faked scenes for May 2021 (10 % cloud)
  and May 2022 (80 %) with `pool_years: [2021, 2022]` selects the 2021 scene.
- `test_pooled_frame_label_names_source_year` — the label carries `← 2021`.
- `test_pool_strategy_median_composites_all_years`
- `test_validate_rejects_anomaly_with_pool_years`
- `test_validate_rejects_unknown_pool_strategy`