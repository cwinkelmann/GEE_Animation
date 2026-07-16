# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`gee_animation` — a Python package + CLI that turns a Google Earth Engine
Sentinel-2 time series over a forest area into an annotated **NDVI timelapse**
(MP4 + GIF). Frames are assembled **locally, frame-by-frame** (download → colorize
→ annotate → encode), not server-side, so per-frame styling (month label, shared
NDVI colorbar, no-data masking) is under our control. Built for vegetation/forest
work at HNEE; the default Earth Engine project is `hnee-331218`.

Design spec and implementation plan live in `docs/superpowers/`.

## Commands

```bash
pip install -e ".[dev]"                      # install package + pytest
pytest -m "not integration"                  # fast unit tests — NO network/EE
pytest tests/test_config.py::test_rejects_end_before_start   # run a single test
GEE_INTEGRATION=1 pytest -m integration      # opt-in live-EE test (needs auth)
earthengine authenticate                     # one-time EE auth (or tool prompts)
gee-animation --config config.yaml           # run the pipeline (see config/example.yaml)
```

The unit suite mocks/injects Earth Engine and never hits the network. The single
`@pytest.mark.integration` test is deselected by default and skips unless
`GEE_INTEGRATION` is set.

## Architecture

Config-driven pipeline. `cli.run()` orchestrates the modules in order; each module
has one responsibility and takes Earth Engine as an **injected dependency**
(`ee_module=ee`, or an injected `fetch`/`deps`) so it is testable without live EE.

Data flow (`gee_animation/`):

```
config.yaml
  → config.RunConfig.from_yaml()   validated run parameters (pure)
  → auth.init(project)             ee.Initialize; Authenticate + retry on failure
  → aoi.parse(cfg.aoi)             bbox or GeoJSON → ee.Geometry
  → collection.build(cfg, geom)    S2_SR_HARMONIZED, SCL cloud mask, +NDVI band
  → compositing.monthly_median()   → [Frame(label="YYYY-MM", image)], empty months skipped
  → render.render(frames, geom)    per frame: fetch NDVI thumbnail → colorize →
                                     mask no-data → annotate → colorbar → MP4+GIF
```

Key conventions and invariants:

- **NDVI** = `(B8 − B4)/(B8 + B4)`, computed server-side in `collection.add_ndvi`.
  `imaging.py` holds a pure numpy `ndvi()`/`colorize()` for unit-testing the math.
- **Fixed viz across frames:** `cfg.ndvi_min/max` and `palette` are applied
  identically to every frame (in both `getThumbURL` and `colorize`) so colour is
  comparable across the animation.
- **Cloud/no-data pixels** come back transparent from EE; `render._fetch_thumbnail`
  reads the alpha channel (`convert("LA")`) and `apply_nodata` paints them neutral
  grey — they must never be colorized as low-NDVI (that would read as bare soil).
- **GIF frame duration** is milliseconds for `imageio>=2.28` (`_write_gif` uses
  `duration=1000/fps`). MP4 uses `imageio-ffmpeg`; `assemble` falls back to
  GIF-only if MP4 encoding fails.
- **Errors fail fast:** empty collection/month → `RuntimeError`; `cli.main` catches
  `(ConfigError, RuntimeError)` and returns exit code 1.
- v1 supports one sensor (`sentinel2`) and one cadence (`monthly`); `config.py`
  validates against these and is structured to extend.

`01_animation_single_images.py` is a thin example that calls `cli.run()`.

## Testing conventions

- Test files mirror modules (`tests/test_<module>.py`). EE is faked at the
  `ee_module` seam (see `tests/test_collection.py`, `tests/test_aoi.py`) or via an
  injected `fetch`/`deps` (see `tests/test_render.py`, `tests/test_cli.py`).
- Keep the unit suite network-free; put anything needing live EE behind the
  `integration` marker + `GEE_INTEGRATION` env guard.

---

# context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

## BLOCKED commands — do NOT attempt these

### curl / wget — BLOCKED
Any Bash command containing `curl` or `wget` is intercepted and replaced with an error message. Do NOT retry.
Instead use:
- `ctx_fetch_and_index(url, source)` to fetch and index web pages
- `ctx_execute(language: "javascript", code: "const r = await fetch(...)")` to run HTTP calls in sandbox

### Inline HTTP — BLOCKED
Any Bash command containing `fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, or `http.request(` is intercepted and replaced with an error message. Do NOT retry with Bash.
Instead use:
- `ctx_execute(language, code)` to run HTTP calls in sandbox — only stdout enters context

### WebFetch — BLOCKED
WebFetch calls are denied entirely. The URL is extracted and you are told to use `ctx_fetch_and_index` instead.
Instead use:
- `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` to query the indexed content

## REDIRECTED tools — use sandbox equivalents

### Bash (>20 lines output)
Bash is ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands.
For everything else, use:
- `ctx_batch_execute(commands, queries)` — run multiple commands + search in ONE call
- `ctx_execute(language: "shell", code: "...")` — run in sandbox, only stdout enters context

### Read (for analysis)
If you are reading a file to **Edit** it → Read is correct (Edit needs content in context).
If you are reading to **analyze, explore, or summarize** → use `ctx_execute_file(path, language, code)` instead. Only your printed summary enters context. The raw file content stays in the sandbox.

### Grep (large results)
Grep results can flood context. Use `ctx_execute(language: "shell", code: "grep ...")` to run searches in sandbox. Only your printed summary enters context.

## Tool selection hierarchy

1. **GATHER**: `ctx_batch_execute(commands, queries)` — Primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — Query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — Sandbox execution. Only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` — Fetch, chunk, index, query. Raw HTML never enters context.
5. **INDEX**: `ctx_index(content, source)` — Store content in FTS5 knowledge base for later search.

## Subagent routing

When spawning subagents (Agent/Task tool), the routing block is automatically injected into their prompt. Bash-type subagents are upgraded to general-purpose so they have access to MCP tools. You do NOT need to manually instruct subagents about context-mode.

## Output constraints

- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never return them as inline text. Return only: file path + 1-line description.
- When indexing content, use descriptive source labels so others can `ctx_search(source: "label")` later.

## ctx commands

| Command | Action |
|---------|--------|
| `ctx stats` | Call the `ctx_stats` MCP tool and display the full output verbatim |
| `ctx doctor` | Call the `ctx_doctor` MCP tool, run the returned shell command, display as checklist |
| `ctx upgrade` | Call the `ctx_upgrade` MCP tool, run the returned shell command, display as checklist |
