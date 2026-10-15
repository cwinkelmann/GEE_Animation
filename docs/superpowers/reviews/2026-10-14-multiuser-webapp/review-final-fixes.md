# Scoped re-review of the final fix rounds (`50e633c..635938b`, 8 commits)

**Verdict: changes-requested** — every one of the 15 findings is addressed and 22 of 23 of my own
mutations are caught, but one accepted ruling is wrong and I can demonstrate it: a submit that
times out on a stalled-then-recovering event loop tells the user "the server is busy" while a real
render is created and runs for up to four hours, invisible to them.

Everything below was probed, not read, unless it says otherwise. All probes ran in the worktree
against the `GEE_animation` conda env; nothing was installed; the tree is clean.

---

## 1. The whole-branch review's findings

| # | Finding | Status | Evidence |
|---|---|---|---|
| 1 | Critical — Scene-inventory button runs EE in the shared web process | **FIXED** | Drove every server-mode handler with `ee.Initialize/Authenticate/Reset`, `ee.data.getInfo`, `auth.init` and `DEFAULT_DEPS.init` replaced by a raising sentinel. Server-mode handler set is exactly `{_apply_preset, _cancel, _fill_runs, _go, _load, _sync_index}`; `_inventory` absent; none raised the sentinel; `_go` reached only `context.submit`/`context.job_status` |
| 2 | Important — 90-minute job timeout | **FIXED** | `AppConfig.job_timeout_s=14400` (env `GEE_JOB_TIMEOUT_S`), threaded to `JobQueue`, exposed as `ServerContext.job_timeout_s`; GUI bound = timeout + 1800 and restarted when the job starts. M16/M17 caught |
| 3 | Important — cancel unreachable | **FIXED** | See §3 |
| 4 | Important — retention cannot see a failed run | **FIXED** | See §4 |
| 5 | Important — Gradio's cache unmounted and unswept | **FIXED** (static review only — no Docker daemon) | `ENV GRADIO_TEMP_DIR` gone from the Dockerfile, set in `deploy/compose.yaml`; `/data/cache/gradio-tmp` created and chowned before `USER app`; `app.sweep_temp` runs only when the variable is set, so single-user stays on `/tmp` |
| 6 | Important — `project.validate` dead code | **FIXED** | Called in `worker.main` before the render, inside the same `except` that classifies `invalid_grant` and scrubs the token; `submit` stays network-free (caps + path work + `project.remember` only). M14 caught by `tests/test_webapp_worker.py` |
| 7 | Important — time-series chart blank in server mode | **FIXED** | Drove worker → JSON → `job_status` → `_go`: chart came back with 2 rows. `_series_payload` is strict JSON (`allow_nan=False`, NaN/inf → null). M15 caught |
| 8 | Important — queue-timeout test hangs instead of failing | **FIXED** | `_wait_bounded` uses `asyncio.wait({task}, timeout=5)` (not `wait_for`) and `pytest.fail`s |
| 9 | Important — unbounded zip expansion | **FIXED** | `MAX_ZIP_UNCOMPRESSED_BYTES = 2 GiB`, `MAX_ZIP_MEMBERS = 1000`, summed from `infolist()` before `extractall`. M13 caught |
| 10 | Minor — `job_status` ownership, bare `KeyError` | **FIXED** | `UnknownJobError(LookupError)`; through the real `ServerContext`, user B and an anonymous request both get the *same* message as a non-existent id (no oracle). M5 caught |
| 11 | Minor — the queue never prunes | **FIXED** | See §6 |
| 12 | Minor — default `out_dir` at the output root | **FIXED (moot)** | The only reachable path was finding 1's inventory handler, now unregistered in server mode; `submit` always sets `out_dir`. The reviewer's suggested comment at `gui.py:492` was not added |
| 13 | Minor — window where a live token is not in the redactor's list | **FIXED** | `secrets_now` = `sessions.all()` ∪ `queue.live_secrets()`; `_inflight` filled at submit, released in `_run`'s `finally`. M9 caught |
| 14 | Minor — two surviving caps boundary mutations | **FIXED** | Two boundary tests added; cap message now in whole days, so it no longer self-contradicts |
| 15 | Minor — README / deploy polish | **PARTIAL** | README reworded ("no credential is ever written to disk"), the 4 h cap documented, retention wording corrected. The reviewer's `deploy.resources.limits.memory` and `/login` healthcheck were never dispatched and are still absent from `deploy/compose.yaml` |

---

## 2. Credential constraint — does it still hold? **Yes.**

Re-traced and probed:

- **argv**: `spawn.py:34-38` execs `[sys.executable, "-m", "gee_animation.webapp.worker"]`. No token.
- **environment**: `worker_env()` strips `GEE_OAUTH_*`, `GEE_COOKIE_SECRET`, `DNS_API_TOKEN`; the
  refresh token is never put in the environment at all.
- **disk**: the only writes are the AOI copy and render outputs. `grep -rn refresh_token
  gee_animation/` gives 11 sites, all in-memory (`session.py`, `app.py:156/306`, `jobs.py:61`,
  `oauth.py:61-67`, `worker.py:54-57`, `project.py:36`). `Session.refresh_token` is `repr=False`.
