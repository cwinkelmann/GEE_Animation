# Frame Interpolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert generated frames between observations so an animation reads as continuous motion rather than a slideshow.

**Architecture:** Interpolation operates on imagery only, before overlays are drawn — blending finished frames would ghost the labels and colour bar. A generator yields `(imagery, valid, label, is_real)` tuples; the existing per-frame drawing loop consumes them unchanged. Because 10× frames cannot be held in memory, frames stream to the encoder instead of accumulating in a list.

**Tech Stack:** Python 3.11, numpy, Pillow, imageio + imageio-ffmpeg, pytest. No new dependencies.

Spec: `docs/superpowers/specs/2026-08-05-frame-interpolation-design.md`

## Global Constraints

- The unit suite stays network-free. Earth Engine is faked at the `ee_module` seam or via the injected `fetch`/`deps` parameters. Anything needing live EE goes behind the `integration` marker and the `GEE_INTEGRATION` env guard.
- `pytest -m "not integration"` must pass after every task. Current baseline: **343 passed, 2 deselected**.
- **Observed frames must remain byte-identical** to a non-interpolated render of the same config. Interpolation adds frames; it never alters a real one.
- `interpolate: 0` or absent produces output identical to today.
- Do not change `compositing.py`, `collection.py`, `inventory.py`, `cache.py`, or `metadata.py`.
- Do not change fetch behaviour or the cache key.
- Test files mirror modules (`tests/test_<module>.py`).
- Follow the surrounding code's idiom — comment density, naming, explanatory docstring style.
- The working tree has PRE-EXISTING unrelated changes to `docs/FEATURES.md`, `.claude/`, `.idea/`, and an untracked `config/wne_2yr_ndvi.example.yaml`. Stage only your own files; never `git add -A`.

---

## File Structure

| File | Responsibility |
|---|---|
| `gee_animation/interpolate.py` | **New.** Pure functions: blend two `(values, valid)` pairs at fraction `t`; expand a frame list into a sequence with generated frames between observations. No PIL, no I/O, no EE. |
| `gee_animation/config.py` | Add `interpolate`, `interpolate_mode` fields; parse from `render:`; validate. |
| `gee_animation/render.py` | Consume the sequence in `render()`; stream frames to the encoder; label generated frames; extend the info bar. |
| `tests/test_interpolate.py` | **New.** Unit tests for the pure blending and sequencing logic. |
| `tests/test_config.py` | Validation tests for the new keys. |
| `tests/test_render.py` | Integration of interpolation into `render()`: byte-identity of observed frames, labels, streaming, PNG/GIF policy. |

`interpolate.py` is deliberately free of PIL and I/O so the blending rules — especially the no-data table — can be tested directly on small arrays.

---

### Task 1: Blending two observations

**Files:**
- Create: `gee_animation/interpolate.py`
- Test: `tests/test_interpolate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `blend(a_vals, a_valid, b_vals, b_valid, t) -> (values, valid)`. `a_vals`/`b_vals` are float arrays of identical shape — 2-D index units for `data` mode, or `H×W×3` colour for `crossfade`; the function does not care which. `a_valid`/`b_valid` are boolean arrays of shape `values.shape[:2]`. `t` is a float in `[0, 1]`. Returns arrays of the same shapes.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pytest

from gee_animation.interpolate import blend


def test_blend_midpoint_of_two_valid_frames():
    a = np.array([[0.0, 10.0]])
    b = np.array([[10.0, 20.0]])
    ok = np.ones((1, 2), dtype=bool)
    vals, valid = blend(a, ok, b, ok, 0.5)
    assert np.allclose(vals, [[5.0, 15.0]])
    assert valid.all()


def test_blend_endpoints_are_exact():
    a = np.array([[1.0]]); b = np.array([[2.0]])
    ok = np.ones((1, 1), dtype=bool)
    assert blend(a, ok, b, ok, 0.0)[0] == pytest.approx(1.0)
    assert blend(a, ok, b, ok, 1.0)[0] == pytest.approx(2.0)


def test_blend_holds_the_valid_endpoint_instead_of_fading_to_grey():
    # A cloud hole present in one observation and absent in the next must not
    # produce a grey pulse: the generated pixel holds the valid endpoint's value.
    a = np.array([[5.0]]); b = np.array([[99.0]])
    a_ok = np.array([[True]]); b_ok = np.array([[False]])
    vals, valid = blend(a, a_ok, b, b_ok, 0.5)
    assert vals[0, 0] == pytest.approx(5.0)      # A's value, not a blend toward B
    assert valid[0, 0]                            # and it counts as data

    vals, valid = blend(b, b_ok, a, a_ok, 0.5)   # mirror: B invalid on the left
    assert vals[0, 0] == pytest.approx(5.0)
    assert valid[0, 0]


def test_blend_marks_pixels_invalid_only_when_both_endpoints_are():
    a = np.array([[1.0]]); b = np.array([[2.0]])
    no = np.array([[False]])
    _vals, valid = blend(a, no, b, no, 0.5)
    assert not valid[0, 0]


def test_blend_handles_colour_frames():
    a = np.zeros((1, 1, 3)); b = np.full((1, 1, 3), 10.0)
    ok = np.ones((1, 1), dtype=bool)
    vals, _valid = blend(a, ok, b, ok, 0.5)
    assert np.allclose(vals, 5.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_interpolate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.interpolate'`

