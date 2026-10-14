# tests/test_webapp_app.py
from pathlib import Path

import types

import pytest
from fastapi.testclient import TestClient

from gee_animation.webapp.app import AppConfig, create_app


def _app(tmp_path):
    cfg = AppConfig(client_id="cid", client_secret="sec",
                    redirect_uri="https://carrot.aisl.c4ds.hnee.de/oauth2/callback",
                    cookie_secret="test-signing-key", output_root=Path(tmp_path))
    return create_app(cfg, oauth_client=None, spawn=lambda job, on_event: None)


def test_an_anonymous_request_to_the_app_is_sent_to_login(tmp_path):
    client = TestClient(_app(tmp_path), follow_redirects=False)
    response = client.get("/")
    assert response.status_code in (302, 303, 307)
    assert "/login" in response.headers["location"]


def test_a_stray_stdout_line_from_the_render_path_does_not_kill_the_job(monkeypatch):
    """Ruling N: a library printing to stdout must not corrupt the JSON-lines protocol.

    This drives the REAL `WorkerProcess.run` with a faked subprocess, rather than
    re-implementing the read loop in the test — a test that reimplements the code it
    covers passes no matter what the code does (Ruling Q).
    """
    import asyncio

    from gee_animation.webapp import spawn as spawn_mod

    class FakeStdin:
        def write(self, data): pass
        async def drain(self): pass
        def close(self): pass

    class FakeStdout:
        def __init__(self, lines):
            self._lines = list(lines)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._lines:
                raise StopAsyncIteration
            return self._lines.pop(0)

    class FakeProc:
        returncode = 0

        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = FakeStdout([
                b"Using TensorFlow backend.\n",                      # library chatter
                b'{"event": "progress", "message": "fetching"}\n',
                b"not json either\n",
                b'{"event": "done", "mp4": "/out/x.mp4"}\n',
            ])

        async def wait(self):
            return 0

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(spawn_mod.asyncio, "create_subprocess_exec", fake_exec)

    seen = []
    proc = spawn_mod.WorkerProcess({"id": "j1", "params": {}})
    asyncio.run(proc.run(seen.append))

    assert seen == [{"event": "progress", "message": "fetching"},
                    {"event": "done", "mp4": "/out/x.mp4"}]


def test_the_log_redactor_is_installed_once_the_app_has_started(tmp_path):
    """Ruling J: installing before uvicorn configures logging would attach the filter to
    handlers that are then replaced, leaving credentials unredacted.

    Asserts on the DELTA, not on absolute state: filters installed on the root logger
    are process-global and survive the test that added them, so an absolute assertion
    here would pass or fail depending on which tests ran first (Ruling K).
    """
    import logging

    from gee_animation.webapp.logsafe import RedactingFilter

    root = logging.getLogger()
    count = lambda: sum(isinstance(f, RedactingFilter) for f in root.filters)
    before = count()
    with TestClient(_app(tmp_path)):
        assert count() == before + 1
    # Clean up after ourselves so the next test sees the logging state it expects.
    for f in [f for f in root.filters if isinstance(f, RedactingFilter)]:
        root.removeFilter(f)


def test_the_session_store_and_queue_live_on_app_state(tmp_path):
    app = _app(tmp_path)
    assert app.state.sessions is not None
    assert app.state.queue is not None


def test_the_sweep_runs_at_startup(tmp_path):
    old = Path(tmp_path) / "abc123" / "ancient"
    old.mkdir(parents=True)
    f = old / "ancient.mp4"
    f.write_bytes(b"\x00\x00mp4")
    import os
    stale = __import__("time").time() - 30 * 86400
    os.utime(f, (stale, stale))
    os.utime(old, (stale, stale))     # the sweep also reads the directory's own mtime
    with TestClient(_app(tmp_path)):
        pass
    assert not old.exists()


# --- end-to-end through the real wiring (no network, no real render) -------------------

import time as _time


class _FakeWorker:
    """Stands in for the subprocess: records the payload, replies like webapp.worker."""
    payloads: list = []

    def __init__(self, payload):
        self.payload = payload
        _FakeWorker.payloads.append(payload)

    async def run(self, on_event):
        # The AOI must still be readable when the "worker" runs, as the real one would.
        aoi = Path(self.payload["params"]["aoi_path"])
        on_event({"event": "done", "mp4": "/o/x.mp4", "gif": None, "frames": None,
                  "zip": None, "status": f"rendered {aoi.read_text()}"})

    def kill(self):
        pass


def _server(tmp_path):
    _FakeWorker.payloads = []
    cfg = AppConfig(client_id="cid", client_secret="sec", redirect_uri="https://x/cb",
                    cookie_secret="k", output_root=Path(tmp_path))
    return create_app(cfg, oauth_client=None, spawn=lambda job, on_event: _FakeWorker(job))


def _wait_done(ctx, job_id, request):
    for _ in range(100):
        state = ctx.job_status(request, job_id)
        if state["state"] in ("done", "error", "cancelled"):
            return state
        _time.sleep(0.05)
    raise AssertionError("job never finished")


def test_a_signed_in_user_gets_the_gradio_page_and_an_anonymous_one_is_redirected(tmp_path):
    """Drives the real mount: the auth dependency must admit a valid session cookie."""
    app = _server(tmp_path)
    sid = app.state.sessions.new("a@b.de", "RT-1")
    with TestClient(app, follow_redirects=False) as client:
        assert client.get("/").status_code in (302, 303, 307)
        assert client.get("/config").status_code == 401        # API calls keep their 401
        client.cookies.set("sid", sid)
        assert client.get("/").status_code == 200
        assert client.get("/config").status_code == 200


def test_submit_hands_the_worker_a_private_copy_of_the_aoi_the_project_and_the_credentials(
        tmp_path, monkeypatch):
    cache = Path(tmp_path) / "gradio_cache"
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(cache))
    app = _server(tmp_path / "out")
    sid = app.state.sessions.new("a@b.de", "RT-1")
    upload = cache / "abc" / "aoi.geojson"
    upload.parent.mkdir(parents=True)
    upload.write_text("the-aoi")
    request = types.SimpleNamespace(cookies={"sid": sid}, app=app)
    params = {"start": "2024-01-01", "end": "2024-12-31", "preset": "720p", "sensor": "s2",
              "index": "ndvi", "aoi_path": str(upload), "project": " my-proj "}
    with TestClient(app):
        ctx = app.state.context
        state = _wait_done(ctx, ctx.submit(request, params), request)
        upload.unlink()                                  # Gradio cleans its cache
    payload = _FakeWorker.payloads[0]
    assert payload["project"] == "my-proj"
    assert payload["refresh_token"] == "RT-1" and payload["client_id"] == "cid"
    aoi = Path(payload["params"]["aoi_path"])
    assert aoi != upload and aoi.read_text() == "the-aoi"
    assert str(aoi).startswith(payload["params"]["out_dir"])      # swept with the run
    assert "a@b.de" not in payload["params"]["out_dir"]
    assert state["state"] == "done" and state["result"]["status"] == "rendered the-aoi"
    assert app.state.sessions.get(sid).project == "my-proj"


