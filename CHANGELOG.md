# Changelog

Notable changes to `gee_animation`. Versions follow [semantic versioning](https://semver.org):
the minor number moves when something is added, the patch number when something is fixed.

## 0.2.0 — 2026-10-15

First tagged release. The repository carried `0.1.0` from its first commit and was never
tagged, so everything below the "Earlier" heading already existed; this release's own work is
the multi-user server.

### Added

- **Multi-user web server** (`gee_animation/webapp/`, new `server` extra). Each person signs
  in with their own Google account and renders under their own Cloud/Earth Engine project with
  their own credentials — nobody borrows the operator's. FastAPI hosts the OAuth flow and
  mounts the existing Gradio app.
  - `session.py` — opaque session ids, 7-day window, prior sessions dropped on re-sign-in
  - `oauth.py` — Google OAuth with offline access, so a render outlives its access token
  - `project.py`, `caps.py` — per-user project resolution; request caps (12 years, 1440p,
    allowlisted presets) that fail closed
  - `jobs.py`, `spawn.py`, `worker.py` — queue, 3 concurrent renders, 4-hour timeout,
    per-user cancel; one OS subprocess per render
  - `storage.py` — per-user output directories, 14-day retention sweep
  - `logsafe.py` — token redaction across the logging tree
- **Deployment** (`deploy/`): compose stack, Caddy reverse proxy with TLS, `.env.example`,
  and a "Multi-user server" section in the README.
- `gui.py` gained one seam, `build_app(context=None)`. With `context=None` the
  single-operator app behaves exactly as before.
- Review record for the above in `docs/superpowers/reviews/2026-10-14-multiuser-webapp/`.

### Security

- A Google refresh token exists only in process memory and on a worker's stdin — never in
  `argv`, an environment variable, a file, a log record, or a user-facing error.
- Renders never run in the web process: `ee._state._state` is a process-wide global, so an
  `ee.Initialize` on the server would attribute one person's render to whatever credentials
  the host holds. Every render is a subprocess.
- One user cannot read, cancel, or overwrite another's runs.

### Known limitations

Nothing in `deploy/` has been built or started — there was no Docker daemon available — so
those files are static-reviewed only. Before the server runs it needs a browser-trusted
certificate, a Google OAuth client, a stable `GEE_COOKIE_SECRET`, and an Earth Engine project
per user. See `docs/superpowers/specs/2026-10-14-multiuser-webapp-handoff.md`, which also
lists the browser and live-Earth-Engine checks that only a deployment can settle.

### Earlier (untagged, released here as part of 0.2.0)

Timelapse rendering for 11 products — `ndvi`, `nbr`, `ndmi`, `ndwi`, `ndre`, `evi`, `cir`,
`rgb`, `lst`, `lst_sharp`, `landcover` — across Sentinel-2, Landsat, MODIS and Dynamic World;
frames assembled locally so month labels, colorbars and no-data masking stay under our
control; random-forest thermal sharpening; stem-density and footprint overlays; harmonic
seasonal smoothing; chunked rendering for decade-long runs; thumbnail caching; GeoTIFF export;
a `gee-animation` CLI and a Gradio GUI.

## 0.1.0

Initial package version, never tagged.