- [ ] **Step 3: Write the implementation**

```python
"""Generate frames between observations so playback reads as motion.

Pure array maths — no PIL, no I/O, no Earth Engine — so the blending rules (in
particular the no-data table) can be tested directly on small arrays.
"""
from __future__ import annotations

import numpy as np


def blend(a_vals, a_valid, b_vals, b_valid, t: float):
    """Interpolate between two observations at fraction `t` in [0, 1].

    `a_vals`/`b_vals` are float arrays of identical shape — 2-D index units, or
    H×W×3 colour; this does not care which. `a_valid`/`b_valid` are boolean masks
    of shape ``values.shape[:2]``.

    Where both endpoints have data, the value is a linear blend. Where only one
    does, that endpoint's value is **held** rather than faded toward the no-data
    colour: a cloud hole present in one observation and absent in the next would
    otherwise pulse grey in and out on every transition, implying data appeared
    and vanished when in truth one observation simply had a hole. A pixel is
    no-data only when neither endpoint observed it.
    """
    both = a_valid & b_valid
    valid = a_valid | b_valid
    blended = a_vals * (1.0 - t) + b_vals * t
    # Broadcast the 2-D masks over a trailing colour axis when present.
    sel = both[..., None] if blended.ndim == 3 else both
    a_only = (a_valid & ~b_valid)[..., None] if blended.ndim == 3 else (a_valid & ~b_valid)
    values = np.where(sel, blended, np.where(a_only, a_vals, b_vals))
    return values, valid
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_interpolate.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/interpolate.py tests/test_interpolate.py
git commit -m "Add frame blending with hold-the-valid-endpoint no-data rule"
```

---

### Task 2: Expanding a frame list into a sequence

**Files:**
- Modify: `gee_animation/interpolate.py`
- Test: `tests/test_interpolate.py`

**Interfaces:**
- Consumes: `blend` from Task 1.
- Produces: `expand(items, steps, period_gap) -> Iterator[tuple]`, yielding `(values, valid, label, is_real)`.
  - `items` is a list of `(values, valid, label)` for the observed frames, in order.
  - `steps` is `cfg.interpolate` — generated frames per **one-period** step.
  - `period_gap(label_a, label_b) -> int` returns how many periods apart two labels are, so spacing is proportional to elapsed time. Task 3 supplies the real one; tests pass a stub.
  - `is_real` is `True` for observed frames, `False` for generated ones.
  - Generated labels are `f"{label_a} -> {label_b}  {pct}%"` where `pct` is `round(100 * t)`.

- [ ] **Step 1: Write the failing tests**

