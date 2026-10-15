# Final whole-branch review — `worktree-multiuser-webapp` (c74da2e..50e633c, 33 commits)

Reviewer: final gate. Read in the order the brief specified: `final-review-brief.md`,
`code-reviewer.md`, the 226 KB `review-c74da2e..50e633c.diff`, `progress.md`, then the spec and
plan. Everything below was read in the worktree, and every empirical claim was probed with the
interpreter in the `GEE_animation` conda env (no `pip install`, no Docker, no network).

**Verdict: ship-with-fixes.** One Critical, seven Important, several Minor. The security core —
the credential path, the per-user storage boundary, the queue — is genuinely well built and
genuinely well tested. What is wrong is at the seams the plan never drew a line through: one
pre-existing GUI button that was never routed through the new architecture, a timeout that
contradicts both the caps and the README, retention that cannot see a failed run, and a chart that
is silently always empty.

---

## Strengths (specific, and verified rather than assumed)

- **The test suite is real.** I ran 22 mutations against the invariants that matter. **20 were
  caught**, each by a test that failed for the right reason. Caught: AOI containment removed
  (`app.py:83`), `user_dir` losing its per-user hash, `worker_env` scrubbing nothing,
  `Session.refresh_token` losing `repr=False`, the redactor turned into a no-op, the session
  cookie losing `httponly/secure`, the "exited without a done event" guard, `_safe_part` dropping
  sanitisation, `caps.check` removed from `submit`, `submit` ignoring the session identity,
  `position()` always 0, `STDOUT_LIMIT` back to asyncio's 64 KiB, `_LoginRedirect` passing
  everything through, `app.load(_fill_runs)` removed, `sweep` deleting nothing, the callback
  accepting a missing refresh token, the worker's token scrub removed, `start_new_session`
  dropped, and `submit` not copying the AOI next to the run. Only the two boundary mutations the
  ledger already flagged survived. For a branch whose tests were written by the agents that wrote
  the code, that is an unusually honest suite.
- **Two flagged-for-final-review items are now resolved empirically, not by reading docs.**
  1. *Does Gradio accept a dropdown selection whose choices were set per-request via
     `gr.update(choices=...)`?* **Yes.** I drove the real objects: `build_app(context=…)`, a
     `StateHolder`, `postprocess_data` of the `app.load` → `_fill_runs` event, then
     `preprocess_data` of the `load.click` → `_load` event. The session's component copy picks up
     the new choices (`[('run1 (3 frames)', '/data/out/AAA/run1/x.mp4')]`), the legitimate value
     is accepted, and a forged path (`/data/out/BBB/secret/y.mp4`) is **refused** by
     `Dropdown.preprocess` with `Value: … is not in the list of choices`. So "Load a previous
     animation" works in server mode *and* the dropdown is a second, independent containment
     check. Nothing to fix.
  2. *The two caps boundary mutations.* Confirmed surviving: `<` → `<=` on the preset cap and
     `>` → `>=` on the year cap each pass all 839 tests. Behaviour today is correct; only the
     regression fence is missing.
- `logsafe.py` is the right shape: a filter on the logger *and* every handler (Ruling J), on
  uvicorn's own loggers (Ruling K), on `logging.lastResort` paired with its removal in a
  `try/finally`, and it withholds the record rather than failing open when the secret list is
  unavailable.
- `spawn.py` gets the three things that are easy to get wrong right: `limit=STDOUT_LIMIT` (the
  64 KiB `done`-event bug), `start_new_session=True` so `killpg` reaches ffmpeg, and dropping
  `self._payload` the moment the credentials are on the pipe.
- `_contained_aoi` is a proper fix for the Task 9 Critical, not a patch: it resolves first, then
  requires containment in an allow-list of roots, and it runs *before* any directory is created.
- The departures from the plan that I checked against the spec were all defensible: `project.remember`
  instead of `set_project` (Ruling F), the asymmetric `from None`/`from exc` in `project.validate`
  (Ruling U), `concurrency_limit=None` (Ruling X), and the explicit
  `__annotations__["request"] = gr.Request` (Ruling V).

### Rulings I think are mis-ruled

- **Ruling I is mis-ruled, and its own cost-if-wrong understates it.** The ledger judged "a run
  whose media failed to encode is never swept" to be "a leak, not a loss". In the shipped system
  the common case is not a failed encode — it is a *failed, cancelled, timed-out or
  OOM-killed render*, and `submit` creates `run_dir/aoi/<file>` before the worker even starts, so
  every one of those leaves a directory the sweep structurally cannot see. See Finding 4.
