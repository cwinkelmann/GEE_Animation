# Multi-user GEE animation web app for a trusted group — design

Date: 2026-10-14 · Status: **awaiting review** · Author: pair-designed with Christian

## Problem

`gee_animation/gui.py` is an excellent single-operator tool — it rendered the R12/R13
and Grumsin sets — and a `Dockerfile` already runs it. It is not safe to hand to a
group of colleagues as it stands:

| # | What | Where |
|---|---|---|
| 1 | No authentication at all; whoever reaches port 7860 renders on HNEE's Earth Engine quota | `gui.py:1111` |
| 2 | "Load a previous animation" lists **every** run in `OUTPUT_DIR`, so each user sees and can replay every other user's renders and their uploaded AOIs | `list_previous_runs`, `gui.py:190` |
| 3 | One EE identity for everybody: no per-user attribution, one shared quota, and third-party use under HNEE's service account is a terms question nobody has answered | `Dockerfile` `EE_PROJECT` |
| 4 | Renders run in the Gradio process; no cancel, and a stall (which happens — see the `long-running-renders` skill) blocks everyone | `gui.py:1013` |
| 5 | `out/` grows without bound; neither `out/` nor the thumbnail cache is a volume in the documented `docker run`, so restarts re-download everything | `gui.py:446`, `cache.py:128` |

Goal: ~5–20 named HNEE colleagues on the VPN each render timelapses **from their own
AOI, under their own Earth Engine project**, on `carrot.aisl.c4ds.hnee.de`, with a few
renders running at once and disk that stays bounded.

Non-goals: the public internet; per-user billing dashboards; replacing the
single-operator local GUI, which must keep working unchanged.

## Constraints discovered (verified, not assumed)

1. **EE client state is one process-wide global.** `ee._state._state` is a module-level
   `EEState` holding `credentials` and `cloud_api_user_project`; there is no contextvar
   or thread-local despite the docstring. Two renders under different users' credentials
   in one process clobber each other. **Per-user identity therefore requires one OS
   process per render job.** This is the single biggest architectural consequence.
2. `ee.Initialize(credentials=..., project=...)` accepts any google-auth credentials, so
   a user's OAuth token drives the whole existing pipeline with no change to
   `collection`/`compositing`/`render`.
3. `gradio.mount_gradio_app(..., auth_dependency=...)` exists in Gradio 6.20 — the
   supported hook for an auth system that is not Gradio's own. `gr.Request.username` is
   only populated by Gradio's built-in `auth=`, so identity comes from our dependency.
4. Google rejects raw-IP and plain-HTTP OAuth redirect URIs (localhost excepted), and a
   redirect host must sit under a **verified authorized domain**. `carrot.aisl.c4ds.hnee.de`
   is fine — `hnee.de` is registrable — and Google never contacts the host itself: only
   the user's browser follows the redirect, so VPN-only reachability is not a problem.

## Decisions taken

| Decision | Choice | Rationale |
|---|---|---|
| Identity | **Google OAuth per user**, user names their own EE Cloud project | Quota, attribution and terms land on the user, not on HNEE. No long-lived secrets at rest. |
| Not chosen | Bring-your-own service-account key | Works without TLS, but makes us custodian of colleagues' private keys. Kept as a future provider behind the same seam, not built. |
| Host process | FastAPI app, Gradio **mounted** at `/` | OAuth needs real routes (`/login`, `/oauth2/callback`, `/logout`) that Gradio cannot express. |
| Render execution | **Subprocess per job**, pool of N | Forced by constraint 1; also buys cancel, memory isolation and stall recovery. |
| Concurrency | N = 3 workers, queue beyond that | Christian's choice ("a few in parallel"). With per-user projects these no longer contend for one quota. |
| Storage | `OUTPUT_DIR/<user-hash>/<run>` | Fixes blocker 2 at the root rather than by hiding a UI control. |
| Retention | Sweep runs older than 14 days | Bounded disk with nobody having to remember. |
| Single-operator mode | Untouched; new entry point `gee-animation-server` | The local GUI is how production videos get made. It must not regress. |