def test_submit_refuses_an_expired_session_a_missing_project_and_an_over_cap_run(tmp_path):
    from gee_animation.webapp.caps import CapExceeded
    from gee_animation.webapp.project import ProjectError
    app = _server(tmp_path)
    sid = app.state.sessions.new("a@b.de", "RT-1")
    good = {"start": "2024-01-01", "end": "2024-12-31", "preset": "720p", "project": "p"}
    with TestClient(app):
        ctx = app.state.context
        with pytest.raises(PermissionError):
            ctx.submit(types.SimpleNamespace(cookies={"sid": "gone"}, app=app), good)
        with pytest.raises(ProjectError):
            ctx.submit(types.SimpleNamespace(cookies={"sid": sid}, app=app),
                       {**good, "project": ""})
        with pytest.raises(CapExceeded):
            ctx.submit(types.SimpleNamespace(cookies={"sid": sid}, app=app),
                       {**good, "end": "2040-01-01"})
    assert _FakeWorker.payloads == []


def test_the_redactor_is_installed_on_uvicorns_own_loggers_and_scrubs_a_live_token(tmp_path):
    import io
    import logging

    from gee_animation.webapp.logsafe import RedactingFilter
    app = _server(tmp_path)
    app.state.sessions.new("a@b.de", "SECRET-REFRESH-TOKEN")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)              # like uvicorn's own handler
    uv = logging.getLogger("uvicorn.error")
    uv.addHandler(handler)
    try:
        with TestClient(app):
            assert any(isinstance(f, RedactingFilter) for f in uv.filters)
            uv.error("boom SECRET-REFRESH-TOKEN")
    finally:
        uv.removeHandler(handler)
        for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            for f in [f for f in lg.filters if isinstance(f, RedactingFilter)]:
                lg.removeFilter(f)
            for h in lg.handlers:
                for f in [f for f in h.filters if isinstance(f, RedactingFilter)]:
                    h.removeFilter(f)
    assert "SECRET-REFRESH-TOKEN" not in stream.getvalue()
    assert "boom" in stream.getvalue()


def test_create_app_refuses_an_empty_cookie_secret(tmp_path):
    with pytest.raises(ValueError):
        create_app(AppConfig("c", "s", "r", output_root=Path(tmp_path)), None, lambda j, e: None)



@pytest.mark.filterwarnings("ignore::authlib.deprecate.AuthlibDeprecationWarning")
def test_the_real_oauth_client_asks_google_for_a_refresh_token(tmp_path):
    """access_type=offline must reach the authorize URL (it was silently dropped when
    passed via client_kwargs, which would have failed every render after an hour)."""
    import asyncio
    import warnings
    from gee_animation.webapp.app import build_oauth_client
    with warnings.catch_warnings():      # authlib's own import-time httpx deprecation
        warnings.simplefilter("ignore")
        import authlib.integrations.starlette_client  # noqa: F401
    meta = {"authorization_endpoint": "https://a/auth", "token_endpoint": "https://a/t",
            "issuer": "https://a", "jwks_uri": "https://a/j"}
    cfg = AppConfig("cid", "sec", "https://x/cb", output_root=Path(tmp_path), cookie_secret="k")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = build_oauth_client(cfg, server_metadata=meta)
        url = asyncio.run(client.create_authorization_url("https://x/cb"))["url"]
    assert "access_type=offline" in url and "prompt=consent" in url
    assert "earthengine" in url and "email" in url


def _project_box(app):
    import gradio as gr
    return next(c for c in app.blocks.values() if isinstance(c, gr.Textbox)
                and "project" in (c.label or "").lower())


def test_server_mode_does_not_prefill_our_project_and_single_user_mode_keeps_it(tmp_path):
    from gee_animation.gui import build_app
    server = _project_box(build_app(context=_server(tmp_path).state.context))
    assert server.value in ("", None) and "own" in server.label.lower()
    assert _project_box(build_app()).value == "hnee-331218"


def test_server_mode_preset_application_does_not_refill_the_project(tmp_path):
    """Applying a preset resets every input to its default, including the project."""
    from gee_animation.gui import build_app
    app = build_app(context=_server(tmp_path).state.context)
    box = _project_box(app)
    fn = next(f for f in app.fns.values() if f.name == "_apply_preset")
    out = fn.fn("")
    idx = [c._id for c in fn.outputs].index(box._id)
    assert out[idx]["value"] in ("", None)


def test_submit_names_the_fix_when_the_project_is_empty(tmp_path):
    from gee_animation.webapp.project import ProjectError
    app = _server(tmp_path)
    sid = app.state.sessions.new("a@b.de", "RT-1")
    with TestClient(app):
        with pytest.raises(ProjectError) as err:
            app.state.context.submit(
                types.SimpleNamespace(cookies={"sid": sid}, app=app),
                {"start": "2024-01-01", "end": "2024-12-31", "preset": "720p", "project": " "})
    assert "own Google Cloud project id" in str(err.value)
    assert "Earth Engine" in str(err.value)


# --- fix round 2 ---------------------------------------------------------------------

def _req(app, sid):
    return types.SimpleNamespace(cookies={"sid": sid}, app=app)


def _good(**extra):
    return {"start": "2024-01-01", "end": "2024-12-31", "preset": "720p", "project": "p",
            "sensor": "s2", "index": "ndvi", **extra}


def _seed_run(root, email, name):
    from gee_animation.webapp.storage import user_dir
    run = user_dir(root, email) / name
    run.mkdir(parents=True)
    (run / f"{name}.mp4").write_bytes(b"\x00")


def test_each_user_lists_only_their_own_runs_and_an_unknown_session_lists_none(tmp_path):
    """Per-user isolation, through the real ServerContext (not just oauth.current_user)."""
    app = _server(tmp_path)
    _seed_run(tmp_path, "a@b.de", "run_of_a")
    _seed_run(tmp_path, "c@d.de", "run_of_c")
    sid_a = app.state.sessions.new("a@b.de", "RT-A")
    sid_c = app.state.sessions.new("c@d.de", "RT-C")
    ctx = app.state.context
    labels = lambda sid: " ".join(label for label, _ in ctx.list_runs(_req(app, sid)))
    assert "run_of_a" in labels(sid_a) and "run_of_c" not in labels(sid_a)
    assert "run_of_c" in labels(sid_c) and "run_of_a" not in labels(sid_c)
    assert ctx.list_runs(_req(app, "stale-sid")) == []
    assert ctx.list_runs(types.SimpleNamespace(cookies={}, app=app)) == []


