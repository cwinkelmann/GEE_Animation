# Task 3 report: secret-redacting log filter

**Status:** DONE_WITH_CONCERNS

## What was implemented

- `gee_animation/webapp/logsafe.py`: `RedactingFilter(secrets)` and `install(logger, secrets)`, verbatim from the brief. The filter formats each record, replaces every live secret (from the callable) with `***`, and rewrites `record.msg`/`record.args` only when a replacement happened.
- `tests/test_webapp_logsafe.py`: the three tests from the brief, verbatim.

## Tests and results

- Focused: `3 passed`.
- Full unit suite (`-m "not integration"`): `745 passed, 1 skipped, 2 deselected`. This matches the expected 742 + 3.

## TDD evidence

- RED: `conda run -n GEE_animation python -m pytest tests/test_webapp_logsafe.py -q` gave `ImportError`/`ModuleNotFoundError: No module named 'gee_animation.webapp.logsafe'` at collection. That is the expected reason, since the module did not exist yet.
- GREEN: the same command gave `3 passed in 0.02s`.

## Test honesty check

I ran the tests with redaction disabled by monkeypatching `RedactingFilter.filter` to `return True` (script at the scratchpad `mutate_logsafe.py`, with `PYTHONPATH` set to the worktree):
- `test_a_refresh_token_never_reaches_a_log_record`: FAILED (secret visible).
- `test_redaction_covers_the_exception_text_too`: FAILED (`bad token tok-ABC` visible).
- `test_messages_without_secrets_are_untouched`: passed, as it should, since it is the negative control.

So the two secret tests depend on the filter being active.

## Files changed

- Created: `gee_animation/webapp/logsafe.py`
- Created: `tests/test_webapp_logsafe.py`
- Created: `.superpowers/sdd/2026-10-14-multiuser-webapp/task-3-report.md` (this file)

Commit: `4688826 feat(webapp): redacting log filter so credentials cannot reach logs`

## Self-review findings

- The diff matches the brief's code exactly. Names and structure are as specified, and there is no extra code (YAGNI).
- `gui.py` and `tests/test_gui.py` are untouched.
- No `pip install` was run. Running the scratch scripts needed `PYTHONPATH` set to the worktree, because the shared editable install does not resolve `gee_animation.webapp` from the scratch directory. Nothing was re-pointed.

## Concerns

1. **Child loggers bypass a filter installed on a parent logger.** This matters for the spec's "structurally impossible" claim. Python runs logger-level filters only on the logger the call was made on. Records from child loggers propagate to the parent's handlers without running the parent's filters. Probe result, from the scratch script `child_logger_check.py`:
   ```
   direct: ***
   child: TOKEN-XYZ
   ```
   So a refresh token logged through `gee_animation.something` would not be redacted if the filter is installed on `gee_animation` or on the root logger. Task 9 should install the filter on the handlers instead (`handler.addFilter`), which also covers records from every child logger. Alternatively, `install` could attach the filter to each handler of the target logger. The brief specifies the logger-level version, so I did not change it. Please decide before Task 9 wires this up.
2. Secrets are compared as plain substrings. A secret that is split across two log arguments, or encoded (for example, URL-quoted), would not be caught. That is a limit of the brief's design, not a bug in the code as written.

---

## Fix report: child-logger propagation (commit 895b3e6)

