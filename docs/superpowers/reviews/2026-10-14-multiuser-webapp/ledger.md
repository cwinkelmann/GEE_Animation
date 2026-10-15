# SDD ledger — plan: docs/superpowers/plans/2026-10-14-multiuser-webapp.md

Spec: docs/superpowers/specs/2026-10-14-multiuser-webapp-design.md (reachable, binding)
Worktree: /Users/christian/work/hnee/GEE_animation/.claude/worktrees/multiuser-webapp
Branch: worktree-multiuser-webapp, based on main @ c74da2e
Baseline: 730 passed, 1 skipped, 2 deselected (clean)
Docs commit: a00bc41

## Pre-flight scan

### Pairs sharing a file or an interface

| Pair | Produces → consumes | Finding |
|---|---|---|
| T1 / T5 / T9 | all edit `pyproject.toml` | Different sections (optional-deps, pytest markers, scripts). Additive, no conflict. |
| T1 → T7 | `SessionStore.new/get/drop` → `oauth.add_routes`, `oauth.current_user` | Signatures agree. |
| T2 → T8/T9 | `storage.list_runs(root, email)` → context `list_runs(request)` | Bridged by `ServerContext` in T9. Agrees after the per-request fix already applied to the plan. |
| T4 → gui | `gui.run_animation`, `gui.DEFAULT_DEPS` (read-only) | No textual conflict with T8's edit of `build_app`. |
| T5 → T9 | `JobQueue(spawn, max_concurrency)` → `create_app` | Constructor agrees. **`submit()` is never called — see Finding A.** |
| T6 → ? | `caps.check` | **Never called by any task — Finding A.** |
| T3 → ? | `logsafe.install` | **Never called by any task — Finding B.** |
| T2 → ? | `storage.user_dir` | **Never used to set a render's `out_dir` — Finding A.** |
| T9 → T10 | `gee-animation-server` console script | Agrees. |

### Per-task self-consistency

| Task | Tests vs code, files created vs later touched | Finding |
|---|---|---|
| T1 | consistent | — |
| T2 | `sweep` walks root/user/run, matching the test helper | — |
| T3 | consistent | — |
| T4 | `runner(project=..., **job["params"])` | **Duplicate kwarg if `params` carries `project` — Finding C.** |
| T5 | FakeProc contract matches `_run`'s use of `proc.run(on_event)` / `proc.kill()` | — |
| T6 | preset ordering refuses 4k under a 1440p cap | — |
| T7 | routes use the injected client; tests fake it | — |
| T8 | per-request context consistent | **One test reaches into `app.fns` Gradio internals — Finding D.** |
| T9 | `auth_dependency` returning None drives Gradio's login redirect | — |
| T10 | consistent | — |

### Rulings

Ruling A: **The queue, the caps and the per-user output directory are orphaned — nothing
submits a render through them.** Tasks 5, 6 and 2's `user_dir` are built but never wired, so
after all ten tasks the server would still render in-process, defeating the spec's central
decision (one process per job). Decided: Task 8 also routes submission through the context
(`context.submit(request, params)` / `context.job_status(job_id)`), and Task 9's
`ServerContext.submit` is the integration point that calls `caps.check`, sets
`out_dir=user_dir(...)`, and hands the session's credentials to `JobQueue.submit`. The
single-operator path (`context is None`) keeps calling `run_animation` in-process, unchanged.
Plan text amended so the extracted briefs carry it. Cost if wrong: Tasks 8 and 9 grow;
if the split is awkward the integration could instead become an 11th task — cheap to redo
because both tasks are late and nothing depends on them.

Ruling B: **`logsafe` is never installed.** Decided: Task 9's `create_app` installs the
redacting filter over the live sessions' refresh tokens, so the protection is active rather
than merely available. Cost if wrong: a few lines in the wrong module; trivially moved.

Ruling C: **`worker.main` would raise `TypeError: got multiple values for keyword
'project'`** whenever the GUI's params carry `project`, which they always do (`gui.py:1013`
passes `project=project`). Decided: the worker pops `project` from `params` and uses the
job's project, which is the authoritative one — the user's own, validated at login. A test
pins it. Cost if wrong: none; the job-level project is the only one that can be right.

Ruling D: **A Task 8 test asserted on `app.fns` Gradio internals**, which is a version-coupled
private API and would break on a Gradio upgrade. Decided: that test calls the context's
`list_runs` directly, the way the second test already does. Cost if wrong: slightly weaker
coupling to the load-handler registration; the behaviour is still covered by Task 9's
`ServerContext` test.

Ruling E: **Never `pip install -e` from this worktree.** The conda env `GEE_animation` holds a
single editable install; re-pointing it would silently change which code the main checkout and
the five other worktrees run (the "the code that renders is not the code in front of you" trap
in the `reproducing-site-videos` skill). `authlib`, `itsdangerous` and `pytest-asyncio` were
installed as plain packages instead; implementers run pytest from the worktree root, where the
local `gee_animation/` package wins. Cost if wrong: none — verified
`gee_animation.__file__` resolves inside the worktree.

## Progress

Task 1: dispatched (implementer haiku, BASE 3f8a86d, brief task-1-brief.md)
Task 1: implementer DONE (37bba59, 735 passed = baseline 730 + 5); review dispatched (sonnet)
Task 1: Ruling F: `SessionStore.set_project` raises KeyError on an unknown sid — real, but the
  caller does not exist until Task 7 (project entry) and Task 9 (`ServerContext.submit`, which
  already handles `session is None`). Decided: not an expansion of Task 1; carried into the
  Task 7 dispatch so the handling lands beside its caller. Cost if wrong: an expired session
  entering a project id surfaces a generic error instead of "sign in again" — cosmetic.
Task 1: Ruling G: the implementer attributed its commit to Claude Haiku 5.5 (the model that
  actually wrote it) rather than the Opus 5 line in my brief, because its own harness
  attribution reminder names its model. Decided: each agent attributes its own work — that is
  the accurate record. Cost if wrong: a mixed set of Co-Authored-By names in git log, which a
  rebase could normalise and nobody needs.
Task 1: review ✅ spec compliant, quality Approved, 0 critical / 0 important.
Task 1: ⚠️ resolved by controller — `git diff --stat 3f8a86d..37bba59 -- tests/test_gui.py
  gee_animation/gui.py` is empty, so the GUI regression gate is untouched; implementer's
  735 passed stands (baseline 730 + 5 new).
Task 1: minor (deferred): redundant `__str__` returning repr (plan-mandated, harmless)
Task 1: minor (deferred): `set_project` bare KeyError — see Ruling F, lands in Task 7
Task 1: minor (deferred): `all()` annotated bare `list` rather than `list[Session]`
Task 1: minor (deferred): unused `import pytest` in tests/test_webapp_session.py
Task 1: minor (deferred): sessions have no expiry or size bound
Task 1: complete (commits 3f8a86d..37bba59, review clean)
Task 2: dispatched (implementer haiku, BASE 37bba59, brief task-2-brief.md)
Task 2: implementer DONE (e1fac54, 740 passed = 735 + 5); review dispatched (sonnet)
Task 2: Ruling H: the implementer flagged that `sweep` only deletes run SUBdirectories, so a
  render must write one subdirectory per run. That is already what Ruling A specifies —
  `ServerContext.submit` sets `out_dir = user_dir(...)/<sensor>_<index>_<timestamp>`. No change;
  recorded so Task 9's reviewer can check the pairing holds. Cost if wrong: files written
  straight into a user directory would never be swept, and disk would grow unbounded.