def test_the_upload_limit_is_enforced_by_the_server(tmp_path):
    """A 2 MB upload against a 1 MB cap is refused (probes enforcement, not a variable)."""
    cfg = AppConfig("cid", "sec", "https://x/cb", cookie_secret="k",
                    output_root=Path(tmp_path), max_upload_mb=1)
    app = create_app(cfg, None, lambda j, e: _FakeWorker(j))
    sid = app.state.sessions.new("a@b.de", "RT-1")
    with TestClient(app) as client:
        client.cookies.set("sid", sid)
        small = client.post("/gradio_api/upload", files={"files": ("a.geojson", b"x" * 1000)})
        big = client.post("/gradio_api/upload",
                          files={"files": ("a.geojson", b"x" * (2 * 1024 * 1024))})
    assert small.status_code == 200
    assert big.status_code == 413


@pytest.mark.parametrize("kind", ["direct", "symlink", "dotdot"])
def test_an_aoi_path_outside_the_upload_cache_is_refused_and_nothing_is_copied(
        tmp_path, monkeypatch, kind):
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(Path(tmp_path) / "gradio_cache"))
    app = _server(tmp_path / "out")
    sid_a = app.state.sessions.new("a@b.de", "RT-A")
    app.state.sessions.new("c@d.de", "RT-C")
    _seed_run(tmp_path / "out", "c@d.de", "victim")
    from gee_animation.webapp.storage import user_dir
    secret = user_dir(tmp_path / "out", "c@d.de") / "victim" / "secret.geojson"
    secret.write_text("OTHER-USERS-FILE")
    cache = Path(tmp_path) / "gradio_cache"
    cache.mkdir(exist_ok=True)
    if kind == "direct":
        evil = secret
    elif kind == "symlink":                                  # a link INSIDE the cache
        evil = cache / "link.geojson"
        evil.symlink_to(secret)
    else:                                                    # lexically inside, really outside
        evil = cache / ".." / "out" / "leak.json"
        evil.resolve().write_text("OTHER-USERS-FILE")
    assert Path(evil).resolve().is_file()                    # so only containment can refuse it
    with TestClient(app):
        with pytest.raises(PermissionError) as err:
            app.state.context.submit(_req(app, sid_a), _good(aoi_path=str(evil)))
    assert str(tmp_path) not in str(err.value)               # names no paths
    assert not list((tmp_path / "out").glob("*/*/aoi/*")) and _FakeWorker.payloads == []
    assert not user_dir(tmp_path / "out", "a@b.de").exists()


def test_a_staged_preset_aoi_is_accepted(tmp_path, monkeypatch):
    """Presets are staged into gettempdir()/gee_preset_*; they must keep working."""
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(Path(tmp_path) / "gradio_cache"))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    staged = Path(tmp_path) / "gee_preset_abc" / "p.geojson"
    staged.parent.mkdir()
    staged.write_text("preset")
    app = _server(tmp_path / "out")
    sid = app.state.sessions.new("a@b.de", "RT-A")
    with TestClient(app):
        ctx = app.state.context
        _wait_done(ctx, ctx.submit(_req(app, sid), _good(aoi_path=str(staged))),
                   _req(app, sid))
    assert Path(_FakeWorker.payloads[0]["params"]["aoi_path"]).read_text() == "preset"


def test_run_directories_are_unique_and_built_from_safe_characters(tmp_path, monkeypatch):
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(Path(tmp_path) / "gradio_cache"))
    app = _server(tmp_path / "out")
    sid = app.state.sessions.new("a@b.de", "RT-A")
    with TestClient(app):
        ctx = app.state.context
        ids = [ctx.submit(_req(app, sid), _good(sensor="../../etc", index="a b/c"))
               for _ in range(2)]
        for i in ids:
            _wait_done(ctx, i, _req(app, sid))
    dirs = [Path(p["params"]["out_dir"]) for p in _FakeWorker.payloads]
    assert dirs[0] != dirs[1]                                    # same second, still distinct
    from gee_animation.webapp.storage import user_dir
    for d in dirs:
        assert d.parent == user_dir(tmp_path / "out", "a@b.de")  # no escape from the user dir
        assert all(c.isalnum() or c in "_-" for c in d.name)


def test_submit_tells_the_user_the_server_is_busy_when_the_loop_is_wedged(tmp_path, monkeypatch):
    import asyncio
    from gee_animation.webapp import app as app_mod
    monkeypatch.setattr(app_mod, "SUBMIT_TIMEOUT_S", 0.2)
    app = _server(tmp_path)
    sid = app.state.sessions.new("a@b.de", "RT-A")
    with TestClient(app):
        import threading
        wedged = asyncio.new_event_loop()                        # a loop stuck in a blocking call
        t = threading.Thread(target=wedged.run_forever, daemon=True)
        t.start()
        wedged.call_soon_threadsafe(_time.sleep, 1.0)
        app.state.loop = wedged
        try:
            with pytest.raises(RuntimeError, match="busy"):
                app.state.context.submit(_req(app, sid), _good())
        finally:
            wedged.call_soon_threadsafe(wedged.stop)
            t.join(timeout=3)
            wedged.close()


def test_the_retention_sweep_repeats_while_the_server_is_up_and_stops_on_shutdown(
        tmp_path, monkeypatch, caplog):
    import os
    from gee_animation.webapp import app as app_mod
    monkeypatch.setattr(app_mod, "SWEEP_INTERVAL_S", 0.05)
    monkeypatch.setattr(app_mod, "SHUTDOWN_TIMEOUT_S", 1.0)
    app = _server(tmp_path)
    with caplog.at_level("ERROR", logger="gee_animation.webapp.app"):
        with TestClient(app):
            sweeper = app.state.sweeper
            old = Path(tmp_path) / "u" / "ancient"
            old.mkdir(parents=True)                               # created AFTER startup
            f = old / "ancient.mp4"
            f.write_bytes(b"\x00")
            stale = _time.time() - 30 * 86400
            os.utime(f, (stale, stale))
            os.utime(old, (stale, stale))
            for _ in range(100):
                if not old.exists():
                    break
                _time.sleep(0.05)
            assert not old.exists()
            assert not sweeper.done()
    assert sweeper.cancelled()
    assert "did not stop" not in caplog.text                      # shutdown did not time out


def test_shutdown_is_bounded_when_the_sweeper_will_not_stop(tmp_path, monkeypatch, caplog):
    """A wedged sweeper must not hang the server's shutdown; it is reported instead."""
    import asyncio
    from gee_animation.webapp import app as app_mod
    monkeypatch.setattr(app_mod, "SHUTDOWN_TIMEOUT_S", 0.3)

    async def stubborn():
        # Swallows cancellation, but only for a second, so the test loop can still be
        # torn down afterwards (asyncio.run waits for every task).
        deadline = _time.monotonic() + 1.0
        while _time.monotonic() < deadline:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                pass

    app = _server(tmp_path)
    start = _time.monotonic()
    with caplog.at_level("ERROR", logger="gee_animation.webapp.app"):
        with TestClient(app):
            app.state.sweeper.cancel()
            app.state.sweeper = asyncio.run_coroutine_threadsafe(
                _spawn(stubborn), app.state.loop).result()
    assert _time.monotonic() - start < 5
    assert "did not stop" in caplog.text