- Everything else known-and-accepted I agree with. Ruling P's comment does **not** overclaim: it
  says "Not expected to fire … Kept deliberately so that if a future change adds an await ahead of
  this line", which is exactly what the instrumentation showed. Ruling W is the right call at this
  scale. Ruling CC is correctly parked and correctly documented.

---

## Findings

### 1. Critical — the Scene-inventory button runs Earth Engine inside the shared web process

`gee_animation/gui.py:1156` — `inv.click(_inventory, inputs, outputs)` is wired
**unconditionally**. `_go` was carefully routed through `context.submit` in server mode
(`gui.py:1073`); `_inventory` was not. `_inventory` calls `run_inventory(...)` → `_prepare(...)` →
`deps.init(project)` → `gee_animation/auth.py:19 init()` → `ee.Initialize(...)` **in the FastAPI /
Gradio process**.

Four things go wrong at once, and the first is the one invariant this whole branch exists to
protect:

- `ee._state._state` is the process-wide global the spec's Constraint 1 is built around. The
  inventory path writes it in the *shared* process. Two users clicking "📋 Scene inventory (CSV)"
  at the same time clobber each other's EE state; a third user's `project.validate` (if it is ever
  wired, Finding 6) would read whatever the last clicker left.
- The web process holds **no user credentials**. `auth.init` falls through to
  `ee.Initialize(project=…)` with ADC and then to `ee.Authenticate()` — which in a container with
  no tty either raises or sits waiting for input, on a Gradio worker thread.
- If an operator ever sets `EE_SERVICE_ACCOUNT_KEY` (the Dockerfile documents exactly that for the
  single-user container, and the image is shared), `auth.init` takes the service-account branch and
  **every signed-in user's inventory runs under HNEE's identity** — spec blocker 3 reopened, no
  attribution, HNEE's quota.
- `_prepare` defaults `out_dir` to `Path(OUTPUT_DIR) / f"{sensor}_{index}_{stamp}"`
  (`gui.py:446`), i.e. straight into `/data/out/` — a sibling of the per-user hash directories,
  outside anyone's `user_dir`, invisible to `list_runs`, and misread by `sweep` as a *user*
  directory.

Failure scenario: user A signs in, uploads an AOI, types their project id, clicks **📋 Scene
inventory (CSV)** instead of Generate. The web process calls `ee.Initialize` with no credentials;
they get an auth error or a wedged worker thread. On a host where a service-account key is
present, they instead get a CSV billed to `hnee-331218` with a `/data/out/landsat_lst_…/`
directory nobody owns and retention cannot reach.

No test covers the inventory button in server mode.

**Fix (small):** in server mode either hide `inv` (`visible=context is None`) or route it through
the queue the way `_go` is routed. Hiding it is one line and ships today; routing it needs a worker
mode for `run_inventory`. Do not leave it wired as-is.

### 2. Important — the queue's 90-minute timeout contradicts both the caps and the README

`gee_animation/webapp/jobs.py:25` — `timeout_s: float = 5400` (90 min), and
`create_app` never overrides it: `JobQueue(spawn=spawn, max_concurrency=cfg.max_concurrency)`
(`app.py:174`). `AppConfig` has no field for it and `main()` reads no environment variable, so an
operator cannot raise it without editing source.

Meanwhile:
- `README.md` (Limits): *"Renders are long (a ten-year monthly animation can take tens of minutes
  to hours), so the Caddyfile allows 4-hour proxy timeouts."*
- `deploy/Caddyfile:18` — `read_timeout 4h`.
- `gui.py:172` — `SERVER_POLL_TIMEOUT_S = 7200` (2 h), commented as "Comfortably above the
  JobQueue's own 5400 s run timeout".
- `caps.check` allows **12 years** at any supported cadence. 12 years at `10day` is ~438 frames; at
  `semimonthly`, ~288. With `index="lst_rf"` the pipeline trains a random forest *per frame*.

Failure scenario: a user submits 2014-01-01 → 2025-12-31, cadence `10day`, index `lst_rf` — all
within the caps, all offered by the dropdowns. At minute 90 the worker is SIGKILLed mid-render and
the user is told *"Render timed out after 90 minutes and was stopped. Try a shorter date range."*
Their Earth Engine quota is spent, the partial run directory is left behind (and is unsweepable —
Finding 4), and the documentation told them four hours was fine.

**Fix:** add `job_timeout_s` to `AppConfig`, pass it to `JobQueue`, read it from
`GEE_JOB_TIMEOUT_S` in `main()`, default it to something consistent with the Caddyfile (4 h), and
make the README state the real number. Alternatively cap the *frame count*, not just the year span,
so the caps and the timeout agree.

### 3. Important — cancel is implemented, tested, and unreachable