Task 2: minor (deferred): `sweep` rmtree's any subdirectory under a user dir without checking
  it is a run — safe only while the output root holds per-user output and nothing else (it is a
  dedicated volume, /data/out, in Task 10's compose file).
Task 2: review ❌ 2 important (sweep deletes non-run dirs; `now or time.time()` treats 0.0 as unset);
  ⚠️ resolved by controller — git diff --stat 37bba59..e1fac54 of gui.py/test_gui.py is empty.
Task 2: Ruling I: of the reviewer's two offered fixes for the sweep trap, chose "sweep only
  directories containing a .mp4 or .gif" over "document the contract". Reason: it makes `sweep`
  and `gui.list_previous_runs` agree on what a run IS, rather than relying on a future task to
  honour a comment. Cost if wrong: a run whose media failed to encode is never swept and sits on
  disk until someone removes it by hand — a leak, not a loss, and the safer side of this trade.
Task 2: fix round 1/5 dispatched (resumed original implementer)
Task 2: fix round 1/5 (2 addressed, 0 open; commits e1fac54..3aca13d). Re-reviewer confirmed by
  reading that both new tests fail against the old code, so they genuinely pin the fix.
Task 2: controller ran the full suite after the fix: 742 passed, 1 skipped, 2 deselected.
Task 2: minor (deferred): `_is_run` rglobs per directory (fine at this scale); a run whose media
  was removed but which still holds files is never swept (follows from Ruling I).
Task 2: complete (commits 37bba59..3aca13d, review clean)
Task 3: dispatched (implementer haiku, BASE 3aca13d, brief task-3-brief.md)
Task 3: implementer DONE_WITH_CONCERNS (4688826, 745 passed) — and the concern is a real plan
  defect it proved with a probe.
Task 3: Ruling J: `Logger.addFilter` applies only to records logged DIRECTLY on that logger;
  records propagating from child loggers reach the parent's HANDLERS but never its filters. The
  plan had Task 9 install the filter on the root logger, so with essentially every log line in
  this server coming from a child logger (`gee_animation.*`), the redaction would have covered
  almost nothing — the exact "available but not active" failure Ruling B existed to prevent.
  Decided: `install()` attaches the filter to every handler on the logger AND to the logger
  itself, with a test pinning the child-logger path. Addressed before review, per the
  DONE_WITH_CONCERNS route. Out of scope and carried to Task 9: handlers added after install
  (uvicorn configures logging at startup), which is an ordering problem for that task.
  Cost if wrong: if handler-level attachment turns out to double-redact or to miss uvicorn's
  non-propagating loggers, Task 9's dispatch adjusts the install point — the filter itself stands.
Task 3: fix round 1/5 dispatched (resumed original implementer)
Task 3: pre-review fix for Ruling J landed (895b3e6, 746 passed, child-logger test RED-verified).
Task 3: review ✅ spec compliant (amended), quality Needs fixes — 1 important: exc_info/stack_info
  bypass redaction, so log.exception() on an auth error writes the token into the traceback.
  Reviewer also confirmed by reading that tests 1-3 genuinely depend on the filter and test 4 is
  a negative check. ⚠️ resolved by controller: git diff --stat 3aca13d..895b3e6 of
  gui.py/test_gui.py is empty.
Task 3: fix round 1/5 dispatched (resumed original implementer) — redact exc_info/stack_info via
  record.exc_text, plus fold in the minor "a filter must never raise into application code".
Task 3: fix round 1 implementer killed mid-task by a network error (ENOTFOUND), after editing
  logsafe.py + tests but before committing. Work found intact in the working tree; controller
  committed its own plan amendment separately (6e70e9f) to keep the trees from mixing, then
  resumed the same agent from its transcript rather than re-dispatching. Its own last note —
  that the stack_info test was weak because no secret appears in that stack — was carried back
  to it as a requirement, not left as an observation.
Task 3: fix round 1/5 (2 addressed, 0 open; commits 895b3e6..1b2078c). Re-reviewer verified both
  new tests fail without the fix, and that the fail-closed path emits a visible
  "[log message withheld: redaction unavailable]" rather than silence.
Task 3: Ruling K (from the re-review's out-of-scope observations, both about Task 9):
  (a) uvicorn's loggers carry their own handlers with propagate=False, so the planned root-only
  install would never see their records — Task 9 now installs on root AND on uvicorn,
  uvicorn.error, uvicorn.access. (b) The startup-install test I wrote into the plan asserted
  root had NO RedactingFilter before startup; filters on the root logger are process-global and
  survive the test that added them, so that assertion was order-sensitive and would fail
  depending on which tests ran first. Rewritten to assert a delta and to clean up after itself.
  Cost if wrong: (a) a few redundant install calls if uvicorn's config changes; (b) none.
Task 3: minor (deferred): the never-raise paths (secrets callable raises, mismatched format
  args) were verified with a scratch script but have no committed tests.
Task 3: minor (deferred): setting record.exc_info=None hides structured exception info from a
  later structured handler (Sentry-style) — the prescribed trade-off.
Task 3: complete (commits 3aca13d..1b2078c, review clean)
Task 4: dispatched (implementer sonnet, BASE 1b2078c, brief task-4-brief.md)
Task 4: implementer DONE_WITH_CONCERNS (6e0235a, 754 passed = 748 + 6); review dispatched (sonnet)
Task 4: Ruling L: the brief's `worker.main` scrubbed the refresh token out of the error message
  BEFORE classifying whether it was an expired/revoked grant, so a token that is a substring of
  the words being classified on corrupts the classification (the brief's own test token "r" is a
  substring of both "expired" and "revoked"). The implementer reordered it to classify first and
  scrub only otherwise. Accepted — the ordering in my brief was wrong, not the implementation.
  Cost if wrong: an auth failure is reported as a generic error instead of "sign in again".
Task 4: Ruling M: the brief's "error event never echoes the token" test raised an exception whose
  text never contained the token, so it would have passed with no scrubbing at all. Implementer
  made the raised text carry the token. Accepted. This is the THIRD vacuous test found in this
  plan (after Task 2's unexercised clock and Task 3's exception-as-argument test) — a pattern in
  my plan authoring worth reporting to the user at the end, not just fixing case by case.
Task 4: deferred minor to confirm in Task 5's review: a job missing the `refresh_token` key
  raises before the try block and emits no event at all; the queue's "process exited without a
  done event" path is what contains it.
Task 4: review ✅ spec compliant, quality Approved, 0 critical / 0 important. Reviewer confirmed
  both of the implementer's deviations are correct and that each test can now fail without the
  code it covers (it traced the revoked-token test: with scrub-first the marker becomes
  "invalid_g***ant" and classification fails).
Task 4: Ruling N (from the review's ⚠️): a stray stdout line from the render path would corrupt
  the worker's JSON-lines protocol. Controller checked: `grep "print("` over render.py,
  collection.py, compositing.py, sharpen_local.py is zero, and the only stdout print in the
  package (cli.py:87) is not on the worker's path — it calls gui.run_animation, not cli.run.
  Logging goes to stderr. So the risk is third-party only. Decided: Task 9's spawn.py skips
  lines that do not parse as JSON rather than dying on someone else's chatter, with a test.
  Cost if wrong: a malformed event from our own worker would be silently ignored instead of
  crashing loudly — contained, because the queue already treats "exited with no done event" as
  an error.
Task 4: minor (deferred): the no-op `init` test pins less than its docstring claims — a lambda
  returning None satisfies it; nothing proves run_animation actually calls deps.init
  (plan-mandated test).
Task 4: minor (deferred): scrub covers only the literal token (not URL-encoded or escaped
  forms); unused tmp_path/pytest import; progress event and the `***` mask never asserted.
Task 4: complete (commits 1b2078c..6e0235a, review clean)
Task 5: dispatched (implementer sonnet, BASE 6e0235a, brief task-5-brief.md)
Task 5: implementer DONE_WITH_CONCERNS (277a665, 760 passed = 754 + 6). No pyproject change was
  needed — the installed pytest-asyncio registers the asyncio marker itself.
Task 5: Ruling O: all three of the implementer's self-flagged coverage gaps are on properties the
  queue exists to guarantee, so they are addressed BEFORE review rather than left for a reviewer:
  (a) the "process exited with no event -> error" branch, which is the only thing stopping a
  malformed job (possible per Task 4) from sitting in `running` forever; (b)
  test_a_job_survives_its_submitter_disconnecting, which would pass for any queue that finishes
  its jobs — my test, and the FOURTH in this plan that could not fail; rewritten to pin that a
  finished job's result stays retrievable after the queue has moved on; (c) cancelling a job
  that is still queued, which also pins the `if state == cancelled: return` guard in _run.
  Cost if wrong: time spent on tests a reviewer might have waived; the alternative is shipping a
  queue whose three failure modes are all unexercised.
Task 5: pre-review coverage round landed (4c1cf32) + guard comment (18667a6); 762 passed.
Task 5: review ❌ quality Needs fixes — 3 important: (1) the timeout test does not prove the kill
  (deleting proc.kill() leaves it green, and a leaked worker keeps running under the user's EE
  credentials); (2) an exception from spawn/proc.run/on_event strands the job in `running`
  forever, since only TimeoutError and CancelledError are caught and wait() swallows the rest;
  (3) the guard comment is wrong.
Task 5: Ruling P — REVERSES Ruling (my previous instruction on the cancelled guard). I told the
  implementer the guard catches a job cancelled between acquiring the semaphore and the first
  await. The reviewer instrumented a copy of jobs.py on Python 3.11.15 and could not make the
  guard fire under cancel-queued, cancel-after-release, or cancel-before-first-step. My reasoning
  was wrong in a basic way: in a single-threaded event loop a synchronous stretch is ATOMIC with
  respect to other tasks, so while _run runs from acquire to proc.run, no cancel() can interleave;
  cancellation lands inside Semaphore.acquire's `await fut`, before the guard. Decided: keep the
  guard but replace the comment with an honest one (not expected to fire; kept so a future await
  before that point cannot let a cancelled job spawn a worker). Cost if wrong: two lines of
  defensive code that never execute — against a comment that would have misled every future
  reader into trusting an interleaving that does not exist.
Task 5: fix round 1/5 dispatched (resumed original implementer) — kill assertion, except Exception
  with a generic message (never the exception text, which can carry credential material), honest
  comment, and a `if state == running` guard on the timeout branch.
Task 5: fix round 1/5 (4 addressed, 0 open; commits 18667a6..f5d6d55; 764 passed, 10 queue tests).
  Re-reviewer verified each mutation claim by reading, traced every write to job.message to
  confirm no caught exception text can reach the user, and confirmed the except body is
  synchronous so it cannot block the semaphore release.
Task 5: Ruling Q (from the re-review's out-of-scope observation): the stdout-chatter test I wrote
  into Task 9 re-implements the read loop inline instead of driving WorkerProcess.run, so it
  would pass whatever spawn.py does — the FIFTH test in this plan that could not fail. Rewritten
  to monkeypatch asyncio.create_subprocess_exec and drive the real run(). Cost if wrong: the test
  couples to spawn.py's internals via monkeypatch; the alternative was a test that proves nothing.
Task 5: complete (commits 6e0235a..f5d6d55, review clean)
Task 6: dispatched (implementer haiku, BASE f5d6d55, brief task-6-brief.md)
Task 6: implementer DONE_WITH_CONCERNS (b283b9a, 770 passed = 764 + 6) — both concerns are
  defects in my brief.
Task 6: Ruling R: `caps.check` failed OPEN twice. (a) `_PRESET_ORDER` lists only the four sizes,
  so any other string ("8k", a typo, a crafted value) skipped the check entirely — and the whole
  reason this gate lives below the UI is that a request need not come from the dropdown.
  (b) A reversed date range yields a negative span, which can never exceed the cap, so a
  nonsensical run passed; RunConfig.validate() would catch it later, but the user would see the
  error from the wrong layer. Decided: fail closed on both — accept only gui.PRESET_CHOICES'
  five values (exhaustive for this app) and require end strictly after start, each with a test
  asserting the message names the offending value. Addressed before review. Cost if wrong: a
  preset added to the GUI later must also be added here or it is refused — a loud failure, which
  is the right direction for a limit.
Task 6: fix for Ruling R landed (382abe0, 774 passed, 10 caps tests, RED was 4x DID NOT RAISE).
Task 6: review ✅ spec compliant, quality Approved, 0 critical / 0 important. Reviewer enumerated
  every input shape and confirmed only a non-dict `params` escapes as something other than
  CapExceeded, and verified the 12-year boundary is exact (4383 days = 12.0, allowed; one more
  day refused).
Task 6: ⚠️ resolved by controller — ran gui.PRESET_CHOICES against caps._ALLOWED_PRESETS: no GUI
  choice is refused and caps allows nothing the GUI never offers. The two lists agree today;
  nothing pins them together, which is the deferred minor below.
Task 6: minor (deferred, FLAG TO FINAL REVIEW): two boundary mutations pass the whole suite —
  `<` to `<=` on the preset cap (would refuse 1440p itself) and `>` to `>=` on the year cap
  (would refuse an exact 12-year range). The behaviour is correct today (reviewer verified by
  reading); only the regression protection is missing. Deferred per the process rule that minors
  do not enter the fix loop, not because it is unimportant.
Task 6: minor (deferred): ".1f" rounding gives a self-contradicting message just over the cap
  ("That range is 12.0 years; server mode allows 12 years"); missing date key gives a cryptic
  message; no test pins _ALLOWED_PRESETS to gui.PRESET_CHOICES.
Task 6: complete (commits f5d6d55..382abe0, review clean)
Task 7: dispatched (implementer sonnet, BASE 382abe0, brief task-7-brief.md)
Task 7: implementer DONE_WITH_CONCERNS (e3579db, 784 passed = 774 + 10). It added
  `project.remember(store, sid, project_id)` rather than changing session.py's contract, which
  resolves Ruling F — Task 9 must call `remember`, never `set_project` directly.
Task 7: Ruling S — the most consequential finding of the task, and it came from the implementer:
  Authlib's `authorize_redirect` stores the OAuth CSRF state in a Starlette session, so /login
  raises at runtime without SessionMiddleware, which my plan never added; and the client must
  request access_type=offline + prompt=consent or Google returns NO refresh token, in which case
  the callback's token["refresh_token"] raises KeyError and the whole design fails — renders
  outlive the one-hour access token. The plan already had the client kwargs but nothing said
  they were load-bearing. Decided: Task 9 adds SessionMiddleware with a new AppConfig
  `cookie_secret` (from GEE_COOKIE_SECRET, stable across restarts), the client kwargs carry a
  comment saying they are required, and Task 10's .env.example + compose gain the secret. The
  implementer separately handles a token that arrives without a refresh_token as a clear
  "sign in again" rather than a KeyError. Cost if wrong: if the deployment's consent screen
  returns refresh tokens without prompt=consent, the only cost is an extra consent click.
Task 7: pre-review fixes dispatched — route tests incl. the three cookie flags (HttpOnly, Secure,
  SameSite=Lax), which the brief never tested though the spec's threat table requires them; and
  scrubbing the credential's refresh token out of validate()'s fallback error text.
Task 7: pre-review fixes landed (2ee3148, 790 passed). The new route tests immediately found a
  bug in the BRIEF's own code: `Request` was imported inside add_routes while the module has
  `from __future__ import annotations`, so FastAPI could not resolve the annotation and treated
  `request` as a query parameter — every OAuth route returned 422 and the entire sign-in flow
  was non-functional. Invisible until something actually drove the routes, which the brief never
  did. Strongest argument in this plan for testing the thing rather than around it.
Task 7: review ❌ quality Needs fixes — 2 important: (1) redaction covers refresh_token but not
  the access token, and `raise ... from exc` carries the unredacted original as __cause__ into
  any traceback (Task 3's redactor only knows live SESSION refresh tokens, so it cannot catch an
  access token riding along); (2) token["userinfo"]["email"] raises a bare KeyError when Google
  omits userinfo, a 500 instead of an actionable message.
Task 7: Ruling T: SCOPES stays exactly the two Earth Engine scopes — openid/email are requested
  by Task 9's client scope string instead, because SCOPES is also what the WORKER uses when it
  rebuilds credentials, where identity scopes are pointless. The callback handles missing
  userinfo as a 400 rather than widening SCOPES. Cost if wrong: if some deployment needs openid
  in SCOPES itself, one line moves.
Task 7: Ruling U: the fallback branch raises `from None` (its message embeds EE text), while the
  two specific branches keep `from exc` (they never embed exception text, so chaining is free
  debugging value). Asymmetric on purpose; the implementer documents why so it is not "tidied".
Task 7: minor (deferred): OAuthError from authorize_access_token surfaces as a 500; GET /logout
  is cross-site reachable (effect: forced logout only); get-then-set race in remember; old
  sessions linger when the same user signs in again; no test covers the OAuth state parameter
  (Authlib owns it, and it needs the SessionMiddleware that Task 9 adds).
Task 7: fix round 1/5 dispatched (resumed original implementer)
Task 7: fix round 1/5 (2 addressed, 0 open; commits 2ee3148..650a29f; 794 passed). Re-reviewer
  verified the no-__cause__ test fails on two of three assertions if reverted, and confirmed
  SCOPES is still exactly the two Earth Engine scopes.
Task 7: minor (deferred): redaction is substring-based, so a URL-encoded or otherwise transformed
  echo of a token is not covered; a bytes-valued token attribute is skipped.
Task 7: complete (commits 382abe0..650a29f, review clean)
Task 8: dispatched (implementer sonnet, BASE 650a29f, brief task-8-brief.md)
Task 8: implementer DONE_WITH_CONCERNS (dc928ba, 806 passed = 794 + 12; test_gui.py unmodified,
  82 green); review dispatched (sonnet).
Task 8: Ruling V: the brief's `request: gr.Request` recipe does not work in gui.py — SECOND
  appearance of the same trap that made every OAuth route 422 in Task 7. `from __future__ import
  annotations` plus a function-local `gr` import leaves the annotation an unresolvable string,
  so Gradio would not inject the request and would demand an input component for it. Accepted the
  implementer's fix: explicit `__annotations__["request"] = gr.Request` on _fill_runs and _go,
  pinned by a test. Cost if wrong: if a future Gradio resolves string annotations itself, the
  assignment is redundant but harmless. NOTE FOR FINAL REVIEW: this codebase-wide pattern
  (postponed annotations + function-local imports + a framework that introspects annotations)
  has now bitten twice; worth a single documented convention rather than a third discovery.
Task 8: the implementer wrote 12 tests where the brief specified 4, because the brief's 4 call
  the fake context directly and prove only that build_app accepts `context=` — they would pass
  with gui.py unchanged. The 8 it added drive the real _fill_runs and _go handlers. Accepted.
Task 8: Ruling W: the server branch polls with time.sleep(2), holding a Gradio worker thread for
  the whole render. Making _go async is NOT the fix — the same function serves the in-process
  path where run_animation blocks, and awaiting there would block the event loop for everyone.
  Decided: accept at this deployment's scale (<=20 users, 3 slots, Gradio's default 40 worker
  threads) and document it as a known limit. Cost if wrong: with far more users the UI stops
  responding while renders run; the mitigation is a one-line max_threads bump, or splitting the
  server branch into its own async handler.
Task 8: carried to Task 9's dispatch: `submit` must handle the uploaded AOI path (the worker is a
  separate process reading that file) and the done event must carry `status`, which _go's server
  branch returns.
Task 8: review ✅ spec compliant, quality Approved-with-conditions. The reviewer walked the
  params=dict(...) refactor keyword by keyword (all 46 identical, no mutation before the
  no-context call, only a side-effect-free ordering change) and confirmed the single-operator
  path is behaviourally identical.
Task 8: Ruling X: Gradio's default concurrency limit is 1 PER EVENT FUNCTION, and go.click set
  none — so user B's click would wait in Gradio's own queue until user A's entire render
  finished, and the three render slots, the caps and the worker pool would all be stepped around
  by a framework default. Same failure shape as preflight Finding A. Decided: set an explicit,
  generous limit on go.click in server mode only (NOT 3 — every waiting user holds a polling
  invocation, including those whose job is still queued, so the Gradio-level limit must sit well
  above the render-slot count; the real limit stays JobQueue's 3). Implementer to verify the
  exact semantics against the installed Gradio 6.20 rather than trust my reading. Cost if wrong:
  too low a number reintroduces serialisation; too high is bounded by Gradio's max_threads=40.
Task 8: Ruling Y: deleted (not renamed) test_the_context_run_list_is_used_instead_of_the_global_
  output_dir — my brief's test, which builds the app then calls its own fake and never examines
  the stray directory it creates. SIXTH test in this plan that could not fail. Test 11 covers the
  ground honestly. Cost if wrong: none; the suite is better one test shorter than carrying one
  that reads like coverage.
Task 8: also folding in a wall-clock bound on the polling loop (the queue guarantees terminality
  for a RUNNING job, but a job wedged in `queued` would poll forever holding a worker thread).
Task 8: DEFERRED, NEEDS LIVE VERIFICATION (not unit-testable): whether Gradio accepts a dropdown
  selection whose choices were set per-request via gr.update(choices=...) — the dropdown is built
  empty in server mode. Must be smoke-tested against a running server before go-live.
Task 8: fix round 1/5 dispatched (resumed original implementer)
Task 8: fix round 1/5 (3 addressed, 0 open; commits dc928ba..84c9e7d). Re-reviewer read the
  installed Gradio 6.20 source and confirmed concurrency_limit=None means unlimited while
  "default" resolves to the queue's default of 1 — the claim the whole fix rests on.
Task 8: controller ran the full suite: 807 passed, 1 skipped, 2 deselected, 1 warning.
Task 8: the 1 warning is UPSTREAM, not ours: fastapi/testclient.py imports starlette's
  TestClient, which warns "Using httpx with starlette.testclient is deprecated; install httpx2".
  It appeared when Task 7 began using TestClient. Not suppressing it — a blanket filter would
  hide real warnings later. Flagged so the final review does not read it as our noise.
Task 8: minor (deferred): a job queued behind several long jobs could legitimately exceed the
  7200s poll bound, showing "gave up waiting" while the job stays queued and may still run
  unseen; the fake-clock test patches the global time module, which works but is fragile.
Task 8: complete (commits 650a29f..84c9e7d, review clean)
Task 9: dispatched (implementer sonnet, BASE 84c9e7d, brief task-9-brief.md REGENERATED to carry
  rulings J, K, N, Q and S)
Task 9: implementer DONE_WITH_CONCERNS (70a9bf9, 819 passed = 807 + 12). Four integration bugs
  found by driving the real wiring, all accepted:
  (a) Gradio returns 401 to an anonymous page load rather than redirecting — a pure-ASGI
      _LoginRedirect now handles "/" and HTML GETs;
  (b) Gradio runs auth_dependency and list_runs on its own SUB-APP, whose .state has no
      `sessions`, so oauth.current_user raised AttributeError on every page load — fixed with a
      closure over app.state.sessions, leaving oauth.py untouched;
  (c) MY PLAN BUG: the worker reads job["project"] but the brief's credentials dict never carried
      one, so every render would have died on a KeyError. submit now resolves the project and
      puts it in the payload;
  (d) the uploaded AOI is copied into <run_dir>/aoi/, so the worker's path survives Gradio's
      cache cleanup AND is swept with the run — one change solving both problems.
Task 9: Ruling Z: main() must pass proxy_headers + forwarded_allow_ips to uvicorn. Caddy
  terminates TLS and proxies over plain HTTP, so without them Starlette sees http://, the
  https_only session cookie misbehaves, and OAuth fails in a way that reads as "login just
  doesn't work". Configurable from the environment rather than a hard-coded wildcard.
Task 9: Ruling AA: in SERVER mode the project input must start EMPTY. It defaulted to
  hnee-331218 — the author's own Cloud project — so a colleague who left it alone would get an
  opaque Earth Engine permission error, defeating the design's whole premise that each person
  renders under their own project. The single-operator path keeps today's default; that tool is
  used by the one person whose project it is. Cost if wrong: one extra field to fill at first
  use, against silently billing/attributing everyone's renders to one institution project.
Task 9: fix round 1/5 dispatched (resumed original implementer)
Task 9: fix round 1 DONE (e71d6f5, 823 passed = 819 + 4). Rulings Z and AA implemented, plus a
  third bug the implementer found while doing them: **passing `access_type`/`prompt` through
  Authlib's `client_kwargs` does NOT reach the authorize URL** (verified: only `prompt=consent`
  survived; `access_type=offline` was silently dropped). Google would then have issued NO refresh
  token, and every render outliving the one-hour access token would have failed partway. Moved to
  `authorize_params` via a new `build_oauth_client(cfg, server_metadata=None)`, with a test that
  builds the real Authlib client and asserts the three params in the URL.
  This makes Ruling S half wrong in its mechanism (the kwargs are required, but not where the plan
  put them); the requirement itself stands.
Task 9: review package review-84c9e7d..3800af5.diff (3 commits, 42151 bytes); reviewer dispatched
  (sonnet) with the token-handling, `from __future__ import annotations`, vacuous-test and
  _LoginRedirect reachability checks called out explicitly.
Task 9: review came back spec OK / quality changes-requested (review-9.md). It agreed with five
  of the implementer's six deviations. Eleven findings, 1 Critical + 3 Important. What it verified
  as HOLDING: the refresh token lives only in the session store, memory and the worker's stdin
  (not argv, env, disk or any log path); no `from __future__ import annotations` 422 anywhere
  (the real /login, /oauth2/callback and /logout work, and gr.Request cookies reach submit and
  list_runs); _LoginRedirect leaks nothing (anonymous hits every API route as 401-or-redirect,
  authenticated requests never redirected); out_dir and the run list are per-user; rulings J/K
  ordering correct. 98 passed for test_webapp_app + test_gui; test_gui unmodified.
Task 9: fix round 2/5 dispatched (resumed implementer), brief task-9-fix-2.md. The two that
  matter:
  CRITICAL path traversal in submit. `aoi_path` arrives in the CLIENT's FileData payload and
  Gradio validates only the extension, so a signed-in user can name any .json/.geojson/.zip path
  on the server — including inside another user's run directory — and submit copies it into their
  own aoi/ dir where they can read it. Reviewer probed it end to end with a path in a third
  user's run dir. This is the first cross-user read in the whole plan, and it arrived through a
  change made for a GOOD reason (deviation 5 copied the upload so it survives Gradio's cache
  cleanup and is swept with the run). Fix is a containment check on the source, resolve() before
  is_relative_to, not a revert.
  IMPORTANT 64 KiB stdout line limit. asyncio's StreamReader default limit is 64 KiB and the
  worker's `done` event carries the full frames path list, so a daily-cadence multi-year render —
  the exact job this tool exists for — reports "The render failed to start" AFTER succeeding.
  Caught only because the reviewer looked at what the protocol actually carries rather than at
  the read loop in isolation.
Task 9: Ruling BB: items 5-10 (lastResort redaction, scrubbing the server's own secrets from the
  worker env, process-group kill, run-dir collision + sanitisation, a bounded timeout on the
  threadsafe hand-off, a daily sweep + dropping credentials from finished jobs) are all FIXED
  this round rather than parked. Each is a handful of lines, and four of the six are the
  difference between a promise the README will make and one the code keeps. Cost if wrong: a
  larger fix diff to re-review in one round.
Task 9: Ruling CC: PARKED — the proxy allow-list trusting all of 10.0.0.0/8. It is configurable
  via GEE_FORWARDED_ALLOW_IPS and documented, and the deployer on a VPN-only host should tighten
  it to the Caddy container address; Task 10's README says so. Cost if wrong: on a host where an
  untrusted party can reach port 7860 from an RFC1918 address, X-Forwarded-* could be spoofed.
  Not the case behind Caddy on carrot.
Task 10: brief REGENERATED after amending the plan for rulings Z and AA (commit pending above) —
  the original brief was extracted at 13:56, before rulings S, Z and AA existed, so it would have
  produced a .env.example with no GEE_COOKIE_SECRET and no GEE_FORWARDED_ALLOW_IPS: a stack that
  cannot boot and an OAuth flow that loops. Also closed the env list against inventing variables
  the app never reads — the caps (3 concurrent / 100 MB / 14 days) are AppConfig dataclass
  defaults, which I verified in the shipped code rather than trusting the plan.
Task 9: fix round 2 DONE (7529b63, one commit, 834 passed = 823 + 11). All 11 items fixed, with a
  claimed mutation check per item. Item 1: submit resolves the AOI path following symlinks and
  requires it under Gradio's upload folder or a `gee_preset_*` staging dir, checked before
  anything is created, error names no paths. Item 2: stdout limit 32 MiB, over-limit reports
  "result too large to read back" instead of the misleading start-up message. Item 5: the
  implementer VERIFIED rather than reasoned — a child-logger record carrying a token did reach
  logging.lastResort unredacted until the filter was installed; the lifespan removes it again on
  shutdown because that object is process-global. Items 3 and 4: vacuous tests replaced by a real
  two-user isolation test and a real 413 probe. jobs.py untouched.
Task 9: fix round 2 re-review dispatched (sonnet), scoped to "did each of the 11 get fixed and did
  the fix break anything", package review-3800af5..7529b63.diff. Told to ATTACK the item-1
  containment check rather than read it (symlink out of the upload folder, .. segments, a
  user-creatable gee_preset_* dir, macOS case-insensitivity, TOCTOU between check and copy) and
  to spot-check the mutation claims on items 1-4 itself rather than trust the report.
Task 9: implementer's two self-flagged residual concerns, carried into the re-review and, if they
  survive, into the final whole-branch review:
  (a) if the 24 h sweeper's shutdown cancellation regresses, the SUITE HANGS rather than fails —
      its own mutation check was caught only by a 300 s timeout. A test that hangs CI instead of
      failing is a finding in its own right; the re-review is asked whether it can fail fast.
  (b) worker STDERR is still inherited unredacted into the container log. The worker scrubs its
      own exceptions, but a library writing to stderr bypasses the redactor entirely. Not a
      refresh-token path on any route found so far (the token never reaches the worker's stderr
      writers), but it is the one logging surface the redactor does not cover.
Task 9: fix round 2 re-review: all 11 FIXED, each with a mutation check the REVIEWER ran itself
  rather than trusting the report. On the Critical it probed 12 bypass variants against the AOI
  containment check — direct path, file symlink, directory symlink, `..`, upper-case (macOS is
  case-insensitive), NUL byte, relative traversal — all refused; no TOCTOU, since the resolved
  path is the one copied and clients cannot plant symlinks in the cache; case-insensitivity only
  makes the check stricter. Credential constraint re-confirmed after the env scrubbing and payload
  clearing: token only in memory and on stdin, worker_env drops GEE_OAUTH_*/GEE_COOKIE_SECRET,
  WorkerProcess._payload cleared after hand-over, new error messages carry no token or path.
  Hostile run-dir values sanitise to `______etc`, `a_b_c`, `_hidden`. 834 passed; test_gui
  unmodified at 82; pyproject untouched; tree clean.
Task 9: fix round 3/5 dispatched (resumed implementer), brief task-9-fix-3.md — four new findings:
  N1 IMPORTANT the sweeper test HANGS instead of failing if cancellation regresses (the
  implementer's own flagged concern, now confirmed: the reviewer's 60 s alarm is what stopped it,
  and pytest-timeout is deliberately absent). N2/N3 exception safety (one OSError ends retention
  for the rest of uptime; a startup failure after the lastResort install leaks that filter
  process-wide). N4 the eighth assertion in this plan that passed for the wrong reason — the `..`
  case names a nonexistent file, so is_file() rejects it before the containment check runs.
Task 9: Ruling DD: N1 is fixed in PRODUCTION, not just in the test — bound the lifespan's shutdown
  await with asyncio.wait_for. A test-only fix would leave a wedged sweeper able to hang a real
  server's shutdown, and the production bound makes the regression an assertion failure rather
  than a hang for free. Cost if wrong: a shutdown that abandons an in-flight sweep after a few
  seconds, which is the correct trade for a server that must come back up.
Task 9: Ruling EE: PARKED — Gradio's upload cache is shared across users, so B could read A's
  cached AOI upload IF B knew its path. The names are content-addressed (knowing the path means
  already having the file), and per-user cache dirs are not available in one shared Gradio
  process. Cost if wrong: a user who obtains another's upload hash could read that AOI polygon.
  SURFACE THIS TO THE USER — it is the one cross-user read that survives the branch.
Task 9: fix round 3 DONE (6cb60d6, 839 passed = 834 + 5; I verified 839 myself).
Task 9: Ruling DD AMENDED BY THE IMPLEMENTER, and it is right. I ruled "bound the shutdown await
  with asyncio.wait_for". The implementer tried exactly that and found it STILL HANGS: on timeout
  wait_for cancels the task and then awaits the cancellation to complete, which a task that
  swallows CancelledError never does. It shipped `asyncio.wait({task}, timeout=5)` instead, which
  does not cancel and so is genuinely bounded, plus a test that hands shutdown a sweeper which
  swallows cancellation and asserts shutdown returns within 5 s and logs "did not stop". This is
  the SECOND of my rulings a subagent has empirically disproved (see Ruling P). Both times the
  disproof came from instrumenting the actual interpreter rather than reasoning about the API —
  and both times my version would have shipped something that looked correct and silently was not.
  Sent for independent verification in the round-3 re-review.
Task 9: round 3 fixes as reported: N1 production bound + two tests (one asserting
  sweeper.cancelled() and no "did not stop" log, one with a stubborn sweeper); N2 `_sweep_once`
  catches Exception and is shared by the startup sweep and the loop, so a bad filesystem no longer
  stops the server starting; N3 the lastResort install moved into the lifespan paired with its
  removal in an OUTER try/finally; N4 the traversal test is parametrized (direct / symlink-in-cache
  / dotdot) and the dotdot case now creates the file it names, so only the containment check can
  refuse it. Mutation checks claimed for all four.
Task 9: round 3 re-review dispatched (sonnet), package review-7529b63..6cb60d6.diff. Told to
  verify BOTH halves of the wait_for correction empirically on this interpreter, to check whether
  cancelling-without-awaiting leaves a pending task that emits a second warning (the suite's only
  expected warning is the pre-existing httpx StarletteDeprecationWarning), and to guard its own
  mutation runs with shell timeouts because a regression here manifests as a hang and
  pytest-timeout is deliberately absent.
Task 9: complete. Round-3 re-review verdict all-addressed; N1-N4 all FIXED with mutation checks the
  reviewer ran itself. It CONFIRMED the implementer's correction of my Ruling DD empirically on
  Python 3.11.15: `asyncio.wait_for` against a task swallowing CancelledError blocked until the
  task finished on its own (7.9 s for an 8 s task, i.e. forever for a truly wedged one), while
  `asyncio.wait({t}, timeout=0.5)` returned in 0.5 s with the task still pending. So my ruling was
  wrong and the shipped code is right. Also established: `except Exception` does not swallow
  CancelledError (BaseException in 3.11); removeFilter is by identity because Filter has no
  __eq__, and runs in the outer finally on every exit path; no "Task was destroyed but it is
  pending" or "never awaited" output anywhere in the suite under -s; the sweeper's
  asyncio.to_thread is not a hang source.
  Honest residual the reviewer named and declined to fix, which I accept: the stubborn-sweeper test
  proves only that the LIFESPAN returns, not that process-level shutdown is bounded — if the
  sweeper truly never stops, the event loop's own teardown (anyio's portal under TestClient,
  asyncio.run under uvicorn) still waits for it. Same behaviour as before the fix; not a regression.
  Suite 839 passed, 1 skipped, 2 deselected, 1 warning (pre-existing httpx one). test_gui
  unmodified. Commits: 70a9bf9, e71d6f5, 7529b63, 6cb60d6.
Task 10: dispatched (implementer sonnet, BASE 6cb60d6, brief task-10-brief.md as regenerated after
  rulings Z and AA).
Task 10: implementer DONE_WITH_CONCERNS (dd92642 Dockerfile+deploy/+.gitignore, 39905f5 README;
  839 passed, baseline unchanged). Verified by it: compose.yaml parses; every GEE_* variable the
  code reads is supplied by .env.example/compose/Dockerfile; /oauth2/callback is a real route; the
  README's cap numbers match AppConfig and caps.py (3 concurrent, 100 MB, 14 days, 12 years,
  1440p). **deploy/.env was NOT git-ignored** — it added the rule, and `git check-ignore -v
  deploy/.env` now prints it. The refresh-token warning is in verbatim.
  NOT verified, honestly reported: the Docker CLI is installed but the DAEMON IS NOT RUNNING (I
  confirmed this myself: the socket does not exist), so no `docker compose config`, no build, no
  run, and the Caddyfile was not validated. Accepted — this is the right way to report it, and it
  goes to the user as a pre-deploy gap rather than being papered over.