async def _spawn(coro_fn):
    import asyncio
    return asyncio.create_task(coro_fn())


def test_a_sweep_that_raises_does_not_end_retention_or_stop_startup(tmp_path, monkeypatch, caplog):
    from gee_animation.webapp import app as app_mod
    monkeypatch.setattr(app_mod, "SWEEP_INTERVAL_S", 0.05)
    calls = []

    def flaky(root, max_age_days=14):
        calls.append(1)
        raise OSError("half-deleted directory")

    monkeypatch.setattr(app_mod.storage, "sweep", flaky)
    app = _server(tmp_path)
    with caplog.at_level("ERROR", logger="gee_animation.webapp.app"):
        with TestClient(app):                                     # startup sweep raised too
            for _ in range(100):
                if len(calls) >= 4:
                    break
                _time.sleep(0.05)
            assert len(calls) >= 4 and not app.state.sweeper.done()
    assert "retention sweep failed" in caplog.text


def test_a_failed_startup_does_not_leak_the_last_resort_filter(tmp_path, monkeypatch):
    import logging
    from gee_animation.webapp import app as app_mod
    before = list(logging.lastResort.filters)

    def boom(*a, **k):
        raise RuntimeError("startup failed")

    monkeypatch.setattr(app_mod.logsafe, "install", boom)
    with pytest.raises(RuntimeError):
        with TestClient(_server(tmp_path)):
            pass
    assert list(logging.lastResort.filters) == before


def test_a_child_logger_record_that_falls_to_last_resort_is_redacted(tmp_path, monkeypatch):
    """With no handler on root (the uvicorn case) records go to logging.lastResort."""
    import io
    import logging
    stream = io.StringIO()
    monkeypatch.setattr(logging, "lastResort", logging.StreamHandler(stream))
    logging.lastResort.setLevel(logging.WARNING)
    root = logging.getLogger()
    saved, root.handlers = root.handlers, []
    app = _server(tmp_path)
    app.state.sessions.new("a@b.de", "LEAKY-REFRESH-TOKEN")
    try:
        with TestClient(app):
            logging.getLogger("some.library").warning("oops LEAKY-REFRESH-TOKEN")
        logging.getLogger("some.library").warning("after shutdown")
    finally:
        root.handlers = saved
        from gee_animation.webapp.logsafe import RedactingFilter
        for f in [f for f in root.filters if isinstance(f, RedactingFilter)]:
            root.removeFilter(f)
    out = stream.getvalue()
    assert "oops" in out and "LEAKY-REFRESH-TOKEN" not in out
    assert not logging.lastResort.filters                          # removed again on shutdown


# --- spawn ------------------------------------------------------------------------------

class _LimitedStdout:
    """Like asyncio.StreamReader: a line longer than `limit` raises ValueError."""

    def __init__(self, lines, limit):
        self._lines, self._limit = list(lines), limit

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        line = self._lines.pop(0)
        if len(line) > self._limit:
            raise ValueError("Separator is found, but chunk is longer than limit")
        return line


class _FakeProc:
    returncode = None
    pid = 4242

    def __init__(self, lines, limit):
        class _In:
            def write(self, d): pass
            async def drain(self): pass
            def close(self): pass
        self.stdin = _In()
        self.stdout = _LimitedStdout(lines, limit)

    async def wait(self):
        return 0


def _run_worker(monkeypatch, lines, payload=None):
    import asyncio
    from gee_animation.webapp import spawn as spawn_mod
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured.update(kwargs)
        return _FakeProc(lines, kwargs.get("limit", 2 ** 16))     # asyncio's default is 64 KiB

    monkeypatch.setattr(spawn_mod.asyncio, "create_subprocess_exec", fake_exec)
    seen = []
    proc = spawn_mod.WorkerProcess(payload or {"id": "j", "params": {}})
    killed = []
    monkeypatch.setattr(proc, "kill", lambda: killed.append(1))
    asyncio.run(proc.run(seen.append))
    return seen, captured, killed, proc


def test_a_done_event_longer_than_64_kib_is_delivered_intact(monkeypatch):
    import json
    frames = [f"/out/run/frame_{i:06d}.png" for i in range(5000)]       # ~150 KB line
    line = (json.dumps({"event": "done", "mp4": "/o.mp4", "frames": frames}) + "\n").encode()
    assert len(line) > 65536
    seen, _, killed, _ = _run_worker(monkeypatch, [line])
    assert seen == [{"event": "done", "mp4": "/o.mp4", "frames": frames}] and not killed


def test_a_line_beyond_even_the_raised_limit_fails_the_job_with_an_honest_message(monkeypatch):
    from gee_animation.webapp import spawn as spawn_mod
    huge = b"x" * (spawn_mod.STDOUT_LIMIT + 1) + b"\n"
    seen, _, killed, _ = _run_worker(monkeypatch, [huge])
    assert killed and len(seen) == 1 and seen[0]["event"] == "error"
    assert "too large" in seen[0]["message"] and "start" not in seen[0]["message"]


def test_the_worker_does_not_inherit_the_servers_secrets_and_gets_its_own_session(monkeypatch):
    for k, v in {"GEE_OAUTH_CLIENT_SECRET": "S1", "GEE_OAUTH_CLIENT_ID": "I",
                 "GEE_COOKIE_SECRET": "S2", "DNS_API_TOKEN": "S3", "GEE_OUTPUT_DIR": "/data/out",
                 "PATH": "/usr/bin"}.items():
        monkeypatch.setenv(k, v)
    _, captured, _, _ = _run_worker(monkeypatch, [])
    env = captured["env"]
    assert not [k for k in env if k.startswith("GEE_OAUTH_")] and "GEE_COOKIE_SECRET" not in env
    assert "DNS_API_TOKEN" not in env
    assert env["GEE_OUTPUT_DIR"] == "/data/out" and env["PATH"] == "/usr/bin"
    assert captured["start_new_session"] is True


def test_a_finished_job_no_longer_holds_the_refresh_token(monkeypatch):
    *_, proc = _run_worker(monkeypatch, [],
                           payload={"id": "j", "refresh_token": "RT-SECRET", "params": {}})
    assert "RT-SECRET" not in repr(vars(proc))


