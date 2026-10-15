# Task 4 report: render worker

## Implemented
`gee_animation/webapp/worker.py` and `tests/test_webapp_worker.py`, per the brief, with two deviations (below).

## Deviations from the brief
1. **Brief bug in worker.py.** The brief scrubs the token before classifying the error. Its test token is `"r"`, and `"r"` occurs inside "expired" and "revoked", so the scrub produced `***` mangled text, the revoke marker no longer matched, and `test_a_revoked_token_becomes_an_actionable_error_event` failed. Fix: classify first, scrub only otherwise.
2. **Brief's `test_the_error_event_never_echoes_the_token` could not fail.** The raised text ("bad credential r") never contained the token "r-SECRET". The test now raises "bad credential r-SECRET", so it fails without the scrub. It also asserts the rest of the message survives.

## TDD evidence
- RED: with worker.py absent, `conda run -n GEE_animation python -m pytest tests/test_webapp_worker.py -q` gave `ImportError: cannot import name 'worker' from 'gee_animation.webapp'` (expected: module missing). I wrote the implementation before that run and moved it aside to capture the failure.
- Intermediate: with the brief's verbatim worker, 1 failed and 5 passed (the revoked-token test, cause above).
- GREEN: after the fix, the full suite gives `754 passed, 1 skipped, 2 deselected`.

## Files
- gee_animation/webapp/worker.py
- tests/test_webapp_worker.py

## Self-review
- The tests are network-free, with a fake `ee` and a fake runner.
- The no-op `init` is pinned by a test that fails if `init` is not overridden.
- gui.py and test_gui.py are untouched.

## Concerns
- The scrub is a plain `replace`. A very short token, or one a later wrapper or exception formats differently (for example URL-encoded), could still leak. Real refresh tokens are long, but the logsafe filter remains the backstop.
- If `job["refresh_token"]` is missing, `KeyError` is raised before the try block and the worker exits with a traceback but no error event. That traceback names the key, not the value. The parent should treat "no done or error event" as a failure.