```python
from gee_animation.interpolate import expand


def _items(*labels):
    import numpy as np
    ok = np.ones((1, 1), dtype=bool)
    return [(np.array([[float(i)]]), ok, lab) for i, lab in enumerate(labels)]


def _adjacent(a, b):        # every pair is one period apart
    return 1


def test_expand_inserts_steps_between_each_pair():
    out = list(expand(_items("2022-05", "2022-06"), steps=3, period_gap=_adjacent))
    assert [lab for _v, _ok, lab, _real in out] == [
        "2022-05",
        "2022-05 -> 2022-06  25%",
        "2022-05 -> 2022-06  50%",
        "2022-05 -> 2022-06  75%",
        "2022-06",
    ]
    assert [real for *_r, real in out] == [True, False, False, False, True]


def test_expand_spacing_is_proportional_to_the_gap():
    # A two-period gap gets twice the generated frames of a one-period gap, so
    # playback speed tracks elapsed time rather than frame index.
    gaps = {("a", "b"): 1, ("b", "c"): 2}
    out = list(expand(_items("a", "b", "c"), steps=2, period_gap=lambda x, y: gaps[(x, y)]))
    generated = [lab for _v, _ok, lab, real in out if not real]
    assert sum(lab.startswith("a ->") for lab in generated) == 2
    assert sum(lab.startswith("b ->") for lab in generated) == 4


def test_expand_with_zero_steps_yields_only_observations():
    out = list(expand(_items("x", "y"), steps=0, period_gap=_adjacent))
    assert [lab for _v, _ok, lab, _r in out] == ["x", "y"]
    assert all(real for *_r, real in out)


def test_expand_single_frame_is_unchanged():
    out = list(expand(_items("only"), steps=5, period_gap=_adjacent))
    assert [lab for _v, _ok, lab, _r in out] == ["only"]


def test_expand_generated_values_lie_between_the_endpoints():
    out = list(expand(_items("a", "b"), steps=1, period_gap=_adjacent))
    mid = [v for v, _ok, _lab, real in out if not real][0]
    assert 0.0 < float(mid) < 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_interpolate.py -v`
Expected: FAIL — `ImportError: cannot import name 'expand'`

- [ ] **Step 3: Write the implementation**

Append to `gee_animation/interpolate.py`:

```python
def expand(items, steps: int, period_gap):
    """Yield ``(values, valid, label, is_real)`` with generated frames between items.

    `steps` is the number of generated frames per **one-period** step. Periods are
    not evenly spaced — bins below `min_scenes` are dropped — so a gap of k periods
    gets ``k * steps`` generated frames and playback speed tracks elapsed time. A
    fixed count per pair would play a three-month absence as fast as a one-month
    step, implying change happened faster than it did.
    """
    items = list(items)
    for i, (values, valid, label) in enumerate(items):
        yield values, valid, label, True
        if steps <= 0 or i + 1 >= len(items):
            continue
        b_vals, b_valid, b_label = items[i + 1]
        n = max(1, int(period_gap(label, b_label))) * steps
        for k in range(1, n + 1):
            t = k / (n + 1)
            gen_vals, gen_valid = blend(values, valid, b_vals, b_valid, t)
            yield gen_vals, gen_valid, f"{label} -> {b_label}  {round(100 * t)}%", False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_interpolate.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/interpolate.py tests/test_interpolate.py
git commit -m "Expand observations into a sequence with proportional interpolation"
```

---

### Task 3: Period distance from frame labels

**Files:**
- Modify: `gee_animation/interpolate.py`
- Test: `tests/test_interpolate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `period_gap(label_a, label_b) -> int` — the real `period_gap` for Task 2's parameter. Labels are `YYYY-MM` (monthly) or `YYYY-MM-DD` (sub-monthly), as produced by `compositing.period_starts`. Returns whole periods between them, minimum 1.

Monthly labels differ by whole months; sub-monthly labels are day-based. Deriving the gap from the label alone keeps `interpolate.py` free of any dependency on `compositing`.

- [ ] **Step 1: Write the failing tests**

```python
from gee_animation.interpolate import period_gap


def test_period_gap_counts_months_for_monthly_labels():
    assert period_gap("2022-05", "2022-06") == 1
    assert period_gap("2022-05", "2022-08") == 3
    assert period_gap("2021-11", "2022-02") == 3      # across a year boundary


def test_period_gap_counts_ten_day_slots_for_sub_monthly_labels():
    assert period_gap("2022-05-01", "2022-05-11") == 1
    assert period_gap("2022-05-01", "2022-06-01") == 3   # 3 slots per month
    assert period_gap("2022-05-21", "2022-06-01") == 1


def test_period_gap_is_at_least_one():
    assert period_gap("2022-05", "2022-05") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_interpolate.py::test_period_gap_counts_months_for_monthly_labels -v`
Expected: FAIL — `ImportError: cannot import name 'period_gap'`

- [ ] **Step 3: Write the implementation**

Append to `gee_animation/interpolate.py` (add `from datetime import date` at the top of the file):

```python
def _slot(label: str) -> int:
    """Ordinal position of a period label, in slots since year 0.

    Monthly labels ("2022-05") count whole months. Sub-monthly labels
    ("2022-05-11") count the 1st/11th/21st slots `compositing.period_starts`
    produces, three per month — so a 10-day cadence and a monthly one both give
    sensible distances without `interpolate` needing to know the cadence.
    """
    parts = label.split("-")
    year, month = int(parts[0]), int(parts[1])
    months = year * 12 + month
    if len(parts) < 3:
        return months * 3
    day = int(parts[2])
    return months * 3 + (0 if day < 11 else 1 if day < 21 else 2)