Task 10: Ruling FF: the Caddyfile must PARSE AS SHIPPED, overriding the plan. The plan mandated a
  literal `dns <provider> {env.DNS_API_TOKEN}` placeholder; the implementer followed it correctly,
  but that file cannot parse, and stock caddy:2 carries no DNS plugin even if it could — so
  anyone following the README gets a container that dies on the TLS block. Inverted: the
  file-based cert is now the active default (parses with stock caddy:2, and matches what an HNEE
  internal CA issues), with DNS-01 kept as a commented alternative noting it needs a Caddy built
  via xcaddy. A deployment artifact that cannot start is a defect whatever the plan said. Cost if
  wrong: someone taking the DNS-01 route has to uncomment two lines.
Task 10: Ruling GG: `ENV EE_PROJECT=hnee-331218` STAYS in the image. Verified gui.py:699 —
  `project_default = "" if context is not None else os.environ.get("EE_PROJECT", "hnee-331218")` —
  so server mode ignores it and Ruling AA still holds; the same image also serves the documented
  single-user container, where that default is correct. README gains one line saying it has no
  effect in server mode, so nobody sets it and wonders why. Cost if wrong: none found.
Task 10: pre-review fix dispatched for FF + GG (concerns 1, 2, 4). Concern 3 — the credentials
  mount moving /root/.config/earthengine -> /home/app/.config/earthengine now that the image runs
  as non-root — was caught and already fixed by the implementer; a real bug that only appears
  because Task 10 added the non-root user.
