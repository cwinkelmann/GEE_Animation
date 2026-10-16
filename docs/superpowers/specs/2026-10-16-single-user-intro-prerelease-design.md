# Single-user intro prerelease: the Gradio app + a teaching notebook series — design

Date: 2026-10-16 · Status: **awaiting review** · Author: pair-designed with Christian

## Intent

A **prerelease for review, not for tagging**: one branch (`prerelease/single-user-intro`)
and one PR that a few named reviewers read notebook-by-notebook. It presents the
project's *single-user* story — the CLI and the Gradio app — through a numbered
notebook series written for **HNEE students**, covering how everything is set up,
the gridded (pixel-grid) LST work, and the random-forest re-sharpening work.

The one stylistic requirement that shapes every notebook: they must **invite
extension**. A student finishing a notebook should know exactly what to change to
make the workflow theirs, and where the seam behind it lives in the package.

Success looks like: a student with nothing but a Google account gets from zero to
their first animation through 00 → 01 alone; every notebook's suggested fork
renders in minutes, not hours; reviewers can approve or veto each notebook
independently on the PR.

## What already exists (and is reused, not rewritten)

| Piece | Where | State |
|---|---|---|
| Colab quickstart | `notebooks/colab_gee_animation.ipynb` | good; install-cell comment still says "version stays 0.1.0" (it is 0.2.0) |
| NDVI walkthrough | `notebooks/ndvi_timelapse.ipynb` | good; overlaps the quickstart |
| Own-index notebook | `notebooks/custom_index.ipynb` | good; *is* the extension story |
| Single view + GeoTIFF | `notebooks/single_view_from_geojson.ipynb` | good |
| RF sharpening explainer | `docs/notebooks/lst_rf_sharpening.ipynb` + 2 sample GeoTIFFs in `docs/notebooks/data/` | excellent; runs offline from EE against the bundled rasters |
| Pixel-grid overlay | `render.pixel_grid`, `config/*_grid*.yaml`, plan `2026-09-17-pixel-grid-overlay.md` | implemented, tested |
| Gradio app | `gee-animation-gui`, `gui.py` | the single-user product |
| Carrot deployment | container `gee-animation` (image `gee-timelapse`), up since ~Oct 8, port `0.0.0.0:7860` | **live**; deployed 2026-09-30 by rsync → `docker build` → `docker run` (see §4) |
| CI | `.github/workflows/ci.yml` | one pytest job; extended here, not replaced |

## 1. The series

Six numbered notebooks in `notebooks/`, read in order, each independently
runnable. Three are new, three are renames/moves of existing ones.

| # | File | Content | Source |
|---|---|---|---|
| 00 | `00_setup.ipynb` | Google account → Earth Engine signup → **your own** Cloud project (never `hnee-331218`) → authenticate (Colab and local conda) → a 5-line smoke render proving it works | new |
| 01 | `01_first_timelapse.ipynb` | the Colab quickstart, refreshed: stale version comment fixed, "Make it yours" section added | rename of `colab_gee_animation.ipynb` |
| 02 | `02_gradio_app.ipynb` | the app in three tiers — see §4 | new |
| 03 | `03_gridded_lst.ipynb` | why a thermal pixel is 100 m (Landsat) / 1 km (MODIS); `render.pixel_grid` + `upscale: nearest`; one LST frame rendered with grid off and on; what the grid honestly shows about resolution | new |
| 04 | `04_lst_sharpening.ipynb` | the existing explainer, moved; gains a fetch cell so Colab gets the sample rasters | move of `docs/notebooks/lst_rf_sharpening.ipynb` (data → `notebooks/data/`) |
| 05 | `05_build_your_own_index.ipynb` | the own-index notebook, renamed; ends at the `adding-a-product` checklist (`.claude/skills/adding-a-product/`) as the "graduate to a PR" path | rename of `custom_index.ipynb` |

`ndvi_timelapse.ipynb` and `single_view_from_geojson.ipynb` stay as un-numbered
extras, listed after the series in the README table.

**Renames break old deep links** (e.g. the Colab badge URL bookmarked anywhere as
`notebooks/colab_gee_animation.ipynb`). Accepted for a prerelease; reviewers can
veto on the PR if a link matters.

## 2. Conventions every notebook follows

- **Colab badge + install cell** at the top, in the pattern
  `colab_gee_animation.ipynb` already established (`IS_COLAB` guard, install from
  the branch, force-reinstall note). Badges point at this branch while the PR is
  open and are flipped to `main` on merge.
- **Parameters cell** immediately after install: AOI, dates, product, output dir —
  the first thing a student edits is unmissable and in one place.
- **Small AOIs, short date ranges** so a fork renders in minutes. Live-EE
  notebooks (00–03) say so in their first cell; 04 runs offline from EE.
