import inspect
import types

import pytest

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


# --- review round A: containment and correctness -----------------------------------------

def _inventory_button(app):
    return next(b for b in app.blocks.values()
                if getattr(b, "value", None) == "📋 Scene inventory (CSV)")


def _wired_targets(app):
    return {t[0] for dep in app.get_config_file()["dependencies"] for t in dep["targets"]}


def test_the_scene_inventory_button_does_not_exist_in_server_mode():
    """`run_inventory` calls ee.Initialize, and ee._state._state is process-wide: in the
    shared web process it would clobber every other user's state (and run under a
    service-account key if the host has one). Assert on what the browser is given -- the
    component's visibility and the events it is wired to -- not on Gradio internals."""
    server = gui.build_app(context=types.SimpleNamespace(list_runs=lambda r: []))
    button = _inventory_button(server)
    assert button.visible is False
    assert button._id not in _wired_targets(server)

    local = gui.build_app()
    assert _inventory_button(local).visible is True
    assert _inventory_button(local)._id in _wired_targets(local)


def test_the_server_mode_handler_set_is_the_audited_one(monkeypatch):
    """A tripwire, not a proof. Every handler below was audited by reading: none reaches
    ee/auth/deps.init in the web process (`_go` goes through context.submit; the rest are
    UI plumbing or read files). A NEW handler fails this test, forcing the same audit."""
    def boom(*a, **k):
        raise AssertionError("Earth Engine initialised in the web process")
    monkeypatch.setattr(gui.auth, "init", boom)
    monkeypatch.setattr(gui.DEFAULT_DEPS, "init", boom, raising=False)
    monkeypatch.setattr(gui, "run_animation", boom)
    monkeypatch.setattr(gui, "run_inventory", boom)
    ctx = types.SimpleNamespace(list_runs=lambda r: [], submit=lambda r, p: "j",
                                job_status=lambda j: {"state": "error", "message": "m"})
    app = gui.build_app(context=ctx)
    names = {b.fn.__name__ for b in app.fns.values()}
    assert "_inventory" not in names
    # What remains is pure UI plumbing; none of it may call into the ee-touching names.
    assert names <= {"_fill_runs", "_go", "_load", "_sync_index", "_apply_preset"}


def test_the_poll_bound_is_not_shorter_than_the_jobs_own_timeout(monkeypatch):
    """A poll that gives up before the job's deadline reports failure on a render that is
    still running fine. The old constant (7200 s) was independent of the queue's timeout."""
    clock = {"t": 0.0}

    def monotonic():
        clock["t"] += 1000.0
        return clock["t"]

    gave_up_at = {}
    monkeypatch.setattr(gui.time, "monotonic", monotonic)
    monkeypatch.setattr(gui.time, "sleep", lambda s: None)
    ctx = types.SimpleNamespace(
        list_runs=lambda r: [], submit=lambda r, p: "j", job_timeout_s=20000,
        job_status=lambda j: gave_up_at.update(t=clock["t"]) or
        {"state": "running", "message": "", "position": 0})
    go = _fn(gui.build_app(context=ctx), "_go")
    out = go(*_go_args(go), progress=lambda *a, **k: None, request=None)
    assert "gave up waiting" in out[5]
    # Started running at ~t=1000-3000; it must have been polled for at least the timeout.
    assert gave_up_at["t"] >= 20000 + 1000


def test_a_series_in_the_done_event_reaches_the_chart_in_server_mode(monkeypatch):
    """Worker line -> JSON -> job result -> `_go` -> chart rows."""
    import io
    import json
    from gee_animation.webapp import worker
    out = io.StringIO()
    job = json.dumps({"refresh_token": "r", "client_id": "c", "client_secret": "s",
                      "project": "p", "params": {}})
    runner = lambda **kw: ("/o/x.mp4", None, [], None, "ok",
                           [("2022-05", 0.5, 0.25), ("2022-06", 0.75, None)])
    worker.main(io.StringIO(job), out, ee_module=types.SimpleNamespace(
        Initialize=lambda **k: None), runner=runner)
    done = next(json.loads(l) for l in out.getvalue().splitlines()
                if json.loads(l)["event"] == "done")

    monkeypatch.setattr(gui.time, "sleep", lambda s: None)
    ctx = types.SimpleNamespace(
        list_runs=lambda r: [], submit=lambda r, p: "j",
        job_status=lambda j: {"state": "done", "message": "", "result": done})
    go = _fn(gui.build_app(context=ctx), "_go")
    chart = go(*_go_args(go), progress=lambda *a, **k: None, request=None)[6]
    assert list(chart["area"]) == ["inside AOI", "outside AOI", "inside AOI"]
    assert list(chart["value"]) == [0.5, 0.25, 0.75]


def _zip_with(path, members):
    import zipfile
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_a_zip_that_expands_beyond_the_bound_is_refused_before_extracting(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "MAX_ZIP_UNCOMPRESSED_BYTES", 10_000)
    bomb = _zip_with(tmp_path / "bomb.zip", {"a.shp": b"\0" * 50_000})   # compresses to ~50 B
    assert bomb.stat().st_size < 1_000
    with pytest.raises(ValueError, match="too large to unpack"):
        gui._region_aoi_from_upload(str(bomb))


def test_a_zip_with_too_many_members_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "MAX_ZIP_MEMBERS", 5)
    many = _zip_with(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(6)})
    with pytest.raises(ValueError, match="too large to unpack"):
        gui._region_aoi_from_upload(str(many))


def test_an_ordinary_shapefile_zip_still_unpacks(tmp_path):
    ok = _zip_with(tmp_path / "ok.zip", {"area.shp": b"x", "area.dbf": b"y"})
    assert gui._region_aoi_from_upload(str(ok))["shapefile"].endswith("area.shp")
