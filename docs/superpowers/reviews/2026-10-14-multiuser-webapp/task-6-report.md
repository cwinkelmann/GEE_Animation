# Task 6 report: per-run caps

## Implemented
- `gee_animation/webapp/caps.py`: `check(params, max_years=12, max_preset="1440p")` and `CapExceeded(ValueError)`, verbatim from the brief.
- `tests/test_webapp_caps.py`: the six tests from the brief, verbatim.

## Tests and results
- RED: `conda run -n GEE_animation python -m pytest tests/test_webapp_caps.py -q`
  collection error `ModuleNotFoundError: No module named 'gee_animation.webapp.caps'`, as expected.
- GREEN: same command, `6 passed in 0.01s`.
- Full unit suite: `conda run -n GEE_animation python -m pytest -m "not integration" -q` gives `770 passed, 1 skipped, 2 deselected`, matching the expected count.
- Message probes:
  - 26-year range: "That range is 26.0 years; server mode allows 12 years per render. Split it into two runs."
  - 4k: "4k is larger than server mode allows; the maximum is 1440p."

## What would break each test
- `test_a_run_inside_the_limits_passes`: any check that refuses a valid 4-year 1080p run (e.g. wrong comparison direction, or the year test using `>=`).
- `test_too_many_years_is_refused_with_the_limit_named`: removing the years check, or a message that drops "12 years" (the `match` is on the message text, so the limit is actually verified).
- `test_too_large_a_preset_is_refused`: removing the preset check, or a message that drops "1440p".
- `test_the_native_preset_is_allowed`: a check that treats any non-listed preset string as refused.
- `test_a_missing_or_blank_preset_is_allowed`: a check that does `params["preset"]` (KeyError or refusal on blank).
- `test_an_unparsable_date_is_refused_rather_than_ignored`: removing the try/except, or swallowing the ValueError instead of raising `CapExceeded`.

## Files changed
- `gee_animation/webapp/caps.py` (new)
- `tests/test_webapp_caps.py` (new)
- Not touched: `tests/test_gui.py`; the pre-existing modified `docs/superpowers/plans/2026-10-14-multiuser-webapp.md` was left unstaged.

## Commit
- `b283b9a` "feat(webapp): per-run year and preset caps", with `Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>`.

## Self-review findings
- Code and tests match the brief exactly; no extra code added.
- The error messages name the limit (12 years, 1440p), so the message assertions are meaningful.

## Concerns (not fixed, because the brief specifies the code verbatim)
1. **Unknown preset strings pass.** `_PRESET_ORDER` lists only 4k/1440p/1080p/720p. A preset such as `"8k"` or any typo is silently allowed. Probe: `check({"start":"2024-01-01","end":"2025-01-01","preset":"8k"})` returns without error. Fail-closed would refuse anything not in the allowed set. Today's GUI only offers the listed names, so this matters only for crafted requests, which is the threat Task 9 exists to stop.
2. **Reversed date range passes.** `start > end` gives negative years and the check never fires. Probe: `start=2025-01-01, end=2024-01-01` passes. Probably the config validator catches this earlier, but the cap does not depend on it.
3. **The year check uses 365.25-day years.** A 12-year span is about 4383 days. This is a boundary-precision point only.

Concerns 1 and 2 are worth a decision before Task 9 relies on this module as the sole gate.

---

## Fix report: fail closed on unknown presets, refuse reversed ranges

Commit `382abe0` "fix(webapp): caps fail closed on unknown presets and refuse reversed ranges" (Co-Authored-By: Claude Haiku 5.5).

### Changes to `gee_animation/webapp/caps.py`
- Preset allow-list `_ALLOWED_PRESETS` = the four sizes, `"native (no upscaling)"` and blank. This matches `gui.PRESET_CHOICES` (`["4k","1440p","1080p","720p", NATIVE_PRESET]`) plus blank. The native string is hard-coded rather than imported from `gui`, so the server module does not pull in Gradio.
- Any other preset raises `CapExceeded`: `Unknown preset '8k'; allowed presets are 4k, 1440p, 1080p, 720p, native (no upscaling), or blank.`
- `end <= start` raises `CapExceeded`: `The end date 2024-01-01 is not after the start date 2025-01-01; a run needs an end date after its start date.` The check runs after date parsing and before the year cap.
- Unchanged: native and blank are allowed; a preset larger than the cap is refused.

### TDD evidence
- RED: `conda run -n GEE_animation python -m pytest tests/test_webapp_caps.py -q`
  ```
  FFFF ... 4 failed, 6 passed in 0.22s
  FAILED test_an_unknown_preset_is_refused_and_named - Failed: DID NOT RAISE CapExceeded
  FAILED test_the_unknown_preset_refusal_lists_the_allowed_presets - Failed: DID NOT RAISE CapExceeded
  FAILED test_a_reversed_date_range_is_refused - Failed: DID NOT RAISE CapExceeded
  FAILED test_an_end_equal_to_the_start_is_refused - Failed: DID NOT RAISE CapExceeded
  ```
  This is the fail-open behaviour the coordinator described.
- GREEN: same command.
  ```
  ..........                                                               [100%]
  10 passed in 0.01s
  ```
- Full suite: `conda run -n GEE_animation python -m pytest -m "not integration" -q`
  ```
  774 passed, 1 skipped, 2 deselected in 21.39s
  ```
  The count moved from 770 to 774 (the four new tests).

### The four new tests and what would break each
- `test_an_unknown_preset_is_refused_and_named` (`match="'8k'"`): breaks if the allow-list check is removed (DID NOT RAISE). It also breaks if the message omits the offending value, since the match needs the repr `'8k'`.
- `test_the_unknown_preset_refusal_lists_the_allowed_presets` (`match` on "allowed presets are .*720p.*native \(no upscaling\)"): breaks on any generic message (for example "Invalid preset"), or on a message that drops the list or the native option. This is the specific-message assertion the coordinator asked for.
- `test_a_reversed_date_range_is_refused` (`match="is not after the start date 2025-01-01"`): breaks if the `end <= start` check is removed, or if the message drops the values or the phrase. A generic "bad date range" message would fail it.
- `test_an_end_equal_to_the_start_is_refused`: breaks if the check is `end < start` rather than `<=`. This is the only test on the strict-after boundary, and it also needs the phrase and the date.

### Existing tests (re-verified against the new code)
- The native-preset test and the blank-preset test still pass, so the allowed set is correct.
- The preset-too-large test (`4k` against `1440p`) still raises and still matches "1440p".
- The years-cap message still matches "12 years".

### Residual notes
- Preset validation runs only after the years cap. A request that is both over 12 years and carries an unknown preset gets the years message. Both are refusals, so this is not a bypass.
- The date-parse failure message is unchanged from the brief.