- **"Make it yours"** as the closing section: two or three graded forks of the
  workflow just shown (change a parameter → change a behaviour → change the code),
  then exactly one pointer to the seam behind it — `products.py`'s registry, the
  config key in `config.py`, or the object `animate()` returns.
- **Zero stored outputs** committed, ever (enforced by CI, §5).
- Students authenticate with **their own** Earth Engine project. No notebook ever
  hardcodes `hnee-331218`; the parameters cell names the project explicitly.

## 3. Content notes for the new notebooks

**00 Setup.** The register-a-project flow is the real barrier for students, so it
gets the screenshots-in-prose treatment: EE signup, creating a Cloud project,
choosing noncommercial registration, `earthengine authenticate` locally vs
`ee.Authenticate()` in Colab, and what the common failure
(`not signed up for Earth Engine or project is not registered`) actually means.
Ends with `animate()` over a tiny AOI, one month, one product.

**03 Gridded LST.** Teaches that the grid is *the honest view of the data*: the
same frame with `pixel_grid: false` looks smooth only because of upscaling.
Renders one month over a small AOI with `lst`, grid off then on
(`upscale: nearest`), then points at `03`→`04` as question → answer: "the grid
shows you the 100 m problem; the next notebook is the method that addresses it."
Make-it-yours: put the grid on NDVI at 10 m; change the mission; read
`_cap_dimensions` to see why the fetch happens at native scale.

**04 RF re-sharpening.** Content unchanged (it is reviewed material). Additions
only: the Colab fetch cell (download the two GeoTIFFs from
`raw.githubusercontent.com` when not present locally), the "Make it yours"
section (swap a predictor out, re-run the degrade-and-recover test, point at
`sharpen_local.py`), and the degrade-and-recover test framed explicitly as *the
template for validating any variant you build*.

## 4. Notebook 02: the Gradio app, three tiers

1. **Locally** (the student path): `pip install -e ".[gui]"`, `gee-animation-gui`,
   your own project, your own quota. What the preset dropdown is, where outputs
   land, that one render runs at a time.
2. **On carrot** (the proven deployment, documented verbatim): rsync the tree →
   `docker build` → `docker run` with `/raid/cwinkelmann/w/gee_out` and
   `gee_cache` mounted, the service-account key in `/keys`,
   `EE_PROJECT=hnee-331218`, default AOI `wne.geojson`, port 7860 — the recipe
   that produced the container running there now. Labelled honestly: **a
   single-operator deployment** — bound to `0.0.0.0:7860` on the VPN, no
   authentication, one shared Earth Engine identity, so students look but render
   locally. One line names the multi-user web app
   (`docs/multiuser-server.md`, §5) as the successor built to fix exactly this.
3. **In Colab** (a demo, labelled as such): one cell —
   `build_app().launch()` — works because Gradio auto-creates a share link in
   Colab. Explicitly "don't build on this".

## 5. README, docs, CI

- **README**: a *Notebooks* table (the series, then the extras) becomes the front
  door right after *Install*. The **Multi-user server** section (~60 lines) moves
  verbatim to `docs/multiuser-server.md`; the README keeps a one-line pointer.
  The Docker section stays (it is the carrot recipe's reference) and gains the
  one-paragraph honesty note from §4 tier 2.
- **CHANGELOG**: the *Unreleased* section records the series and the README
  reshape. No version bump, no tag.
- **CI** (`.github/workflows/ci.yml`): one new step running
  `scripts/check_notebooks.py` — every `*.ipynb` in the repo parses as nbformat 4
  and contains zero stored outputs / zero execution counts. The script gets a
  unit test like any other script. This turns the repo's existing hand-enforced
  hygiene rule into a gate.

## 6. Non-goals

- No tag, no version bump, no PyPI/TestPyPI.
- No change to the multi-user webapp, `deploy/`, or the carrot container.
- No new products or sensors (`modis_snow` stays parked), no renderer changes —
  if 03 reveals a rendering wart, it is reported, not fixed here.
- Notebooks are parsed in CI, **not executed**: 00–03 need live Earth Engine and
  a personal project; 04 could execute offline but pulls the `ml` extra plus
  ~10 MB of rasters into every CI run for little marginal safety. Revisit if a
  notebook rots.

## 7. Delivery and review

Branch `prerelease/single-user-intro` off `main`, one PR carrying the series in
reviewable commits (one notebook per commit where practical: 00, 01, 02, 03, 04
move+additions, 05, then README/docs, then CI). The PR description maps each
notebook to one line of intent so reviewers can divide them up. **The PR stays
open until the reviewers are done; merging is Christian's call.** On merge, the
Colab badges flip from the branch to `main` — a named checklist item in the PR
description, executed as the PR's final commit once the reviewers sign off.
