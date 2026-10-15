# Final whole-branch review — pointers for the reviewer

Read `/Users/christian/.claude/plugins/cache/claude-plugins-official/superpowers/6.4.1/skills/requesting-code-review/code-reviewer.md`
first and follow it. This file supplies the branch-specific context that template asks for.

## What the branch does

Turns a single-operator Gradio app (`gee_animation/gui.py`) that renders Google Earth Engine
satellite timelapses into a small multi-user web service for ~20 trusted people behind the HNEE
VPN at `carrot.aisl.c4ds.hnee.de`. Each person signs in with Google OAuth and renders under
**their own** Google Cloud / Earth Engine project with **their own** credentials — nothing is
billed or attributed to the institution's project.

The architectural constraint that shapes everything: `ee._state._state` is one **process-wide
global** holding `credentials` and `cloud_api_user_project`, with no contextvar or thread-local.
Two users cannot share a process. Hence one OS subprocess per render job, credentials handed over
on the worker's **stdin**.

- Spec: `docs/superpowers/specs/2026-10-14-multiuser-webapp-design.md`
- Plan: `docs/superpowers/plans/2026-10-14-multiuser-webapp.md` (10 tasks)
- Ledger with every ruling: `.superpowers/sdd/2026-10-14-multiuser-webapp/progress.md`

## The one hard security constraint

A Google refresh token may exist **only** in process memory and on a worker's stdin. Never in
`argv` (world-readable via /proc), never in an environment variable, never on disk, never in a log
record, never in a user-facing error or traceback. Trace every path a credential can take and say
whether that holds. `gee_animation/webapp/logsafe.py` exists to make the log case impossible
rather than merely unlikely.

## Failure modes this branch has actually produced — look for more of each

1. **The `from __future__ import annotations` trap, twice.** FastAPI and Gradio resolve parameter
   annotations at runtime. A stringified or function-locally imported `Request` makes FastAPI treat
   the parameter as a *query* parameter: every route returns 422 and the whole sign-in flow is
   silently dead. Task 7 shipped with every OAuth route returning 422; Task 8 had the same bug in
   a different disguise, fixed with an explicit `__annotations__["request"] = gr.Request`.
2. **Tests that cannot fail — seven caught so far.** Assertions on a variable instead of the
   behaviour (`app.state.max_upload_bytes` rather than a 413), tests that re-implement the loop
   they claim to cover, tests that never reach the module under test. For every test in this
   branch, name the production change that breaks it; if you cannot, that is a finding.
3. **Client-controlled data treated as server fact.** The Critical of Task 9: `aoi_path` arrives
   inside the *client's* FileData payload and Gradio validates only the extension, so a signed-in
   user could name any `.json` on the box — including inside another user's run directory — and
   `submit` copied it somewhere they could read it. Isolation held everywhere the plan modelled a
   trust boundary and broke where it did not. Look for other places client-supplied strings reach
   the filesystem, a subprocess, or Earth Engine.
4. **Correct-looking code whose payload breaks it.** `async for line in proc.stdout` is fine until
   you ask what the lines contain: asyncio's 64 KiB StreamReader limit against a `done` event
   carrying the full frame-path list made long renders report "failed to start" *after* succeeding.
   Three reviews read that loop and passed it.
5. **Failing open.** `caps.check` failed open twice (unknown presets, reversed date ranges) before
   Ruling R. Check every validation path for what it does with input it does not recognise.

## Known-and-accepted, so do not re-litigate (but say if you think one is wrong)

Every item below is in the ledger with its reasoning and its cost-if-wrong. Flag any you consider
mis-ruled; do not spend the review re-deriving them.

- **Ruling P reverses an earlier ruling of mine.** I claimed the `cancelled` guard in `jobs.py`
  catches a cancel between semaphore acquisition and the first await; a reviewer instrumented it
  on Python 3.11.15 and disproved it (in a single-threaded loop a synchronous stretch is atomic;
  cancellation lands inside `Semaphore.acquire`'s `await fut`). The guard stayed with an honest
  comment. If you think the comment still overclaims, say so.
- **Ruling W:** `time.sleep(2)` polling holds a Gradio worker thread. Accepted at this scale
  (<=20 users, 3 slots, max_threads 40); making `_go` async would block the event loop on the
  in-process single-user path.
- **Ruling CC (parked):** `GEE_FORWARDED_ALLOW_IPS` defaults to loopback + all RFC1918, so all of
  10.0.0.0/8 is trusted for `X-Forwarded-*`. Configurable and documented; the deployer tightens it.
- **Rulings H/I:** the sweep deletes only run subdirectories containing an `.mp4`/`.gif` — the same
  definition `gui.list_previous_runs` uses.
- **Ruling T:** `oauth.SCOPES` holds exactly the two Earth Engine scopes; `openid`/`email` come
  from the client scope string.
- Grep the ledger for `minor (deferred)` — roughly twenty small items I deferred to you rather
  than spend a fix round on. Two are explicitly flagged FOR THE FINAL REVIEW:
  - Task 6: two boundary mutations in `caps` pass the whole suite (behaviour is correct; only the
    regression protection is missing).
  - Task 8: whether Gradio accepts a dropdown selection whose choices were set per-request via
    `gr.update(choices=...)` — not unit-testable, needs the live app.

## Two residual concerns from the Task 9 implementer

- If the 24 h sweeper's shutdown cancellation regresses, the suite **hangs** instead of failing.
  A test that hangs CI rather than failing is a finding in its own right.
- Worker **stderr** is inherited unredacted into the container log. The worker scrubs its own
  exceptions, but a library writing to stderr bypasses `logsafe` entirely. No refresh-token path
  to it has been found; it is the one logging surface the redactor does not cover.

## Not in scope for you

- Live Earth Engine behaviour, the real Google consent screen, a real browser upload, and
  "renders are billed to the user's own project" — all post-deploy checks, listed in the
  "Deployment smoke-test checklist" at the end of `task-9-report.md`.
- Two spec questions left deliberately open: a user's EE project id does not persist across
  restarts, and runs cannot be shared between users.
- Two institutional prerequisites block launch regardless of code: a browser-trusted TLS
  certificate for a VPN-only host (DNS-01 or an HNEE internal CA), and a Google OAuth client whose
  consent screen is Internal, or External/testing with `hnee.de` verified and <=100 named users.

## Constraints on you

- Unit suite stays **network-free**; `tests/test_gui.py` must be **unmodified** and green (82).
- New dependencies only in the `server` extra of `pyproject.toml`.
- **Do NOT run `pip install` or `pip install -e` for any reason.** The conda env `GEE_animation`
  holds one editable install shared with five other worktrees of this repo; re-pointing it would
  silently change which code they all run. Run tests with
  `conda run -n GEE_animation python -m pytest -m "not integration" -q` from the worktree.
  Note: `conda run` does not forward stdin, so a `python - <<'PY'` heredoc through it silently
  runs nothing and exits 0 — write scripts to a file.
- You may edit source temporarily for mutation checks but must restore it; finish with
  `git status --porcelain` clean and say so. Never commit.