def period_gap(label_a: str, label_b: str) -> int:
    """Whole periods between two frame labels, at least 1."""
    return max(1, _slot(label_b) - _slot(label_a))
```

Note `_slot` multiplies monthly labels by 3 so both label styles share one scale.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_interpolate.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/interpolate.py tests/test_interpolate.py
git commit -m "Derive period distance from frame labels for proportional spacing"
```

---

### Task 4: Config keys and validation

**Files:**
- Modify: `gee_animation/config.py` (dataclass fields near `workers: int = 4` at line 114; `from_yaml` `render.get(...)` block near line 177; `validate()` near line 216)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RunConfig.interpolate: int = 0` and `RunConfig.interpolate_mode: str = "auto"`, read from `render.interpolate` and `render.interpolate_mode`.

- [ ] **Step 1: Write the failing tests**

```python
def test_interpolate_defaults_to_off():
    cfg = RunConfig.from_yaml(_write(_base()))
    assert cfg.interpolate == 0 and cfg.interpolate_mode == "auto"


def test_interpolate_is_read_from_the_render_block():
    y = _base().replace("  fps: 4", "  fps: 4\n  interpolate: 10\n  interpolate_mode: crossfade")
    cfg = RunConfig.from_yaml(_write(y))
    assert cfg.interpolate == 10 and cfg.interpolate_mode == "crossfade"


def test_validate_rejects_negative_interpolate():
    y = _base().replace("  fps: 4", "  fps: 4\n  interpolate: -1")
    with pytest.raises(ConfigError, match="interpolate"):
        RunConfig.from_yaml(_write(y))


def test_validate_rejects_unknown_interpolate_mode():
    y = _base().replace("  fps: 4", "  fps: 4\n  interpolate_mode: wobble")
    with pytest.raises(ConfigError, match="interpolate_mode"):
        RunConfig.from_yaml(_write(y))


def test_validate_rejects_data_mode_for_a_composite_index():
    # rgb/cir arrive from EE already coloured, so there is no index array to
    # interpolate; the error must name the alternative.
    y = _base().replace("index: ndvi", "index: rgb").replace(
        "  fps: 4", "  fps: 4\n  interpolate: 5\n  interpolate_mode: data")
    with pytest.raises(ConfigError, match="crossfade"):
        RunConfig.from_yaml(_write(y))
```

Check `tests/test_config.py`'s existing `_base()` / `_write()` helpers and match their conventions; adjust the `.replace()` anchors if `_base()` differs.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -k interpolate -v`
Expected: FAIL — `AttributeError: 'RunConfig' object has no attribute 'interpolate'`

- [ ] **Step 3: Write the implementation**

Add near `workers` in the dataclass:

```python
    # Generated frames inserted between observations so playback reads as motion
    # (from render.interpolate / render.interpolate_mode). 0 = off. "auto" picks
    # data-space interpolation for single-band indices and cross-fade for
    # composites, which arrive from EE already coloured.
    interpolate: int = 0
    interpolate_mode: str = "auto"
```

In `from_yaml`'s render block:

```python
                interpolate=int(render.get("interpolate", 0) or 0),
                interpolate_mode=str(render.get("interpolate_mode", "auto")),
```

In `validate()`:

```python
        if self.interpolate < 0:
            raise ConfigError("render.interpolate must be >= 0")
        if self.interpolate_mode not in INTERPOLATE_MODES:
            raise ConfigError(
                f"unknown render.interpolate_mode {self.interpolate_mode!r}; "
                f"supported: {sorted(INTERPOLATE_MODES)}")
        if self.interpolate_mode == "data" and INDICES[self.index].composite:
            raise ConfigError(
                f"render.interpolate_mode: data needs a single-band index; "
                f"{self.index!r} is a composite — use crossfade (or auto)")
```

Add a module-level constant beside `SUPPORTED_CADENCES`:

```python
INTERPOLATE_MODES = {"auto", "crossfade", "data"}
```

