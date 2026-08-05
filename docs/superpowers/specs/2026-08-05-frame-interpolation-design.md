# Frame interpolation — design

Insert generated frames between observations so an animation reads as continuous motion
rather than a slideshow.

## Context

Animations are built one frame per period — monthly by default. At any watchable frame
rate that reads as a jump-cut sequence: the 5-year runs are 60 frames covering 60 months,
and vegetation green-up between two consecutive frames arrives as a step change.

The request is for roughly 10 intermediate frames per observation so the result "looks
like it was filmed".

**This fabricates imagery for dates that were never observed.** Everything else in this
pipeline is scrupulous about that line — native resolution is never faked, borrowed years
carry `← YYYY`, pooled modes are labelled cosmetic, and pixels with no observation are
painted neutral grey rather than guessed. Interpolation is a legitimate presentation
device, but it gets the same treatment: visible on the frame, named in the info bar, and
impossible to mistake for an observation.

## Configuration

```yaml
render:
  fps: 24                  # output frame rate
  interpolate: 10          # frames inserted per one-period step (0 or absent = off)
  interpolate_mode: auto   # auto | crossfade | data
```

- `interpolate: 0` or absent — feature off, behaviour identical to today.
- `interpolate_mode: auto` — `data` for single-band indices, `crossfade` for composites
  (`rgb`, `cir`). Composites arrive from Earth Engine already coloured, so there is no
  index array to interpolate; `data` mode is not available for them and `auto` resolves
  this without the user having to know.
- `fps` keeps its plain meaning: output frames per second. Interpolating multiplies the
  frame count, so duration grows proportionally unless `fps` is raised. Deliberately no
  hidden auto-scaling — 10× interpolation at `fps: 2` yields a ten-times-longer video,
  which is occasionally what someone wants.

Validation: `interpolate >= 0`; `interpolate_mode` in the known set; reject
`interpolate_mode: data` for a composite index with a message naming `crossfade`.

## Proportional spacing

Periods are not evenly spaced. Bins below `min_scenes` are dropped, so the 5-year LST run
produced 55 frames for 60 months. `interpolate` therefore counts frames **per one-period
step**, and a gap of *k* periods receives `k × interpolate` intermediate frames.

Playback speed then tracks elapsed time: a two-month gap takes twice as long to traverse
as a one-month gap. The alternative — a fixed count between every pair — would play a
three-month absence at the same rate as a one-month step, implying change happened faster
than it did.

## Interpolation modes

**`data`** — interpolate the index values (before colorizing), then colorize each
generated frame with the run's fixed `viz_min`/`viz_max`. Linear interpolation of NDVI or
temperature between two dates is a crude but defensible temporal model, and because every
frame is colorized with the same fixed range the colour bar stays exactly valid.

**`crossfade`** — linearly blend the RGB arrays. The only option for composites. Reads
unmistakably as a dissolve, which is itself honest: nobody mistakes a visible cross-fade
for an observation.

Both are `a * (1 - t) + b * t` on float, at fractions `t = i / (steps + 1)`. The
difference is only *what* is blended — index values or finished colour.

## No-data handling

`apply_nodata` paints pixels with no valid observation neutral grey. Blending *after*
that step would smear grey into valid imagery around every cloud hole.

So values and validity masks are carried separately, and `apply_nodata` runs last, on each
generated frame:

| endpoint A | endpoint B | generated pixel |
|---|---|---|
| valid | valid | blended |
| valid | invalid | **hold A's value**, marked valid |
| invalid | valid | **hold B's value**, marked valid |
| invalid | invalid | invalid → neutral grey |

Holding the valid endpoint rather than fading toward grey matters: a cloud hole present in
one observation and absent in the next would otherwise produce a grey pulse pumping in and
out on every transition, which is both ugly and misleading — it would suggest data
appearing and vanishing where in fact one observation simply had a hole.

## Where it happens in `render()`

Interpolation operates on **imagery only, before overlays**. Blending finished frames
would cross-fade the period label, the `n=` scene count and the colour bar into
illegible ghosting.

Current per-frame loop:

```
fetch → colorize → apply_nodata → upscale → georeferenced overlays
      → margins → labels → letterbox
```

New shape:

```
fetch all (already concurrent)
  → generate sequence of (imagery, label, is_real)      <- interpolation lives here
  → existing per-frame drawing loop, unchanged, over that sequence
```

The drawing loop does not need to know whether a frame was observed or generated; it
receives imagery and a label exactly as it does today. This keeps the change additive and
leaves the Task 4 margin ordering and the region-mask hoist untouched.

## Labelling

| frame | label |
|---|---|
| observed | `2022-05  n=3` — unchanged from today |
| generated | `2022-05 -> 2022-06  30%` |

The info bar gains `interpolated: N frames between observations`, alongside the existing
pooled-years note where both apply.

A borrowed frame under `gap_fill` keeps its `← YYYY`; a frame generated *between* a real
and a borrowed frame is labelled by its endpoints as above. The arrow characters are
folded for drawing by the existing `_drawable` mechanism (Pillow's default font has no
`←` glyph).

## Streaming — required, not an optimisation

`render()` accumulates every finished frame in a list before encoding. At 60 observations
× 10 that is 600 frames; at 2560×2815×3 bytes each, roughly **12 GB**.

Generated frames are therefore **streamed** to the encoder — `imageio.get_writer()` /
`append_data()` — rather than collected, making peak memory O(1) frames instead of
O(frame count). This is the refactor two efficiency reviews flagged and we deferred twice;
interpolation makes it mandatory.

Consequences:

- **Per-frame PNGs are written for observed frames only.** 600 PNGs of which 540 are
  generated would be noise, and the existing filenames key off the period label.
- **GIF defaults off when `interpolate > 0`.** 600 quantized frames would be enormous and
  slow; `render.gif: true` still forces it for anyone who wants it.
- The return contract of `assemble()` is unchanged: MP4 path, optional GIF path, and the
  observed frames' PNG paths.

## Testing

Network-free throughout, via the injected `fetch` seam.

- **Observed frames are byte-identical** to a non-interpolated render of the same config.
  This is the central guarantee: interpolation adds frames, it never alters real ones.
- Frame count matches proportional spacing, including across a multi-period gap.
- Labels: observed frames keep their plain label; generated frames carry endpoints and
  percentage; the info bar names the interpolation.
- No-data: a pixel valid in one endpoint and invalid in the other holds the valid value
  and does not fade toward grey; a pixel invalid in both stays grey.
- `interpolate: 0` produces exactly today's output.
- `interpolate_mode: data` on a composite index is rejected at validation.
- Streaming: peak retained frames does not scale with the interpolated frame count.

## Out of scope

- **Optical-flow / motion interpolation.** The subject does not move — vegetation greens
  up and fields are harvested in place — so flow is ill-posed here and tends to warp field
  boundaries and smear cloud edges. It also needs a heavy dependency. Cross-fade and
  data-space interpolation cover the request.
- Easing curves. Linear only; anything else would misrepresent rates of change.
- Interpolating across `pool_years` provenance boundaries differently from any other pair.