## Architecture

```
browser (on VPN)
  │  https://carrot.aisl.c4ds.hnee.de
  ▼
Caddy (TLS, long timeouts, websockets)
  ▼
FastAPI  ──────────────────────────────────────────────┐
  ├─ /login, /oauth2/callback, /logout   (authlib)      │
  ├─ SessionStore: sid -> {email, refresh_token, project}│  in memory
  └─ Gradio app mounted at /  (auth_dependency -> email) │
         │ submit                                        │
         ▼                                               │
     JobQueue (asyncio) ── max 3 running ────────────────┘
         │ spawn, stdin = credentials JSON
         ▼
     worker process:  ee.Initialize(user creds, user project)
                      cli.run(cfg)  ->  OUTPUT_DIR/<user-hash>/<run>/
```

### Components

**`gee_animation/webapp/auth.py`** — OAuth client (authlib), scopes **`earthengine` +
`cloud-platform` only** (not the `drive`/`devstorage` scopes `ee.oauth.SCOPES` lists;
this app exports nothing to Drive). Exposes `login`, `callback`, `logout`, and
`current_user(request) -> str | None` used as Gradio's `auth_dependency`.

**`gee_animation/webapp/session.py`** — `SessionStore`: signed cookie `sid` → record
holding the user's email, refresh token and chosen EE project. **In memory only**; a
restart logs everyone out, which is the correct trade for a group this size. No token
is ever written to disk or logged.

**`gee_animation/webapp/project.py`** — `validate_project(creds, project_id)`: initialise
EE and run one trivial `ee.Number(1).getInfo()`. Called when the user enters their
project id, so a missing EE registration or API-not-enabled is reported at login with
the fix, not three minutes into a render.

**`gee_animation/webapp/jobs.py`** — `JobQueue`: at most 3 concurrent
`asyncio.create_subprocess_exec` workers, each handed `{refresh_token, client_id,
client_secret, project, config}` **on stdin** (never argv, which is world-readable in
`/proc`; never env, which is readable by any same-UID process). Tracks state, streams
the worker's progress lines back to the user's Gradio session, and supports cancel by
killing the process group. A worker inherits a refresh token rather than an access
token because renders outlive the 1-hour access-token lifetime.

**`gee_animation/webapp/worker.py`** — the subprocess entry point: reads the JSON,
builds `google.oauth2.credentials.Credentials`, `ee.Initialize(...)`, then calls the
existing `cli.run(cfg)`. Nothing below this line changes.

**`gee_animation/webapp/storage.py`** — `user_dir(email)` =
`OUTPUT_DIR/<sha256(email)[:16]>/`, `list_runs(email)` (the fix for blocker 2 —
`list_previous_runs` becomes user-scoped), and `sweep(max_age_days=14)` run at startup
and then hourly.

**`gee_animation/gui.py`** — one change: `build_app()` takes an optional
`context` object providing `user_dir`, `list_runs` and `submit`. When absent it behaves
exactly as today, so the local single-operator GUI is untouched.

### Caps

A single run may not exceed **12 years** of monthly frames or the **1440p** preset in
server mode (configurable). Without this one user occupies a worker for an hour.
Enforced in `jobs.py`, not in the UI, so it cannot be bypassed.

## Prerequisites (institutional, not code — both block launch)

1. **A TLS certificate for `carrot.aisl.c4ds.hnee.de` that users' browsers trust.** The
   host is VPN-only, so Let's Encrypt HTTP-01 will not work; use **DNS-01** (needs access
   to the `hnee.de` zone) or an HNEE internal CA already trusted on staff machines.
2. **A Google Cloud OAuth client** in an HNEE-owned project, redirect URI
   `https://carrot.aisl.c4ds.hnee.de/oauth2/callback`, with either
   - consent screen **Internal** (requires HNEE Google Workspace / Cloud Identity) — no
     domain verification, no user cap; or
   - **External / testing** — `hnee.de` verified in Search Console by whoever controls
     the DNS, and up to 100 named test users. Either path avoids Google app verification.

