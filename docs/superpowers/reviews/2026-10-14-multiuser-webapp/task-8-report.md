# Task 8 report

Commit: dc928ba. Files: gee_animation/gui.py, tests/test_webapp_context.py (new, 12 tests). test_gui.py untouched.

## Implemented
`build_app(context=None)`. With a context: run dropdown starts empty and is filled per request by `app.load` and Refresh through `context.list_runs(request)`; `_go` submits via `context.submit(request, params)`, polls `job_status`, never calls `run_animation`.

## gui.py hunks
1. Signature `build_app(context=None)` + docstring.
2. `prev` dropdown: `[]` and label "yours" in server mode; unchanged otherwise (label text identical: "from <OUTPUT_DIR>/").
3. `refresh.click` lambda replaced by `_fill_runs(request: gr.Request)`; `app.load(_fill_runs...)` only with a context. `_fill_runs.__annotations__["request"] = gr.Request` (see finding 1).
4. `_go`: new last param `request: gr.Request = None`; the kwargs previously written inline in the `run_animation(...)` call are now `params = dict(...)` and the call is `run_animation(**params)`; server branch between them; `progress(0.05, ...)` moved to after the branch (still before run_animation). `_go.__annotations__["request"] = gr.Request`.

## Tests / results
tests/test_gui.py + new file: 94 passed (82 + 12). Full suite: 806 passed, 1 skipped, 2 deselected (brief expected 798; I added 8 tests beyond the brief's 4).

## TDD evidence
RED (gui.py reverted, `pytest tests/test_webapp_context.py -q`): `10 failed, 2 passed` (TypeError: build_app() got an unexpected keyword argument 'context'; the 2 passes are the no-context tests that don't pass a context). Note: RED was run after implementing, by reverting gui.py via diff/checkout/apply, not before.
GREEN: `94 passed` for the two files.

## What breaks each test / what proves what
Fake-only (prove nothing about gui.py): `..submits_through_the_context_instead_of_rendering_inline`, `..lists_only_what_the_context_returns`, `..context_run_list_is_used_instead_of_the_global_output_dir` (the brief's three; they call the fake directly. They only fail pre-change because of the `context=` kwarg, i.e. they prove `build_app` accepts it). Deleting the whole server branch would not break them.
Drive real handlers (prove gui.py uses the context):
- `fill_runs_handler_returns_the_contexts_runs_not_the_output_dir`: breaks if `_fill_runs` reads OUTPUT_DIR/list_previous_runs (a stray run is planted) or drops the request.
- `fill_runs_without_context_still_scans_output_dir`: breaks if the no-context path stops using list_previous_runs.
- `load_handler_is_registered_only_with_a_context`: breaks if app.load is removed (1 vs 2 registrations) or registered without a context.
- `go_in_server_mode_submits_polls_and_never_renders_inline`: run_animation raises if called; breaks if branch is removed, request not forwarded, params lack run_animation kwargs, polling/result unpacking wrong.
- `..reports_a_failed_job_as_an_error` / `..surfaces_a_submit_refusal`: break if error states or a submit exception (caps) are not turned into an error message.
- `go_without_context_still_renders_in_process`: breaks if the single-operator path stops calling run_animation(**params).
- `gradio_recognises_request_as_an_injected_parameter`: breaks if the annotation fix is removed (see below).
Not covered: a real HTTP request with real Gradio injection end to end (handlers called directly, plus the injection check via gradio's own helper).

## Self-review findings
1. REAL BUG in the brief's recipe: `gui.py` has `from __future__ import annotations` and `gr` is imported inside `build_app`, so `request: gr.Request` is an unresolvable string; Gradio's `get_type_hints` fails, it does not treat the parameter as special, and warned "Expected 1 arguments ... received 0" (it would demand an input component, so identity would never arrive). Fixed by assigning the real class into `__annotations__`; the warnings vanished and a test pins it.
2. Branch placed inside the existing `try`, so submit exceptions (cap refusals) show as UI errors rather than crashing.
3. I used `state.get(...)` for message/position and a fallback message `job <state>` (the brief used `state["message"]`), tolerant of the minimal fake status dicts.
4. No-context path: behaviourally identical (same kwargs, same progress calls and order); the only structural change is the dict-then-splat refactor, done so the server `params` is exactly the run_animation kwargs.

## Concerns
- The `__annotations__` assignment is a workaround; module-level `gradio` import is not possible (optional extra).
- `time.sleep(2)` polling blocks a Gradio worker thread for the job's duration; Task 9 should confirm queue concurrency settings allow that.
- The server `result` dict contract (mp4/gif/frames/zip/status keys) is assumed from the brief; Task 9's context must produce `status`.
- The `params` dict contains `aoi_path=aoi_file` (a Gradio temp path); Task 9's `submit` must copy/validate it into the user's directory.

## Fix round 1
1. Concurrency: Gradio 6.20 `concurrency_limit` accepts int | None | "default"; "default" resolves to `queue(default_concurrency_limit)`, itself 1; None = unlimited (events.py docs, queueing.py:218-225). Chose `None` on `go.click` in server mode only (single-operator keeps "default"); max_threads (40) is the ceiling and JobQueue's 3 slots limit renders. Test `..lifts_gradios_one_at_a_time_limit..` inspects the registered BlockFunction: breaks if the kwarg is removed ("default" != None) or applied to the no-context app.
2. Deleted `test_the_context_run_list_is_used_instead_of_the_global_output_dir`.
3. Poll bound: `SERVER_POLL_TIMEOUT_S = 7200` (> 5400 queue timeout), monotonic deadline, returns an error "gave up waiting for the render job (still <state> after 120 min)". Test `..gives_up_on_a_job_stuck_in_the_queue` (fake clock): breaks if the deadline check is removed (infinite loop) or the message changes.
Results: test_webapp_context + test_gui 95 passed; full suite below in the final line of this round.
