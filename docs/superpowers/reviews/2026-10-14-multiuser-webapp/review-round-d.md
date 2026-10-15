# Review round D — the submit-timeout race, verified empirically

Branch `worktree-multiuser-webapp`, range `5dec6f3..6994b91` (2 commits).
Interpreter: `Python 3.11.15 | packaged by conda-forge | (main, Jun 11 2026, 03:29:05) [Clang 19.1.7]`
(`conda run -n GEE_animation python -VV`). Import check: `gee_animation.__file__` resolves to
**this worktree** under `python -m pytest` (cwd precedes the editable finder, which maps to the
main checkout) — so everything below tested the worktree's code.

**Verdict: sound.** I could not produce the bad outcome in any ordering I could construct.
Two narrow documentation/robustness findings, below; neither changes behaviour today.

Probe scripts and raw logs: `/private/tmp/claude-501/.../scratchpad/` (session scratchpad,
outside the repo; deleted at the end — nothing left in the tree).

---

## 1. Can the bad outcome still be produced? No.

Attacks (all real clocks, `_blocking_server`, `SUBMIT_TIMEOUT_S = 0.3`):

| Attack | Runs | Result |
|---|---|---|
| Stall sweep 0.2 / 0.31 / 0.45 / 0.8 / 1.4 / 2.0 s, grace 0.5 s, ×3 each | 18 | invariant held every time; after draining the loop I polled for a further 1 s for a late job |
| Branch census, same sweep | 18 | 15 took the **busy** branch and **every one of those really had a job created** (`busy_with_job = 15`); all 15 ended `cancelled`, none left a worker alive, 3 returned an id normally |
| Cleanup issued while the job is **already running** with a spawned worker (worker thread slowed 0.5 s / 1.5 s before the cleanup hop) | 2 | states when the cleanup was queued: `[('running', True)]` → final `['cancelled']`, `proc.killed = True` |
| Cleanup hop **itself** times out (`CANCEL_TIMEOUT_S = 0.2`, loop wedged 2.0 s) then the loop recovers | 4 | job still ends `cancelled`: the queued cleanup coroutine runs on recovery, behind the submit — this path is not covered by the branch's own tests |
| Two overlapping submits from one user (one healthy render running, second submit stalled into "busy") | 1 | the healthy render stayed `running`; only the doomed submit's job was undone |
| Recovery exactly at cancellation delivery, 20 000 trials of the raw asyncio pattern with ±300 µs jitter | 20 000 | never produced "cancel()==False and a pending result" (see §5) |

Ordering argument, confirmed by probe: both hops use `call_soon_threadsafe` on the same loop, so
the cleanup is strictly FIFO-behind the submit; in the fast case the cleanup was queued while
`_jobs` was still empty (`STATES WHEN CLEANUP WAS QUEUED: []`) and still cancelled the job that
appeared afterwards. `cancel_if_present` → `cancel` contains no suspension point, so it is atomic
with respect to the loop, and `_run_guarded` re-checks `job.state == "cancelled"` after the
semaphore, so a job cancelled while queued can never spawn a worker.

No ordering left a render alive after a failure was reported, and none killed a render whose id
had been returned.

## 2. Does the cleanup touch only this submit's job? Yes, and it is silent for an unknown id.

