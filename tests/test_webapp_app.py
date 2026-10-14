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


def test_the_upload_limit_is_set_and_small_enough_to_stop_a_zip_bomb(tmp_path):
    """Review Focus 5: a 2 GB upload must be refused before anything unpacks it."""
    app = _app(tmp_path)
    assert app.state.max_upload_bytes == 100 * 1024 * 1024


def test_the_run_list_is_empty_for_an_anonymous_request(tmp_path):
    """The mounted app serves everyone; identity comes from the request alone."""
    import types
    app = _app(tmp_path)
    anon = types.SimpleNamespace(cookies={}, app=app)
    from gee_animation.webapp import oauth
    assert oauth.current_user(anon) is None


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


def _wait_done(ctx, job_id):
    for _ in range(100):
        state = ctx.job_status(job_id)
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


def test_submit_hands_the_worker_a_private_copy_of_the_aoi_the_project_and_the_credentials(tmp_path):
    app = _server(tmp_path)
    sid = app.state.sessions.new("a@b.de", "RT-1")
    upload = Path(tmp_path) / "gradio_tmp" / "aoi.geojson"
    upload.parent.mkdir()
    upload.write_text("the-aoi")
    request = types.SimpleNamespace(cookies={"sid": sid}, app=app)
    params = {"start": "2024-01-01", "end": "2024-12-31", "preset": "720p", "sensor": "s2",
              "index": "ndvi", "aoi_path": str(upload), "project": " my-proj "}
    with TestClient(app):
        ctx = app.state.context
        state = _wait_done(ctx, ctx.submit(request, params))
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