**Status:** DONE (after the controller's ruling)

### What changed

- `install(logger, secrets)` now attaches the filter to the logger and to every handler the logger has at install time. The handler attachment catches records propagated from child loggers. The logger attachment still covers direct records and loggers with no handlers.
- New test `test_a_secret_logged_on_a_child_logger_is_redacted_before_the_parents_handler`. It installs its own `logging.StreamHandler` on `test.propagate` (not caplog) and asserts on the `io.StringIO` buffer, so the assertion reads exactly what the parent's handler wrote via propagation. caplog's handler is on the root logger and would not isolate this path, which is why I did not use it.
- The three original tests are unchanged.

### RED (before the fix)

Command: `conda run -n GEE_animation python -m pytest tests/test_webapp_logsafe.py -q`

```
>       assert "child-SECRET-VALUE" not in written
E       AssertionError: assert 'child-SECRET-VALUE' not in 'token is ch...CRET-VALUE\n'
E         'child-SECRET-VALUE' is contained here:
E           token is child-SECRET-VALUE
FAILED tests/test_webapp_logsafe.py::test_a_secret_logged_on_a_child_logger_is_redacted_before_the_parents_handler
1 failed, 3 passed in 0.38s
```

The failure is the expected one: the secret reaches the parent's handler through propagation.

### GREEN (after the fix)

Command: `conda run -n GEE_animation python -m pytest tests/test_webapp_logsafe.py -q`

```
....                                                                     [100%]
4 passed in 0.02s
```

Full suite command: `conda run -n GEE_animation python -m pytest -m "not integration" -q`

```
746 passed, 1 skipped, 2 deselected in 30.72s
```

The count is 746 rather than 745 because the new test was added.

### Concerns for Task 9

- Handlers added after `install()` runs are not covered. Task 9 must call `install` after logging is configured (for example, after uvicorn sets up its handlers). Ordering should be enough. A handler added later would need its own `addFilter` call, or the install call repeated for it. I did not try to solve this here.
- Each `install` call creates a new `RedactingFilter`. If it is called twice on the same logger, the handlers get two filters. That is harmless but redundant.

---

## Fix report round 1: tracebacks, stack text, filter must not raise (commit 1b2078c)

**Status:** DONE

### What changed

- **Traceback leak (Important).** `Formatter.format()` renders `exc_info` after filters run, so a secret in an exception message reached the handler. The filter now renders the traceback itself with `logging.Formatter().formatException`, redacts it into `record.exc_text`, and sets `record.exc_info = None`. Later formatters reuse `exc_text`. Previously stored `exc_text` is also redacted.
- **`stack_info`.** Redacted the same way, in place.
- **Never raise (Minor).** If the secrets callable raises, the record is emitted as `[log message withheld: redaction unavailable]` with traceback and stack withheld. This is the security-correct choice, since nothing can be verified without the secret list. If redaction fails after the secrets are known (for example, `getMessage` raising `TypeError` on mismatched args), the plain `record.msg` is redacted, args are dropped, and the traceback is withheld. The filter returns True in both cases.
- **Test for the traceback.** `test_a_secret_inside_an_exception_traceback_is_redacted` uses `log.exception` on a child logger. The secret is in the exception message only. The raise line uses an f-string over a module constant, so the source line does not carry the literal and cannot make the test pass by accident. Assertions are on a `StreamHandler` over `io.StringIO` installed on the parent.
- **Test for `stack_info`.** `test_a_secret_in_the_stack_info_source_line_is_redacted`. `stack_info` prints the calling frame's source line, not locals, so the only way a secret reaches it is as a literal on the call-site line. The test writes the literal onto that line. My first draft used a constant name on the line, and the test failed for that reason; that was a bug in the test, which I fixed.

### RED: traceback test, before the fix

Command: `conda run -n GEE_animation python -m pytest tests/test_webapp_logsafe.py -q`

```
>       assert "exc-SECRET-VALUE" not in written
E       assert 'exc-SECRET-VALUE' not in 'render fail...CRET-VALUE\n'
E         'exc-SECRET-VALUE' is contained here:
E           sed token exc-SECRET-VALUE
E           RuntimeError: EE refused token exc-SECRET-VALUE
FAILED tests/test_webapp_logsafe.py::test_a_secret_inside_an_exception_traceback_is_redacted
1 failed, 4 passed in 0.08s
```

### RED: stack_info test, against the filter with stack redaction removed (a mutation, restored afterwards)

Command: `conda run -n GEE_animation python -m pytest tests/test_webapp_logsafe.py -q -k stack_info`

```
E       assert 'stk-SECRET-VALUE' not in 'checking\nS...RET-VALUE"\n'
E         'stk-SECRET-VALUE' is contained here:
E           ue); _ = "stk-SECRET-VALUE"
1 failed, 5 deselected in 0.04s
```

### GREEN

Covering tests: `conda run -n GEE_animation python -m pytest tests/test_webapp_logsafe.py -q`

```
......                                                                   [100%]
6 passed in 0.01s
```

Full suite: `conda run -n GEE_animation python -m pytest -m "not integration" -q`

```
748 passed, 1 skipped, 2 deselected in 20.77s
```

The count is the 742 baseline plus six logsafe tests.

### Fallback check (scratch script, not a committed test)

- A secrets callable that raises: the record is emitted as the withheld placeholder, and nothing propagates to the caller.
- Mismatched args with a working callable (`log.error("secret-less message %s")` with no args): the template is emitted as the plain message, and nothing propagates to the caller.
- The `stack_info` check in the scratch script was weak (no secret was on that stack). The committed stack test replaces it.

### Concerns

- The new `stack_info` test puts the secret literal in its own source. It is the only realistic path for a secret to reach `stack_info`, but it is contrived. Real leaks through `stack_info` are unlikely.
- Withholding the whole message when the secret list cannot be read loses information. That is intended, but it means a failing `SessionStore` silences logs rather than leaking.
- The coverage limits from the previous report still hold. Handlers added after `install()` are not covered, so Task 9 must install after logging is configured. The 'handlers added later' issue is also noted in the plan commit `6e70e9f`.