`INDICES` is already imported in `config.py` (`from .products import INDICES, get_product`) — confirm before use.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -k interpolate -v && pytest -m "not integration" -q`
Expected: PASS; suite ≥ 343 + 5

- [ ] **Step 5: Commit**

```bash
git add gee_animation/config.py tests/test_config.py
git commit -m "Add render.interpolate and render.interpolate_mode config keys"
```

---

### Task 5: Stream frames to the encoder

**Files:**
- Modify: `gee_animation/render.py` (`assemble` at line 794, `_write_mp4`, `_write_gif`, and `render()`'s accumulation of `rgb_frames`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `assemble_stream(frames_iter, cfg) -> list[Path]` — same return contract as `assemble` (MP4 path, then GIF path if written), but consumes an **iterator** of `H×W×3 uint8` arrays and never holds them all. `assemble(frames_rgb, cfg)` remains as a thin wrapper over it so existing callers and tests are unaffected.

**Why this task exists:** `render()` collects every finished frame in a list before encoding. With 60 observations × 10 generated frames that is 600 frames; at 2560×2815×3 bytes each, roughly 12 GB. Streaming makes peak memory O(1) frames.

- [ ] **Step 1: Write the failing tests**

```python
def test_assemble_stream_writes_mp4_from_an_iterator(tmp_path):
    from gee_animation.render import assemble_stream
    cfg = _cfg(tmp_path)
    frames = (np.full((16, 16, 3), v, np.uint8) for v in (0, 128, 255))
    paths = assemble_stream(frames, cfg)
    assert any(p.suffix == ".mp4" for p in paths)
    assert all(p.exists() for p in paths)


def test_assemble_stream_does_not_retain_every_frame(tmp_path):
    """Peak retained frames must not scale with the sequence length — this is what
    makes 600-frame interpolated runs possible at all."""
    from gee_animation.render import assemble_stream
    cfg = _cfg(tmp_path)
    cfg.gif = False
    live = []
    peak = []

    def gen():
        for i in range(40):
            a = np.full((16, 16, 3), i % 256, np.uint8)
            live.append(a)
            peak.append(len(live))
            yield a
            live.clear()          # caller drops its own reference each iteration

    assemble_stream(gen(), cfg)
    assert max(peak) <= 2, f"held up to {max(peak)} frames at once"


def test_assemble_still_accepts_a_list(tmp_path):
    cfg = _cfg(tmp_path)
    paths = assemble([np.zeros((16, 16, 3), np.uint8)] * 2, cfg)
    assert any(p.suffix == ".mp4" for p in paths)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_render.py -k assemble_stream -v`
Expected: FAIL — `ImportError: cannot import name 'assemble_stream'`

- [ ] **Step 3: Write the implementation**

Replace `_write_mp4`/`_write_gif`'s list parameters with incremental writers and add:

```python
def assemble_stream(frames_iter, cfg) -> list[Path]:
    """Encode an *iterator* of frames to MP4 (and GIF unless disabled).

    Frames are written as they arrive rather than collected: an interpolated run
    can reach several hundred frames, and at 4K that would be tens of gigabytes
    held at once. `assemble` wraps this for callers that already have a list.
    """
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / f"{cfg.name}.mp4"
    gif_path = out_dir / f"{cfg.name}.gif"
    want_gif = bool(getattr(cfg, "gif", True))
    paths: list[Path] = []
    gif_frames: list[np.ndarray] = []      # GIF needs them all; capped to preview size

    mp4 = imageio.get_writer(mp4_path, fps=cfg.fps, macro_block_size=1)
    try:
        for frame in frames_iter:
            mp4.append_data(_pad_to_even(frame))
            if want_gif:
                gif_frames.append(_cap_edge(frame, _GIF_MAX_EDGE))
    except Exception as exc:                       # ffmpeg missing / encode error
        mp4.close()
        mp4_path.unlink(missing_ok=True)
        if not want_gif:
            raise RuntimeError(f"MP4 encoding failed and render.gif is false: {exc}") from exc
        log.warning("MP4 write failed (%s); producing GIF only", exc)
    else:
        mp4.close()
        paths.append(mp4_path)

    if want_gif and gif_frames:
        _write_gif(gif_path, gif_frames, cfg.fps)
        paths.append(gif_path)
    return paths


def assemble(frames_rgb: list[np.ndarray], cfg) -> list[Path]:
    """List-taking wrapper over `assemble_stream` (unchanged contract)."""
    return assemble_stream(iter(frames_rgb), cfg)