def test_kill_signals_the_whole_process_group_and_survives_an_already_gone_worker(monkeypatch):
    import signal
    from gee_animation.webapp import spawn as spawn_mod
    calls = []
    monkeypatch.setattr(spawn_mod.os, "getpgid", lambda pid: pid + 1)
    monkeypatch.setattr(spawn_mod.os, "killpg", lambda pg, sig: calls.append((pg, sig)))
    proc = spawn_mod.WorkerProcess({})
    proc._proc = _FakeProc([], 10)
    proc.kill()
    assert calls == [(4243, signal.SIGKILL)]

    def gone(pg, sig):
        raise ProcessLookupError
    monkeypatch.setattr(spawn_mod.os, "killpg", gone)
    proc.kill()                                                   # must not raise


# --- review round A -----------------------------------------------------------------------

def test_the_job_timeout_is_configurable_defaults_to_four_hours_and_reaches_the_queue(tmp_path):
    base = dict(client_id="c", client_secret="s", redirect_uri="https://x/cb",
                cookie_secret="k", output_root=Path(tmp_path))
    assert AppConfig(**base).job_timeout_s == 14400
    app = create_app(AppConfig(**base, job_timeout_s=1234), oauth_client=None,
                     spawn=lambda job, on_event: None)
    assert app.state.queue._timeout == 1234
    assert app.state.context.job_timeout_s == 1234     # what the GUI derives its poll bound from


def test_the_sweeper_also_reclaims_stale_entries_in_gradios_temp_dir(tmp_path, monkeypatch):
    import os
    temp = Path(tmp_path) / "cache" / "gradio-tmp"
    stale, fresh = temp / "stale", temp / "fresh"
    for d in (stale, fresh):
        d.mkdir(parents=True)
        (d / "x.png").write_bytes(b"png")
    old = _time.time() - 30 * 86400
    for p in (stale, stale / "x.png"):
        os.utime(p, (old, old))
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(temp))
    with TestClient(_server(Path(tmp_path) / "out")):
        pass
    assert not stale.exists() and fresh.exists() and temp.is_dir()


def test_without_a_dedicated_gradio_temp_dir_nothing_outside_the_output_root_is_swept(
        tmp_path, monkeypatch):
    """The single-operator path, now that GRADIO_TEMP_DIR is set by compose and not by
    the image: the default (the system temp dir's /gradio) may be shared with other
    processes, so the sweep must not go near it. Drives the real startup sweep."""
    from gee_animation.webapp import app as app_mod
    from gee_animation.webapp import storage as storage_mod
    monkeypatch.delenv("GRADIO_TEMP_DIR", raising=False)
    assert app_mod.gradio_temp_dir() is None
    calls = []
    monkeypatch.setattr(storage_mod, "sweep_temp",
                        lambda *a, **k: calls.append(a) or [])
    with TestClient(_server(tmp_path)):
        pass
    assert calls == []                              # not called at all

    temp = Path(tmp_path) / "gradio-tmp"
    temp.mkdir()
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(temp))
    with TestClient(_server(Path(tmp_path) / "out2")):
        pass
    assert [a[0] for a in calls] == [temp]          # and called once it is configured


def test_only_the_server_redirects_gradios_copies_into_the_cache_volume():
    """Not buildable here (no Docker daemon): pin by reading the two files.

    The image serves both modes and only the server has a sweeper, so GRADIO_TEMP_DIR
    belongs in the compose service, not in the Dockerfile: in the single-operator
    container a persistent directory nothing reclaims is worse than Gradio's /tmp
    default. The directory itself must still exist and be app-owned in the image.
    """
    import re
    root = Path(__file__).resolve().parent.parent
    text = (root / "Dockerfile").read_text()
    env = dict(re.findall(r"^\s+(\w+)=(\S+?)(?: \\)?$", text, re.M))
    assert "GRADIO_TEMP_DIR" not in env                 # single-user keeps /tmp
    cache = env["GEE_ANIMATION_CACHE_DIR"]
    compose = (root / "deploy" / "compose.yaml").read_text()
    configured = re.search(r"^\s+GRADIO_TEMP_DIR:\s*(\S+)\s*$", compose, re.M).group(1)
    assert configured.startswith(cache + "/")
    before_user = text.split("USER app")[0]
    assert f"mkdir -p /data/out {configured}" in before_user
    assert "chown -R app /data" in before_user


def test_a_failed_google_sign_in_is_a_friendly_page_not_a_500_and_is_reachable_anonymously(
        tmp_path):
    from authlib.integrations.base_client.errors import OAuthError

    class Denied:
        async def authorize_access_token(self, request):
            raise OAuthError(error="access_denied")

    cfg = AppConfig(client_id="c", client_secret="s", redirect_uri="https://x/cb",
                    cookie_secret="k", output_root=Path(tmp_path))
    client = TestClient(create_app(cfg, Denied(), lambda job, on_event: None),
                        follow_redirects=False)
    r = client.get("/oauth2/callback")
    assert r.status_code in (302, 303, 307) and r.headers["location"] == "/login-failed"
    assert "set-cookie" not in r.headers
    page = client.get("/login-failed", headers={"accept": "text/html"})
    assert page.status_code == 200 and "Sign-in failed" in page.text and "/login" in page.text


# --- review round B --------------------------------------------------------------------

class _BlockingWorker:
    """A worker that never finishes by itself, so a test can stop it instead."""
    procs: list = []

    def __init__(self, payload):
        self.payload = payload
        self.killed = False
        _BlockingWorker.procs.append(self)

    async def run(self, on_event):
        import asyncio
        on_event({"event": "progress", "message": "rendering"})
        await asyncio.sleep(3600)

    def kill(self):
        self.killed = True


def _blocking_server(tmp_path, max_concurrency=1):
    _BlockingWorker.procs = []
    cfg = AppConfig(client_id="cid", client_secret="sec", redirect_uri="https://x/cb",
                    cookie_secret="k", output_root=Path(tmp_path),
                    max_concurrency=max_concurrency)
    return create_app(cfg, oauth_client=None,
                      spawn=lambda job, on_event: _BlockingWorker(job))


def _wait_state(ctx, job_id, request, want, seconds=5.0):
    """Poll until the job reaches `want`. Bounded: a state that never arrives FAILS this
    test within `seconds` rather than hanging the suite."""
    deadline = _time.monotonic() + seconds
    state = {"state": "?"}
    while _time.monotonic() < deadline:
        state = ctx.job_status(request, job_id)
        if state["state"] == want:
            return state
        _time.sleep(0.02)
    raise AssertionError(f"job stayed {state['state']!r} and never reached {want!r}")


def _wait_queue_state(app, job_id, want, seconds=5.0):
    """Like `_wait_state`, but straight off the queue -- for a job whose session has
    been dropped, where `job_status` rightly refuses to answer. Bounded the same way."""
    deadline = _time.monotonic() + seconds
    while _time.monotonic() < deadline:
        if app.state.queue.status(job_id).state == want:
            return
        _time.sleep(0.02)
    raise AssertionError(f"job never reached {want!r}")