- **log records**: the redactor's list is now sessions ∪ in-flight jobs, installed on root and the
  three uvicorn loggers plus `logging.lastResort`. Probed that a token survives a dropped session:
  `live_secrets()` listed `TOK-A`/`TOK-B` while the jobs ran and `[]` once every job reached a
  terminal state.
- **`live_secrets()`**: `grep` shows exactly one consumer, `secrets_now` in `app.py:157`. Nothing
  logs, returns or serialises it. The implementer's own flag is clear.
- **user-facing errors**: `worker.main` classifies before scrubbing then `message.replace(token,
  "***")`; `project.validate` scrubs `credentials.refresh_token`/`.token` and raises `from None` on
  the branch that embeds exception text.
- **Session expiry / re-sign-in**: enforced on `get` (probed at the exact boundary: `max_age - 1`
  live, `max_age` rejected), and the entry is *popped*, so the token leaves the store rather than
  being hidden. `all()` also prunes. `new()` drops the prior session for the same email,
  case- and whitespace-insensitively; after a re-sign-in only the new token is reachable.

One documentation inaccuracy, not a constraint breach — see NEW-3.

## Per-user isolation — does it hold? **Yes.**

Probed, not reasoned:

- **Stop**: `cancel_for("b@x.de")` with A's job running cancelled **1** job (B's own queued one),
  left A `running` and left A's worker **and its child** alive. Through the real
  `ServerContext.cancel`, B's stop returned **0** and A's job stayed `running`; an anonymous
  request raised `PermissionError`. No job id crosses the wire (`stop.click(_cancel, None, status)`
  has no inputs), so there is nothing to tamper with. Unknown email → 0, empty email → 0, a second
  cancel → 0, a finished job → no-op.
- **job_status**: B and anonymous are both refused with the unknown-id message.
- **Storage**: `user_dir` is a sha256 prefix of the lowered email; `list_runs` is scoped to it;
  the sweep never leaves a user directory.
- **Open routes**: exact `frozenset` membership plus an empty, deliberate prefix tuple. Driven over
  HTTP: `/login-failed` → 200 anonymously; `/logout-everyone` and `/login-as-admin` → 307 `/login`;
  an `OAuthError` callback → 307 `/login-failed`.
- **`from __future__ import annotations` trap**: `gradio.helpers.special_args` injects the real
  `gr.Request` into `_cancel`, `_go` and `_fill_runs` (the explicit
  `_cancel.__annotations__["request"] = gr.Request` does its job); `oauth.Request` is a module-level
  import, and `/login-failed` takes no parameters — no 422 anywhere.

---

## 3. Cancellation, in detail (all probed with real subprocesses)

A real `JobQueue` with `max_concurrency=1` and a stand-in worker that forks a `sleep 300` child
(the ffmpeg stand-in), both in their own session:

- A running, B queued at position 1; `live_secrets` = both tokens.
- `cancel_for(B)` → 1; A untouched, A's worker **and** its child still alive.
- `cancel_for(A)` → 1; after 0.4 s the worker **and the child** are both gone — `killpg` on the
  group works (M23 confirms a plain `proc.kill()` is caught by a test).
- The semaphore slot freed: a newly submitted C went `running`.
- Second `cancel_for(A)` → 0. `finished` stamps set on both A and B.
- `_go` leaves within one poll and returns the "**Stopped.**" branch (M19 caught).
- `ServerContext.cancel` is bounded at `CANCEL_TIMEOUT_S = 10` across the thread/loop boundary.
- It fails **closed**: no session → `PermissionError` (M4, removing that, is caught).

Race with completion: `cancel` returns early on any terminal state, and a `done` landing after a
cancel is overwritten by the `CancelledError` branch, so the end state is `cancelled` either way.