```

Keep `_write_gif` as it is — a GIF genuinely needs every frame, and it is capped to `_GIF_MAX_EDGE` so the retained copies are small.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_render.py -v && pytest -m "not integration" -q`
Expected: PASS; existing `assemble` tests unchanged

- [ ] **Step 5: Commit**

```bash
git add gee_animation/render.py tests/test_render.py
git commit -m "Stream frames to the encoder instead of accumulating them"
```

---

### Task 6: Wire interpolation into render()

**Files:**
- Modify: `gee_animation/render.py` (`render()` at line 891; `_info_text` near line 215; `_write_frames`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `interpolate.expand`, `interpolate.period_gap` (Tasks 2–3); `assemble_stream` (Task 5); `cfg.interpolate`, `cfg.interpolate_mode` (Task 4).
- Produces: `render()` with unchanged signature and return contract.

**Resolving `auto`:** `crossfade` when `_is_composite(cfg)`, else `data`. In `data` mode the fetched index array is interpolated **before** `colorize`; in `crossfade` mode the already-coloured array is interpolated. Both then go through `apply_nodata` and the existing overlay/margin/label pipeline unchanged.

**Ordering (do not deviate):** interpolation happens on imagery, before any overlay is drawn. Blending finished frames would cross-fade the period label, the `n=` count and the colour bar into illegible ghosting.

**PNG policy:** `_write_frames` receives observed frames only. 600 PNGs of which 540 are generated would be noise, and the filenames key off the period label.

**GIF policy:** when `cfg.interpolate > 0` and the config does not explicitly set `gif`, default it to `False` — several hundred quantized frames is enormous and slow. An explicit `gif: true` still wins.

- [ ] **Step 1: Write the failing tests**

```python
def _fetch_two(image, cfg, geometry=None):
    return np.zeros((20, 20)), np.ones((20, 20), dtype=bool)


def test_render_interpolates_between_frames(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)
    cfg.interpolate = 2
    drawn = []
    real_annotate = r.annotate

    def spy(rgb, label):
        drawn.append(label)
        return real_annotate(rgb, label)

    monkeypatch.setattr(r, "annotate", spy)
    render([Frame("2022-05", object()), Frame("2022-06", object())], cfg,
           fetch=_fetch_two, geometry=None)
    assert drawn == ["2022-05", "2022-05 -> 2022-06  33%",
                     "2022-05 -> 2022-06  67%", "2022-06"]


def test_render_observed_frames_are_byte_identical_with_and_without_interpolation(tmp_path):
    """The central guarantee: interpolation adds frames, it never alters real ones."""
    plain = _cfg(tmp_path / "a"); plain.interpolate = 0
    interp = _cfg(tmp_path / "b"); interp.interpolate = 3
    frames = [Frame("2022-05", object()), Frame("2022-06", object())]
    render(frames, plain, fetch=fake_fetch, geometry=None)
    render(frames, interp, fetch=fake_fetch, geometry=None)
    for label in ("2022-05", "2022-06"):
        a = (tmp_path / "a" / f"anim_{label}.png").read_bytes()
        b = (tmp_path / "b" / f"anim_{label}.png").read_bytes()
        assert a == b, f"{label} changed when interpolation was enabled"


def test_render_writes_pngs_for_observed_frames_only(tmp_path):
    cfg = _cfg(tmp_path); cfg.interpolate = 5
    paths = render([Frame("2022-05", object()), Frame("2022-06", object())], cfg,
                   fetch=fake_fetch, geometry=None)
    pngs = [p for p in paths if p.suffix == ".png"]
    assert sorted(p.stem for p in pngs) == ["anim_2022-05", "anim_2022-06"]


def test_info_text_names_the_interpolation():
    from gee_animation.render import _info_text
    cfg = types.SimpleNamespace(index="ndvi", interpolate=10)
    assert "interpolated: 10 frames between observations" in _info_text(cfg)
    assert "interpolated" not in _info_text(types.SimpleNamespace(index="ndvi", interpolate=0))
```

Follow `tests/test_render.py`'s existing `_cfg` helper and `fake_fetch` style — several tests there already inject a fetch returning `(np.zeros((20, 20)), np.ones((20, 20), dtype=bool))`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_render.py -k interpolat -v`
Expected: FAIL — labels are the plain two, no generated frames

- [ ] **Step 3: Write the implementation**

`render()`'s loop currently reads (line 916):

```python
    for frame, (arr, valid) in _fetch_in_order(frames, cfg, fetch, geometry, workers):
        rgb = arr if arr.ndim == 3 else colorize(arr, cfg.viz_min, cfg.viz_max, cfg.palette)
        rgb = apply_nodata(rgb, valid)
        # (existing code elided: upscale, region overlay, scale bar, margins,
        #  info bar, label, colour bar, letterbox — none of it changes)
        rgb_frames.append(rgb)
    paths = assemble(rgb_frames, cfg)
```

Two changes. First, add a generator **above** `render()` that turns fetched frames into
the interpolated sequence:

```python
def _imagery_sequence(frames, cfg, fetch, geometry, workers, composite):
    """Yield ``(rgb, valid, display_text, is_real)`` — the imagery for every frame,
    generated ones included, before any overlay is drawn.

    Interpolating here rather than on finished frames is deliberate: blending
    completed frames would cross-fade the period label, the n= count and the colour
    bar into illegible ghosting.
    """
    steps = int(getattr(cfg, "interpolate", 0) or 0)
    mode = getattr(cfg, "interpolate_mode", "auto")
    if mode == "auto":
        mode = "crossfade" if composite else "data"

    def display_for(frame):
        n = getattr(frame, "n_scenes", None)
        source = getattr(frame, "source", None)
        shown = f"{frame.label} ← {source}" if source is not None else frame.label
        return f"{shown}  n={n}" if n is not None else shown

    fetched = list(_fetch_in_order(frames, cfg, fetch, geometry, workers))
    if not steps or len(fetched) < 2:
        for frame, (arr, valid) in fetched:
            rgb = arr if arr.ndim == 3 else colorize(arr, cfg.viz_min, cfg.viz_max, cfg.palette)
            yield rgb, valid, display_for(frame), True
        return

    # `data` interpolates index units before colouring, so every generated frame is
    # coloured with the run's fixed viz range and the colour bar stays exactly valid.
    # `crossfade` blends finished colour — the only option for composites, which
    # arrive from EE already coloured.
    if mode == "data" and not composite:
        items = [(arr, valid, frame.label) for frame, (arr, valid) in fetched]
        post = lambda v: colorize(v, cfg.viz_min, cfg.viz_max, cfg.palette)
    else:
        items = [(arr if arr.ndim == 3 else colorize(arr, cfg.viz_min, cfg.viz_max, cfg.palette),
                  valid, frame.label) for frame, (arr, valid) in fetched]
        post = lambda v: v

    display = {frame.label: display_for(frame) for frame, _ in fetched}
    for values, valid, label, is_real in interpolate.expand(
            items, steps, interpolate.period_gap):
        text = display[label] if is_real else label
        yield post(values), valid, text, is_real
```

Note the generated label passes through as-is (`2022-05 -> 2022-06  33%`), while an
observed frame gets its full display text including `n=` and any `← source`.

Second, rewrite the loop body to consume that sequence, drop the local `display`/`text`
composition it replaces, and stream instead of accumulate:

```python
    png_labels: list[str] = []

    def finished():
        nonlocal output, region_masks
        for rgb, valid, text, is_real in _imagery_sequence(
                frames, cfg, fetch, geometry, workers, composite):
            rgb = apply_nodata(rgb, valid)
            ...          # upscale / overlays / margins / info bar — unchanged
            rgb = annotate(rgb, _drawable(text))
            ...          # colorbar / letterbox — unchanged
            if is_real:
                png_labels.append(text.split("  ")[0].replace(" ← ", "_"))
            yield rgb

    paths = assemble_stream(finished(), cfg)
```

Keep `_write_frames` writing observed frames only. The simplest correct approach: have
`finished()` write each observed frame's PNG inline via `_write_frames(out_dir, cfg.name,
[rgb], [frame_label])` as it is produced, collecting the returned paths — that avoids
retaining observed frames just to write them afterwards, which would reintroduce the
memory problem Task 5 solved. Use the frame's `label` (the clean period key), not the
display text.

Extend `_info_text`:

```python
    steps = getattr(cfg, "interpolate", 0)
    if steps:
        text += f"   interpolated: {steps} frames between observations"
