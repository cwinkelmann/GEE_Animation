# Single-user intro prerelease Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reviewable prerelease branch carrying a six-notebook teaching series for HNEE students (setup → first timelapse → Gradio app → gridded LST → RF re-sharpening → build your own index), a README reshaped to the single-user story, and notebook hygiene enforced in CI.

**Architecture:** Content work on one branch (`prerelease/single-user-intro`, cut from `main`), delivered as one PR that stays open for named reviewers. One new script (`scripts/check_notebooks.py`) turns the repo's notebook hygiene conventions into a CI gate; everything else is notebooks and Markdown. No package code changes.

**Tech Stack:** Jupyter/ipynb (nbformat 4 JSON, edited as files), Python stdlib for the checker, GitHub Actions (existing `tests` workflow), `gee_animation`'s public surfaces: `api.animate`, `auth.init`, `cli.run`, `config.RunConfig`, `gui.build_app`.

**Spec:** `docs/superpowers/specs/2026-10-16-single-user-intro-prerelease-design.md`

## Global Constraints

- Branch `prerelease/single-user-intro`; **no tag, no version bump, no PyPI**. The PR is the deliverable and stays open; merging is Christian's call.
- **Commits carry no Claude attribution lines** (standing instruction from Christian).
- Work happens in this worktree (`.claude/worktrees/multiuser-webapp`, already on the branch). Tests run as `PYTHONPATH=$PWD conda run -n GEE_animation python -m pytest -m "not integration" -q`. **Never `pip install` into the env** — it holds a shared editable install.
- Notebook conventions (spec §2), enforced or verified per task: Colab badge + install cell at top; parameters cell right after; **no stored outputs, no execution counts**; **no code cell contains `hnee-331218`** (markdown documenting the operator deployment may); every numbered notebook ends with a "Make it yours" section (2–3 graded forks + exactly one seam pointer).
- Badge and raw-content URLs point at `prerelease/single-user-intro` while the PR is open; flipping them to `main` is a PR checklist item executed as the PR's final commit (spec §7).
- Live-EE verification renders use small AOIs/date ranges and run **one at a time** (shared quota). The executor exports `EE_PROJECT=hnee-331218` locally to execute notebooks; the committed files read the project from the environment, so the id never lands in a cell.
- `render.pixel_grid` is only valid together with `render.preset` (`config.py:503`).

## Review Focus

1. **A notebook gets committed after a test run, carrying outputs/execution counts** — the most likely rot. Pinned by `tests/test_check_notebooks.py::test_flags_stored_outputs_and_execution_counts` (Task 1) and the CI step (Task 8).
2. **A code cell hardcodes `hnee-331218`**, so a student silently bills HNEE — pinned by `tests/test_check_notebooks.py::test_flags_operator_project_in_code_but_not_markdown` (Task 1); today's four notebooks all violate it, fixed in Tasks 3, 7, 8.
3. **03's grid config is invalid** (`pixel_grid` without `preset` fails validation) — pinned by 03's own offline validation cell `RunConfig.from_yaml(p)` over both generated configs (Task 5, step 3), which runs with no EE auth.
4. **04's fetch cell breaks offline reuse** (re-downloads when data exists, or fails where the repo is cloned) — pinned by Task 6 step 4: execute the fetch cell twice locally, assert the second pass downloads nothing.
5. **Badges 404 in Colab** because a URL names `main` before merge or the branch after — pinned by Task 9's PR checklist plus a grep step in Task 9 asserting every badge/raw URL on the branch names the branch.

## File map

| File | Task | Responsibility |
|---|---|---|
| `scripts/check_notebooks.py` + `tests/test_check_notebooks.py` | 1 | hygiene gate |
| `notebooks/00_setup.ipynb` | 2 | new |
| `notebooks/01_first_timelapse.ipynb` | 3 | rename of `colab_gee_animation.ipynb` + refresh; README links updated |
| `notebooks/02_gradio_app.ipynb` | 4 | new |
| `notebooks/03_gridded_lst.ipynb` | 5 | new |
| `notebooks/04_lst_sharpening.ipynb`, `notebooks/data/*.tif` | 6 | move from `docs/notebooks/` + fetch cell |
| `notebooks/05_build_your_own_index.ipynb` | 7 | rename of `custom_index.ipynb` + ending |
| `README.md`, `docs/multiuser-server.md`, `CHANGELOG.md`, `.github/workflows/ci.yml`, the two extras | 8 | front door, single-user story, CI wiring |
| PR | 9 | delivery |