`gee_animation/webapp/jobs.py:51 cancel()` is called from **nowhere** in production code (verified
by grep over `gee_animation/`; the only hits are `jobs.py:58`'s internal `self._tasks[...].cancel()`
and `app.py:263`'s `future.cancel()` on the submit timeout). `ServerContext` exposes `list_runs`,
`submit` and `job_status` only. There is no cancel button, no cancel route, and Gradio's own stop
button cannot interrupt `_go`, which is a synchronous `time.sleep(2)` loop on a worker thread.

This is spec **acceptance criterion 5** — *"Cancelling a render kills its worker within 5 s and
frees the slot"* — not shipped. `tests/test_webapp_jobs.py:60` and `:160` test the queue method, so
the suite looks like it covers the criterion.

Failure scenario: a user submits a 12-year `lst_rf` run by mistake and immediately sees the error.
There is nothing they can do. With three slots, three mistakes hold the whole server for up to 90
minutes (Finding 2) and everyone else sits in the queue.

**Fix:** a "Stop" button beside Generate, wired to a `ServerContext.cancel(request, job_id)` that
checks `job.email == user_of(request)` before calling `queue.cancel`. The ownership check matters
(see Finding 10).

### 4. Important — retention structurally cannot see a failed run, so `/data/out` grows forever

`gee_animation/webapp/storage.py:30` — `_is_run(d)` is true only if `d` contains an `.mp4` or
`.gif`. `sweep` only ever considers directories for which `_is_run` holds. This is Ruling I, and it
is the wrong side of the trade in the shipped system, because `app.py:249-253` creates
`run_dir/aoi/<name>` **before** the job is even queued:

```
aoi_dir = run_dir / "aoi"
aoi_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2(source, aoi_dir / source.name)
```

So *every* render that does not reach a finished `.mp4`/`.gif` leaves a permanent directory:
- a render that errors in Earth Engine (bad project, revoked access, quota, AOI too large),
- a render killed at the 90-minute timeout (Finding 2),
- a worker killed by the OOM killer — three concurrent `lst_rf` renders on a box shared with GPU
  jobs is exactly how that happens,
- every job in flight when the container restarts (`restart: unless-stopped`),
- any partial run that wrote frame PNGs but no media.

These accumulate at one directory per failure, each holding the user's uploaded AOI (up to 100 MB)
and possibly hundreds of frame PNGs, and **nothing in the system will ever delete them**. Spec
blocker 5 ("`out/` grows without bound") is only half fixed.

Failure scenario: 20 users, a few failed renders each per month, each leaving a 20 MB AOI plus
frames. After a year `/data/out` holds gigabytes that `sweep` walks past on every pass, while the
README promises "finished renders are deleted after 14 days".

**Fix:** keep Ruling I's media rule for *recent* directories but add a second rule — a directory
under a user dir whose newest file is older than `max_age_days` is deleted whether or not it holds
media. That still cannot touch anything a user is likely to want, because a 14-day-old directory
with no video is by definition a dead run. Pin it with a test that creates an AOI-only run
directory.

### 5. Important — Gradio's file cache is a second unbounded, unmounted copy of every render

`app.py:274` calls `gr.mount_gradio_app(...)` with no `allowed_paths`. I probed what that means:
`mount_gradio_app` sets `blocks.allowed_paths = []` and leaves `blocks.has_launched = False`, so
`processing_utils._check_allowed` returns early and **does not refuse** a `/data/out/...` path
(good — the video is not broken). But the consequence is that `move_files_to_cache` **copies** every
returned file into `utils.get_upload_folder()`, and `route_utils.file_fetch` then serves it from
there (`created_paths=[upload_dir, get_cache_folder()]`).

So each finished render writes a second full copy of the MP4, the GIF, the frames ZIP and **every
frame PNG** into Gradio's cache — which defaults to `tempfile.gettempdir()/gradio`, i.e. the
container's writable layer. `GRADIO_TEMP_DIR` is not set anywhere (Dockerfile, compose,
`.env.example`), that directory is not one of the two volumes, and `storage.sweep` only ever walks
`cfg.output_root`. Nothing cleans it.

Failure scenario: a user renders a 438-frame run with `raw_frames` on. ~900 MB lands in
`/data/out/<hash>/<run>/` (swept after 14 days) *and* ~900 MB lands in `/tmp/gradio` (never swept).
After a few weeks of normal use the container's filesystem fills; the next render fails on write,
and because the failure leaves a media-less directory, Finding 4 compounds it.

**Fix:** set `GRADIO_TEMP_DIR=/data/cache/gradio` in the Dockerfile or `compose.yaml` so the copies
land on a volume, and extend the sweeper to age them out (or set `delete_cache=(…)` on the Blocks).
The one-line env change is the minimum; without it the deployment's disk story is incomplete.

