# Multi-user web app — handoff notes

Companion to the spec (`2026-10-14-multiuser-webapp-design.md`) and plan
(`../plans/2026-10-14-multiuser-webapp.md`). Those two say what was intended; this says what
was **built**, what was **decided along the way**, and what is **still open**. Written at the
end of implementation so the decisions outlive the session that made them.

## State

41 commits off `main` (`c74da2e`), 31 files, +6247/−24. Unit suite **891 passed, 1 skipped,
2 deselected, 2 warnings** (730 before this branch). `tests/test_gui.py` unchanged throughout,
82 green. The suite is network-free; the one live-Earth-Engine test stays behind the
`integration` marker and `GEE_INTEGRATION`.

New package `gee_animation/webapp/`:

| Module | Responsibility |
|---|---|
| `session.py` | in-memory session store, opaque `sid`, 7-day expiry |
| `oauth.py` | Google OAuth; requests exactly the two Earth Engine scopes |
| `project.py` | resolves and remembers the user's Cloud project; `validate` runs in the worker |
| `caps.py` | request limits — 12 years, 1440p, allowlisted presets; fails closed |
| `jobs.py` | asyncio queue, 3 concurrent, 4-hour job timeout, per-user cancellation |
| `spawn.py` | render subprocess: JSON lines over stdout, 32 MiB limit, process-group kill |
| `worker.py` | the subprocess itself: reads credentials from **stdin**, initialises EE, renders |
| `storage.py` | per-user output directories and the retention sweep (14 days, at startup then every 24 h) |
| `logsafe.py` | redaction filter on root, handlers, uvicorn's loggers and `logging.lastResort` |
| `app.py` | FastAPI host, OAuth routes, `ServerContext`, mounts the Gradio app |

`gee_animation/gui.py` gained one seam: `build_app(context=None)`. `context is None` is the
single-operator path and behaves exactly as before.

## Why one subprocess per render

`ee._state._state` is a single process-wide global holding `credentials` and
`cloud_api_user_project`, with no contextvar or thread-local. Two users cannot share a process
without one silently rendering under the other's identity. Everything else in the design follows
from that one fact, and it is why an event handler calling `ee.Initialize` in the web process is a
security bug rather than a style issue — one such handler (the Scene inventory button) survived
ten task reviews and was caught by the whole-branch review.

## The credential rule

A Google refresh token may exist **only** in process memory and on a worker's stdin. Never in
`argv` (world-readable via `/proc`), never in an environment variable, never on disk, never in a log
record, never in a user-facing error or traceback.

Verified end to end: callback → local variable (Authlib puts only `state`/`nonce` in the cookie) →
`Session(repr=False)` → opaque `sid` cookie → queue payload → the worker's stdin → `ee.Initialize`.
`spawn.worker_env` strips `GEE_OAUTH_*`, `GEE_COOKIE_SECRET` and `DNS_API_TOKEN` from the child's
environment; `WorkerProcess._payload` is cleared once drained; the worker classifies an
`invalid_grant` failure *before* scrubbing the token from the message; `logsafe` redacts `msg`,
`exc_info`, `exc_text` and `stack_info`, and its secret list is the union of live sessions and
in-flight jobs. All 44 logging calls in the installed Authlib 1.9.0 were checked: every one that
prints a token is provider-side and unreachable from the Starlette client.

**Known residual:** worker **stderr** is inherited unredacted into the container log. The worker
scrubs its own exceptions, but a library writing straight to stderr bypasses `logsafe`. No path
was found by which a token reaches it; it is the one logging surface not covered.

## Decisions worth knowing

- **Caps are code, not configuration.** 3 concurrent renders, 100 MB upload, 14-day retention live
  as `AppConfig` defaults. Only the job timeout (`GEE_JOB_TIMEOUT_S`, default 14400) and the proxy
  allow-list (`GEE_FORWARDED_ALLOW_IPS`) are environment-tunable.
- **The project field starts empty in server mode.** It used to default to `hnee-331218` — the
  author's own project — which would have silently attributed every colleague's renders to it.
- **Stop is per-user, not per-job.** The Stop button sends no job id at all; the server resolves the
  user from their session cookie. There is therefore no ownership claim to validate. The cost is
  that Stop cancels *all* of that user's unfinished renders, which the button says.
- **Retention does not require success.** The sweep treats any immediate child of a user directory
  as a run and reclaims it after 14 days whatever it contains, because a failed render is the common
  case and its directory would otherwise be permanent. It never removes a user directory or the
  output root. `gui.list_previous_runs` still lists only runs with media — the two callers
  deliberately disagree.