```

Default GIF off for interpolated runs, in `render()` before assembling:

```python
    if steps and getattr(cfg, "gif", None) is None:
        cfg.gif = False        # several hundred quantized frames is enormous
```

This requires `RunConfig.gif` to default to `None` rather than `True`, with `assemble_stream`'s `getattr(cfg, "gif", True)` reading `True` when unset — verify and adjust the default in `config.py` so existing behaviour is preserved.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -m "not integration" -q`
Expected: PASS; ≥ 343 + all new tests

- [ ] **Step 5: Commit**

```bash
git add gee_animation/render.py tests/test_render.py
git commit -m "Generate interpolated frames in render() and stream them out"
```

---

### Task 7: Example config and documentation

**Files:**
- Create: `config/wne_interpolated.example.yaml`
- Modify: `docs/how-the-timeseries-is-made.md` (new section after §7 "Turning the data into a picture")
- Test: none (documentation and a config that the existing config-loading tests already cover by pattern)

- [ ] **Step 1: Write the example config**

```yaml
# Smooth playback: generated frames between observations so the animation reads
# as motion rather than a slideshow.
#
# Generated frames show dates that were never observed. They are labelled
# "2022-05 -> 2022-06  30%" on the frame and the info bar names the
# interpolation, so an observed frame is never mistaken for a generated one.
name: wne_interpolated
project: hnee-331218

aoi:
  frame: { bbox: [13.7949, 52.9299, 13.9985, 53.0421] }
  region: { shapefile: docs/aoi/wne/wne.shp }
  region_max_cloud_percent: 20

start: "2022-01-01"
end: "2023-01-01"

sensor: sentinel2
index: ndvi
cadence: monthly
max_cloud_percent: 70
draw_region: true
pool_years: [2018, 2026]
pool_strategy: gap_fill

viz: { min: 0.0, max: 0.95 }

render:
  fps: 24                 # output frame rate; 24-30 reads as filmed
  interpolate: 10         # generated frames per one-period step
  interpolate_mode: auto  # data-space for indices, cross-fade for rgb/cir
  scale: 10
  dimensions: 1440
  crs: auto
  preset: 1080p
  aspect: match
  upscale: lanczos
  region_line_width: 5
```

