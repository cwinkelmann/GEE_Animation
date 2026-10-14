import inspect
import types

from gee_animation import gui


def _fn(app, name):
    return next(b.fn for b in app.fns.values() if b.fn.__name__ == name)


def _block(app, name):
    return next(b for b in app.fns.values() if b.fn.__name__ == name)


def _go_args(go):
    n = len([p for p in inspect.signature(go).parameters.values()
             if p.default is inspect.Parameter.empty])
    return [None] * n


def test_build_app_without_a_context_is_unchanged():
    app = gui.build_app()
    assert app is not None          # the single-operator path still builds


def test_server_mode_submits_through_the_context_instead_of_rendering_inline():
    """The whole point of the context: in server mode the web process must not render."""
    submitted = []
    ctx = types.SimpleNamespace(
        list_runs=lambda request: [],
        submit=lambda request, params: submitted.append(params) or "job-1",
        job_status=lambda job_id: {"state": "done", "message": "ok",
                                   "result": {"mp4": "/data/out/ab/x.mp4"}, "position": 0})
    app = gui.build_app(context=ctx)
    assert app is not None
    # (fake-only) proves the fake works; the driving tests below prove gui uses it.
    assert ctx.submit(types.SimpleNamespace(cookies={}), {"index": "ndvi"}) == "job-1"
    assert submitted == [{"index": "ndvi"}]


def test_the_run_browser_lists_only_what_the_context_returns():
    calls = []

    def list_runs(request):
        calls.append(request)
        return [("alice_ndvi (12 frames)", "/data/out/ab12/alice_ndvi/alice_ndvi.mp4")]

    ctx = types.SimpleNamespace(list_runs=list_runs)
    app = gui.build_app(context=ctx)
    assert app is not None
    # Registered for load, not called at build time: identity exists only per request.
    assert calls == []
    assert ctx.list_runs("fake-request")[0][0].startswith("alice_ndvi")


# --- driving the real handlers: these prove gui.py itself uses the context ---

def test_fill_runs_handler_returns_the_contexts_runs_not_the_output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "OUTPUT_DIR", str(tmp_path))
    stray = tmp_path / "other" / "secret"
    stray.mkdir(parents=True)
    (stray / "secret.mp4").write_bytes(b"x")
    req = object()
    ctx = types.SimpleNamespace(list_runs=lambda r: [("mine", "/p/mine.mp4")] if r is req else [])
    out = _fn(gui.build_app(context=ctx), "_fill_runs")(req)
    assert out["choices"] == [("mine", "/p/mine.mp4")]


def test_fill_runs_without_context_still_scans_output_dir(monkeypatch):
    monkeypatch.setattr(gui, "list_previous_runs", lambda *a, **k: [("g", "/g.mp4")])
    out = _fn(gui.build_app(), "_fill_runs")(None)
    assert out["choices"] == [("g", "/g.mp4")]


def test_load_handler_is_registered_only_with_a_context():
    def targets(app):
        return [b for b in app.fns.values() if b.fn.__name__ == "_fill_runs"]
    ctx = types.SimpleNamespace(list_runs=lambda r: [])
    assert len(targets(gui.build_app(context=ctx))) == 2   # refresh + load
    assert len(targets(gui.build_app())) == 1               # refresh only


def test_go_in_server_mode_submits_polls_and_never_renders_inline(monkeypatch):
    def boom(**kw):
        raise AssertionError("rendered in the web process")
    monkeypatch.setattr(gui, "run_animation", boom)
    monkeypatch.setattr(gui.time, "sleep", lambda s: None)
    seen = {}
    states = iter([{"state": "queued", "message": "", "position": 2},
                   {"state": "running", "message": "", "position": 0},
                   {"state": "done", "message": "",
                    "result": {"mp4": "/o/x.mp4", "gif": "/o/x.gif", "status": "ok"}}])
    ctx = types.SimpleNamespace(
        list_runs=lambda r: [],
        submit=lambda r, p: seen.update(request=r, params=p) or "job-1",
        job_status=lambda j: next(states))
    go = _fn(gui.build_app(context=ctx), "_go")
    out = go(*_go_args(go), progress=lambda *a, **k: None, request="REQ")
    assert seen["request"] == "REQ"
    assert "aoi_path" in seen["params"] and "sensor" in seen["params"]
    assert out[0] == "/o/x.mp4" and out[1] == "/o/x.gif" and out[5] == "ok"


def test_go_in_server_mode_reports_a_failed_job_as_an_error(monkeypatch):
    monkeypatch.setattr(gui.time, "sleep", lambda s: None)
    ctx = types.SimpleNamespace(
        list_runs=lambda r: [], submit=lambda r, p: "j",
        job_status=lambda j: {"state": "error", "message": "quota exceeded"})
    go = _fn(gui.build_app(context=ctx), "_go")
    out = go(*_go_args(go), progress=lambda *a, **k: None, request=None)
    assert out[0] is None and "quota exceeded" in out[5]


def test_go_in_server_mode_surfaces_a_submit_refusal():
    def refuse(r, p):
        raise RuntimeError("over the cap")
    ctx = types.SimpleNamespace(list_runs=lambda r: [], submit=refuse,
                                job_status=lambda j: {})
    go = _fn(gui.build_app(context=ctx), "_go")
    out = go(*_go_args(go), progress=lambda *a, **k: None, request=None)
    assert "over the cap" in out[5]


def test_go_without_context_still_renders_in_process(monkeypatch):
    got = {}

    def fake(**kw):
        got.update(kw)
        return "m.mp4", None, None, None, "msg", []
    monkeypatch.setattr(gui, "run_animation", fake)
    go = _fn(gui.build_app(), "_go")
    out = go(*_go_args(go), progress=lambda *a, **k: None)
    assert got and out[0] == "m.mp4" and out[5] == "msg"


def test_gradio_recognises_request_as_an_injected_parameter():
    """String annotations (from __future__) would leave gr.Request unresolved and Gradio
    would then demand an input component for it -- identity would never arrive."""
    from gradio.utils import get_type_hints, is_special_typed_parameter
    ctx = types.SimpleNamespace(list_runs=lambda r: [])
    app = gui.build_app(context=ctx)
    for name in ("_fill_runs", "_go"):
        fn = _fn(app, name)
        assert is_special_typed_parameter("request", get_type_hints(fn)), name


def test_server_mode_lifts_gradios_one_at_a_time_limit_on_the_render_button():
    """Gradio's default (1 per event fn) would serialise all users behind the first."""
    ctx = types.SimpleNamespace(list_runs=lambda r: [])
    server = _block(gui.build_app(context=ctx), "_go")
    assert server.concurrency_limit is None          # None = unlimited in Gradio 6.20
    assert _block(gui.build_app(), "_go").concurrency_limit == "default"   # unchanged


def test_go_in_server_mode_gives_up_on_a_job_stuck_in_the_queue(monkeypatch):
    clock = iter(range(0, 10**6, 3000))
    monkeypatch.setattr(gui.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(gui.time, "sleep", lambda s: None)
    ctx = types.SimpleNamespace(
        list_runs=lambda r: [], submit=lambda r, p: "j",
        job_status=lambda j: {"state": "queued", "message": "", "position": 9})
    go = _fn(gui.build_app(context=ctx), "_go")
    out = go(*_go_args(go), progress=lambda *a, **k: None, request=None)
    assert out[0] is None and "gave up waiting" in out[5]
