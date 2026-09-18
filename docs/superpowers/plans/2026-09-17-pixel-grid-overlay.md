# Pixel-grid overlay for LST frames — implementation plan

Status: **implemented** (2026-09-17, approved as "just add 100m grid"). Decisions
taken on the two open questions: `upscale: nearest` for the grid variant, and
"· 100 m grid" appended to the subtitle. Shipped as `render.pixel_grid`,
`config/r12_lst_pretty_10yr_grid.yaml`, tests in test_config / test_cache /
test_render.
Trigger: the R12 surface-temperature request — "a version where the pixel raster
is overlayed … for LST the 100 m grid would be fine".

Companion run already in flight: `config/r12_lst_pretty_10yr.yaml` (harmonic LST
on the R12 frame, same recipe as `wne_lst_pretty_2018_2026`). The grid variant
must be a **pure cache hit** on that run's thumbnails — this plan adds one
client-side overlay and nothing that touches what Earth Engine computes.

## What is being drawn, and why it is honest

The thermal band of Landsat 8/9 is acquired at **100 m** and delivered resampled
to the 30 m reflectance grid. `render._cap_dimensions` already knows this: the
R12 LST run logs

```
capping fetch dimensions 1440 -> 162 to native (~100m/px for lst)
```

so every LST frame *is* a 162 × 69 raster of ~100.6 m cells (16.3 km / 162),
anchored at the frame's bbox corner in UTM 33N, which lanczos then upscales to
1920 wide. **The rendered raster is therefore known exactly** — it is the
thumbnail's own pixel grid — and the overlay should trace *those* cell edges,
not multiples of 100 m in UTM. A UTM-anchored mesh would sit up to a cell off the
blocks the viewer actually sees and cut through them, which is the one thing a
"pixel raster" overlay must not do.

It is still not the TIRS acquisition grid (Earth Engine resampled to ours), and
the frame will not claim it is: it is the grid of the values on screen.

Not in scope: a grid on the NDVI run. Its fetch is capped at 10 m => ~1,640 cells
across 1920 px, a solid wall of lines.

## Design

### Config: `render.pixel_grid: true`

- Boolean flag, default off (today's behaviour, byte-identical). No metres, no
  CRS validation: the spacing is whatever the fetched raster is, which
  `_cap_dimensions` already made honest to the product's native GSD.
- Parsed with the other render flags (`_RENDER_FLAGS` / `_flag`), so a typo'd
  value gets the one-line config error.

### Cache: client-side

Add `"pixel_grid"` to `cache.CLIENT_SIDE_FIELDS` with the same one-line
justification the other overlay knobs carry (drawn locally over pixels already
fetched). Without this the grid variant would refetch all 116 months.

### Render: one mask, drawn under the region outline

`render._grid_mask(native_shape, out_shape, line_px) -> np.ndarray` — float32
`(h, w)` alpha in 0..1, with a line at every native cell boundary
`x = j · w / native_w` for `j = 0..native_w` and `y = i · h / native_h`, rounded
to the output pixel. It needs only the two array shapes, both known at the
first frame (the fetched array before `_upscale_rgb`, and the upscaled one after),
so it is built once and reused like `region_masks`. Line width
`max(1, round(h / 1080))`. No supersampling: axis-aligned lines have no staircase.

In `render()`, right after the upscale and **before** `_composite_region`:

```
native_hw = rgb.shape[:2]                      # before _upscale_rgb
...
if pixel_grid and grid_mask is None:
    grid_mask = _grid_mask(native_hw, rgb.shape[:2], ...)
rgb = _composite_alpha(rgb, grid_mask, GRID_RGB, GRID_ALPHA)   # touched pixels only
```

Grid first so the amber region outline stays on top. Drawn on pure imagery, before
any furniture (Trap 1 in `draw_region`'s docstring): once margins exist the
mapping would slide. Because it sits before the RAW capture point, `raw_frames`
of a grid run carry the grid — intended: the grid run is its own `name`.

Colour: white at 35 % alpha (`GRID_RGB = (255, 255, 255)`, `GRID_ALPHA = 0.35`).
On the turbo palette this reads on the dark-blue and red ends and softens over
yellow-green rather than vanishing; a black mesh dies on the cold end. Fixed
constants for now, not config — one flag is enough to ship.

Density check on this frame: 162 cells across 1920 px, one every 11.85 px; 1 px
lines touch ~8 % of the imagery. Visible mesh, not a haze.

### Upscale: `nearest` for the grid variant

With lanczos the blocks are smoothed and a mesh over smooth gradients looks
arbitrary. The grid config sets `upscale: nearest` (as the MODIS config does to
"keep the 1 km cells honest") so each cell is a flat block and the mesh sits on
its edges. `upscale` is already client-side, so this is free too. **Open question
for Christian:** nearest + grid (blocks, honest) vs lanczos + grid (smooth,
prettier, edges less meaningful)? The plan assumes nearest; both are cache hits,
so trying the other afterwards costs only an encode.

### On-frame wording

The header's second line is shared with the provenance caveats (here
"modelled · 10 generated frames"), and `_fit_header_line2` drops the subtitle
before the caveats. Proposal: leave the subtitle alone and add **nothing**
on-frame — the mesh is self-evident, and "100 m" belongs in the description
where the video is shown. **Open question for Christian:** do you want
"· 100 m pixel grid" appended to the subtitle anyway (may truncate at 720p)?

## Steps (TDD, in this order)

1. `tests/test_config.py`: parses `pixel_grid: true`; rejects a non-boolean the
   way the other `_RENDER_FLAGS` do; absent => `False`.
2. `config.py`: field + parse.
3. `tests/test_cache.py` (or wherever `CLIENT_SIDE_FIELDS` is pinned): the thumb
   key is unchanged when `pixel_grid` flips.
4. `cache.py`: allowlist entry.
5. `tests/test_render.py`:
   - `_grid_mask((5, 10), (50, 100))`: alpha = 1 exactly on columns 0,10,…,100
     (clipped to 99) and rows 0,10,…,40, 0 elsewhere.
   - composite touches only mask pixels (bit-exact elsewhere, like the region
     outline test).
   - `render()` builds the mask once across frames, draws it before the region
     outline (outline pixels win where they cross), and skips it when unset.
6. `render.py`: `_grid_mask`, the composite, the `render()` wiring.
7. Docs: README "Output knobs" line, `docs/rendering-products.md` free-list,
   and the `rendering-showcase-animations` skill's **Free** list (`pixel_grid`).
8. `config/r12_lst_pretty_10yr_grid.yaml`: copy of the LST config with
   `name: r12_lst_pretty_10yr_grid`, `pixel_grid: true`, `upscale: nearest`.
   Render — expected to fetch nothing; the cost is the 1,276-frame encode.
9. Verify: read one summer and one winter frame; confirm 20 cells span the 2 km
   scale bar, and that every mesh line sits on a block edge.

Estimated effort: under an hour of code and tests, then the encode.

## Not doing

- A metre-spacing knob (`pixel_grid_m`) anchored to UTM multiples — rejected
  above because it would cut through the rendered cells.
- Colour/alpha knobs.