def test_a_user_can_stop_their_own_render_and_the_freed_slot_goes_to_the_next_user(
        tmp_path):
    """Spec acceptance criterion 5. Before this, `jobs.cancel` was unreachable: three
    mistaken 12-year submits held every slot for the full four-hour timeout."""
    app = _blocking_server(tmp_path, max_concurrency=1)
    sid_a = app.state.sessions.new("a@b.de", "RT-A")
    sid_b = app.state.sessions.new("b@b.de", "RT-B")
    with TestClient(app):
        ctx = app.state.context
        ra, rb = _req(app, sid_a), _req(app, sid_b)
        a_job = ctx.submit(ra, _good())
        _wait_state(ctx, a_job, ra, "running")
        b_job = ctx.submit(rb, _good())
        assert ctx.job_status(rb, b_job)["state"] == "queued"   # waiting for the slot

        assert ctx.cancel(ra) == 1
        _wait_state(ctx, a_job, ra, "cancelled")
        assert _BlockingWorker.procs[0].killed          # the process group was signalled
        _wait_state(ctx, b_job, rb, "running")          # and the slot was really freed
        assert ctx.cancel(rb) == 1


def test_user_b_cannot_stop_user_as_render(tmp_path):
    """The one that matters. `cancel` takes no job id at all: ownership comes from the
    session, so there is nothing for a client to name -- the AOI-path lesson."""
    app = _blocking_server(tmp_path, max_concurrency=2)
    sid_a = app.state.sessions.new("a@b.de", "RT-A")
    sid_b = app.state.sessions.new("b@b.de", "RT-B")
    with TestClient(app):
        ctx = app.state.context
        ra, rb = _req(app, sid_a), _req(app, sid_b)
        a_job = ctx.submit(ra, _good())
        _wait_state(ctx, a_job, ra, "running")

        assert ctx.cancel(rb) == 0                      # B has nothing of their own
        _time.sleep(0.1)
        assert ctx.job_status(ra, a_job)["state"] == "running"
        assert not _BlockingWorker.procs[0].killed
        assert ctx.cancel(ra) == 1


def test_stopping_without_a_session_is_refused_rather_than_cancelling_anything(tmp_path):
    app = _blocking_server(tmp_path, max_concurrency=1)
    sid = app.state.sessions.new("a@b.de", "RT-A")
    with TestClient(app):
        ctx = app.state.context
        job = ctx.submit(_req(app, sid), _good())
        _wait_state(ctx, job, _req(app, sid), "running")
        with pytest.raises(PermissionError):
            ctx.cancel(_req(app, "never-issued"))
        assert ctx.job_status(_req(app, sid), job)["state"] == "running"
        ctx.cancel(_req(app, sid))


def test_job_status_answers_only_the_owner_and_hides_an_unknown_id_the_same_way(tmp_path):
    """The only client-reachable identifier that indexes server state. An id belonging to
    someone else must look exactly like an id that does not exist."""
    from gee_animation.webapp.jobs import UnknownJobError
    app = _blocking_server(tmp_path, max_concurrency=1)
    sid_a = app.state.sessions.new("a@b.de", "RT-A")
    sid_b = app.state.sessions.new("b@b.de", "RT-B")
    with TestClient(app):
        ctx = app.state.context
        ra, rb = _req(app, sid_a), _req(app, sid_b)
        a_job = ctx.submit(ra, _good())
        _wait_state(ctx, a_job, ra, "running")
        with pytest.raises(UnknownJobError, match="not known"):
            ctx.job_status(rb, a_job)                   # B asking about A's render
        with pytest.raises(UnknownJobError, match="not known"):
            ctx.job_status(_req(app, "gone"), a_job)    # no session: fails closed
        with pytest.raises(UnknownJobError, match="not known"):
            ctx.job_status(ra, "never-issued")
        assert ctx.job_status(ra, a_job)["state"] == "running"   # the owner still sees it
        ctx.cancel(ra)


def test_a_stop_request_does_not_pin_the_gradio_thread_on_a_wedged_loop(tmp_path,
                                                                        monkeypatch):
    """Cancel crosses the same thread/loop boundary as submit, so it needs the same
    bound: without it a wedged loop holds a Gradio worker thread forever."""
    import asyncio
    import threading
    from gee_animation.webapp import app as app_mod
    monkeypatch.setattr(app_mod, "CANCEL_TIMEOUT_S", 0.2)
    app = _blocking_server(tmp_path, max_concurrency=1)
    sid = app.state.sessions.new("a@b.de", "RT-A")
    with TestClient(app):
        wedged = asyncio.new_event_loop()
        t = threading.Thread(target=wedged.run_forever, daemon=True)
        t.start()
        wedged.call_soon_threadsafe(_time.sleep, 1.0)
        app.state.loop = wedged
        try:
            started = _time.monotonic()
            with pytest.raises(RuntimeError, match="stop request"):
                app.state.context.cancel(_req(app, sid))
            assert _time.monotonic() - started < 0.9     # it did not wait out the block
        finally:
            wedged.call_soon_threadsafe(wedged.stop)
            t.join(timeout=3)
            wedged.close()


def test_a_cancelled_runs_directory_is_reclaimable_by_the_sweep(tmp_path, monkeypatch):
    """A cancelled render leaves a directory holding only the AOI copy. Round A made the
    sweep count any immediate child of a user directory as a run; confirm that covers
    this case, or a stopped render would sit in /data/out forever."""
    from gee_animation.webapp import storage
    cache = Path(tmp_path) / "gradio_cache"
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(cache))
    upload = cache / "u" / "aoi.geojson"
    upload.parent.mkdir(parents=True)
    upload.write_text("the-aoi")
    root = Path(tmp_path) / "out"
    app = _blocking_server(root, max_concurrency=1)
    sid = app.state.sessions.new("a@b.de", "RT-A")
    with TestClient(app):
        ctx = app.state.context
        request = _req(app, sid)
        job = ctx.submit(request, _good(aoi_path=str(upload)))
        _wait_state(ctx, job, request, "running")
        assert ctx.cancel(request) == 1
        _wait_state(ctx, job, request, "cancelled")
    run_dir = Path(_BlockingWorker.procs[0].payload["params"]["out_dir"])
    assert run_dir.is_dir() and not list(run_dir.glob("*.mp4"))     # no media at all
    assert storage.sweep(root, max_age_days=0, now=_time.time() + 10) == [run_dir]
    assert not run_dir.exists()


