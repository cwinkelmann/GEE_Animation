# Task 9 report

## Implemented
- `gee_animation/webapp/app.py`: AppConfig, create_app, ServerContext, main(); `spawn.py` (skips non-JSON stdout lines); `gee-animation-server` console script.
- Deviations from the brief (each found by driving the real wiring):
  1. Gradio answers an anonymous page load with **401, not a redirect** (brief's comment was wrong). Added pure-ASGI `_LoginRedirect` (redirects "/" and HTML GETs; API calls keep 401).
  2. Gradio runs `auth_dependency` / `list_runs` on its own **sub-app**; `request.app.state.sessions` does not exist there (AttributeError). App uses a closure `user_of(request)` over `app.state.sessions` instead of `oauth.current_user` (oauth.py untouched; it remains correct for the outer app).
  3. Lifespan context manager instead of deprecated `on_event` (avoids new warnings).
  4. The worker reads `job["project"]` but the brief's credentials dict had none. `submit` takes project from params (GUI textbox) or session, rejects empty, calls `project.remember`, and adds `project` to the payload. NOT validated against EE in the web process (no network in submit); a wrong project fails in the worker with EE's message.
  5. AOI: `submit` copies the uploaded file into `<run_dir>/aoi/` and passes that path (swept with the run, survives Gradio cache cleanup).
  6. Empty `cookie_secret` raises ValueError. `app.state.context` exposes ServerContext for tests.

## Tests: 12 in tests/test_webapp_app.py (7 from brief + 5 added); full suite 819 passed, 1 skipped, 2 deselected, 1 warning (the pre-existing TestClient one). test_gui untouched.
TDD: tests written together with code via script, so no clean module-missing RED run; real REDs were: anonymous `/` returned 401 (brief test 1), `State has no attribute 'sessions'` (signed-in page test), `DID NOT RAISE ProjectError` (empty project). GREEN: `pytest tests/test_webapp_app.py -q` -> 12 passed.

## What breaks each test
- anonymous redirect: removing _LoginRedirect (401). signed-in page: using oauth.current_user in auth_dependency (AttributeError); also checks /config 401 anon.
- upload limit / state / sweep / redactor-delta: removing the attribute, state objects, storage.sweep call, logsafe.install in startup.
- uvicorn logger test: emptying the uvicorn logger loop (mutation verified: fails). Scrubs a live token through a handler on uvicorn.error.
- submit test: replacing shutil.copy2 (verified fails; worker reads the file after upload deleted), dropping project/credentials, status key (asserts result["status"] survives job_status).
- refuse test: removing session/project/caps checks.
- stray-stdout test drives real WorkerProcess.run; deleting the `except ValueError: continue` fails it.
- cookie secret test: removing the guard.

## Files
gee_animation/webapp/app.py, spawn.py, pyproject.toml, tests/test_webapp_app.py.

## Concerns (live server only)
- `gr.Request.cookies` delegating to starlette in server mode is assumed (submit/list_runs tested with SimpleNamespace requests, not a real gr.Request through a running event).
- Gradio uploaded-file serving and `max_file_size` not exercised with a real upload.
- Project defaults to GUI textbox default (EE_PROJECT / hnee-331218) for users who don't change it; they would get a permission error from EE. Consider blanking the default in server mode (gui.py change, out of scope).
- `https_only=True` session cookie means /login only works over HTTPS (proxy must set X-Forwarded-Proto / uvicorn --proxy-headers; main() does not set forwarded_allow_ips).
- ServerContext.submit blocks a Gradio threadpool thread on run_coroutine_threadsafe(...).result() (brief).

## Fix round (coordinator follow-up)
1. **Proxy headers.** `main()` passes `proxy_headers=True` and `forwarded_allow_ips` from `GEE_FORWARDED_ALLOW_IPS`, default `127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` (loopback + RFC1918, where a compose network allocates the Caddy container). Chosen over a wildcard so a client outside private ranges cannot spoof X-Forwarded-*; uvicorn 0.51 accepts CIDRs (checked). Deployers may tighten to the proxy IP. Not unit-tested (uvicorn.run); the CIDR matching was checked by hand.
2. **Project default.** gui.py, server mode only: `project_default = ""`, label "Your own Google Cloud project id (registered for Earth Engine)", placeholder; the same variable feeds PRESET_PARAMS, because applying a preset would otherwise have refilled hnee-331218 (found while editing). Single-user keeps the default. `submit`'s empty-project error now reads "Enter your own Google Cloud project id, the one you registered for Earth Engine, in the project field".
3. **Found and fixed: OAuth refresh-token bug.** Passing `access_type`/`prompt` via `client_kwargs` does NOT reach the authorize URL (verified: only `prompt=consent` survived, `access_type=offline` was dropped), so Google would have issued no refresh token. Moved to `authorize_params` via new `build_oauth_client(cfg, server_metadata=None)`.
Tests (16 total in file, +4): 
- refresh-token test builds the real Authlib client and asserts `access_type=offline`, `prompt=consent`, EE scope in the URL; breaks if params go back into client_kwargs (mutation verified: fails).
- server-mode project has no default / single-user keeps hnee-331218: breaks if the gui default is restored (mutation verified).
- preset application leaves project empty: breaks if PRESET_PARAMS uses the env default again (mutation verified).
- empty project error names the fix: breaks if message reverts to the old text.
Full suite: 823 passed, 1 skipped, 2 deselected, 1 warning (same pre-existing one). test_gui unmodified and green.

## Deployment smoke-test checklist
- Real gr.Request in submit/list_runs: open https://carrot.aisl.c4ds.hnee.de in a private window -> expect redirect to Google sign-in -> consent screen (must show offline access/consent) -> land on the GUI. Run browser dropdown ("previous runs"): click refresh; expect only your own runs (empty first time). Enter your own project id, upload a small AOI GeoJSON, 2-month range, 720p, click "Generate animation": expect "queued/rendering" progress text, then video. Failure signs: error mentioning 'sessions' or cookies = gr.Request delegation problem.
- Empty project: clear the project box, click Generate: expect the "enter your own Google Cloud project id" message.
- Preset: apply a preset; project box must remain empty.
- Upload limit (max_file_size 100 MB): upload a >100 MB file in the AOI box (e.g. `head -c 120M /dev/zero > big.zip`): expect refusal in the UI and no render; then a small AOI works.
- AOI persistence: after a successful run, check `<output>/<hash>/<run>/aoi/` holds the copy.
- Proxy: after sign-in the cookie `sid` must be Secure and the callback URL https; if login loops, check GEE_FORWARDED_ALLOW_IPS includes the Caddy container address.
- Refresh token: run a render longer than 1 hour (or revoke access in Google account and re-run): expect the "sign in again" hint, not a crash.
- Second user in another browser profile: sees no first user's runs.

## Fix round 2 (review-driven)
Suite: 834 passed, 1 skipped, 2 deselected, 1 warning (baseline 823; +11 net; test_gui untouched). Every mutation below was actually run against tests/test_webapp_app.py (apply mutation, run, restore from backup copy).

1. **Path traversal (Critical).** `_contained_aoi`: `Path.resolve()` (follows symlinks) then `is_relative_to` an allowed root: Gradio's upload folder (`gradio.utils.get_upload_folder()`, read per call so GRADIO_TEMP_DIR is honoured) or `gettempdir()/gee_preset_*` (what `presets.staged_aoi` creates). Checked BEFORE the run dir is created; error names no path. Tests: another user's secret, a symlink inside the cache pointing at it, and a `..` escape are all refused with nothing created and no job spawned; a cache upload and a staged preset still work. Mutation (use raw `Path(aoi_src)`): fails.
2. **64 KiB line limit.** `limit=STDOUT_LIMIT` (32 MiB) on create_subprocess_exec; a ValueError/LimitOverrunError in the read loop now kills the worker and emits an `error` event "result too large to read back" instead of the start-up message. Test: the fake stdout enforces whichever limit it was given (default 64 KiB), a ~150 KB done line must arrive intact; a second test overruns even the raised limit. Mutation (drop limit=): fails.
3. **Per-user run list.** Replaced the vacuous test: two sessions with seeded mp4 runs each see only their own; stale sid and cookie-less request see []. Mutation (list_runs over the whole output root): fails.
4. **Upload limit.** Replaced with a real probe: max_upload_mb=1, 1 KB upload -> 200, 2 MB -> 413. Mutation (drop max_file_size): fails.
5. **lastResort.** Verified rather than reasoned: with root's handlers emptied and logging.lastResort pointed at a stream, a child-logger record carrying a token WAS written unredacted until the filter was added (mutation removing the install fails the test). Filter added in the lifespan next to the uvicorn loggers and removed again on shutdown (process-global object; comment says so).
6. **Worker env.** `spawn.worker_env()` drops `GEE_OAUTH_*` and `GEE_COOKIE_SECRET`, keeps everything else (PATH, GEE_OUTPUT_DIR, ...). Test inspects the env kwarg the subprocess is launched with. Mutation (no env=): fails.
7. **kill().** `os.killpg(os.getpgid(pid), SIGKILL)` with ProcessLookupError guarded; start_new_session retained. Test with patched getpgid/killpg incl. the already-gone case. Mutation (back to proc.kill()): fails.
8. **Run dir names.** `_safe_part` ([A-Za-z0-9_-], 32 chars) for sensor/index plus `-<token_hex(3)>` suffix. Test submits twice in the same second with `../../etc` and `a b/c`: dirs differ, stay directly under the user dir, charset clean. Mutations (no suffix; no sanitiser): both fail.
9. **Submit timeout.** `future.result(timeout=SUBMIT_TIMEOUT_S=30)`; on timeout the future is cancelled and a RuntimeError "The server is busy right now. Please try again in a minute." is raised. Test wedges a real loop in a thread with a blocking call. Mutation (no timeout): fails.
10. **Retention + credentials.** Lifespan runs a sweeper task every SWEEP_INTERVAL_S (24 h), via asyncio.to_thread, cancelled and awaited on shutdown. Test shortens the interval, creates a stale run after startup and sees it deleted, then asserts the task is cancelled on exit. Mutation (no cancel): the shutdown HANGS (test never completes; killed by my 300 s timeout), so it is caught but ungracefully. Credentials: `WorkerProcess._payload` is set to None as soon as it has been written to stdin; test checks the token is absent from the object's vars. Mutation: fails. I did not touch jobs.py; the queue's task frame is released when the task completes.
11. **Comment.** worker.py now says the project is NOT validated up front and a wrong one fails in Initialize. No validation call added.

Concerns: the all-in-one commit (spawn, app, tests are interlinked in one test file). The stalled-shutdown behaviour of mutation 10a means a regression there would hang CI rather than fail cleanly. Worker stderr is still inherited unredacted (reviewer item 6 tail; the worker scrubs its own exceptions). Parked per brief: 10.0.0.0/8 proxy allow-list.

## Fix round 3
Suite: 839 passed, 1 skipped, 2 deselected, 1 warning (baseline 834; test_gui untouched). Mutations were run against tests/test_webapp_app.py with a 90 s subprocess timeout (results below).

- **N1 (production + test).** Lifespan shutdown now cancels the sweeper and then waits with `asyncio.wait({task}, timeout=SHUTDOWN_TIMEOUT_S=5)`, logging an error if it is still pending. Note: I first used `asyncio.wait_for` as briefed, and it still hangs: on timeout wait_for cancels and then waits for the cancellation to finish, which a task that swallows `CancelledError` never does. `asyncio.wait` does not cancel, so shutdown is truly bounded. Tests: the periodic test now asserts `sweeper.cancelled()` and that no "did not stop" error was logged; a new test gives shutdown a sweeper that swallows cancellation and asserts shutdown returns within 5 s and logs "did not stop". Mutations: (a) remove `.cancel()` -> periodic test FAILS by assertion (not a hang; it was slow, 88 s total, from the leftover 0.05 s-interval sweeper during teardown, but finishes); (b) unbounded await (no wait/timeout) -> the stubborn-sweeper test fails.
- **N2.** `_sweep_once()` catches Exception, logs via `log.exception`, and is used by both the startup sweep (a bad filesystem no longer stops the server starting) and the loop. Test: `storage.sweep` patched to raise OSError; asserts >= 4 calls, the task still alive, the log line present. Mutation (catch only ZeroDivisionError): fails.
- **N3.** The lastResort install moved out of `_on_start` into the lifespan, paired with its removal in an outer try/finally around everything. Test: `logsafe.install` patched to raise during startup; `logging.lastResort.filters` must equal its pre-test value. Mutation (removal replaced by pass): fails (also fails the existing lastResort test).
- **N4.** The traversal test is now parametrized (direct / symlink-in-cache / dotdot); the dotdot case creates the file it names and asserts it exists, so only the containment check can refuse it. Mutation (use the lexical path instead of `.resolve()`): the symlink and dotdot cases fail.
Concerns: none new. Parked per brief: shared Gradio upload cache (content-addressed names).