- **Uploaded AOIs are contained.** The AOI path arrives inside the client's payload and Gradio
  validates only the extension, so the resolved path must sit under Gradio's upload cache or a
  staged preset directory. 12 bypass variants were probed (symlinks, `..`, upper-case on a
  case-insensitive filesystem, NUL byte, relative traversal); all refused.
- **Sessions expire after 7 days**, and signing in again drops the previous session. The window has
  to outlast a 4-hour render plus a weekend, because the run browser is session-scoped.
- **Per-user isolation relies on Gradio in one place.** `_load(sel)` passes the client's dropdown
  value to `load_previous_run` with no containment check of our own; `Dropdown.preprocess` rejects
  values outside the per-session choices. That is a dependency, not a check we own — if Gradio's
  behaviour changes, this needs our own check.

## Before this can be deployed

1. **Nothing in `deploy/` has ever been built or started.** No Docker daemon was available. The
   Dockerfile, `compose.yaml` and `Caddyfile` are static-reviewed only: syntax read by hand, every
   environment variable cross-checked against the code that reads it, every path and service name
   cross-checked between files. Run `docker compose config`, build, and start it before trusting it.
2. **A browser-trusted TLS certificate** for `carrot.aisl.c4ds.hnee.de`. The host is VPN-only, so
   Let's Encrypt HTTP-01 cannot reach it. The shipped `Caddyfile` expects a mounted cert and key
   (an HNEE internal CA is the simplest source); DNS-01 is a commented alternative and needs a Caddy
   built with the provider plugin via xcaddy.
3. **A Google OAuth client** whose consent screen is Internal, or External/testing with `hnee.de`
   verified and at most 100 named users. Redirect URI
   `https://carrot.aisl.c4ds.hnee.de/oauth2/callback`. Google refuses raw-IP and plain-HTTP redirect
   URIs, so the hostname and TLS are prerequisites, not polish.
4. **`GEE_COOKIE_SECRET`** generated once and kept stable across restarts
   (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
5. **Each user needs their own Cloud project** with the Earth Engine API enabled and registered.
   `project.validate` gives them the guidance, but only once they submit.
6. **Smoke tests that need a browser and live Earth Engine.** None of these can be unit-tested;
   run them once the stack is up:
   - Open the site in a private window. Expect a redirect to Google sign-in, a consent screen that
     mentions **offline access**, then the GUI. An error mentioning `sessions` or cookies means the
     `gr.Request` delegation is wrong.
   - Enter your own project id, upload a small AOI GeoJSON, pick a 2-month range at 720p, generate.
     Expect queued/rendering progress text, then a video.
   - Clear the project box and generate: expect the "enter your own Google Cloud project id" message.
   - Apply a preset: the project box must stay empty.
   - Upload a file over 100 MB (`head -c 120M /dev/zero > big.zip`): expect refusal in the UI and no
     render; then a small AOI must still work.
   - Click the run-browser refresh: expect only your own runs. Then check in a second browser
     profile that a second user sees none of the first user's runs.
   - Start a long render and click Stop: expect it to stop within a poll or two, and the slot to free.
   - Check `<output>/<user>/<run>/aoi/` holds the copied AOI after a successful run.
   - Confirm the `sid` cookie is `Secure` and the callback URL is https. If sign-in loops, check
     `GEE_FORWARDED_ALLOW_IPS` includes the Caddy container's address.
   - Run something longer than an hour, to prove the refresh token works: expect a completed render,
     not an auth failure partway.
   - **Confirm the render was billed to the signing-in user's own project**, not to HNEE's. This is
     the design's central promise and the only check that can establish it.

## Accepted limitations

- **A render holds the user's refresh token in worker memory.** Anyone with Docker access on the
  host can read it. The README says so in as many words; it is the honest cost of rendering under
  each person's own identity, and nothing is written to disk.
- Gradio's upload cache is shared and content-addressed, so one user could read another's uploaded
  AOI *if they already knew its content hash* — which means already having the file. Per-user cache
  directories are not available in one shared Gradio process.
- `GEE_FORWARDED_ALLOW_IPS` defaults to loopback plus all RFC1918. Narrow it to the Caddy
  container's address if anything untrusted can reach the app's port.
- The run browser polls with `time.sleep(2)` on a Gradio worker thread. Fine for ~20 users against
  Gradio's 40-thread default; it would need to become async at a larger scale.
- A user's Cloud project id is not remembered across restarts, and runs cannot be shared between
  users. Both were left open deliberately in the spec.
- The suite reports two warnings. One is upstream (`StarletteDeprecationWarning` about httpx in
  `fastapi.testclient`). The other is `coroutine 'JobQueue.submit' was never awaited`, emitted at GC
  time by the two tests that deliberately wedge an event loop and close it before its scheduled
  callback runs. Both are test-time only; neither was silenced, so a third warning stays visible.