## Shared cell patterns (referenced by Tasks 2–7; copy verbatim, adjust the extras list per notebook)

**Install cell** (first code cell; extras per notebook: 00/01/03 `[shapefile]`, 02 `[gui,shapefile]`, 04 `[ml]`, 05 `[shapefile]`):

```python
import sys
IS_COLAB = "google.colab" in sys.modules
BRANCH = "prerelease/single-user-intro"   # flipped to "main" when this PR merges
URL = f"gee_animation[shapefile] @ git+https://github.com/cwinkelmann/GEE_Animation.git@{BRANCH}"
if IS_COLAB:
    !pip install -q "{URL}"                              # deps + package (first run)
    !pip install -q --force-reinstall --no-deps "{URL}"  # branch moved? force-refresh our code
# pip resolves by version (0.2.0), which doesn't change per commit on a branch —
# --force-reinstall pulls the latest commit. After a code update: Runtime ->
# Restart session, then Run all, so the new module is re-imported.
```

**Parameters cell** (immediately after; AOI/dates vary per notebook):

```python
import os
PROJECT = os.environ.get("EE_PROJECT", "")   # <-- or paste your own project id here
assert PROJECT, "Set PROJECT to your own Earth Engine Cloud project id — 00_setup.ipynb shows how to create one"
AOI = [13.75, 52.97, 13.82, 53.01]           # [west, south, east, north] — Grumsin beech forest, ~5 km
START, END = "2025-05-01", "2025-08-01"      # short on purpose: a fork should render in minutes
```

**Auth cell:**

```python
from gee_animation import auth
auth.init(PROJECT)
```

**Badge** (first markdown cell): `[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/cwinkelmann/GEE_Animation/blob/prerelease/single-user-intro/notebooks/<FILE>.ipynb)`

**Local execution for verification** (per notebook, where live EE is needed):

```bash
EE_PROJECT=hnee-331218 PYTHONPATH=$PWD conda run -n GEE_animation \
  jupyter nbconvert --to notebook --execute --output-dir /tmp/nbcheck notebooks/<FILE>.ipynb
```

(then confirm the **committed** file still has zero outputs: `python scripts/check_notebooks.py notebooks/<FILE>.ipynb`)

---

### Task 1: Notebook hygiene checker

**Files:**
- Create: `scripts/check_notebooks.py`
- Test: `tests/test_check_notebooks.py`

**Interfaces:**
- Produces: `problems(path) -> list[str]` and `main(paths: list[str] | None) -> int` (0 clean, 1 dirty; `None` = every tracked `*.ipynb` via `git ls-files`). CLI: `python scripts/check_notebooks.py [paths...]`. Tasks 2–8 run it; Task 8 wires it into CI.

- [ ] **Step 1: Write the failing tests**

```python
"""scripts/check_notebooks.py: tracked notebooks carry no outputs, no execution
counts, and never set the operator's EE project in a code cell."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_notebooks as C  # noqa: E402


def _nb(cells):
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": cells}


def _code(source, outputs=None, count=None):
    return {"cell_type": "code", "source": [source], "metadata": {},
            "outputs": outputs or [], "execution_count": count}


def _write(tmp_path, name, nb):
    p = tmp_path / name
    p.write_text(json.dumps(nb), encoding="utf-8")
    return str(p)


def test_a_clean_notebook_has_no_problems(tmp_path):
    p = _write(tmp_path, "clean.ipynb", _nb([
        {"cell_type": "markdown", "source": ["EE_PROJECT=hnee-331218 is the operator's"], "metadata": {}},
        _code("print('hi')")]))
    assert C.problems(p) == []
    assert C.main([p]) == 0


def test_flags_stored_outputs_and_execution_counts(tmp_path):
    p = _write(tmp_path, "dirty.ipynb", _nb([
        _code("1+1", outputs=[{"output_type": "execute_result"}], count=3)]))
    msgs = C.problems(p)
    assert any("stored outputs" in m for m in msgs)
    assert any("execution_count" in m for m in msgs)
    assert C.main([p]) == 1


def test_flags_operator_project_in_code_but_not_markdown(tmp_path):
    p = _write(tmp_path, "proj.ipynb", _nb([
        {"cell_type": "markdown", "source": ["hnee-331218"], "metadata": {}},
        _code('PROJECT = "hnee-331218"')]))
    assert [m for m in C.problems(p) if "hnee-331218" in m] != []


def test_unparseable_notebook_is_a_problem_not_a_crash(tmp_path):
    p = tmp_path / "broken.ipynb"
    p.write_text("{not json", encoding="utf-8")
    assert C.main([str(p)]) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=$PWD conda run -n GEE_animation python -m pytest tests/test_check_notebooks.py -q`