### 6. Important — `project.validate` is dead code, and the spec requires it

`gee_animation/webapp/project.py:18 validate()` is called from **nowhere** in `gee_animation/`
(grep: only its own definition and a docstring reference). Only `remember()` is wired, at
`app.py:240`. `worker.py:47-49` documents the departure — *"NOT validated up front, so a wrong one
fails right here in Initialize, with Earth Engine's message"*.

The spec is explicit about the opposite (`…/specs/2026-10-14-multiuser-webapp-design.md`,
Components): *"`validate_project(creds, project_id)`: initialise EE and run one trivial
`ee.Number(1).getInfo()`. Called when the user enters their project id, so a missing EE
registration or API-not-enabled is reported at login with the fix, not three minutes into a
render."* The module's whole value is its two error branches — the "enable the Earth Engine API at
`https://console.cloud.google.com/apis/library/earthengine.googleapis.com` and register the project"
message, and the "your account has no permission on project X" message. Neither can ever be shown.

Failure scenario: a new user follows the README, creates a Cloud project but forgets to register it
for Earth Engine (the single commonest setup error, which is why this module exists). They upload an
AOI, submit, wait behind two other renders, finally get a worker slot, and receive Earth Engine's
raw `Project … has not been used in project … before or it is disabled` text with no instructions.
They repeat this three times before asking for help.

Note also that validating in the web process is *itself* an `ee.Initialize` in the shared process
(the Finding 1 hazard), so the correct fix is not simply to call `validate` from `submit`. Either
run the probe in a short-lived subprocess (the worker already is one — a `--validate-only` mode is
cheap), or accept the departure and delete the module plus its 94 lines of tests so the next reader
is not misled.

### 7. Important — the time-series chart is permanently empty in server mode

`gee_animation/webapp/worker.py:52-55`:

```
mp4, gif, pngs, zip_path, status, series = runner(...)
_emit(stdout, event="done", mp4=mp4, gif=gif, frames=pngs, zip=zip_path, status=status)
```

`series` is unpacked and thrown away. Correspondingly `gui.py:1096-1097` returns
`pd.DataFrame()` as the 7th output (`chart`), where the single-operator path builds rows from
`series` and plots them. `outputs` is the same list in both modes
(`gui.py:994`), so the component is there, visible, and always blank.

Failure scenario: a user who has used the local GUI renders the same AOI on the server. The video
is right, the status line is right, and the "AOI mean per period" chart — the thing that makes the
run *measurable* rather than just pretty — is empty with no explanation. Nothing in the UI says
why. This is the "silently wrong" case the brief asked me to look for.

**Fix:** add `series=series` to the worker's `done` event (it is a list of
`(period, inside, outside)` tuples and JSON-serialises fine; `STDOUT_LIMIT` is 32 MiB and already
carries the frame list), then build the DataFrame in `_go`'s server branch with the same loop the
local branch uses.

### 8. Important — a regression in the queue's wall-clock timeout hangs CI instead of failing it

The Task 9 implementer flagged this as a possibility for the sweeper; I verified it for the queue.
Replacing `jobs.py:83`

```
await asyncio.wait_for(proc.run(on_event), timeout=self._timeout)
```

with `await proc.run(on_event)` makes `tests/test_webapp_jobs.py` **hang indefinitely on its fourth
test**, `test_a_stalled_job_is_killed_at_the_timeout_and_says_so` (`:80`). I let it run past 600 s
before killing it. Every other mutation in my set failed cleanly in ~25 s.

Failure scenario: someone refactors the queue, drops the `wait_for`, and pushes. CI does not report
a failure — it burns its whole job budget and is killed by the runner's global timeout, which
reads as flaky infrastructure rather than a broken safety net. `pytest-timeout` is deliberately not
installed, so nothing bounds it.

**Fix:** give that one test its own bound — wrap the body in `asyncio.wait_for(..., timeout=5)` and
assert the state, so a missing production timeout fails in five seconds with a clear message. This
is the cheapest fix on the list and the one most likely to save a future session.

### 9. Important (bounded) — a zip bomb is not actually controlled