Task 10: pre-review fix DONE (5671dc2; 839 passed, unchanged). Caddyfile's active line is now
  `tls /etc/caddy/carrot.crt /etc/caddy/carrot.key` with DNS-01 commented above it; compose has
  commented cert/key mounts beside the Caddyfile mount and a note that DNS_API_TOKEN is inert by
  default; the README describes the file-based cert as the default and records that EE_PROJECT has
  no effect in server mode.
Task 10: review dispatched (sonnet), package review-6cb60d6..5671dc2.diff (3 commits). Because no
  build is possible, the review is explicitly STATIC BUT EXACTING — its job is to catch by reading
  what a build would have caught by running. Asked for: extras that exist in pyproject; whether
  the non-root `app` user owns every path written at runtime (/data/out, /data/cache, and a
  writable HOME/MPLCONFIGDIR/Gradio temp — matplotlib and Gradio both write to HOME and will die
  on a read-only one); whether every required env var is actually supplied (a missing one is
  os.environ[...] and crashes at startup); Caddy v2 syntax read by hand, X-Forwarded-* not
  disabled, and WEBSOCKETS, which Gradio's queue depends on; cert mount paths consistent between
  compose and the Caddyfile; README caps checked against AppConfig and caps.py rather than against
  the README's own claims; and a cross-check of every port, path, hostname, service name, volume
  name and variable across all five files — the exact class of bug a build would have caught.