def test_an_open_route_is_matched_exactly_so_a_lookalike_path_still_needs_a_session(
        tmp_path):
    """`/login-failed` was reachable only because it starts with `/login`. A prefix rule
    exempts every future lookalike from the authentication redirect."""
    client = TestClient(_app(tmp_path), follow_redirects=False)
    html = {"accept": "text/html"}
    # The assertion that fails on the prefix rule: a lookalike is NOT exempt.
    for lookalike in ("/logout-everyone", "/login-as-admin", "/oauth2/callback-debug"):
        r = client.get(lookalike, headers=html)
        assert r.status_code in (302, 303, 307), lookalike
        assert r.headers["location"] == "/login", lookalike
    # The open routes themselves are still never sent to /login (this app stands in a
    # dev `oauth_client=None`, so an open path reaches the router -- 200 or 404 -- and
    # what matters is that the middleware let it through).
    for open_path in ("/login", "/logout", "/login-failed", "/oauth2/callback"):
        assert client.get(open_path, headers=html).headers.get("location") != "/login", \
            open_path


# --- review round C --------------------------------------------------------------------

def test_a_dropped_sessions_token_is_still_redacted_while_its_job_is_in_flight(tmp_path):
    """The window rounds B's session expiry opened: before it, a session was never
    dropped, so every live token was always in the redactor's list. Now a logout, an
    expiry or a re-sign-in can drop a session while a render it started still holds its
    token in this process -- and a sessions-only list would stop redacting it."""
    import io
    import logging

    from gee_animation.webapp.logsafe import RedactingFilter
    app = _blocking_server(tmp_path, max_concurrency=1)
    sid = app.state.sessions.new("a@b.de", "SECRET-REFRESH-TOKEN")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    uv = logging.getLogger("uvicorn.error")
    uv.addHandler(handler)
    try:
        with TestClient(app):
            ctx = app.state.context
            request = _req(app, sid)
            job = ctx.submit(request, _good())
            _wait_state(ctx, job, request, "running")
            app.state.sessions.drop(sid)                 # logout / expiry / re-sign-in
            assert app.state.sessions.all() == []        # the session list is now empty
            assert app.state.queue.live_secrets() == ["SECRET-REFRESH-TOKEN"]
            uv.error("boom SECRET-REFRESH-TOKEN")
            held = stream.getvalue()

            # The session is gone, so `ctx.cancel` would (rightly) refuse: go to the
            # queue directly, which is what a logged-out user's job needs anyway.
            import asyncio
            future = asyncio.run_coroutine_threadsafe(
                app.state.queue.cancel_for("a@b.de"), app.state.loop)
            assert future.result(timeout=5) == 1
            _wait_queue_state(app, job, "cancelled")
            # The state flips in `cancel`; the token is released a tick later, when the
            # task unwinds -- deliberately that order, since the handover may be in
            # progress at the instant of the kill. Bounded, so a token that is never
            # released fails this test rather than hanging it.
            deadline = _time.monotonic() + 5
            while app.state.queue.live_secrets() and _time.monotonic() < deadline:
                _time.sleep(0.02)
            assert app.state.queue.live_secrets() == []  # no longer held, no longer listed
    finally:
        uv.removeHandler(handler)
        for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            for f in [f for f in lg.filters if isinstance(f, RedactingFilter)]:
                lg.removeFilter(f)
            for h in lg.handlers:
                for f in [f for f in h.filters if isinstance(f, RedactingFilter)]:
                    h.removeFilter(f)
    assert "boom" in held and "SECRET-REFRESH-TOKEN" not in held


def test_the_installed_redactor_covers_both_sessions_and_jobs_in_flight(tmp_path):
    """Neither source may be dropped: a signed-in user who has not submitted anything
    has no job, and a job whose session is gone has no session. Drives the filter the
    server really installs, not a list comprehension of the test's own."""
    import asyncio
    import io
    import logging

    from gee_animation.webapp.logsafe import RedactingFilter
    app = _blocking_server(tmp_path, max_concurrency=2)
    sid_a = app.state.sessions.new("a@b.de", "TOKEN-A-IN-FLIGHT")
    app.state.sessions.new("b@b.de", "TOKEN-B-SESSION-ONLY")   # never submitted anything
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    uv = logging.getLogger("uvicorn.error")
    uv.addHandler(handler)
    try:
        with TestClient(app):
            ctx = app.state.context
            request = _req(app, sid_a)
            job = ctx.submit(request, _good())
            _wait_state(ctx, job, request, "running")
            app.state.sessions.drop(sid_a)                     # A's session is gone
            uv.error("one TOKEN-A-IN-FLIGHT two TOKEN-B-SESSION-ONLY three")
            logged = stream.getvalue()
            asyncio.run_coroutine_threadsafe(
                app.state.queue.cancel_for("a@b.de"), app.state.loop).result(timeout=5)
            _wait_queue_state(app, job, "cancelled")
    finally:
        uv.removeHandler(handler)
        for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            for f in [f for f in lg.filters if isinstance(f, RedactingFilter)]:
                lg.removeFilter(f)
            for h in lg.handlers:
                for f in [f for f in h.filters if isinstance(f, RedactingFilter)]:
                    h.removeFilter(f)
    assert "one" in logged and "two" in logged and "three" in logged
    assert "TOKEN-A-IN-FLIGHT" not in logged      # held by the job, session dropped
    assert "TOKEN-B-SESSION-ONLY" not in logged   # held by a session, never a job


def test_a_finished_job_is_forgotten_once_its_record_has_aged_out(tmp_path):
    """Finding 11: `Job.result` holds the whole done event (every frame path), and
    nothing ever pruned. A pruned id reads as unknown, not as a traceback."""
    from gee_animation.webapp.jobs import UnknownJobError
    app = _server(tmp_path)
    sid = app.state.sessions.new("a@b.de", "RT-A")
    with TestClient(app):
        ctx = app.state.context
        request = _req(app, sid)
        first = ctx.submit(request, _good())
        _wait_done(ctx, first, request)
        app.state.queue._retain = -1                  # as if an hour had passed
        second = ctx.submit(request, _good())         # submit is where pruning happens
        _wait_done(ctx, second, request)
        with pytest.raises(UnknownJobError, match="not known"):
            ctx.job_status(request, first)
        assert first not in app.state.queue._jobs and first not in app.state.queue._tasks


# --- review round D: the submit timeout race -------------------------------------------

import concurrent.futures


class _FinishedJustTooLate:
    """A future that completes in the instant the caller gives up on it.

    `concurrent.futures.Future.cancel()` returns False once the result is already set,
    and `run_coroutine_threadsafe` sets it from the loop — so a loop that recovers
    between our `result(timeout)` expiring and our `cancel()` call leaves a real job
    behind. The window is microseconds wide in real time; this makes it deterministic
    while keeping the documented contract of the class it stands in for.
    """

    def __init__(self, real, grace_ok=True):
        self._real, self._grace_ok, self._first = real, grace_ok, True

    def result(self, timeout=None):
        if self._first:                      # the caller's first, timed-out attempt
            self._first = False
            raise concurrent.futures.TimeoutError
        if not self._grace_ok:               # a genuinely wedged loop
            raise concurrent.futures.TimeoutError
        return self._real.result(timeout)

    def cancel(self):
        return False                         # too late: the job exists