`gee_animation/gui.py:280-285` — `_region_aoi_from_upload` does
`zipfile.ZipFile(p).extractall(dest)` into a fresh `mkdtemp()` with no bound on the uncompressed
size. The spec's risk table lists *"Uploaded file abuse (zip bombs, huge GeoJSON)"* with the control
*"Gradio `max_file_size` on `launch`/`mount`, plus the existing `_region_aoi_from_upload` extension
allowlist"*. Neither bounds expansion: `max_file_size` (verified: `blocks.max_file_size =
104857600`, and `tests/test_webapp_app.py:357` asserts a real 413) limits the *compressed* upload,
and the extension allowlist is what lets the zip in.

Path traversal is *not* a concern — CPython's `ZipFile._extract_member` sanitises member names
(strips drive letters, leading separators and `..`). Only expansion is.

Failure scenario: a signed-in user uploads a 10 MB zip that expands to 100 GB. The extraction runs
in the worker subprocess and fills the container filesystem, breaking renders for all 20 users
until someone clears it. This needs a signed-in account, so it is a trust-group concern rather than
an attack surface — but it is also reachable by accident with a genuinely large shapefile.

**Fix:** sum `ZipFile.infolist()` `file_size` before extracting and refuse above, say, 500 MB.
Three lines, and it makes the spec's claimed control true.

### 10. Minor — `job_status` has no ownership check and raises a bare `KeyError`

`app.py:267-271` — `ServerContext.job_status(job_id)` takes no request and performs no check that
the job belongs to the caller; `jobs.py:42 status()` raises `KeyError` for an unknown id. Job ids
are `secrets.token_urlsafe(16)` (128 bits), so this is not practically exploitable today — but it is
the only place in the branch where a client-supplied identifier reaches server state without a
check, and Finding 3's cancel button would make it exploitable in a way that *matters* (killing
another user's render). Add `job.email == user_of(request)` when you add cancel, and make an unknown
id a friendly error rather than a `KeyError` traceback.

### 11. Minor — the queue never prunes, so job records live for the process lifetime

`jobs.py:29-31` — `_jobs`, `_tasks` and `_order` are only ever appended to. `Job.result` holds the
whole `done` event, including the full frame-path list (hundreds of paths for a 10-day-cadence
run). At 20 users this is megabytes, not a leak that breaks anything, but `position()` also scans
`_order` linearly on every 2-second poll from every waiting user. Prune terminal jobs older than,
say, an hour.

### 12. Minor — the default `out_dir` lands at the output root, where `sweep` misreads it as a user

`gui.py:446` — `out_dir = out_dir or str(Path(OUTPUT_DIR) / f"{sensor}_{index}_{stamp}")`. In server
mode `submit` always supplies `out_dir`, so the only way to hit this is Finding 1's inventory path —
but if it is hit, `storage.sweep` iterates `root.iterdir()` treating each entry as a *user*
directory and its children as runs, so a run directory at the root is both unlistable and
unsweepable. Worth a comment at minimum; fixed for free if Finding 1 is fixed.

### 13. Minor — a short window where a live refresh token is not in the redactor's secret list

`logsafe.RedactingFilter` redacts only the tokens `secrets_now()` returns, i.e. tokens of live
sessions. In `oauth.add_routes.callback` the token exists in `token["refresh_token"]` from
`authorize_access_token()` (`oauth.py:45`) until `store.new(...)` (`oauth.py:58`). Anything logged in
that window would not be redacted.

I checked whether anything *does* log there: I scanned all 44 logging calls in the installed
authlib 1.9.0. Every `log.debug("Issue token %r", token)` is in authlib's **provider** (server)
code — `oauth2/rfc6749/grants/*`, `oauth2/rfc7523/*`, `oidc/core/grants/*` — none of which the
starlette *client* integration we use ever executes. httpx logs status lines, not bodies. Python
tracebacks do not include frame locals. So no actual leak path exists; the window is a latent one.
Symmetrically, after `logout` drops a session the token remains valid at Google but is no longer
redactable. Both are acceptable; recorded so the next reader does not have to re-derive it.

### 14. Minor — the two caps boundary mutations (confirmed)

`caps.py:38` (`<` → `<=`) and `caps.py:29` (`>` → `>=`) each pass all 839 tests. Two tests —
"1440p is accepted" and "an exactly-12-year range is accepted" — close it.

### 15. Minor — README and deploy polish

- `README.md` (the block-quote warning) ends *"nothing is written to disk"* immediately after the
  paragraph about the refresh token. True of the token; false of the uploaded AOI
  (`run_dir/aoi/…`), the renders, and Finding 5's cache copies. Reword to "no credential is ever
  written to disk".
- `README.md` (Limits) says 4-hour proxy timeouts without mentioning the real 90-minute job cap
  (Finding 2).
- `deploy/compose.yaml:27-28` ships the cert volumes commented out, so a literal `docker compose up
  -d --build` fails on a Caddyfile referencing files that are not mounted. It is documented in both
  the compose comments and the README prerequisite, so this is a documented speed bump rather than a
  defect — but the README's `docker compose up -d --build` block reads as if it is the whole recipe.
- `deploy/compose.yaml` sets no memory limit and no healthcheck on `app`. Three concurrent `lst_rf`
  renders training a forest per frame on a box shared with GPU jobs is the OOM-killer scenario that
  feeds Finding 4. A `deploy.resources.limits.memory` and a `/login` healthcheck are cheap.
- `deploy/.env.example` documents `GEE_FORWARDED_ALLOW_IPS` well (Ruling CC); nothing to add.

---

## The credential path, end to end: does the constraint hold?

**Yes.** Traced as one path, with the probes noted:

1. **Google callback** — `oauth.py:45` `authorize_access_token(request)` returns the token dict. It
   lives in a function local. The Starlette session cookie holds authlib's `state`/`nonce` only (no
   token), and it is signed with `cfg.cookie_secret`, `https_only=True`, `same_site="lax"`
   (`app.py:170`).
2. **Session store** — `oauth.py:58` `store.new(email, refresh_token)`. `Session.refresh_token` is
   `field(repr=False)` and `__str__` returns `repr`, so neither an f-string nor a dataclass repr in
   a log line nor a traceback frame summary can print it. Mutation M4 (removing `repr=False`) is
   caught by a test. The store is a plain dict in memory; nothing writes it anywhere.
3. **Session cookie** — the cookie carries an opaque `sid` (`secrets.token_urlsafe(32)`), not the
   token, with `httponly=True, secure=True, samesite="lax"` (`oauth.py:60`). Mutation M7 caught.
4. **Queue** — `app.py:254-259` builds the credentials dict and passes it to `queue.submit`;
   `jobs.py:37` folds it into `payload`, held only in the `_run` coroutine's frame. When the
   coroutine completes CPython clears that frame, so no terminal job retains it (and
   `tests/test_webapp_app.py:648` pins the equivalent for `WorkerProcess`).
5. **Subprocess stdin** — `spawn.py:34-44`. argv is exactly
   `[sys.executable, "-m", "gee_animation.webapp.worker"]` — no token. `env=worker_env()` strips
   `GEE_OAUTH_*` and `GEE_COOKIE_SECRET`/`DNS_API_TOKEN`; the user's token was never in the
   environment at all. The payload goes on the pipe, then `self._payload = None`. Mutations M3 and
   M20 caught.
6. **`ee.Initialize`** — `worker.py:36-41` builds `google.oauth2.credentials.Credentials` from the
   token in memory. No file, no env.
7. **Error paths** — `worker.py:57-64` classifies `invalid_grant`/`expired or revoked` into
   `_REAUTH_HINT` *before* scrubbing (so a short token cannot mangle the markers), then replaces the
   literal token with `***`. Mutation M18 caught. `jobs.py:92-98` uses a deliberately generic
   message for any spawn-side exception ("The render failed to start."), so no exception text
   reaches the UI from that path. `project.validate` scrubs `credentials.refresh_token` and
   `.token` and raises `from None` on the branch that embeds exception text.
8. **Log records** — `logsafe.install` on the root logger *and* its handlers, on `uvicorn`,
   `uvicorn.error`, `uvicorn.access`, and on `logging.lastResort`, installed in `_on_start` (after
   uvicorn configures logging, per Ruling J) and removed in a `finally`. It redacts `getMessage()`,
   renders and redacts `exc_info` before the formatter can, redacts `exc_text` and `stack_info`, and
   withholds the record entirely if the secret list cannot be obtained. Mutation M6 caught.
9. **What a crash dumps** — a Python traceback carries no frame locals, so an uncaught exception
   anywhere on this path prints no token. Worker memory is readable by anyone with Docker access on
   carrot; that is in the spec's risk table and in the README's block-quote warning, which is the
   right handling.

**The one residual surface** is the one the brief named: the worker's **stderr** is inherited
unredacted into the container log (`spawn.py:34-38` passes no `stderr=`). I looked for a path that
puts a refresh token there and found none — `google.auth` raises rather than printing, authlib's
client side has no token logging (scanned, see Finding 13), and the worker's own `except` writes to
*stdout* through `_emit`. It stays the one logging surface `logsafe` cannot cover. Capturing it and
routing it through the redactor would close the last gap, but I would not block the deploy on it.

Latent-but-unexercised: Finding 13's window between `authorize_access_token` and `store.new`.

## Per-user isolation: does it hold?

**Yes**, for runs, outputs, uploads, sessions and project ids — with one parked exception that I
re-confirmed is real, and one I newly checked and cleared. What I probed:

- **Output directories.** `storage.user_dir` = `root/sha256(email.strip().lower())[:16]`;
  `list_runs` calls `list_previous_runs(base_dir=user_dir(...))`. Mutation M2 (drop the hash) and
  M11 (`submit` using a fixed email) are both caught by tests. `tests/test_webapp_app.py:330`
  pins "each user lists only their own runs, and an unknown session lists none".
- **The AOI path (the Task 9 Critical).** `_contained_aoi` resolves then requires containment in
  Gradio's upload folder or a staged `/tmp/gee_preset_*`. Mutation M1b is caught; M21 (not copying
  the AOI next to the run) is caught.
- **The "Load a previous animation" dropdown — the third vector I went looking for.** `_load(sel)`
  passes the client-supplied dropdown value straight to `load_previous_run`, which reads that path,
  zips its sibling frames *into that directory*, and reads its `metadata.db`. There is no
  containment check in `gui.py` at all. I expected a cross-user read here. **It is closed, but by
  Gradio rather than by this branch:** I drove `preprocess_data` on the real `_load` BlockFunction
  and `Dropdown.preprocess` refuses any value not in the session's choices —
  `Value: /data/out/BBB/secret/y.mp4 is not in the list of choices: ['/data/out/AAA/run1/x.mp4']`.
  The per-session choices come from `context.list_runs(request)`, which is user-scoped. So isolation
  holds. It holds *incidentally*, though: nothing in this branch documents the dependency, and
  `allow_custom_value=True` on that dropdown, or a future Gradio that drops server-side choice
  validation, would silently open a read of any `.mp4`/`.gif` on the box plus a write of a ZIP into
  another user's directory. Worth one comment at `gui.py:1158`.
- **Sessions and project ids.** `sid` is 256-bit random; `user_of` looks up the closure's
  `app.state.sessions`, not `request.app.state` (the Gradio sub-app has no `sessions`). I confirmed
  `gr.Request.cookies` actually resolves (`{'sid': 'abc123'}`) rather than relying on the tests'
  fakes. `project.remember` refuses a vanished session with an actionable message.
- **Run directory names.** `_safe_part` restricts the client-supplied `sensor`/`index` to
  `[A-Za-z0-9_-]{,32}` and `submit` adds `secrets.token_hex(3)`, so no traversal and no collision
  on a double-click. Mutation M9 caught.
- **Still parked and still real:** Gradio's upload cache is shared across users and
  content-addressed, so user B *could* reach user A's upload if they knew its hash. Finding 5 makes
  that cache larger and longer-lived than the parked note assumed (it now holds every *output*
  too, not just uploads), which is one more reason to set `GRADIO_TEMP_DIR` and age it out.
- **Not an isolation break, but a shared namespace:** Finding 1's inventory path writes to
  `/data/out/<run>` rather than under a user hash. No user can read another's data through it, but
  it puts unowned directories in the shared root.

## The ~20 deferred minors: fix or drop

**Fix before deploy (4):**
1. **Task 8's 7200 s poll bound vs a legitimately long queue wait** — a job queued behind two
   12-year renders can exceed the 2-hour poll bound, after which the UI says "gave up waiting"
   while the job stays queued and may still render unseen, into a directory the user is never shown.
   This is the same seam as Finding 2 and should be fixed with it: bound the *render*, not the wait,
   and have the poll loop tell the user their job is still queued rather than claiming it gave up.
2. **Task 6's two boundary mutations** — confirmed surviving. Two one-line tests (1440p accepted; an
   exactly-12-year range accepted). The caps are the only thing standing between one user and a
   worker slot; they deserve a fence at both boundaries.
3. **Task 1's "sessions have no expiry or size bound"** — `Session.created` is recorded and never
   read. A browser left open on the VPN keeps a usable refresh token in server memory indefinitely,
   and `secrets_now()` keeps feeding it to the redactor. An absolute lifetime (say 12 h, checked in
   `SessionStore.get`) is a handful of lines and is the difference between "a restart logs everyone
   out" being a security property and being an accident of uptime.
4. **Task 7's "old sessions linger when the same user signs in again"** — with
   `prompt=consent` every sign-in mints a new refresh token and a new `sid`, and the old
   `Session` is never dropped. A user who signs in from three browsers over a week leaves three live
   sessions, each independently sufficient to render as them. Drop prior sessions for the same email
   in `store.new`, or at least bound the count.

**Fix if a round is cheap (2):**
5. **Task 7's "OAuthError from `authorize_access_token` surfaces as a 500"** — the user's first
   experience of a failed consent (they clicked Deny, or the state cookie expired) is an opaque
   500. The two adjacent branches already return friendly 400s; a `try/except OAuthError` returning
   "Sign-in did not complete. Please try again at /login." matches them.
6. **Task 6's `".1f"` self-contradicting cap message** — "That range is 12.0 years; server mode
   allows 12 years" reads like a bug report waiting to happen. `f"{years:.2f}"` fixes it.

**Drop (the rest):** the redundant `Session.__str__` (plan-mandated, harmless); `all()` annotated
bare `list`; unused `import pytest` / `tmp_path`; `_is_run`'s per-directory `rglob` (fine at this
scale); `logsafe`'s never-raise fallback paths being untested (they are belt-and-braces by
construction); `record.exc_info = None` hiding structured exception info (deliberate, and the
rendered-and-redacted `exc_text` replaces it); Task 4's no-op `init` test pinning less than its
docstring (the behaviour is pinned elsewhere, by the fact that the worker would otherwise
re-authenticate); substring-only redaction not covering URL-encoded or bytes-valued tokens (no path
produces either, and widening it invites false positives); `GET /logout` being cross-site reachable
(effect is a forced logout, which is not worth a CSRF token here); the get-then-set race in
`remember` (single-user, idempotent); no test on the OAuth `state` parameter (authlib owns it);
Task 8's fake-clock test patching the global `time` module (fragile but honest, and it caught M12's
sibling); and `_is_run`'s "a run whose media was removed is never swept" — superseded by Finding 4,
which should be fixed properly rather than as a minor.

## Declined to judge

- Live Earth Engine behaviour, the real Google consent screen, a real browser upload, and
  "renders are billed to the user's own project" — explicitly out of scope; post-deploy checks.
- The entire `deploy/` stack as *executable* artefacts. The Docker daemon is not running on this
  machine, so `docker build`, `docker compose config` and `caddy validate` cannot be run by anyone.
  Reviewed by reading only; Findings 5 and 15 are static observations about them.
- Whether Gradio 6.20's SSR mode changes `_LoginRedirect`'s coverage of the page load. Not
  determinable without a live server; `tests/test_webapp_app.py:162` covers the non-SSR path and
  the Task 9 implementer drove the real wiring.
- The two spec questions left deliberately open (an EE project id not persisting across restarts;
  runs not shareable between users).
- The two institutional prerequisites (browser-trusted TLS for a VPN-only host; an Internal or
  verified-External OAuth consent screen).
- Ruling CC's default `GEE_FORWARDED_ALLOW_IPS` breadth — parked, configurable, documented.
- `gee_animation/gui.py`'s pre-existing single-operator behaviour beyond the server seam;
  `tests/test_gui.py` is unmodified (`git diff --stat c74da2e..50e633c -- tests/test_gui.py` is
  empty) and 82/82 green.

---

## Assessment

**Ready to merge? With fixes.**

Finding 1 should not reach a user: a visible button in the shipped server UI calls `ee.Initialize`
in the shared web process, which is the one thing the spec's Constraint 1 says must never happen,
and on a host with a service-account key present it renders under HNEE's identity for anybody who
signs in. It is also the cheapest fix on the list — `visible=context is None`.

Findings 2, 4, 5 and 7 are each a case of a real person getting something broken, silently wrong or
slowly fatal: a within-caps render killed at 90 minutes against a README that promises four hours;
a retention sweep that cannot see the directories failures actually leave; a second uncleaned copy
of every render on the container's writable layer; and a chart that is always empty. None is hard
to fix, and all four are exactly the kind of thing per-task review cannot see.

Findings 3, 6 and 8 are about the branch telling the truth: cancel is tested but unreachable,
`project.validate` is written, tested and never called, and a regression in the queue's timeout
hangs CI rather than failing it.

The security core is sound and I would deploy it. The credential constraint holds end to end and
per-user isolation holds — one of them, the run dropdown, only because Gradio validates choices
server-side, which I verified rather than assumed and which deserves a comment so the next person
does not remove the guard that is holding it up.

---

### Test summary and working tree

- Baseline reproduced: **839 passed, 1 skipped, 2 deselected, 1 warning** in 26.96 s
  (`conda run -n GEE_animation python -m pytest -m "not integration" -q`). The single warning is the
  pre-existing upstream `StarletteDeprecationWarning` from `fastapi/testclient.py`.
- `tests/test_gui.py` unmodified across the branch and **82 passed** on its own.
- 22 mutations applied and reverted: 20 caught, 2 survived (the two `caps` boundaries the ledger
  flagged), 1 further mutation (removing the queue's `wait_for`) **hangs** the suite on
  `tests/test_webapp_jobs.py:80`.
- No `pip install` was run. No commit was made. All mutations were reverted; all probe scripts live
  in the session scratchpad, outside the repository.
- `git status --porcelain` is **empty**.