- [ ] **Step 2: Add the documentation section**

Insert after §7 of `docs/how-the-timeseries-is-made.md`:

```markdown
## 7a. Interpolated playback (optional)

`render.interpolate: N` inserts N generated frames per one-period step, so an
animation reads as continuous motion instead of a slideshow. Spacing is
proportional: a two-month gap gets twice the generated frames of a one-month gap,
so playback speed tracks elapsed time.

**Generated frames show dates that were never observed.** They are labelled
`2022-05 -> 2022-06  30%` and the info bar states the interpolation, so an
observed frame is never mistaken for a generated one. Observed frames are
byte-identical to a run with interpolation off.

Two modes: `data` interpolates index values before colouring (the colour bar stays
exactly valid); `crossfade` blends finished colour and is the only option for the
`rgb`/`cir` composites, which arrive already coloured. `auto` picks per index.

Where one observation has a cloud hole and the next does not, the generated frames
**hold the valid observation's value** rather than fading toward the no-data grey —
otherwise a healing cloud hole would pulse grey in and out on every transition.

Only observed frames get per-frame PNGs, and GIF output defaults off when
interpolating, since several hundred quantized frames would be enormous.
```

- [ ] **Step 3: Verify the config loads**

Run:
```bash
conda run -n GEE_animation python -c "
from gee_animation.config import RunConfig
c = RunConfig.from_yaml('config/wne_interpolated.example.yaml')
print(c.interpolate, c.interpolate_mode, c.fps)"
```
Expected: `10 auto 24.0`

- [ ] **Step 4: Run the full suite**

Run: `pytest -m "not integration" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config/wne_interpolated.example.yaml docs/how-the-timeseries-is-made.md
git commit -m "Add interpolation example config and document the mode"
```

---

## Verification

After all tasks:

```bash
conda activate GEE_animation
pytest -m "not integration"                 # must be >= 343 + new tests
gee-animation --config config/wne_interpolated.example.yaml
```

Visual acceptance on the rendered MP4:

- Playback is continuous rather than stepped; green-up in April–May reads as a
  gradual change.
- Observed frames carry their plain label (`2022-05  n=3`); generated frames carry
  endpoints and a percentage.
- The info bar names the interpolation.
- Cloud holes do not pulse grey during a transition.
- `out/` contains one PNG per **observed** frame only, and no GIF unless
  `gif: true` was set explicitly.

Then compare against the same config with `interpolate: 0` and confirm the observed
frames' PNGs are byte-identical (the suite asserts this, but confirm once on real data).