Each user needs their own Cloud project with the Earth Engine API enabled and registered
for (non-)commercial use. That is a ~10-minute one-time chore and belongs in the README.

## Deployment on carrot

`docker compose` with two services: Caddy (TLS termination, `proxy_read_timeout` well
past the longest render, websocket upgrade for Gradio) and the app. Volumes:
`/data/out` (per-user renders) and `/data/cache` (`GEE_ANIMATION_CACHE_DIR`, so a restart
does not re-download thumbnails). Rootless Docker, non-root user in the image. Secrets
(OAuth client id/secret, cookie signing key) arrive as env from a root-owned `.env` that
is **not** committed — `work/UltraDetector/.env` being staged in a repo with no
`.gitignore` is the cautionary example.

Renders are CPU-bound (`lst_rf` trains a forest per frame), so this competes with GPU
jobs only for RAM and cores — no GPU booking needed.

## Correctness and security risks

| Risk | Control |
|---|---|
| A user's refresh token leaking into logs or disk | Tokens only in memory and on worker stdin; a log filter test asserts no `refresh_token` string ever reaches a log record. |
| Worker memory readable by other processes on a shared box | Document it: anyone with docker access on carrot can read worker memory. Acceptable for a trusted group; state it in the README so users consent knowingly. |
| One user's AOI/outputs visible to another | Per-user directory + user-scoped `list_runs`; a test asserts `list_runs(a)` never returns a path under `user_dir(b)`. |
| Uploaded file abuse (zip bombs, huge GeoJSON) | Gradio `max_file_size` on `launch`/`mount`, plus the existing `_region_aoi_from_upload` extension allowlist. |
| Stalled worker holds a slot forever | Per-job wall-clock timeout, then kill the process group and report it; the slot is freed. |
| Session fixation / CSRF on the OAuth flow | authlib `state` parameter, `HttpOnly`+`Secure`+`SameSite=Lax` cookie. |
| EE state leaking between users | Structural: one process per job, process exits after the job. |

## Acceptance criteria

1. An unauthenticated request to `/` redirects to Google, and no EE call is possible before login.
2. Two users logged in at once each see only their own previous runs; neither can load the other's by path.
3. Three renders run concurrently; a fourth queues and reports its position.
4. A render started by user A is billed to A's Cloud project (verified in that project's EE usage).
5. Cancelling a render kills its worker within 5 s and frees the slot.
6. A run directory older than 14 days is gone after the sweep; nothing newer is touched.
7. `gee-animation-gui` (local, single-user) behaves exactly as before — `tests/test_gui.py` unchanged and green.
8. `pytest -m "not integration" -q` green, including new tests for session, storage scoping, the queue and the log filter.

## Testing strategy

Unit tests mock at the existing seams: a fake credential provider, a fake worker spawn,
a `tmp_path` output root. No test touches Google or Earth Engine. One
`@pytest.mark.integration` test (behind `GEE_INTEGRATION`) exercises a real OAuth-less
service-account worker end to end, so the worker contract itself is covered.

## Known blockers

- Both prerequisites above. Until the certificate exists, nothing can be tested beyond
  `localhost`, which Google *does* permit as a redirect URI — so development can proceed
  against `http://localhost:7860` with a second OAuth client.
- Whether HNEE has Google Workspace/Cloud Identity decides Internal vs External/testing.
  Christian to confirm; it changes the 100-user cap and the domain-verification chore.

## Open questions

1. Should a user's EE project be remembered between sessions (a small on-disk map of
   email → project id, no secrets), or re-entered at each login? Remembering is friendlier
   and leaks nothing; it is the only persistent state in the design.
2. Does anyone need to *share* a render with a colleague? If yes, that is a small explicit
   feature (a per-run share link), not a reason to widen the default visibility.