def _late_loop_handoff(monkeypatch, grace_ok=True, only_first=True):
    """Make the FIRST hand-off to the loop finish just too late, the rest normal."""
    from gee_animation.webapp import app as app_mod
    real_run = app_mod.asyncio.run_coroutine_threadsafe
    state = {"wrapped": 0}

    def handoff(coro, loop):
        future = real_run(coro, loop)
        state["wrapped"] += 1
        if only_first and state["wrapped"] > 1:
            return future                    # the cleanup hop behaves normally
        return _FinishedJustTooLate(future, grace_ok=grace_ok)

    monkeypatch.setattr(app_mod, "_run_on_loop", handoff)
    return state


def test_a_submit_that_lands_just_after_the_timeout_returns_the_id_not_busy(
        tmp_path, monkeypatch):
    """The bug: the user was told "the server is busy" while the queue went on to spawn
    a worker whose id never reached the browser — a render billing their Earth Engine
    quota and holding a slot for four hours, invisible and uncancellable."""
    _late_loop_handoff(monkeypatch)
    app = _blocking_server(tmp_path, max_concurrency=1)
    sid = app.state.sessions.new("a@b.de", "RT-A")
    with TestClient(app):
        ctx = app.state.context
        request = _req(app, sid)
        job_id = ctx.submit(request, _good())            # no RuntimeError at all
        state = _wait_state(ctx, job_id, request, "running")
        assert state["state"] == "running"               # the id the user got is real
        assert ctx.cancel(request) == 1                  # and it is theirs to cancel


def test_when_the_grace_also_expires_submit_fails_and_leaves_no_job_running(
        tmp_path, monkeypatch):
    """The fallback: if the id never arrives we do report failure — but nothing of this
    user's may be left running, or it would run invisibly."""
    _late_loop_handoff(monkeypatch, grace_ok=False)
    app = _blocking_server(tmp_path, max_concurrency=1)
    sid = app.state.sessions.new("a@b.de", "RT-A")
    with TestClient(app):
        ctx = app.state.context
        request = _req(app, sid)
        with pytest.raises(RuntimeError, match="busy"):
            ctx.submit(request, _good())

        def mine():
            return [j for j in app.state.queue._jobs.values() if j.email == "a@b.de"]

        # The loop DID create a job -- that is the race. Wait for it to appear first,
        # or an empty queue a moment before it is created would pass this vacuously.
        deadline = _time.monotonic() + 5
        while not mine() and _time.monotonic() < deadline:
            _time.sleep(0.02)
        assert mine(), "no job was ever created: this test would prove nothing"
        # ...and the fallback must have left none of them able to run.
        while any(j.state in ("queued", "running") for j in mine()) \
                and _time.monotonic() < deadline:
            _time.sleep(0.02)
        assert [j.state for j in mine()] == ["cancelled"]


def test_the_user_either_gets_an_id_or_no_job_exists_when_the_loop_stalls(
        tmp_path, monkeypatch):
    """The reviewer's own repro, with the real clock: a loop blocked for longer than
    SUBMIT_TIMEOUT_S. Whichever side of the race we land on, "busy" and a live job must
    never both be true."""
    import threading

    from gee_animation.webapp import app as app_mod
    monkeypatch.setattr(app_mod, "SUBMIT_TIMEOUT_S", 0.3)
    monkeypatch.setattr(app_mod, "SUBMIT_GRACE_S", 1.0)
    app = _blocking_server(tmp_path, max_concurrency=1)
    sid = app.state.sessions.new("a@b.de", "RT-A")
    with TestClient(app):
        ctx = app.state.context
        request = _req(app, sid)
        app.state.loop.call_soon_threadsafe(_time.sleep, 0.8)   # stall, then recover
        try:
            job_id = ctx.submit(request, _good())
        except RuntimeError as exc:
            assert "busy" in str(exc)
            # Let the loop drain before judging: a sentinel scheduled now runs AFTER the
            # stall and after the submit hand-off already queued ahead of it, so once it
            # fires, whatever the race was going to do has happened. Bounded.
            drained = threading.Event()
            app.state.loop.call_soon_threadsafe(drained.set)
            assert drained.wait(10), "the event loop never recovered"
            _time.sleep(0.1)                     # let a spawned job reach "running"
            alive = [j.state for j in app.state.queue._jobs.values()
                     if j.state in ("queued", "running")]
            assert alive == [], "told the user 'busy' and left a job running"
        else:
            assert _wait_state(ctx, job_id, request, "running")["state"] == "running"
            ctx.cancel(request)


def test_a_stop_that_lands_just_after_the_timeout_says_it_is_under_way(tmp_path,
                                                                       monkeypatch):
    """Same race on the cancel path, where a late cancellation is benign: it does what
    the user asked, so the message must not claim it failed."""
    _late_loop_handoff(monkeypatch, only_first=False)
    app = _blocking_server(tmp_path, max_concurrency=1)
    sid = app.state.sessions.new("a@b.de", "RT-A")
    with TestClient(app):
        ctx = app.state.context
        request = _req(app, sid)
        job = ctx.submit(request, _good())
        _wait_queue_state(app, job, "running")
        with pytest.raises(RuntimeError, match="being stopped"):
            ctx.cancel(request)
        _wait_queue_state(app, job, "cancelled")      # it really did happen


def test_the_compose_healthcheck_hits_a_route_that_answers_without_a_session(tmp_path):
    """A healthcheck on "/" or /login would pass on a redirect. Ties the deployment file
    to the middleware's open-route set and to a live anonymous request."""
    import re

    from authlib.integrations.base_client.errors import OAuthError
    from gee_animation.webapp.app import _LoginRedirect
    compose = (Path(__file__).resolve().parent.parent / "deploy" / "compose.yaml").read_text()
    url = re.search(r"urlopen\('(http://[^']+)'", compose).group(1)
    path = url.split("7860", 1)[1]
    assert path in _LoginRedirect._OPEN             # not exempt only by luck

    class Denied:
        async def authorize_access_token(self, request):
            raise OAuthError(error="access_denied")

    cfg = AppConfig(client_id="c", client_secret="s", redirect_uri="https://x/cb",
                    cookie_secret="k", output_root=Path(tmp_path))
    client = TestClient(create_app(cfg, Denied(), lambda job, on_event: None),
                        follow_redirects=False)
    assert client.get(path).status_code == 200      # 200, not a redirect
    # A memory limit is documented but deliberately not set: too low OOM-kills a render.
    mem = [line for line in compose.splitlines() if "mem_limit:" in line]
    assert mem and all(line.lstrip().startswith("#") for line in mem)