The one surviving mutation of 23 is harmless: removing `_run_guarded`'s post-semaphore
`if job.state == "cancelled": return` changes nothing, exactly as its own comment says
("not expected to fire … kept deliberately so that if a future change adds an await ahead of this
line"). Defensive, documented, unreachable.

## 4. The retention reversal (Ruling KK)

Built a tree and swept it:

- A **running** run whose only content is an AOI `copy2`'d from a 40-day-old preset **survived**
  (its own directory mtime is current). This is the case Ruling KK was written for. M7 (ignoring the
  directory's own mtime) is caught by `test_a_run_in_flight_with_an_old_copied_aoi_is_not_swept`.
- A genuinely old media-free run (only `aoi/`) **was** swept, so a failed or cancelled render is
  reclaimable. M6 (requiring media again) is caught.
- The output root and all three user directories survived, including an aged, empty one (Ruling H).
  M8 (letting the sweep reach a user directory) is caught.

## 5. Queue pruning

`retain_finished_s=0`, job cancelled, then a second `submit` to trigger the lazy prune:
`status(pruned)` raises `UnknownJobError` with "not known to the server (it may predate a
restart)", and `position(pruned)` is 0 rather than raising. With `retain_finished_s=-1e9` a
**running** job survived an aggressive prune (`finished is None` guards it); M11, pruning
unconditionally, fails 11 tests.

`JobQueue.cancel(pruned_id)` and `JobQueue.wait(pruned_id)` still raise a bare `KeyError`, but
neither is reachable with a pruned id from production (`cancel_for` selects out of `_jobs`;
`wait` has no production caller at all — it is test-only). Noted, not filed.

---

## NEW findings

### NEW-1 — Important. A stalled-then-recovering loop enqueues a render the user was told failed
`gee_animation/webapp/app.py:312-317` (same shape, harmlessly, at `:336-340`).

`future.cancel()` on a `concurrent.futures.Future` returns **False** and does nothing once the
future is running or done, and its return value is ignored here. I drove it: with the event loop
blocked for 0.8 s and `SUBMIT_TIMEOUT_S` at 0.3 s, the user got
*"The server is busy right now. Please try again in a minute."* while the queue created a job
(`state=running`, `owner=a@b.de`), spawned a worker and listed the token in `live_secrets`. The job
id never reached the browser.

Mechanism: `run_coroutine_threadsafe`'s `ensure_future` has already created the task, so its first
step is queued **ahead** of the `_call_check_cancel`→`task.cancel()` that `future.cancel()`
schedules. The task runs, `submit` completes, the cancel is a no-op. This is also what the suite's
second warning witnesses — on my runs it is `coroutine 'JobQueue._run' was never awaited`, not
`JobQueue.submit`: `submit` *did* run and created a `_run` task that the test's loop then closed
under. (The `submit` spelling is the other side of the same race, when the cancel lands before the
chaining callback.) So both warnings are benign test teardown, but **the reasoning attached to them
in the brief is not**: a slow-but-recovering loop can and does enqueue a job after the user was
told it failed.

Failure scenario: the loop stalls for 30 s (three `lst_rf` renders plus a sweep on a box shared
with GPU jobs); the user is told the server is busy, retries, and now holds two of three slots —
one of them invisible to them — for up to four hours of their own Earth Engine quota. Mitigated but
not fixed by Stop, which cancels all of their unfinished renders.

Fix is one line: `if not future.cancel(): return future.result()` — the job id is right there —
or, if the submit must be disowned, cancel it (`cancel_for(session.email)`).

### NEW-2 — Minor. `_sweep_once`'s comment contradicts the fix it describes
`gee_animation/webapp/app.py:168` still reads "Only swept when GRADIO_TEMP_DIR points at a
dedicated directory (**the image sets it**)". Dispatch B item 6 moved it out of the image into
compose and fixed `gradio_temp_dir()`'s docstring, but not this one. A future reader looks for an
`ENV` in the Dockerfile, finds none, and concludes the sweep is dead code.

### NEW-3 — Minor. The comment on the credentials handover overstates what was achieved
`gee_animation/webapp/jobs.py:174-178` says "the token's reachable lifetime in this process is the
handover, not the whole render", and the round-C report claims `WorkerProcess._payload` becomes the
only reference. It does not: `_run(job_id, payload)` keeps `payload` bound in its own frame for the
whole of `await self._run_guarded(...)`. Probed with `gc.get_referrers` mid-render — the payload
dict is still reachable from `_run`'s coroutine. Harmless (process memory is permitted, and
`_inflight` deliberately holds the token for the whole job anyway), but the claim should be
corrected rather than relied on. `spawn.py`'s own `self._payload = None` is real and is tested
(M22 caught).

### NEW-4 — Minor. `README.md:298` names the button "**Stop my render**"
Round C renamed it to "⏹ Stop all my renders" precisely so the wording cannot mislead; the README
kept the old, narrower name, which is the sentence a user would read before losing two renders.

---

## Mis-ruled, in my view

Only the accepted two-warnings reasoning — see NEW-1. The three parked items (shared Gradio upload
cache, inherited worker stderr, RFC1918 proxy allow-list) and the acceptance of Ruling W and of a
per-user rather than per-job Stop all look correctly ruled to me at this scale.

## Tests

`891 passed, 1 skipped, 2 deselected, 2 warnings` in 32 s, network-free, matching the stated
baseline. `tests/test_gui.py` is **unmodified across the whole branch** (`git log c74da2e..635938b
-- tests/test_gui.py` is empty) and green at 82.

Mutation testing: **23 mutations of production source, 22 caught**, each applied with `perl -0pi`,
run, and reverted — including all three areas the brief singled out (the Critical: M1 handler wired,
M2 button visible; ownership: M3 `cancel_for` ignores the email, M4 cancel fails open, M5
`job_status` check removed; retention: M6 media required, M7 directory mtime ignored, M8 user
directory deletable). The single survivor is the documented-unreachable defensive re-check
(§3). No test was found to be vacuous; the series and `project.validate` tests drive the real
worker rather than re-implementing it.

## Working tree

`git status --porcelain` is clean apart from this report. Every probe, mutation and temporary file
lived in the session scratchpad and has been removed; nothing was committed.