Expected: FAIL — `ModuleNotFoundError: check_notebooks`

- [ ] **Step 3: Write the script**

```python
#!/usr/bin/env python3
"""Fail when a tracked notebook would embarrass the repo.

Three rules, all of which have bitten or nearly bitten:
- no stored outputs (a notebook saved after a test run bloats every diff),
- no execution counts (same failure, smaller symptom),
- no code cell contains the operator's Earth Engine project id — a student
  running a cell must never silently render on HNEE's quota. Markdown may
  mention it (the carrot deployment docs do, honestly).
"""
import json
import subprocess
import sys

OPERATOR_PROJECT = "hnee-331218"


def problems(path) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            nb = json.load(f)
    except (OSError, ValueError) as e:
        return [f"unreadable as a notebook: {e}"]
    out = []
    for i, cell in enumerate(nb.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        if cell.get("outputs"):
            out.append(f"cell {i}: stored outputs")
        if cell.get("execution_count") is not None:
            out.append(f"cell {i}: execution_count set")
        if OPERATOR_PROJECT in "".join(cell.get("source", [])):
            out.append(f"cell {i}: code hardcodes {OPERATOR_PROJECT}")
    return out


def main(paths=None) -> int:
    if not paths:
        paths = subprocess.run(["git", "ls-files", "*.ipynb"], check=True,
                               capture_output=True, text=True).stdout.split()
    bad = 0
    for p in paths:
        for msg in problems(p):
            print(f"{p}: {msg}")
            bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD conda run -n GEE_animation python -m pytest tests/test_check_notebooks.py -q`
Expected: 4 passed

- [ ] **Step 5: Run it over the repo and record today's violations**

Run: `conda run -n GEE_animation python scripts/check_notebooks.py`
Expected: exit 1, flagging `hnee-331218` code cells in the four existing notebooks (that is the point — Tasks 3, 7 and 8 clear them; CI is wired in Task 8 only after they are clear). No outputs/execution-count findings.

- [ ] **Step 6: Full suite green, then commit**

Run: `PYTHONPATH=$PWD conda run -n GEE_animation python -m pytest -m "not integration" -q`
Expected: 914 passed (910 + the 4 new), 2 skipped, 2 deselected

```bash
git add scripts/check_notebooks.py tests/test_check_notebooks.py
git commit -m "test: a checker for the notebook hygiene rules"
```

---

### Task 2: `notebooks/00_setup.ipynb`

**Files:** Create: `notebooks/00_setup.ipynb`

**Interfaces:**
- Consumes: shared cell patterns; `animate(region, *, sensor, index, start, end, project, preset, name, ...)` from `gee_animation.api`; `auth.init(project)`.
- Produces: the document every later notebook points to for "get your own project". Section anchor `#2-·-create-your-own-earth-engine-project` must exist (01–05 link to it).

- [ ] **Step 1: Write the notebook** with this structure (markdown unless noted):

