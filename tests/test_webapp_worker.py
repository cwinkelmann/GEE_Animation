import io
import json
import types

import pytest

from gee_animation.webapp import worker


def _job(**over):
    job = {"refresh_token": "r", "client_id": "cid", "client_secret": "sec",
           "project": "user-project", "params": {"sensor": "sentinel2", "index": "ndvi"}}
    job.update(over)
    return json.dumps(job)


def _fake_ee(captured):
    return types.SimpleNamespace(
        Initialize=lambda credentials=None, project=None: captured.update(
            creds=credentials, project=project))


def test_worker_initialises_ee_with_the_users_project(tmp_path):
    captured, out = {}, io.StringIO()
    runner = lambda **kw: ("/out/x.mp4", None, [], None, "ok", [])
    rc = worker.main(io.StringIO(_job()), out, ee_module=_fake_ee(captured), runner=runner)
    assert rc == 0
    assert captured["project"] == "user-project"
    assert captured["creds"].refresh_token == "r"


def test_worker_does_not_let_run_animation_re_authenticate():
    """gui.run_animation calls deps.init(project); EE is already initialised here,
    so the worker must inject a no-op init or the user's credentials are replaced."""
    seen = {}
    def runner(**kw):
        seen["deps"] = kw["deps"]
        kw["deps"].init("ignored-project")       # must not raise, must not re-auth
        return ("/out/x.mp4", None, [], None, "ok", [])
    out = io.StringIO()
    worker.main(io.StringIO(_job()), out, ee_module=_fake_ee({}), runner=runner)
    assert seen["deps"].init("anything") is None


def test_the_jobs_project_wins_over_one_in_the_params():
    """gui's params always carry `project`; passing both would be a TypeError."""
    seen = {}
    def runner(**kw):
        seen.update(kw)
        return ("/out/x.mp4", None, [], None, "ok", [])
    job = _job(params={"sensor": "sentinel2", "project": "stale-from-the-form"})
    worker.main(io.StringIO(job), io.StringIO(), ee_module=_fake_ee({}), runner=runner)
    assert seen["project"] == "user-project"


def test_worker_emits_one_done_event_with_the_media_path():
    out = io.StringIO()
    runner = lambda **kw: ("/out/x.mp4", "/out/x.gif", ["/out/f.png"], None, "ok", [])
    worker.main(io.StringIO(_job()), out, ee_module=_fake_ee({}), runner=runner)
    events = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    done = [e for e in events if e["event"] == "done"]
    assert len(done) == 1 and done[0]["mp4"] == "/out/x.mp4" and done[0]["status"] == "ok"


def test_a_revoked_token_becomes_an_actionable_error_event():
    """Review Focus 2: the token dies mid-render; the worker must say so and exit 1."""
    def runner(**kw):
        raise RuntimeError("invalid_grant: Token has been expired or revoked.")
    out = io.StringIO()
    rc = worker.main(io.StringIO(_job()), out, ee_module=_fake_ee({}), runner=runner)
    events = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    err = [e for e in events if e["event"] == "error"]
    assert rc == 1 and len(err) == 1
    assert "sign in again" in err[0]["message"].lower()


def test_the_error_event_never_echoes_the_token():
    # The exception text really contains the token, so this fails without the scrub.
    def runner(**kw):
        raise RuntimeError("bad credential r-SECRET")
    out = io.StringIO()
    worker.main(io.StringIO(_job(refresh_token="r-SECRET")), out,
                ee_module=_fake_ee({}), runner=runner)
    assert "r-SECRET" not in out.getvalue()
    assert "bad credential" in out.getvalue()


def test_the_series_travels_in_the_done_event_as_strict_json():
    """The time-series chart was blank in server mode because `series` was dropped here.
    numpy scalars, NaN and None must all survive a strict JSON round trip."""
    import numpy as np
    series = [("2022-05", np.float64(0.5), 0.25), ("2022-06", None, float("nan")),
              ("2022-07", float("inf"), np.float32(0.5))]
    runner = lambda **kw: ("/out/x.mp4", None, [], None, "ok", series)
    out = io.StringIO()
    worker.main(io.StringIO(_job()), out, ee_module=_fake_ee({}), runner=runner)

    def strict(token):
        raise AssertionError(f"non-JSON constant {token} on the wire")

    lines = [l for l in out.getvalue().splitlines() if l.strip()]
    events = [json.loads(l, parse_constant=strict) for l in lines]
    done = next(e for e in events if e["event"] == "done")
    assert done["series"] == [["2022-05", 0.5, 0.25], ["2022-06", None, None],
                              ["2022-07", None, 0.5]]