`jobs.py:121-129`: a membership test on the exact id, `return False` for anything not in `_jobs`,
then `self.cancel(job_id)` — one dict entry, one task. Proven by mutation, not by reading:
- M7 — rewrite the body as `cancel_for(self._jobs[job_id].email)`: `test_cancel_if_present…` fails
  (the user's other render dies). So the test really is what stops a `cancel_for`-style cleanup.
- M5 — delete the `job_id not in self._jobs` guard: the same test fails with `KeyError`, i.e. the
  silence for a never-created id is enforced, and it fails **closed** (`False`, nothing cancelled),
  unlike the `caps.check` incidents.
- Overlapping-submit probe (§1) confirms it end-to-end through `ServerContext`.
Minor: `jobs.py:62` `job_id = job_id or secrets.token_urlsafe(16)` — an empty-string id from a
future caller would be silently replaced by a different one, and the cleanup would then miss the
job. Not reachable from `app.py` (`secrets.token_urlsafe(16)` is never empty).

## 3. Is the implementer's correction of your fix right? Yes — measured, not argued.

`scratchpad/probes/p1_semantics.py` on this interpreter, loop stalled 0.8 s, timeout 0.3 s:

```
{'first': 'TIMEOUT', 'state_before_cancel': 'PENDING', 'cancel': True,
 'state_after_cancel': 'CANCELLED', 'side_effect_ran': True}
```

`cancel()` returned **True** and the coroutine's body still ran to completion once the loop
recovered — identical at stalls of 0.31 s, 0.8 s, 2.0 s and 5.0 s. So `if not future.cancel():
return future.result()` would have raised "busy" with a live job behind it: your version was
wrong in exactly the way the implementer described, and `cancel() == True` carries no information
about whether the job exists. The shipped outcome-based fix is the right shape.

## 4. The warning: no `never awaited` in any run.

Full suite ×4 (one before any probing, three with `-W always::RuntimeWarning`):
`897 passed, 1 skipped, 2 deselected, 1 warning` each, `grep -c 'never awaited'` = **0**.
`tests/test_webapp_app.py` + `tests/test_webapp_jobs.py` ×5: `74 passed, 1 warning`, 0 each.
The one remaining warning is the upstream `StarletteDeprecationWarning` (httpx) in
`fastapi/testclient.py:1`. Honest form: it did not appear in 4 full runs + 5 targeted runs
(≈9 runs, ~3600 webapp test executions); the mechanism it witnessed is gone, but it was
timing-dependent, so I would not call it impossible.

## 5. Is the 5 s grace sound? The *retry* is load-bearing; the 5 s *value* is inert.

`JobQueue.submit` has **zero** suspension points — AST walk over `jobs.py` finds no
`Await`/`AsyncWith`/`AsyncFor`, and it calls only `_prune` (sync `def`), `Job`,
`credentials.get`, `_order.append`, `secrets.token_urlsafe`, `asyncio.create_task`. So the
premise holds as written.

But the grace window cannot elapse. `run_coroutine_threadsafe`'s `concurrent.futures.Future` is
never `RUNNING` (it is driven by `_chain_future`, which never calls
`set_running_or_notify_cancel` on the success path), so `cancel()` returns `False` **only when the
future is already done**. Therefore:
- `cancel() == True` → the future is `CANCELLED` → `result(timeout=SUBMIT_GRACE_S)` raises
  `CancelledError` **immediately**. Measured in-app: `('cancel', True, 'CANCELLED')` followed by
  `('result_waited', 0.0)`; the wedged-cleanup probe timed the whole call at 0.52 s = 0.3 + 0 + 0.2.
- `cancel() == False` → `FINISHED` → `result()` returns the id instantly.
20 000 jittered real-timing trials produced `cancel() == True` 19 986 times and never a
False-with-pending-result: the "future finished in the gap" window is sub-microsecond.

So the second `future.result()` call is essential (M1 proves it — without it the id is thrown
away and the user gets "busy" while the job exists), but `SUBMIT_GRACE_S = 5.0` never buys a slow
loop any time, and the comment at `app.py:52-57` and the report's state 1 ("the id arrives within
`SUBMIT_GRACE_S`") describe a behaviour that does not occur. Consequence for concern 1 of the
report: the real worst case is `SUBMIT_TIMEOUT_S + CANCEL_TIMEOUT_S` = 40 s, not 45 s.

Nothing guards the no-await premise: no test, assertion or comment in `jobs.py` would fail if
`submit` gained an await. The sharp edge is **not** the grace value — it is that an await added
*before* `self._jobs[job_id] = Job(...)` would let the cleanup run before the job exists, which
`cancel_if_present` would answer `False` and the job would then run invisibly. A cheap guard:
either an AST test asserting `JobQueue.submit` has no suspension points, or have
`cancel_if_present` record unknown ids in a small `_undone` set that `submit` honours.

## 6. The new tests: each mutated and run.

All 8 mutations applied in-place, run, reverted (`scratchpad/mutate.py`, `mutate2.py`):

| Mutation of production source | Test | Result |
|---|---|---|
| M1 drop the retry after `cancel()` (keep `future.cancel()` only) | `…submit_that_lands_just_after_the_timeout_returns_the_id_not_busy` | failed ✓ |
| M2 drop the `cancel_if_present` cleanup | `…grace_also_expires…` | failed ✓ |
| M2 (again, alone, ×5) | `…either_gets_an_id_or_no_job_exists_when_the_loop_stalls` | 5/5 caught; 5/5 pass clean — **not vacuous** |
| M3 cancel path ignores `cancel()`'s value | `…stop_that_lands_just_after_the_timeout_says_it_is_under_way` | failed ✓ |
| M4 healthcheck URL → `/` | `…compose_healthcheck_hits_a_route_that_answers_without_a_session` | failed ✓ |
| M4b uncomment `mem_limit: 24g` | same test | failed ✓ |
| M5 `cancel_if_present` raises on an unknown id | `test_cancel_if_present…` | failed ✓ |
| M6 queue ignores the caller's chosen id | `test_cancel_if_present…` | failed ✓ |
| M7 cleanup becomes `cancel_for(email)` | `test_cancel_if_present…` | failed ✓ |

Timing sensitivity: the five new `test_webapp_app.py` tests plus the jobs test were run 3× under
12 saturating CPU processes — `6 passed` each time. The deterministic trio
(`…returns_the_id_not_busy`, `…grace_also_expires…`, `…stop_that_lands…`) depends on the
`_FinishedJustTooLate` fake, not on the clock; the real-clock test asserts an invariant on *both*
branches, so a machine that loses the race still proves something. Two notes:
- `tests/test_webapp_app.py:1162-1176` — one `deadline` is shared by both poll loops: if waiting
  for the job to appear consumed the 5 s, the cancellation loop exits instantly and the test
  **fails** rather than passing falsely. Safe direction, but a second deadline would be tidier.
- `_FinishedJustTooLate.cancel()` returning `False` while `result()` still blocks is a future
  state the stdlib never produces (§5). The *outcome* it asserts (id returned, not "busy") is
  real; the blocking is not. Worth a line in the docstring so the next reader does not infer
  that the grace window is exercised.

## New findings

- **Important** — `gee_animation/webapp/app.py:52-57` (and `:343`). `SUBMIT_GRACE_S`'s documented
  purpose cannot occur: after a successful `cancel()` the future is `CANCELLED`, so the grace
  `result()` returns in 0.0 s (measured, 20 000 trials). If a future reviewer trusts the comment
  and, say, moves work into `JobQueue.submit` believing five seconds of slack exists, the only
  thing actually protecting the invariant is the cleanup. Fix is documentation (and optionally
  dropping the constant), not behaviour.
- **Minor** — `gee_animation/webapp/jobs.py:62`. `job_id or secrets.token_urlsafe(16)`: a caller
  that passes an empty id gets a different one, and the submit-timeout cleanup would then fail to
  find its job and leave an invisible render. Unreachable today; `if job_id is None` would close it.
- **Minor** — nothing guards "`JobQueue.submit` contains no awaits" (§5).
- Docker gap unchanged: the compose healthcheck was reviewed by reading only (no daemon here).
  `/login-failed` is in `_LoginRedirect._OPEN` (`app.py:134`) and the test drives a live anonymous
  200, so the route choice is right; the `CMD` form and `python` on the image's PATH stay unverified.

## Tests

`conda run -n GEE_animation python -m pytest -m "not integration" -q` →
**897 passed, 1 skipped, 2 deselected, 1 warning** (4 independent runs, identical);
`tests/test_gui.py` unmodified in this range (`git log 5dec6f3..6994b91 -- tests/test_gui.py` is
empty) and **82 passed**. No `pip install` was run. Working tree: `git status --porcelain` empty.