1. `# Getting set up — your own Earth Engine access` — badge; one paragraph: who this is for (students), what you end with (a rendered animation under *your* project), ~15 minutes.
2. `## 1 · What you need` — a Google account; nothing else. Name the two things being created: an Earth Engine registration and a Cloud project.
3. `## 2 · Create your own Earth Engine project` — prose walk-through: [code.earthengine.google.com/register](https://code.earthengine.google.com/register) → "Register a noncommercial or commercial cloud project" → *Unpaid usage*, academia → create a new Google Cloud project (any id, e.g. `ee-<yourname>`) → finish. State plainly: **the id you just chose is your `PROJECT` everywhere in this series; never use someone else's.**
4. `## 3 · Install` — the shared install cell (code), plus a markdown note for local use: `conda create -n gee python=3.11`, `pip install "gee_animation[shapefile] @ git+https://github.com/cwinkelmann/GEE_Animation.git@prerelease/single-user-intro"`.
5. `## 4 · Authenticate` — parameters cell (code, shared pattern), then the auth cell (code). Markdown: in Colab `ee.Authenticate()` opens a token prompt; locally `earthengine authenticate` once. `auth.init` retries authentication on failure, so running the cell twice is the fix for most hiccups.
6. `## 5 · The smoke test` (code):

```python
from gee_animation.api import animate
anim = animate(AOI, sensor="sentinel2", index="ndvi", start=START, end=END,
               project=PROJECT, preset="720p", name="setup_smoke", out_dir="out")
anim      # plays inline: ~3 monthly frames over the Grumsin beech forest
```

7. `## 6 · When it does not work` — the two real failures: `not signed up for Earth Engine or project is not registered` (the registration from §2 has not finished, or `PROJECT` names a project that is not EE-registered — it is *not* an authentication problem) and a transient `read operation timed out` (re-run the cell; everything fetched so far is cached).
8. `## Make it yours` — (a) change `AOI` to a forest you know (bbox from [geojson.io](https://geojson.io)); (b) swap `index="ndvi"` for `"nbr"` or `"ndwi"`; (c) stretch `START` back one year and watch the frame count grow. Seam pointer: everything `animate()` accepts is one function — `gee_animation/api.py:163`.

- [ ] **Step 2: Execute it locally** (shared verification command, `<FILE>`=`00_setup`). Expected: runs end-to-end, ~3 frames.
- [ ] **Step 3: Checker clean**: `conda run -n GEE_animation python scripts/check_notebooks.py notebooks/00_setup.ipynb` → exit 0.
- [ ] **Step 4: Commit** — `git add notebooks/00_setup.ipynb && git commit -m "docs: 00_setup — your own Earth Engine project, then a smoke render"`

---

### Task 3: `notebooks/01_first_timelapse.ipynb` (rename + refresh)

**Files:**
- Rename: `notebooks/colab_gee_animation.ipynb` → `notebooks/01_first_timelapse.ipynb` (`git mv`)
- Modify: `README.md:6`, `README.md:8`, `README.md:23` (links to the old name)

- [ ] **Step 1: `git mv`**, then edit the notebook JSON:
  - Badge URL → branch + new filename (shared pattern).
  - Install cell → shared pattern (replaces the stale "version stays 0.1.0" comment).
  - Auth section's `PROJECT = "hnee-331218"` cell → shared parameters cell + auth cell, with a markdown line linking `00_setup.ipynb#2-·-create-your-own-earth-engine-project` for "don't have a project yet?".
  - Append `## Make it yours`: (a) different AOI upload; (b) `sensor="landsat", index="lst"` for temperature; (c) `geotiffs=True` and open a frame in QGIS. Seam pointer: the `Animation` object (`api.py`) carries every output path — `anim.frames`, `anim.series`.
- [ ] **Step 2: Update the three README links** to `notebooks/01_first_timelapse.ipynb` (badge URLs at lines 6/8 keep `blob/main` → change to `blob/prerelease/single-user-intro`; Task 9 flips them back with everything else).
- [ ] **Step 3: Execute locally** (shared command). Expected: completes; animation displays.
- [ ] **Step 4: Checker clean** on the file; `git grep -n colab_gee_animation` returns nothing.
- [ ] **Step 5: Commit** — `git commit -am "docs: 01_first_timelapse — renamed quickstart, own-project convention, Make it yours"`

---

### Task 4: `notebooks/02_gradio_app.ipynb`

**Files:** Create: `notebooks/02_gradio_app.ipynb`

**Interfaces:**
- Consumes: `gui.build_app(context=None)`; README Docker section (`README.md:171`); the carrot deployment facts below (verified live 2026-10-16).

- [ ] **Step 1: Write the notebook**:

1. `# The Gradio app — one UI over the same pipeline` — badge; the app is `build_app()` over the exact `RunConfig` the CLI uses; three ways to run it, in order of how much you should trust them.
2. `## 1 · Locally (the way you should run it)` — markdown: `pip install -e ".[gui,shapefile]"`, `earthengine authenticate`, `gee-animation-gui`; what you see: AOI upload or preset dropdown (bundled site footprints from `gee_animation/presets_aoi/`), sensor/index pickers, the two thermal accordions mapping 1:1 onto config keys (`sharpen`, `render.pixel_grid`, `smooth`, `anomaly` …); outputs land in `out/`, one render at a time.
3. `## 2 · On carrot (the deployment that exists)` — markdown, the verbatim recipe that produced the container running there now (deployed 2026-09-30, image `gee-timelapse`, container `gee-animation`):

```
rsync -av --exclude .git --exclude 'out/' --exclude '*.mp4' . carrot:~/GEE_animation/
ssh carrot "cd ~/GEE_animation && docker build -t gee-timelapse ."
ssh carrot "docker rm -f gee-animation 2>/dev/null; docker run -d --name gee-animation \
  -p 7860:7860 \
  -v /raid/cwinkelmann/w/gee_out:/app/out \
  -v /raid/cwinkelmann/w/gee_cache:/cache \
  -v ~/keys:/keys:ro -v ~/GEE_animation/docs/aoi/wne/wne.geojson:/data/aoi.geojson:ro \
  -e EE_SERVICE_ACCOUNT_KEY=/keys/ee-key.json -e EE_PROJECT=hnee-331218 \
  -e GEE_ANIMATION_CACHE_DIR=/cache -e GEE_OUTPUT_DIR=/app/out \
  -e GEE_DEFAULT_AOI=/data/aoi.geojson gee-timelapse"
```

   Followed by the honesty paragraph: this is a **single-operator deployment** — bound to `0.0.0.0:7860` on the HNEE VPN, **no authentication, one shared Earth Engine identity** (`hnee-331218`), everyone sees everyone's runs. Students: look, then render locally under your own project. The multi-user web app (`docs/multiuser-server.md`) was built to fix exactly this and awaits deployment.
4. `## 3 · In Colab (a demo, not a deployment)` — install cell (`[gui,shapefile]` extra), parameters+auth cells, then:

```python
RUN_APP = False    # flip to True to try it; the cell blocks while the app serves
if RUN_APP:
    from gee_animation.gui import build_app
    build_app().launch()   # Colab detected -> Gradio prints a public *.gradio.live link
```

   Markdown: the link is public while the cell runs, the backend is your Colab VM and your EE project; it dies with the runtime. "Yes, it works here too; don't build on it."
5. `## Make it yours` — (a) pre-load your own AOI via `GEE_DEFAULT_AOI`; (b) add a bundled preset: drop a GeoJSON into `gee_animation/presets_aoi/` and watch the dropdown grow; (c) run a thermal render from the accordion and find the same keys in a `config/*.yaml`. Seam pointer: `gui.build_app` (`gui.py:727`) is one function returning a Blocks app — mount it anywhere Gradio mounts.

- [ ] **Step 2: Verify what's executable**: run install/params/auth cells locally plus `RUN_APP=False` path via the shared nbconvert command (the app itself stays unlaunched). Separately, eyeball-check the carrot block against `docker ps` output quoted in the spec's inventory table.
- [ ] **Step 3: Checker clean** (the `hnee-331218` mentions are markdown-only — this is the test of the code-cells-only rule).
- [ ] **Step 4: Commit** — `git commit -m "docs: 02_gradio_app — local first, carrot documented honestly, Colab as demo"`

---

### Task 5: `notebooks/03_gridded_lst.ipynb`

**Files:** Create: `notebooks/03_gridded_lst.ipynb`

**Interfaces:**
- Consumes: `cli.run(config_path) -> list[Path]`; `RunConfig.from_yaml`; the constraint `pixel_grid` ⇒ `preset` (`config.py:503`).
- Produces: the question 04 answers ("the grid shows you the 100 m problem").

- [ ] **Step 1: Write the notebook**:

1. `# Gridded LST — seeing the real resolution` — badge; thesis: the smooth look of an upscaled thermal frame is cosmetic; `render.pixel_grid` draws the native 100 m (Landsat) / 1 km (MODIS) cells so the map stops over-promising. This is also the series' introduction to **config files** — the workflow behind every video in `config/`.
2. Install cell, parameters cell (shared; plus `FRAME = [13.73, 52.96, 13.84, 53.02]` — the region bbox grown ~1 km), auth cell, and dates `START, END = "2024-06-01", "2024-09-01"`.
3. `## 1 · Two configs that differ in two keys` (code):

```python
import pathlib, yaml

def write_cfg(name, pixel_grid):
    cfg = {"name": name, "project": PROJECT,
           "start": START, "end": END, "cadence": "monthly",
           "sensor": "landsat", "index": "lst",
           "aoi": {"frame": {"bbox": FRAME}, "region": {"bbox": AOI}},
           "render": {"preset": "720p", "pixel_grid": pixel_grid,
                      "upscale": "nearest" if pixel_grid else "lanczos"},
           "out_dir": "out"}
    p = pathlib.Path(f"{name}.yaml")
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p

cfg_plain, cfg_grid = write_cfg("lst_plain", False), write_cfg("lst_grid", True)
```

4. `## 2 · Validate before you spend quota` (code — runs without EE):

```python
from gee_animation.config import RunConfig
for p in (cfg_plain, cfg_grid):
    RunConfig.from_yaml(p)          # raises ConfigError if a key is wrong
print("both valid")
```

   Markdown: try deleting `preset` from the grid config and re-running — the error message *is* the documentation (`pixel_grid` without an upscale would turn every pixel into a cell edge).
5. `## 3 · Render both` (code): `from gee_animation import cli` then `paths_plain = cli.run(str(cfg_plain))`, `paths_grid = cli.run(str(cfg_grid))` — one after the other, never parallel (shared quota).
6. `## 4 · Same month, side by side` (code):

```python
import base64
from IPython.display import HTML

def last_png(paths):
    return sorted(str(p) for p in paths if str(p).endswith(".png"))[-1]

def tag(p):
    return ('<img style="width:49%" src="data:image/png;base64,'
            + base64.b64encode(open(p, "rb").read()).decode() + '">')

HTML(tag(last_png(paths_plain)) + tag(last_png(paths_grid)))
```

   Markdown under it: what to see — the subtitle gained "· 100 m grid"; inside one cell the colour is constant because *that is one measurement*; lanczos was never data.
7. `## Make it yours` — (a) `sensor: modis` and watch the cells become 1 km; (b) put the grid on `sentinel2`/`ndvi` (10 m — at this AOI the mesh nearly vanishes: resolution is relative); (c) take `config/r12_zoom_lst_pretty_10yr_grid.yaml` and make it render *your* AOI. Seam pointer: `_cap_dimensions` in `render.py` — read it to see why the fetch happens at the product's native scale, which is what makes the grid honest.

- [ ] **Step 2: Execute locally** (shared command). Expected: two 3-frame renders, side-by-side image shows the mesh on the right only.
- [ ] **Step 3: Checker clean** on the file.
- [ ] **Step 4: Commit** — `git commit -m "docs: 03_gridded_lst — pixel_grid as the honest view, configs as the workflow"`

---

### Task 6: `notebooks/04_lst_sharpening.ipynb` (move + Colab fetch)

**Files:**
- Rename: `docs/notebooks/lst_rf_sharpening.ipynb` → `notebooks/04_lst_sharpening.ipynb`; `docs/notebooks/data/r12_2024-07_{lst,predictors}.tif` → `notebooks/data/` (`git mv`; `docs/notebooks/` ends up empty and disappears)

- [ ] **Step 1: `git mv` the notebook and both rasters.** Then `git grep -n "docs/notebooks"` and update every reference found (the notebook's own fallback path included).
- [ ] **Step 2: Rework the first code cell**: drop the `sys.path.insert` repo-root hack; prepend the shared install cell (`[ml]` extra — rasterio, scikit-learn, joblib; matplotlib is imported by the notebook, so the install line for Colab reads `gee_animation[ml,notebook] @ …`); then the fetch cell:

```python
from pathlib import Path
from urllib.request import urlretrieve

BRANCH = "prerelease/single-user-intro"   # flipped to "main" when this PR merges
RAW = f"https://raw.githubusercontent.com/cwinkelmann/GEE_Animation/{BRANCH}/notebooks/data/"
DATA = Path("data") if Path("data").exists() else Path("nb_data")
DATA.mkdir(exist_ok=True)
for f in ("r12_2024-07_lst.tif", "r12_2024-07_predictors.tif"):
    if not (DATA / f).exists():               # cloned repo: already there, nothing fetched
        print("fetching", f)
        urlretrieve(RAW + f, DATA / f)
```

  (badge too — shared pattern. **No EE auth cell**: this notebook's point is that it runs offline from EE; the one live-EE cell it contains already says so inline.)
- [ ] **Step 3: Append `## Make it yours`** (content is otherwise reviewed material — do not rewrite it): (a) drop one predictor from `PREDICTORS`, refit, compare the OOB score; (b) re-run the degrade-and-recover test on your variant — **that test is the template for validating any variant you build**; (c) export your own frame pair with `animate(..., geotiffs=True)` and sharpen a place you know. Seam pointer: `sharpen_local.py` — `read_geotiff`, `fit_forest`, `sharpen_frame` are the whole method.
- [ ] **Step 4: Execute twice locally** (shared command, no `EE_PROJECT` needed). Expected: both runs complete; the second prints no "fetching" line (Review Focus 4).
- [ ] **Step 5: Checker clean**; commit — `git commit -m "docs: 04_lst_sharpening — moved into the series, fetches its rasters in Colab"`

---

### Task 7: `notebooks/05_build_your_own_index.ipynb` (rename + ending)

**Files:** Rename: `notebooks/custom_index.ipynb` → `notebooks/05_build_your_own_index.ipynb`

- [ ] **Step 1: `git mv`**, update the badge (branch + new name), replace its install cell and `PROJECT = "hnee-331218"` cell with the shared patterns (link 00 for project creation). `git grep -n custom_index` → update references (CHANGELOG's Unreleased mention stays — it records history; update the README/docs links only).
- [ ] **Step 2: Extend the closing section** (`## 8 · What to carry away`): after the existing content, the graduation path — "when your index is worth keeping, register it properly": link `.claude/skills/adding-a-product/SKILL.md` on GitHub (blob URL on the branch) as *the checklist* (four registries, `display_name`/labels, measured `default_viz`, `test_registry_contents` in the same commit), and name `config/<name>.example.yaml` + the README index table as what a finished product ships with. One sentence: a PR that follows the checklist is reviewable in minutes.
- [ ] **Step 3: Execute locally** (shared command). Checker clean on the file.
- [ ] **Step 4: Commit** — `git commit -m "docs: 05_build_your_own_index — renamed, ends at the adding-a-product checklist"`

---

### Task 8: README front door, server docs out, extras compliant, CI wired

**Files:**
- Create: `docs/multiuser-server.md`
- Modify: `README.md` (lines 6/8 already touched in Task 3; Notebooks table after Install; Docker honesty note; Multi-user section → pointer), `CHANGELOG.md:6-16` (Unreleased), `.github/workflows/ci.yml` (one step), `notebooks/ndvi_timelapse.ipynb`, `notebooks/single_view_from_geojson.ipynb` (parameters-cell convention only)

- [ ] **Step 1: Move the Multi-user server section** (`README.md:250-308`, heading included) verbatim into `docs/multiuser-server.md` with one added opening line: *"Status: implemented and reviewed, never yet deployed — see `docs/superpowers/specs/2026-10-14-multiuser-webapp-handoff.md` for what a deployment must verify."* In the README's place, one line: `## Multi-user server` → "Moved to [docs/multiuser-server.md](docs/multiuser-server.md) — the README describes the single-user tool."
- [ ] **Step 2: Insert the Notebooks table** right after the Install section (~line 25):

```markdown
## Notebooks

A guided series — read in order, or jump in; every notebook runs in Colab (badge
inside) or locally, and ends with ways to make the workflow yours:

| # | Notebook | What you learn |
|---|---|---|
| 00 | [Setup](notebooks/00_setup.ipynb) | your own Earth Engine project, auth, a smoke render |
| 01 | [First timelapse](notebooks/01_first_timelapse.ipynb) | `animate()` end to end |
| 02 | [The Gradio app](notebooks/02_gradio_app.ipynb) | the UI locally, on a server, and as a Colab demo |
| 03 | [Gridded LST](notebooks/03_gridded_lst.ipynb) | native resolution, `pixel_grid`, config files |
| 04 | [LST re-sharpening](notebooks/04_lst_sharpening.ipynb) | the 100 m → 20 m random forest, offline on real data |
| 05 | [Build your own index](notebooks/05_build_your_own_index.ipynb) | from formula to registered product |

Extras: [ndvi_timelapse](notebooks/ndvi_timelapse.ipynb) (the pipeline step by step),
[single_view_from_geojson](notebooks/single_view_from_geojson.ipynb) (one month, one image + GeoTIFF).
```

- [ ] **Step 3: Docker section honesty note** — after the `docker run` block (`README.md:188-205` region): one paragraph, same content as 02 tier 2 (single operator, `0.0.0.0:7860`, no auth, one shared EE identity; the multi-user server is the successor).
- [ ] **Step 4: Extras comply** — in both extra notebooks, replace the hardcoded-project cell with the shared parameters+auth cells (no other content changes; they keep their un-numbered names).
- [ ] **Step 5: CHANGELOG** — add to `## Unreleased` → `### Added`: "A numbered teaching notebook series (00 setup … 05 build your own index) written for students, each ending in extension exercises; the README now leads with it and tells the single-user story (the multi-user server docs moved to `docs/multiuser-server.md`); CI enforces notebook hygiene (no outputs, no execution counts, no hardcoded operator project)."
- [ ] **Step 6: Commit the docs work** — `git add -A && git commit -m "docs: README leads with the notebook series; the server docs move to docs/"`
- [ ] **Step 7: Wire CI** — in `.github/workflows/ci.yml`, after the install step:

```yaml
      - name: Notebook hygiene
        # every tracked notebook parses, has no stored outputs or execution
        # counts, and no code cell hardcodes the operator's EE project
        run: python scripts/check_notebooks.py
```

- [ ] **Step 8: Verify** — `conda run -n GEE_animation python scripts/check_notebooks.py` → exit 0 over the whole repo (first time it can); full suite green (`PYTHONPATH=$PWD … pytest -m "not integration" -q`, expected 914 passed); `git grep -nE "colab_gee_animation|custom_index.ipynb|docs/notebooks/"` finds only CHANGELOG history lines.
- [ ] **Step 9: Commit the CI wiring** — `git commit -am "ci: enforce notebook hygiene"`

---

### Task 9: Push and open the PR

- [ ] **Step 1:** `git push -u origin prerelease/single-user-intro`
- [ ] **Step 2:** Grep gate (Review Focus 5): `git grep -n "blob/main/notebooks\|GEE_Animation/main/notebooks"` → nothing (all badge/raw URLs name the branch).
- [ ] **Step 3: Open the PR** (base `main`, title `prerelease: single-user intro — the Gradio app + a teaching notebook series`). Body: one line of intent per notebook (so reviewers can divide them up), the conventions list from the spec §2, the review question for each reviewer ("could you hand this to a student?"), and a checklist with the one merge-time item: **flip every `prerelease/single-user-intro` badge/raw URL to `main` as the final commit before merge**. No attribution footer.
- [ ] **Step 4:** Confirm the `tests` workflow runs on the PR and is green (it includes the new Notebook hygiene step). **Do not merge** — the PR stays open for the reviewers.

---

## Execution notes

- Tasks 2–7 each render against live Earth Engine during verification; run them one at a time.
- Task order matters only in two places: Task 1 before everything (the checker is each task's gate) and Task 8 after 2–7 (CI wiring must land only when the repo passes its own gate). 2–7 are otherwise independent.
- Expected suite count from Task 1 on: **914 passed, 2 skipped, 2 deselected**.