Task 10: review verdict spec OK / quality APPROVED. Static checks that passed: all four extras
  exist in pyproject and `gee-animation-server` is defined; `USER app` is the last privileged step,
  HOME=/home/app is writable, Gradio and matplotlib fall back to /tmp, fresh named volumes inherit
  /data ownership from the image; the compose `command` starts the server while the image CMD still
  starts the single-user GUI; all four required GEE_* vars supplied and the app is on 7860 matching
  the Caddy upstream; GEE_OUTPUT_DIR and GEE_ANIMATION_CACHE_DIR match the volume mounts;
  /oauth2/callback is a real route; Caddy braces balance, read_timeout/write_timeout are valid in
  `transport http`, nothing disables the default X-Forwarded-*, reverse_proxy handles websockets
  with no extra config (Gradio's queue needs them), and the commented compose mounts use the same
  /etc/caddy/carrot.{crt,key} paths as the Caddyfile; `git check-ignore -v deploy/.env` prints
  .gitignore:14; no secret values in any committed file; README caps match AppConfig and caps.py;
  pyproject untouched. 839 passed, test_gui unmodified, tree clean.
Task 10: fix round dispatched for the two Important findings + one cheap Minor:
  (1) .gitignore covers only deploy/.env, but the README tells the operator to put carrot.crt and
      carrot.key in deploy/ — a TLS PRIVATE KEY could be committed. Adding deploy/*.key and
      deploy/*.crt. A key in git history cannot be taken back, and this repo family has already
      had one .env staged in a sibling project.
  (2) A REGRESSION TASK 10 INTRODUCED in a path that previously worked: the container now runs as
      uid 10001, so the single-user Docker path's mounted ~/.config/earthereading credentials and
      key/*.json (mode 600, host-owned) become unreadable on Linux. It worked before only because
      the image ran as root. README gains chmod / --user guidance.
  (3) spawn.worker_env scrubs GEE_OAUTH_* and GEE_COOKIE_SECRET but DNS_API_TOKEN reaches the app
      through env_file and is inherited by render workers. Nothing in a render needs a DNS zone
      token; adding it to the scrub list with a test.
Task 10: Ruling HH: PARKED findings 4 and 5. The cert mounts stay commented — leaving them active
  would be WORSE, because Docker silently creates a DIRECTORY where a missing bind-mount source
  should be, which fails in a far more confusing way than an uncommented line (the README
  prerequisites cover it). And .env.example deliberately does not boot when copied verbatim: an
  empty GEE_COOKIE_SECRET must raise at app.py:117 rather than default to anything guessable, and
  the file says so. Cost if wrong: one extra step at first deploy, which the README documents.
Task 10: Ruling II: no scoped re-review of this fix round. The three changes are a .gitignore line,
  a README paragraph and one entry in a scrub list with a test; I verify them directly, and the
  final whole-branch review sees the same diff anyway. Cost if wrong: the final review catches it
  one step later instead of now.
Task 10: fix round DONE (d4dc190). Verified MYSELF rather than by a re-review (Ruling II):
  `git check-ignore -v` prints .gitignore:15 for deploy/*.key and :16 for deploy/*.crt, and
  spawn.py's `_SCRUBBED_NAMES = ("GEE_COOKIE_SECRET", "DNS_API_TOKEN")`. Both good.
Task 10: Ruling JJ: the implementer added a README line I had NOT asked for and could not test —
  "with --user also add -e HOME=/home/app". Checking it rather than taking it found a bigger
  problem, so the initiative was right even though the line was half-wrong:
  - It correctly identified that --user needs HOME set: the Dockerfile sets no ENV HOME, so Docker
    derives it from the passwd entry, and an arbitrary host uid has none, leaving HOME=/.
  - But /home/app is mode 755 owned by uid 10001: readable and traversable by any uid (so the
    credentials mount does work), yet NOT WRITABLE, and both matplotlib and Gradio write under
    $HOME. They degrade to /tmp with a warning rather than crash.
  - The real break: GEE_OUTPUT_DIR=/data/out and /data/cache are chowned to uid 10001 in the
    image, so under --user the host uid cannot write EITHER, and every render fails. The README
    was calling --user the PREFERABLE option, i.e. recommending the broken path.
  Fixed by making the recipe coherent: --user + `-e HOME=/tmp` (mode 1777, writable by any uid) +
  the credentials mounted beneath it at /tmp/.config/earthengine + host bind mounts for /data/out
  and /data/cache, with chmod kept as the alternative and its cost stated (prefer chmod on a COPY,
  not on ~/.config/earthengine/credentials). Cost if wrong: the single-user container path needs
  adjusting at first use; the multi-user server path is unaffected, since compose supplies named
  volumes that inherit uid 10001 from the image.
Task 10: UNVERIFIED AND MUST BE SURFACED TO THE USER: the entire Docker/compose/Caddy stack has
  never been built or started. The daemon is absent on this machine (I confirmed the socket does
  not exist). Everything above is static reading plus Docker semantics reasoned from the
  Dockerfile. The revised single-user `docker run` recipe in particular is reasoned, not tested.
Task 10: complete (50e633c). The README's single-user section now carries the full --user recipe
  with my exact flags, states that /data/out and /data/cache are owned by uid 10001 so the extra
  mounts are necessary rather than decorative, keeps chmod as the alternative with its cost named
  (do it on a COPY), drops the old -e HOME=/home/app paragraph, and says plainly that the recipe
  was reasoned from the Dockerfile and not tested. Honest, which is what this needed.
ALL 10 TASKS COMPLETE. Suite 839 passed, 1 skipped, 2 deselected, 1 warning (pre-existing httpx
  one) — verified by me on the final branch state, not taken from a report. 33 commits off main at
  c74da2e. Progression across the branch: 730 (baseline) -> 735 -> 742 -> 748 -> 754 -> 764 -> 774
  -> 794 -> 807 -> 819 -> 823 -> 834 -> 839.
FINAL whole-branch review dispatched on the most capable model (opus), package
  review-c74da2e..50e633c.diff (33 commits, 225691 bytes), briefed via final-review-brief.md.
  Scoped deliberately at what per-task reviews STRUCTURALLY CANNOT do: cross-task seams (an
  invariant each side assumes the other maintains), the credential path traced end to end as ONE
  path rather than per-segment, per-user isolation as a system property (two cross-user reads
  found so far — one fixed, one parked; asked to look for a third), whether the flows a real
  person travels actually work given that all 839 tests were written by the agents that wrote the
  code, and the cases the plan never considered (simultaneous submits, two browsers, a session
  expiring mid-render, an OOM-killed worker, a disk filling, a restart with jobs queued, EE access
  revoked between sign-in and render). Told to probe rather than reason wherever probing is
  possible, since two of my rulings were overturned exactly that way.
FINAL whole-branch review verdict: SHIP-WITH-FIXES. 1 Critical, 8 Important, several Minor. Full
  report review-final.md. 22 mutations run: 20 caught, 2 survived (exactly the two the ledger had
  already flagged as Task 6's boundary gaps), 1 hangs.
  CREDENTIAL CONSTRAINT: **holds**, traced end to end as one path — callback -> `token` local
  (authlib puts only state/nonce in the cookie) -> Session(repr=False) -> opaque sid cookie ->
  queue payload (frame cleared on completion) -> stdin only, argv clean, env scrubbed,
  _payload=None after drain -> ee.Initialize -> worker scrubs the literal token and classifies
  invalid_grant FIRST -> jobs.py:92 generic message -> logsafe on root + handlers + uvicorn +
  lastResort, redacting msg/exc_info/exc_text/stack_info; tracebacks carry no locals. It also
  scanned all 44 logging calls in the INSTALLED authlib 1.9.0 and confirmed every
  `log.debug("Issue token %r")` is provider-side and unreachable from the starlette client.
  Residuals: worker stderr inherited unredacted (no token path found), and the window between
  authorize_access_token and store.new.
  PER-USER ISOLATION: **holds**. It went looking for the third vector I asked for and found the
  candidate — `_load(sel)` passes the client's dropdown value straight to `load_previous_run` with
  no containment check — then established it is closed, but BY GRADIO, not by this branch: it drove
  the real postprocess_data/preprocess_data and Dropdown.preprocess refuses a forged path against
  the per-session choices. Worth recording as a dependency we rely on rather than a check we own.
  Also confirmed gr.Request.cookies resolves for real, not only against the test fakes.
Final: Ruling KK: Ruling I is REVERSED (reviewer finding 4, and it is right). I had the sweep use
  the same "a run contains an .mp4/.gif" definition as gui.list_previous_runs. But app.py creates
  run_dir/aoi/<file> BEFORE the job runs, so every errored, timed-out, OOM-killed or
  restart-interrupted render leaves a directory retention can NEVER see. The two callers want
  different definitions: listing should show completed renders, reclaiming disk must not depend on
  success — and a failed render is the common case, not a rare one. Sweep now treats any immediate
  child directory of a user dir as a run; list_previous_runs unchanged. Ruling H still holds (never
  a user dir, never the output root).
Final: Ruling LL: the fixes go out as TWO sequential dispatches rather than one, because the set
  mixes mechanical containment work with two spec gaps that need design, and both touch gui.py and
  app.py (so they cannot run in parallel). A = Critical + findings 2,4,5,6,7,9 + four cheap minors.
  B = the two unshipped spec requirements (cancel wiring, project.validate) + job_status ownership
  + session expiry + dropping prior sessions. One scoped re-review covers both diffs. Cost if
  wrong: one extra sequential dispatch of latency.
Final: dispatch A sent (sonnet), brief final-fix-a.md. Its Critical: gui.py:1156 wires inv.click
  UNCONDITIONALLY, so in server mode the "Scene inventory (CSV)" button calls ee.Initialize IN THE
  SHARED WEB PROCESS, clobbering the process-global ee._state._state that this whole architecture
  exists to work around, and falling through to ee.Authenticate(). On a host with
  EE_SERVICE_ACCOUNT_KEY set — which our own Dockerfile documents — it would run under HNEE's
  service account for ANY signed-in user and write outside every user directory: spec blocker 3,
  the entire reason for this branch, reopened by one unguarded event handler. Fix is one line
  (`visible=context is None`) plus not registering the handler; A is also told to audit every
  other .click/.change/.submit in gui.py for the same thing, since one was missed.
Final: dispatch A DONE_WITH_CONCERNS (0865b18 gui.py + context tests, bb04459 the rest; 861 passed
  = 839 + 22, verified by me). 21 mutations run, ALL caught. Notable decisions it made and I accept:
  - The inventory button was the ONLY handler reaching Earth Engine in server mode; a TRIPWIRE TEST
    now pins the set of handler names, so a future handler trips it. Audit of the others was by
    reading, not by driving them — recorded as such.
  - `AppConfig.job_timeout_s` default 14400, also `GEE_JOB_TIMEOUT_S`; the GUI poll bound is
    timeout + 1800 s and RESTARTS when the job starts running, so a job waiting in the queue is not
    cut off early. That last detail is better than what I briefed.
  - The sweep's newest-mtime rule now includes the DIRECTORY's own mtime, because shutil.copy2
    preserves the source file's mtime — so a running job whose AOI came from an old preset would
    otherwise have its directory deleted out from under it. I had not thought of that; it is a real
    consequence of the AOI-copy fix from an earlier round meeting Ruling KK.
  - Zip bound 2 GiB declared uncompressed or 1000 members.
  - A failed Google sign-in redirects to a new /login-failed page rather than /login, because
    /login would loop a user who denied consent. Good call, not briefed.
  Could not verify (no Docker daemon): any build or run; that a fresh named volume inherits
  ownership of gradio-tmp; that a bind-mounted cache dir is writable. Dockerfile change is checked
  by reading plus a text-level test only.
Final: Ruling MM: GRADIO_TEMP_DIR moves from the Dockerfile to compose (dispatch A's own concern,
  and it is right). Setting it in the image gives it to the SINGLE-OPERATOR container too, which
  has no sweeper — so Gradio's copies went from /tmp (cleared on restart) to a PERSISTENT cache
  volume nothing reclaims. The fix made the single-user case worse than before. Server-specific
  config belongs in compose, not in an image serving both modes. Cost if wrong: none found; the
  sweep already no-ops when the variable is unset.
Final: Ruling NN: `_LoginRedirect._OPEN` must match EXACTLY, not by prefix. It is
  `("/login", "/oauth2/callback", "/logout")` tested with `startswith`, so dispatch A's new
  /login-failed page is anonymously reachable ONLY because "/login-failed".startswith("/login") is
  true. That is luck, not design: the prefix rule silently exempts every future path beginning with
  an open route's name from the auth redirect. Changing to exact membership, listing /login-failed,
  and keeping any genuine prefix case as a deliberate commented entry. Cost if wrong: a path added
  later that should be public needs one line instead of inheriting exemption by accident — which is
  the point.
Final: dispatch B sent (OPUS, not sonnet — it designs cancellation across a thread/event-loop
  boundary with an ownership check, which is the "architecture and judgment" tier). Seven items:
  the two unshipped spec requirements (cancel wiring, project.validate in the WORKER since submit
  must stay network-free), job_status ownership, session expiry (created is written but never read),
  dropping prior sessions on re-sign-in, plus rulings MM and NN.
Final: dispatch B DONE_WITH_CONCERNS (e06820b items 4+5, 9c24542 item 2, 9704852 item 6, beb55a7
  items 1+3+7; 883 passed = 861 + 22, verified by me). 15 mutations run, ALL caught.
Final: Ruling OO: ACCEPT the implementer's deviation on cancellation, which is BETTER than what the
  reviewer proposed. The reviewer suggested `cancel(request, job_id)` with an ownership check. The
  implementer dropped the job id entirely: a server-mode-only Stop button calls
  ServerContext.cancel(request) -> JobQueue.cancel_for(email), resolving the email from the session,
  so NO JOB ID CROSSES THE WIRE and ownership is the only selector — there is nothing for a client
  to claim. Given this branch already shipped one cross-user read by trusting a client-supplied path
  inside a FileData payload, removing the selector beats validating it. Crosses to the loop like
  submit does (run_coroutine_threadsafe, CANCEL_TIMEOUT_S = 10), kills the process group, releases
  the semaphore; _go's time.sleep(2) loop is structurally unchanged (Ruling W) and reports
  "Stopped." within one poll; no session -> PermissionError, unknown email -> 0, finished job ->
  no-op (fails CLOSED, which is the lesson of Ruling R). Cost: Stop is per-user, so it cancels ALL
  of that user's unfinished renders — accepted, with the label required to say so (round C item 2).
Final: Ruling PP: session window 7 days, accepted as reasoned by the implementer — it must exceed a
  4 h render plus a night and a weekend, because the run browser is session-scoped and the person
  has to come back to find their video, and it must be far shorter than a `restart: unless-stopped`
  container's uptime.
Final: Ruling QQ: the widened token-redaction window is a REGRESSION THIS ROUND INTRODUCED and gets
  fixed, not parked. Before items 4 and 5, sessions were never dropped, so every live refresh token
  was always in the redactor's list; now expiry or a re-sign-in can drop a session whose token an
  undrained worker payload still holds in this process, and the redactor silently stops listing it.
  Narrow (it closes when the payload drains and _payload=None) but new. Fix: the secrets callable
  returns the UNION of session tokens and in-flight job tokens. The implementer identified the
  right root cause itself ("track live jobs, not sessions"); the union expresses that without
  restructuring. Cost if wrong: a few more strings in a redaction list that is already O(sessions).
Final: round C dispatched (resumed B's implementer): the redaction union, Stop's label saying it
  stops ALL your unfinished renders, and finding 11's queue pruning — with an explicit instruction
  to STOP and let me park it if pruning turns out to be more than a few lines rather than
  restructuring the queue at this stage.
Final: still parked and going to the user: Ruling EE (shared content-addressed Gradio upload cache),
  worker stderr inherited unredacted (no token path found), Ruling CC (proxy allow-list trusts
  RFC1918), and dispatch B's untested items — the Docker build, the real EE "not registered" path,
  and Stop's browser behaviour while _go polls (both events are concurrency_limit=None, but each
  waiting _go holds a thread against Gradio's max_threads default of 40: room at 20 users, reasoned
  not measured).
Final: round C DONE (92c330c wording, 635938b redaction union + queue pruning; 891 passed = 883 + 8,
  verified by me). 8 mutations, all caught. Two findings of its own, both accepted:
  - The redaction window was WIDER than my description in Ruling QQ: `_run` held the payload dict
    in a FRAME LOCAL for the whole render, not just until the drain, so the token's reachable
    lifetime in the web process was the entire job. It added `payload = None` right after the worker
    takes it, which makes WorkerProcess._payload the only reference and makes my model true. The
    union releases at job end rather than exactly at drain: over-listing a token costs nothing,
    under-listing is a token a log line can print.
  - It did NOT drop job records on first report as my wording suggested, and was right not to:
    `_go` does read `result` from the call that ends its loop, but a page reload or a second poll
    would then hit an unknown-job error. Finished jobs are forgotten an hour after finishing
    (pruned lazily on submit), and a pruned id reads as "not known to the server" — the same
    message that id already had after a restart.
Final: Ruling RR: ACCEPT a suite baseline of TWO warnings rather than one. The second is
  `coroutine 'JobQueue.submit' was never awaited`, emitted at GC time by the two wedged-loop tests,
  which close their loop before `run_coroutine_threadsafe`'s scheduled callback can run — so no
  task is ever created and the raw coroutine is collected un-awaited. I TRIED to pin it with a
  per-test filterwarnings marker and it did not work, because the warning fires during GC in some
  later test, not in the test that allocates it; reverted. The implementer's choice to leave it
  visible rather than silence it was the better call.
  While chasing it I suspected a real production bug — a wedged-but-recovering loop running the
  coroutine AFTER the user was told "the server is busy", enqueueing a render they think failed and
  spending their quota twice. It is NOT a bug: app.py:314 and :337 already call `future.cancel()` on
  both timeout branches, which prevents the task from ever starting. The implementer had already
  handled it; I had not read far enough before worrying.
Final: scoped re-review dispatched (OPUS), package review-50e633c..635938b.diff (8 commits,
  139577 bytes). Scoped to "did each whole-branch finding get fixed, and did the fixes break
  anything", with the heaviest weight on: whether ANY server-mode path still reaches ee./auth.init
  in the web process (the first audit was by reading, and one handler had already been missed);
  cancellation as the largest new surface (cross-user stop, slot release, process-group death,
  double cancel, cancel racing completion — must fail closed); the retention reversal's
  running-job-with-old-preset-AOI case specifically; and the credential path re-traced now that
  sessions can be DROPPED. Told to probe rather than reason, and to mutate the tests guarding the
  Critical, cancellation ownership and the retention reversal itself rather than trust the
  implementers' mutation claims.
Final: scoped re-review of the fix rounds: all 15 whole-branch findings addressed (13 FIXED, 1 moot
  via finding 1, finding 15 PARTIAL — compose memory limit + healthcheck were never dispatched),
  22 of its own 23 mutations caught. The lone survivor is _run_guarded's post-semaphore `cancelled`
  re-check, which Ruling P already established empirically as unreachable and documents honestly.
  CREDENTIAL CONSTRAINT: **still holds** after the rounds that moved it. Probed argv (only
  `-m ...worker`), worker_env scrubbing, every refresh_token site, live_secrets()'s single consumer
  (secrets_now — nothing logs, returns or serialises it), redaction of a DROPPED session's token
  while its job still runs, session expiry at the exact boundary (the entry is popped, not hidden),
  and re-sign-in dropping the prior session case-insensitively.
  PER-USER ISOLATION: **holds**, probed with REAL SUBPROCESSES rather than fakes: cancel_for(B) left
  A's render running with A's worker AND its ffmpeg-stand-in child alive; through the real
  ServerContext, B's Stop returned 0 and an anonymous one raised PermissionError; A's own cancel
  killed the whole process group and freed the semaphore; double cancel / unknown / empty email all
  returned 0. job_status refuses B and anonymous with the same non-oracle message. Over HTTP:
  /login-failed 200 anonymously while /logout-everyone and /login-as-admin both 307 to /login — so
  Ruling NN's exact-match fix works. gradio.helpers.special_args injects the real gr.Request into
  _cancel/_go/_fill_runs: no 422, no lost request (the trap that bit this branch twice).
Final: Ruling SS: **RULING RR WAS WRONG AND IS REVERSED.** I accepted a two-warning baseline on the
  reasoning that app.py already calls future.cancel() on both timeout branches, so a
  slow-but-recovering loop could not enqueue a job after the user was told it failed. The reviewer
  probed it: `future.cancel()` RETURNS FALSE once the task has started, and its return value is
  ignored. With the loop blocked 0.8 s and SUBMIT_TIMEOUT_S 0.3 s, the user got "The server is busy
  right now" while the queue created a `running` job and spawned a worker — and the job id never
  reached the browser, so that render is invisible AND uncancellable through the UI (Stop is
  per-user, so it would in fact catch it, but nothing tells the user it exists). The second warning
  it sees is JobQueue._run, not submit: the same race from the other side.
  This is the THIRD ruling of mine a subagent has empirically disproved on this branch (P, DD, RR),
  and all three followed one pattern: I reasoned from an API's documented contract, they ran the
  interpreter. Noted for the handoff — the pattern is the finding, not the individual errors.
  Fix dispatched: handle cancel()'s return value, and on False REPORT THE JOB rather than lying —
  take the result with a grace window and return the job id, which is the correct answer because
  the render genuinely exists. Only if that also expires, error AND cancel the user's jobs so
  nothing runs invisibly.
Final: round D dispatched (resumed B's implementer, opus): the submit race, three now-wrong comments
  (app.py:168 GRADIO_TEMP_DIR "set by the image"; jobs.py:174-178 claiming the token's reachable
  lifetime is only the handover, which the reviewer disproved with gc.get_referrers and which the
  implementer itself had already found in round C; README:298 "Stop my render" vs "Stop all my
  renders"), and finding 15's remainder — a /login-failed healthcheck plus a memory limit left
  COMMENTED with sizing guidance, because a limit set too low OOM-kills a long render, which is a
  worse failure than having none.
Final: round D DONE_WITH_CONCERNS (74d2796 the race + two stale comments, 6994b91 README wording +
  compose healthcheck and documented memory limit). **897 passed, 1 skipped, 2 deselected, 1
  WARNING** — verified by me over two consecutive runs. The suite is back to ONE warning: the
  "coroutine was never awaited" one was this race seen from the other side and is gone.
Final: Ruling TT: **MY FIX FOR RULING SS WAS ALSO WRONG**, and the implementer corrected it with a
  real-clock test rather than an argument. I prescribed `if not future.cancel(): return
  future.result()`. But `cancel()` returning TRUE only means the future was still pending, and
  asyncio delivers the chained cancellation to the task AFTER its first step is scheduled — so a
  loop recovering at that moment runs queue.submit to completion anyway. Its test failed on my
  version within minutes (timeout 0.3 s, stall 0.8 s, cancel() returned True, a `running` job
  existed). So the bug was right, the diagnosis was right, and the prescription was wrong in the
  same way for the second time: I predicted the outcome from a return value instead of establishing
  it.
  The shipped design stops predicting: ServerContext.submit CHOOSES THE JOB ID BEFORE the hand-off,
  so either the id arrives within a 5 s grace and is returned normally, or no id reached the browser
  and this submit's job is undone via the new JobQueue.cancel_if_present(id) — silent for an id that
  never existed, and reaching no OTHER render the same user has running. That last point matters:
  my earlier instinct of a cancel_for(email) cleanup would have killed the user's other live render.
  The id is server-generated and never leaves the process, so it adds no client-claim surface.
  Grace window 5 s, justified: JobQueue.submit contains no awaits, so once started the work left is
  a few dict writes and a create_task.
Final: round D self-flagged concerns, all accepted: (a) a truly wedged loop now costs a Gradio
  thread up to 45 s (30 + 5 + 10) before reporting "busy" — every hop bounded, and the old 30 s was
  only shorter because it lied; (b) _cancel prefixes the honest late-confirmation message with
  "Error:" because it prefixes everything it catches — text honest, prefix not, left rather than
  adding a sentinel; (c) the compose healthcheck has never been run by a Docker daemon, so the CMD
  form and `python` on PATH in the final image are read-only checks.
  The implementer also caught one of its OWN new tests passing vacuously on the first harness run
  (it polled an empty queue before the job existed) and fixed it to wait for the job and assert it
  appeared. That is the eighth vacuous test on this branch and the first an implementer caught in
  its own work before review.
Final: targeted re-review of round D dispatched (OPUS), package review-5dec6f3..6994b91.diff. Scoped
  to ONE race in ONE function, because reasoning about it has now been wrong twice: can the bad
  outcome still be produced with adversarial real-clock timing (stalls just under / around / just
  over the grace, recovery at the exact moment cancellation is delivered, cleanup racing the job's
  transition to running, two overlapping submits from one user); does the cleanup touch ONLY this
  submit's job and fail closed on an unknown id; is the implementer's correction of MY fix actually
  right on this interpreter; can the "never awaited" warning still appear; is `submit` really
  await-free and is anything guarding that; and a mutation per new test, given the implementer
  found one of its own passing vacuously.
Final: targeted re-review of round D: **SOUND**. Could not produce the bad outcome in any ordering
  it constructed. Real-clock stall sweep (0.2/0.31/0.45/0.8/1.4/2.0 s against a 0.3 s timeout, x3)
  plus a branch census: 15 of 18 "busy" runs REALLY HAD created a job, and every one ended
  `cancelled` with no worker alive; 3 returned an id. Cleanup issued while the job was already
  `running` with a spawned worker (worker thread slowed 0.5/1.5 s) -> `cancelled`, `proc.killed`.
  The cleanup hop ITSELF timing out on a wedged loop that then recovers — a case the branch's own
  tests do not cover — still ended `cancelled`, 4/4. Two overlapping submits from one user: the
  healthy render survived. Nothing ever killed a render whose id had been returned.
  Cleanup scope proven BY MUTATION: changing it to cancel_for(email), or dropping the unknown-id
  guard, both fail the test; it fails CLOSED. cancel_if_present -> cancel has no suspension point,
  so it is atomic on the loop.
  My fix confirmed wrong on this interpreter (3.11.15): cancel() returned True (PENDING->CANCELLED)
  and the coroutine body still ran to completion at stalls of 0.31/0.8/2.0/5.0 s.
  The "never awaited" warning: 0 occurrences in 4 full runs with -W always::RuntimeWarning plus 5
  webapp-only runs. Timing-dependent before, so recorded as "not provably impossible".
  All 6 new tests passed 3x under 12 saturating CPU processes; 8 mutations applied and reverted, all
  caught.
Final: Ruling UU: round E dispatched for the re-review's four items. The one that matters is a GUARD
  ON THE NO-AWAIT PREMISE: nothing enforces that JobQueue.submit stays await-free, and the whole
  cleanup design rests on it — an `await` added BEFORE `self._jobs[job_id] = Job(...)` would let the
  cleanup miss the job and recreate the invisible render we spent two rounds killing. Pinning it
  with an AST test rather than the reviewer's suggested `_undone` id set: the test states the
  premise and tells anyone who legitimately needs an await there that they must also make the
  cleanup robust, where extra machinery would just absorb the change silently. Cost if wrong: a
  future legitimate await requires touching a test that explains why.
Final: Ruling VV: correct SUBMIT_GRACE_S's comments rather than its value. A run_coroutine_threadsafe
  future is NEVER in the RUNNING state, so cancel()==True means CANCELLED, so the grace result()
  returns in 0.0 s — measured, and 20 000 jittered trials never produced cancel()==False with a
  pending result. The retry is load-bearing; the 5 s value is inert; the real worst case is 40 s,
  not the 45 I quoted. A comment promising slack that cannot exist is worse than none, because the
  next person sizes new work against it. Cost if wrong: none; behaviour unchanged.
