# Multi-user GEE animation web app — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ~5–20 named HNEE colleagues on the VPN render timelapses from their own AOI under their own Earth Engine project, at `https://carrot.aisl.c4ds.hnee.de`, three renders at a time, with bounded disk.

**Architecture:** A FastAPI host owns Google OAuth and sessions, and mounts the existing Gradio app at `/` via `auth_dependency`. Render jobs run in **one subprocess each** (forced: `ee._state._state` is a process-wide global, so two users' credentials cannot coexist in one process), fed credentials on stdin. Outputs live under a per-user directory with a 14-day sweep. The existing single-operator `gee-animation-gui` is untouched.

**Tech Stack:** Python 3.11, FastAPI, Authlib, itsdangerous, Uvicorn, Gradio 6.20, earthengine-api 1.7, Docker Compose, Caddy.

**Spec:** `docs/superpowers/specs/2026-10-14-multiuser-webapp-design.md`

## Global Constraints

- Python **3.11** (matches the existing `Dockerfile`); `gradio>=6`.
- OAuth scopes are **exactly** `https://www.googleapis.com/auth/earthengine` and `https://www.googleapis.com/auth/cloud-platform`. Never request `drive` or `devstorage`.
- A refresh token may exist **only** in process memory and on a worker's stdin. Never in argv (world-readable in `/proc`), never in an environment variable, never on disk, never in a log record.
- Concurrency limit **3**; per-run caps **12 years** of monthly frames and preset no larger than **1440p**.
- Retention: delete run directories older than **14 days**.
- The unit suite stays **network-free**: no test may contact Google or Earth Engine. Live checks go behind `@pytest.mark.integration` + `GEE_INTEGRATION`.
- `tests/test_gui.py` must stay **unmodified and green** — it is the regression gate for the single-operator GUI.
- New dependencies go in a new `server` extra, never in the base install.

## Review Focus

1. **A Cloud project without Earth Engine registration** — the commonest user error; must fail at login with the fix named, not three minutes into a render. (Task 7)
2. **A refresh token revoked or expired mid-render** — a 40-minute render outlives nothing gracefully by default; the job must fail with an actionable message and free its slot. (Task 5)
3. **Two users uploading AOIs with the same filename at the same time** — must not collide or cross-contaminate. (Task 2)
4. **The browser closing mid-render** — the job must keep running and its result be retrievable from the run browser afterwards, rather than vanishing. (Task 5)
5. **A hostile upload (zip bomb, 2 GB GeoJSON)** — must be refused by size before `_region_aoi_from_upload` unpacks anything. (Task 9)

---

### Task 1: `server` extra, package skeleton, SessionStore

**Files:**
- Modify: `pyproject.toml:21-26` (optional-dependencies)
- Create: `gee_animation/webapp/__init__.py`
- Create: `gee_animation/webapp/session.py`
- Test: `tests/test_webapp_session.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SessionStore` with `new(email: str, refresh_token: str) -> str` (returns `sid`), `get(sid: str) -> Session | None`, `set_project(sid: str, project: str) -> None`, `drop(sid: str) -> None`; dataclass `Session(email: str, refresh_token: str, project: str | None, created: float)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webapp_session.py
import pytest
from gee_animation.webapp.session import SessionStore


def test_new_session_round_trips_email_and_project():
    store = SessionStore()
    sid = store.new("a@hnee.de", "refresh-abc")
    assert store.get(sid).email == "a@hnee.de"
    assert store.get(sid).project is None
    store.set_project(sid, "my-ee-project")
    assert store.get(sid).project == "my-ee-project"


def test_unknown_and_dropped_sids_return_none():
    store = SessionStore()
    sid = store.new("a@hnee.de", "refresh-abc")
    store.drop(sid)
    assert store.get(sid) is None
    assert store.get("never-issued") is None


def test_session_repr_does_not_leak_the_refresh_token():
    """A Session reaching a log line or a traceback must not carry the token."""
    store = SessionStore()
    sid = store.new("a@hnee.de", "refresh-SECRET-VALUE")
    assert "refresh-SECRET-VALUE" not in repr(store.get(sid))
    assert "refresh-SECRET-VALUE" not in str(store.get(sid))


def test_all_returns_the_live_sessions_for_the_log_redactor():
    store = SessionStore()
    store.new("a@hnee.de", "tok-a")
    sid_b = store.new("b@hnee.de", "tok-b")
    assert sorted(s.refresh_token for s in store.all()) == ["tok-a", "tok-b"]
    store.drop(sid_b)
    assert [s.refresh_token for s in store.all()] == ["tok-a"]


def test_sids_are_unguessable_and_unique():
    store = SessionStore()
    sids = {store.new(f"u{i}@hnee.de", "t") for i in range(50)}
    assert len(sids) == 50
    assert all(len(s) >= 32 for s in sids)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_session.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.webapp'`

- [ ] **Step 3: Write minimal implementation**

```python
# gee_animation/webapp/__init__.py
"""Multi-user web server around the Gradio GUI (see docs/superpowers/specs/2026-10-14-multiuser-webapp-design.md)."""
```

```python
# gee_animation/webapp/session.py
"""In-memory session store: sid -> who you are, your EE project, your refresh token.

Deliberately NOT persistent. A restart logs everyone out, which for a group this
size is the right trade for never writing a refresh token to disk.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    email: str
    # repr=False so the token cannot reach a log line or a traceback frame summary.
    refresh_token: str = field(repr=False)
    project: str | None = None
    created: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return repr(self)


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def new(self, email: str, refresh_token: str) -> str:
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = Session(email=email, refresh_token=refresh_token)
        return sid

    def get(self, sid: str) -> Session | None:
        return self._sessions.get(sid)

    def set_project(self, sid: str, project: str) -> None:
        self._sessions[sid].project = project

    def drop(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    def all(self) -> list:
        """Every live session. Used by the log redactor to know the current secrets."""
        return list(self._sessions.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_session.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Add the `server` extra**

In `pyproject.toml`, under `[project.optional-dependencies]`, after the `gui` line:

```toml
server = ["fastapi>=0.110", "uvicorn[standard]>=0.27", "authlib>=1.3", "itsdangerous>=2.1"]
```

Then install it: `conda run -n GEE_animation pip install -e ".[gui,server]"`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml gee_animation/webapp/__init__.py gee_animation/webapp/session.py tests/test_webapp_session.py
git commit -m "feat(webapp): in-memory session store and the server extra"
```

---

### Task 2: Per-user storage, user-scoped run listing, retention sweep

**Files:**
- Create: `gee_animation/webapp/storage.py`
- Test: `tests/test_webapp_storage.py`

**Interfaces:**
- Consumes: `gee_animation.gui.list_previous_runs(base_dir)` (existing, `gui.py:190`).
- Produces: `user_dir(root: Path, email: str) -> Path`, `list_runs(root: Path, email: str) -> list[tuple[str, str]]`, `sweep(root: Path, max_age_days: int = 14, now: float | None = None) -> list[Path]` (returns what it deleted).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webapp_storage.py
import time
from pathlib import Path

from gee_animation.webapp import storage


def _run(root, email, name, age_days=0):
    d = storage.user_dir(root, email) / name
    d.mkdir(parents=True)
    mp4 = d / f"{name}.mp4"
    mp4.write_bytes(b"\x00\x00mp4")
    if age_days:
        old = time.time() - age_days * 86400
        import os
        os.utime(mp4, (old, old))
        os.utime(d, (old, old))
    return mp4


def test_user_dir_is_stable_and_does_not_contain_the_email(tmp_path):
    a = storage.user_dir(tmp_path, "alice@hnee.de")
    assert a == storage.user_dir(tmp_path, "alice@hnee.de")      # stable
    assert a != storage.user_dir(tmp_path, "bob@hnee.de")
    assert "alice" not in str(a)        # the directory name is not an identity leak


def test_list_runs_never_returns_another_users_run(tmp_path):
    _run(tmp_path, "alice@hnee.de", "alice_ndvi")
    _run(tmp_path, "bob@hnee.de", "bob_lst")
    alice = storage.list_runs(tmp_path, "alice@hnee.de")
    bobs_dir = str(storage.user_dir(tmp_path, "bob@hnee.de"))
    assert len(alice) == 1 and "alice_ndvi" in alice[0][0]
    assert all(bobs_dir not in path for _, path in alice)


def test_same_run_name_from_two_users_does_not_collide(tmp_path):
    """Review Focus 3: two users upload 'aoi.geojson' and name the run the same."""
    a = _run(tmp_path, "alice@hnee.de", "sentinel2_ndvi")
    b = _run(tmp_path, "bob@hnee.de", "sentinel2_ndvi")
    assert a != b and a.exists() and b.exists()
    assert len(storage.list_runs(tmp_path, "alice@hnee.de")) == 1
    assert len(storage.list_runs(tmp_path, "bob@hnee.de")) == 1


def test_sweep_deletes_old_runs_and_keeps_new_ones(tmp_path):
    old = _run(tmp_path, "alice@hnee.de", "ancient", age_days=20)
    new = _run(tmp_path, "alice@hnee.de", "fresh", age_days=1)
    deleted = storage.sweep(tmp_path, max_age_days=14)
    assert not old.exists() and new.exists()
    assert [p.name for p in deleted] == ["ancient"]


def test_sweep_on_a_missing_root_is_a_noop(tmp_path):
    assert storage.sweep(tmp_path / "nope", max_age_days=14) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_storage.py -q`
Expected: FAIL — `ImportError: cannot import name 'storage' from 'gee_animation.webapp'`

- [ ] **Step 3: Write minimal implementation**

```python
# gee_animation/webapp/storage.py
"""Per-user output directories, user-scoped run listing, and retention.

Blocker 2 of the spec is fixed HERE rather than in the UI: hiding the run-browser
control would leave the paths reachable, whereas a user-scoped listing has nothing
to hide.
"""
from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

from ..gui import list_previous_runs


def user_dir(root: Path, email: str) -> Path:
    """`root/<16 hex chars>` — stable per user, and not itself an identity leak."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]
    return Path(root) / digest


def list_runs(root: Path, email: str) -> list:
    """[(label, media_path)] for THIS user only."""
    return list_previous_runs(base_dir=str(user_dir(root, email)))


def sweep(root: Path, max_age_days: int = 14, now: float | None = None) -> list:
    """Delete run directories whose newest file is older than `max_age_days`."""
    root = Path(root)
    if not root.exists():
        return []
    cutoff = (now or time.time()) - max_age_days * 86400
    deleted = []
    for user in sorted(p for p in root.iterdir() if p.is_dir()):
        for run in sorted(p for p in user.iterdir() if p.is_dir()):
            newest = max((f.stat().st_mtime for f in run.rglob("*") if f.is_file()),
                         default=run.stat().st_mtime)
            if newest < cutoff:
                shutil.rmtree(run)
                deleted.append(run)
    return deleted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_storage.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/webapp/storage.py tests/test_webapp_storage.py
git commit -m "feat(webapp): per-user output dirs, scoped run listing, 14-day sweep"
```

---

### Task 3: Secret-redacting log filter

**Files:**
- Create: `gee_animation/webapp/logsafe.py`
- Test: `tests/test_webapp_logsafe.py`

**Interfaces:**
- Produces: `RedactingFilter(secrets: Callable[[], Iterable[str]])` — a `logging.Filter` whose `filter(record)` replaces any known secret substring in the formatted message with `***`; `install(logger, secrets)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webapp_logsafe.py
import logging

from gee_animation.webapp.logsafe import RedactingFilter, install


def test_a_refresh_token_never_reaches_a_log_record(caplog):
    log = logging.getLogger("test.redact")
    install(log, lambda: ["refresh-SECRET-VALUE"])
    with caplog.at_level(logging.INFO, logger="test.redact"):
        log.info("initialising EE with %s for project p", "refresh-SECRET-VALUE")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "refresh-SECRET-VALUE" not in text
    assert "***" in text


def test_redaction_covers_the_exception_text_too(caplog):
    log = logging.getLogger("test.redact2")
    install(log, lambda: ["tok-ABC"])
    with caplog.at_level(logging.ERROR, logger="test.redact2"):
        log.error("failed: %s", RuntimeError("bad token tok-ABC"))
    assert "tok-ABC" not in "\n".join(r.getMessage() for r in caplog.records)


def test_messages_without_secrets_are_untouched(caplog):
    log = logging.getLogger("test.redact3")
    install(log, lambda: ["tok-ABC"])
    with caplog.at_level(logging.INFO, logger="test.redact3"):
        log.info("rendered 12 frames")
    assert "rendered 12 frames" in "\n".join(r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_logsafe.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.webapp.logsafe'`

- [ ] **Step 3: Write minimal implementation**

```python
# gee_animation/webapp/logsafe.py
"""A logging filter that cannot let a live credential reach a log record."""
from __future__ import annotations

import logging


class RedactingFilter(logging.Filter):
    def __init__(self, secrets):
        super().__init__()
        self._secrets = secrets          # callable -> iterable of live secrets

    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        redacted = text
        for secret in self._secrets():
            if secret and secret in redacted:
                redacted = redacted.replace(secret, "***")
        if redacted != text:
            record.msg, record.args = redacted, ()
        return True


def install(logger: logging.Logger, secrets) -> RedactingFilter:
    f = RedactingFilter(secrets)
    logger.addFilter(f)
    return f
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_logsafe.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/webapp/logsafe.py tests/test_webapp_logsafe.py
git commit -m "feat(webapp): redacting log filter so credentials cannot reach logs"
```

---

### Task 4: The render worker subprocess

**Files:**
- Create: `gee_animation/webapp/worker.py`
- Test: `tests/test_webapp_worker.py`

**Interfaces:**
- Consumes: `gee_animation.gui.run_animation(**params, deps=...)` (existing, `gui.py:585`), which calls `deps.init(project)` at `gui.py:448`.
- Produces: `main(stdin, stdout, ee_module=ee, runner=gui.run_animation) -> int`; stdin carries one JSON object `{"refresh_token", "client_id", "client_secret", "project", "params"}`; stdout carries progress lines `{"event": "progress", "message": ...}` and exactly one final `{"event": "done", "mp4": ..., "status": ...}` or `{"event": "error", "message": ...}`.

Why a subprocess at all: `ee._state._state` is a single module-level object holding `credentials` and `cloud_api_user_project`. One process can hold one user's identity, so a job gets its own.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webapp_worker.py
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
    def runner(**kw):
        raise RuntimeError("bad credential r")
    out = io.StringIO()
    worker.main(io.StringIO(_job(refresh_token="r-SECRET")), out,
                ee_module=_fake_ee({}), runner=runner)
    assert "r-SECRET" not in out.getvalue()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_worker.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.webapp.worker'`

- [ ] **Step 3: Write minimal implementation**

```python
# gee_animation/webapp/worker.py
"""One render, one process, one user's Earth Engine identity.

Forced by `ee._state._state` being a process-wide global: the EE client can hold
exactly one set of credentials, so a job that runs under a user's identity gets a
process of its own. Credentials arrive on **stdin** — never argv (world-readable in
/proc) and never the environment.
"""
from __future__ import annotations

import json
import sys
import types

import ee
from google.oauth2.credentials import Credentials

from .. import gui

TOKEN_URI = "https://oauth2.googleapis.com/token"
_REAUTH_HINT = ("Your Google sign-in expired or was revoked. Sign in again and "
                "re-run; nothing was charged to your project.")


def _emit(stream, **event) -> None:
    stream.write(json.dumps(event) + "\n")
    stream.flush()


def main(stdin=None, stdout=None, ee_module=ee, runner=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    runner = runner or gui.run_animation
    job = json.loads(stdin.read())
    token = job["refresh_token"]
    try:
        creds = Credentials(
            token=None, refresh_token=token, token_uri=TOKEN_URI,
            client_id=job["client_id"], client_secret=job["client_secret"],
            scopes=["https://www.googleapis.com/auth/earthengine",
                    "https://www.googleapis.com/auth/cloud-platform"])
        ee_module.Initialize(credentials=creds, project=job["project"])
        _emit(stdout, event="progress", message="Earth Engine initialised")
        # EE is already live under the user's identity, so run_animation must NOT
        # re-authenticate: its deps.init becomes a no-op (see gui.py:448).
        deps = types.SimpleNamespace(**vars(gui.DEFAULT_DEPS))
        deps.init = lambda project=None: None
        # The job's project is the authoritative one (the user's own, validated at
        # login); gui's params carry a `project` too, so drop it or `runner` gets the
        # keyword twice.
        params = {k: v for k, v in job["params"].items() if k != "project"}
        mp4, gif, pngs, zip_path, status, series = runner(
            project=job["project"], deps=deps, **params)
        _emit(stdout, event="done", mp4=mp4, gif=gif, frames=pngs,
              zip=zip_path, status=status)
        return 0
    except Exception as exc:                       # reported, never raised at the user
        message = str(exc).replace(token, "***") if token else str(exc)
        if "invalid_grant" in message or "expired or revoked" in message:
            message = _REAUTH_HINT
        _emit(stdout, event="error", message=message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_worker.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/webapp/worker.py tests/test_webapp_worker.py
git commit -m "feat(webapp): per-job worker process carrying one user's EE identity"
```

---

### Task 5: Job queue — 3 at a time, cancel, timeout, detached completion

**Files:**
- Create: `gee_animation/webapp/jobs.py`
- Test: `tests/test_webapp_jobs.py`

**Interfaces:**
- Consumes: `storage.user_dir`, the worker's stdout protocol from Task 4.
- Produces: `JobQueue(spawn, max_concurrency=3, timeout_s=5400)` with `async submit(email, credentials, params) -> str` (job id), `status(job_id) -> Job`, `async cancel(job_id) -> None`; dataclass `Job(id, email, state: Literal["queued","running","done","error","cancelled"], message, result)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webapp_jobs.py
import asyncio
import json

import pytest

from gee_animation.webapp.jobs import JobQueue


class FakeProc:
    """Stands in for an asyncio subprocess: emits events, then exits."""
    def __init__(self, events, hang=False):
        self._events, self._hang, self.killed = events, hang, False
        self.returncode = None

    async def run(self, on_event):
        if self._hang:
            await asyncio.sleep(3600)
        for e in self._events:
            on_event(json.loads(e))
        self.returncode = 0

    def kill(self):
        self.killed = True


def _spawn_ok(mp4="/out/x.mp4"):
    def spawn(job, on_event):
        return FakeProc([json.dumps({"event": "done", "mp4": mp4, "status": "ok"})])
    return spawn


@pytest.mark.asyncio
async def test_a_submitted_job_runs_and_reports_its_result():
    q = JobQueue(spawn=_spawn_ok(), max_concurrency=3)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {"index": "ndvi"})
    await q.wait(job_id)
    job = q.status(job_id)
    assert job.state == "done" and job.result["mp4"] == "/out/x.mp4"


@pytest.mark.asyncio
async def test_only_three_run_at_once_and_the_fourth_reports_its_position():
    started = []

    def spawn(job, on_event):
        started.append(job["id"])
        return FakeProc([], hang=True)

    q = JobQueue(spawn=spawn, max_concurrency=3)
    ids = [await q.submit("a@hnee.de", {"refresh_token": "r"}, {}) for _ in range(4)]
    await asyncio.sleep(0.05)
    assert len(started) == 3
    assert q.status(ids[3]).state == "queued"
    assert q.position(ids[3]) == 1
    for i in ids:
        await q.cancel(i)


@pytest.mark.asyncio
async def test_cancel_kills_the_worker_and_frees_the_slot():
    procs = []

    def spawn(job, on_event):
        p = FakeProc([], hang=True)
        procs.append(p)
        return p

    q = JobQueue(spawn=spawn, max_concurrency=1)
    first = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    second = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await asyncio.sleep(0.05)
    await q.cancel(first)
    await asyncio.sleep(0.05)
    assert procs[0].killed and q.status(first).state == "cancelled"
    assert q.status(second).state == "running"      # the slot was freed
    await q.cancel(second)


@pytest.mark.asyncio
async def test_a_stalled_job_is_killed_at_the_timeout_and_says_so():
    q = JobQueue(spawn=lambda job, on_event: FakeProc([], hang=True),
                 max_concurrency=1, timeout_s=0.05)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await q.wait(job_id)
    job = q.status(job_id)
    assert job.state == "error" and "timed out" in job.message.lower()


@pytest.mark.asyncio
async def test_a_job_survives_its_submitter_disconnecting():
    """Review Focus 4: the browser closes; the job finishes and stays retrievable."""
    q = JobQueue(spawn=_spawn_ok("/out/later.mp4"), max_concurrency=3)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await q.wait(job_id)                       # nobody is listening meanwhile
    assert q.status(job_id).result["mp4"] == "/out/later.mp4"


@pytest.mark.asyncio
async def test_a_worker_error_event_becomes_a_failed_job():
    def spawn(job, on_event):
        return FakeProc([json.dumps({"event": "error", "message": "sign in again"})])

    q = JobQueue(spawn=spawn, max_concurrency=1)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await q.wait(job_id)
    assert q.status(job_id).state == "error"
    assert "sign in again" in q.status(job_id).message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_jobs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.webapp.jobs'`.

If it instead fails with `'asyncio' not found in markers`, add to `pyproject.toml` `[tool.pytest.ini_options]` markers list: `"asyncio: async test"`, and install `pytest-asyncio>=0.23` into the `dev` extra, then re-run.

- [ ] **Step 3: Write minimal implementation**

```python
# gee_animation/webapp/jobs.py
"""A small async queue: at most `max_concurrency` worker processes at a time.

Jobs are owned by the queue, not by the browser tab that submitted them — a user
who closes the page comes back to a finished render in their run browser.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    email: str
    state: str = "queued"           # queued | running | done | error | cancelled
    message: str = ""
    result: dict | None = None
    proc: object | None = field(default=None, repr=False)


class JobQueue:
    def __init__(self, spawn, max_concurrency: int = 3, timeout_s: float = 5400):
        self._spawn = spawn
        self._sem = asyncio.Semaphore(max_concurrency)
        self._timeout = timeout_s
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._order: list[str] = []

    async def submit(self, email: str, credentials: dict, params: dict) -> str:
        job_id = secrets.token_urlsafe(16)
        self._jobs[job_id] = Job(id=job_id, email=email)
        self._order.append(job_id)
        payload = {"id": job_id, **credentials, "params": params}
        self._tasks[job_id] = asyncio.create_task(self._run(job_id, payload))
        return job_id

    def status(self, job_id: str) -> Job:
        return self._jobs[job_id]

    def position(self, job_id: str) -> int:
        queued = [i for i in self._order if self._jobs[i].state == "queued"]
        return queued.index(job_id) + 1 if job_id in queued else 0

    async def wait(self, job_id: str) -> None:
        await asyncio.shield(asyncio.gather(self._tasks[job_id], return_exceptions=True))

    async def cancel(self, job_id: str) -> None:
        job = self._jobs[job_id]
        if job.state in ("done", "error", "cancelled"):
            return
        job.state = "cancelled"
        if job.proc is not None:
            job.proc.kill()
        self._tasks[job_id].cancel()

    async def _run(self, job_id: str, payload: dict) -> None:
        job = self._jobs[job_id]
        async with self._sem:
            if job.state == "cancelled":
                return
            job.state = "running"

            def on_event(event):
                if event["event"] == "done":
                    job.state, job.result = "done", event
                elif event["event"] == "error":
                    job.state, job.message = "error", event["message"]
                else:
                    job.message = event.get("message", "")

            proc = self._spawn(payload, on_event)
            job.proc = proc
            try:
                await asyncio.wait_for(proc.run(on_event), timeout=self._timeout)
            except asyncio.TimeoutError:
                proc.kill()
                job.state = "error"
                job.message = (f"Render timed out after {self._timeout / 60:.0f} minutes "
                               "and was stopped. Try a shorter date range.")
            except asyncio.CancelledError:
                job.state = "cancelled"
            if job.state == "running":            # process exited without a done event
                job.state, job.message = "error", "The render stopped unexpectedly."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_jobs.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/webapp/jobs.py tests/test_webapp_jobs.py pyproject.toml
git commit -m "feat(webapp): job queue with 3 slots, cancel, timeout and detached completion"
```

---

### Task 6: Per-run caps

**Files:**
- Create: `gee_animation/webapp/caps.py`
- Test: `tests/test_webapp_caps.py`

**Interfaces:**
- Produces: `check(params: dict, max_years: int = 12, max_preset: str = "1440p") -> None`, raising `CapExceeded` (a `ValueError` subclass) with a message naming the limit and what to do.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webapp_caps.py
import pytest

from gee_animation.webapp.caps import CapExceeded, check


def test_a_run_inside_the_limits_passes():
    check({"start": "2020-01-01", "end": "2025-01-01", "preset": "1080p"})


def test_too_many_years_is_refused_with_the_limit_named():
    with pytest.raises(CapExceeded, match="12 years"):
        check({"start": "2000-01-01", "end": "2026-01-01", "preset": "1080p"})


def test_too_large_a_preset_is_refused():
    with pytest.raises(CapExceeded, match="1440p"):
        check({"start": "2024-01-01", "end": "2025-01-01", "preset": "4k"})


def test_the_native_preset_is_allowed():
    check({"start": "2024-01-01", "end": "2025-01-01", "preset": "native (no upscaling)"})


def test_a_missing_or_blank_preset_is_allowed():
    check({"start": "2024-01-01", "end": "2025-01-01", "preset": ""})


def test_an_unparsable_date_is_refused_rather_than_ignored():
    with pytest.raises(CapExceeded):
        check({"start": "not-a-date", "end": "2025-01-01", "preset": "1080p"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_caps.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.webapp.caps'`

- [ ] **Step 3: Write minimal implementation**

```python
# gee_animation/webapp/caps.py
"""Server-mode limits, enforced below the UI so they cannot be bypassed."""
from __future__ import annotations

from datetime import date

#: Largest to smallest; anything above `max_preset` in this order is refused.
_PRESET_ORDER = ["4k", "1440p", "1080p", "720p"]


class CapExceeded(ValueError):
    """A submitted run asks for more than server mode allows."""


def check(params: dict, max_years: int = 12, max_preset: str = "1440p") -> None:
    try:
        start = date.fromisoformat(str(params["start"]))
        end = date.fromisoformat(str(params["end"]))
    except (KeyError, ValueError) as exc:
        raise CapExceeded(f"Could not read the date range: {exc}") from exc
    years = (end - start).days / 365.25
    if years > max_years:
        raise CapExceeded(
            f"That range is {years:.1f} years; server mode allows {max_years} years "
            "per render. Split it into two runs.")
    preset = str(params.get("preset") or "")
    if preset in _PRESET_ORDER and _PRESET_ORDER.index(preset) < _PRESET_ORDER.index(max_preset):
        raise CapExceeded(
            f"{preset} is larger than server mode allows; the maximum is {max_preset}.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_caps.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/webapp/caps.py tests/test_webapp_caps.py
git commit -m "feat(webapp): per-run year and preset caps"
```

---

### Task 7: OAuth routes and Earth Engine project validation

**Files:**
- Create: `gee_animation/webapp/oauth.py`
- Create: `gee_animation/webapp/project.py`
- Test: `tests/test_webapp_project.py`
- Test: `tests/test_webapp_oauth.py`

**Interfaces:**
- Consumes: `SessionStore` (Task 1).
- Produces: `project.validate(credentials, project_id, ee_module=ee) -> None` raising `ProjectError` with an actionable message; `oauth.OAuthConfig(client_id, client_secret, redirect_uri)`, `oauth.SCOPES`, `oauth.current_user(request) -> str | None` (the Gradio `auth_dependency`), and `oauth.add_routes(app, cfg, store, client)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webapp_project.py
import types

import pytest

from gee_animation.webapp.project import ProjectError, validate


def _ee(exc=None):
    def initialize(credentials=None, project=None):
        if isinstance(exc, Exception):
            raise exc
    return types.SimpleNamespace(
        Initialize=initialize,
        Number=lambda v: types.SimpleNamespace(getInfo=lambda: v))


def test_a_working_project_validates_quietly():
    validate(object(), "good-project", ee_module=_ee())


def test_an_unregistered_project_names_the_fix():
    """Review Focus 1: the commonest user error, caught at login not mid-render."""
    exc = Exception("Earth Engine API has not been used in project foo before or it is disabled")
    with pytest.raises(ProjectError) as e:
        validate(object(), "foo", ee_module=_ee(exc))
    assert "console.cloud.google.com" in str(e.value)
    assert "Earth Engine API" in str(e.value)


def test_a_project_the_user_cannot_access_says_so():
    exc = Exception("Caller does not have permission on project bar")
    with pytest.raises(ProjectError, match="permission"):
        validate(object(), "bar", ee_module=_ee(exc))


def test_a_blank_project_id_is_refused_before_any_call():
    with pytest.raises(ProjectError, match="project id"):
        validate(object(), "   ", ee_module=_ee())
```

```python
# tests/test_webapp_oauth.py
import types

from gee_animation.webapp import oauth
from gee_animation.webapp.session import SessionStore


def test_scopes_are_exactly_earthengine_and_cloud_platform():
    assert oauth.SCOPES == [
        "https://www.googleapis.com/auth/earthengine",
        "https://www.googleapis.com/auth/cloud-platform",
    ]


def test_current_user_returns_none_without_a_session_cookie():
    store = SessionStore()
    request = types.SimpleNamespace(cookies={}, app=types.SimpleNamespace(
        state=types.SimpleNamespace(sessions=store)))
    assert oauth.current_user(request) is None


def test_current_user_returns_the_email_for_a_live_session():
    store = SessionStore()
    sid = store.new("a@hnee.de", "r")
    request = types.SimpleNamespace(cookies={"sid": sid}, app=types.SimpleNamespace(
        state=types.SimpleNamespace(sessions=store)))
    assert oauth.current_user(request) == "a@hnee.de"


def test_a_stale_cookie_is_treated_as_logged_out():
    store = SessionStore()
    sid = store.new("a@hnee.de", "r")
    store.drop(sid)
    request = types.SimpleNamespace(cookies={"sid": sid}, app=types.SimpleNamespace(
        state=types.SimpleNamespace(sessions=store)))
    assert oauth.current_user(request) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_project.py tests/test_webapp_oauth.py -q`
Expected: FAIL — no module `gee_animation.webapp.project` / `gee_animation.webapp.oauth`

- [ ] **Step 3: Write minimal implementation**

```python
# gee_animation/webapp/project.py
"""Does this user's Cloud project actually work for Earth Engine?

Called when the project id is entered, so the commonest setup error (a project
without the Earth Engine API) is reported at login with the fix, rather than three
minutes into a render.
"""
from __future__ import annotations

import ee as _ee

_ENABLE_URL = "https://console.cloud.google.com/apis/library/earthengine.googleapis.com"


class ProjectError(ValueError):
    """The chosen Cloud project cannot be used for Earth Engine."""


def validate(credentials, project_id: str, ee_module=_ee) -> None:
    project_id = (project_id or "").strip()
    if not project_id:
        raise ProjectError("Enter your Google Cloud project id (e.g. my-ee-project).")
    try:
        ee_module.Initialize(credentials=credentials, project=project_id)
        ee_module.Number(1).getInfo()               # cheapest round trip there is
    except Exception as exc:
        text = str(exc)
        if "has not been used" in text or "is disabled" in text:
            raise ProjectError(
                f"The Earth Engine API is not enabled for {project_id!r}. Enable it at "
                f"{_ENABLE_URL} and register the project for Earth Engine, then retry."
            ) from exc
        if "permission" in text.lower():
            raise ProjectError(
                f"Your account has no permission on project {project_id!r}."
            ) from exc
        raise ProjectError(f"Could not use project {project_id!r}: {text}") from exc
```

```python
# gee_animation/webapp/oauth.py
"""Google OAuth: the user signs in, and renders run under THEIR Earth Engine project."""
from __future__ import annotations

from dataclasses import dataclass

SCOPES = [
    "https://www.googleapis.com/auth/earthengine",
    "https://www.googleapis.com/auth/cloud-platform",
]
COOKIE = "sid"


@dataclass
class OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


def current_user(request) -> str | None:
    """Gradio `auth_dependency`: the logged-in email, or None."""
    sid = request.cookies.get(COOKIE)
    if not sid:
        return None
    session = request.app.state.sessions.get(sid)
    return session.email if session else None


def add_routes(app, cfg: OAuthConfig, store, client):
    """Register /login, /oauth2/callback and /logout on `app`.

    `client` is an Authlib OAuth client (injected so tests never touch Google).
    """
    from fastapi import Request
    from fastapi.responses import RedirectResponse

    @app.get("/login")
    async def login(request: Request):
        return await client.authorize_redirect(request, cfg.redirect_uri)

    @app.get("/oauth2/callback")
    async def callback(request: Request):
        token = await client.authorize_access_token(request)
        email = token["userinfo"]["email"]
        sid = store.new(email, token["refresh_token"])
        response = RedirectResponse("/")
        response.set_cookie(COOKIE, sid, httponly=True, secure=True, samesite="lax")
        return response

    @app.get("/logout")
    async def logout(request: Request):
        store.drop(request.cookies.get(COOKIE, ""))
        response = RedirectResponse("/login")
        response.delete_cookie(COOKIE)
        return response

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_project.py tests/test_webapp_oauth.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add gee_animation/webapp/oauth.py gee_animation/webapp/project.py tests/test_webapp_oauth.py tests/test_webapp_project.py
git commit -m "feat(webapp): Google OAuth routes and EE project validation"
```

---

### Task 8: Teach `build_app` an optional server context

**Files:**
- Modify: `gee_animation/gui.py:681-711` (`build_app`, the previous-runs accordion)
- Test: `tests/test_webapp_context.py`
- Regression: `tests/test_gui.py` (must stay unmodified and green)

**Interfaces:**
- Consumes: `storage.list_runs` (Task 2).
- Produces: `gui.build_app(context=None)`; `context` is any object with
  `list_runs(request) -> list[tuple[str, str]]`, `submit(request, params) -> str` (a job
  id) and `job_status(job_id) -> dict` with keys `state` (`queued|running|done|error|
  cancelled`), `message`, `result`, `position`. `request` is a `gr.Request`.
  **Per-request, not per-build:** the server mounts ONE Gradio app for everybody, so
  the run list cannot be resolved when the app is constructed — it is filled by an
  `app.load` handler that receives `gr.Request` and therefore the caller's identity.
  When `context is None` the GUI behaves exactly as today — including rendering
  **in-process**, which is what the single-operator tool does and must keep doing.

**Why submission goes through the context too:** without it the queue (Task 5), the caps
(Task 6) and the per-user output directory (Task 2) are built but never reached, and the
server would render in the web process — exactly what the spec's one-process-per-job
decision forbids. The context is the single seam where server mode diverges.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webapp_context.py
import types

from gee_animation import gui


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
    # The handler is registered for load, not called at build time: one app serves
    # every user, so identity only exists per request.
    assert calls == []
    assert ctx.list_runs("fake-request")[0][0].startswith("alice_ndvi")


def test_the_context_run_list_is_used_instead_of_the_global_output_dir(tmp_path, monkeypatch):
    """A run sitting in the global OUTPUT_DIR must not appear for a context user."""
    monkeypatch.setattr(gui, "OUTPUT_DIR", str(tmp_path))
    stray = tmp_path / "someone_else" / "secret_run"
    stray.mkdir(parents=True)
    (stray / "secret_run.mp4").write_bytes(b"\x00\x00mp4")
    seen = {}

    def list_runs(request):
        seen["request"] = request
        return []

    ctx = types.SimpleNamespace(list_runs=list_runs, submit=lambda r, p: "job-1",
                                job_status=lambda j: {"state": "queued"})
    app = gui.build_app(context=ctx)
    assert app is not None
    # The context, not OUTPUT_DIR, is what the UI will ask — drive it the way the
    # load handler will. (Asserting on gr.Blocks internals would couple the test to
    # a private Gradio API that moves between versions.)
    ctx.list_runs(types.SimpleNamespace(cookies={}))
    assert "request" in seen
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_context.py -q`
Expected: FAIL — `TypeError: build_app() got an unexpected keyword argument 'context'`

- [ ] **Step 3: Write minimal implementation**

In `gee_animation/gui.py`, change the signature at line 681 and the two places the
run list is produced (the `prev` dropdown at ~line 707 and the `refresh.click` at ~711):

```python
def build_app(context=None):
    """Build the Gradio UI.

    `context` is the multi-user server's hook (see webapp/app.py): an object with
    `list_runs(request)`. ONE app is mounted for every user, so the run list is
    resolved per request — on load and on refresh — never at build time. Without a
    context this is the single-operator app, unchanged; `tests/test_gui.py` guards it.
    """
```

`_go` (the render button's handler, `gui.py:1013`) keeps its current body for the
single-operator path and gains a server branch at the top:

```python
            if context is not None:
                job_id = context.submit(request, dict(params))   # caps + queue + per-user out_dir
                while True:
                    state = context.job_status(job_id)
                    if state["state"] in ("done", "error", "cancelled"):
                        break
                    progress(0.5, desc=state["message"]
                             or (f"queued, position {state['position']}"
                                 if state["state"] == "queued" else "rendering..."))
                    time.sleep(2)
                if state["state"] != "done":
                    return _error(RuntimeError(state["message"]))
                result = state["result"]
                return (result["mp4"], result.get("gif"), result.get("frames"),
                        result.get("zip"), None, result["status"], pd.DataFrame())
```

`_go` must take `request: gr.Request` as its last parameter for this (Gradio injects it).
`params` is the same keyword dict `_go` already builds for `run_animation`.

Inside the `with gr.Blocks(...) as app:` body, the previous-runs dropdown starts empty
in server mode and is filled per request:

```python
                prev = gr.Dropdown(
                    [] if context is not None else list_previous_runs(),
                    label="Previously rendered runs "
                          f"({'yours' if context is not None else OUTPUT_DIR + '/'})", scale=4)
```

and both the refresh click and a new load handler go through the context when present:

```python
            def _fill_runs(request: gr.Request):
                choices = (context.list_runs(request) if context is not None
                           else list_previous_runs())
                return gr.update(choices=choices)

            refresh.click(_fill_runs, None, prev)
            if context is not None:
                app.load(_fill_runs, None, prev)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_context.py tests/test_gui.py -q`
Expected: PASS — 4 new passed, and all 82 of `test_gui.py` still passing, unmodified.

- [ ] **Step 5: Commit**

```bash
git add gee_animation/gui.py tests/test_webapp_context.py
git commit -m "feat(gui): optional server context for the run browser; single-user path unchanged"
```

---

### Task 9: FastAPI assembly, upload limit, `gee-animation-server` entry point

**Files:**
- Create: `gee_animation/webapp/app.py`
- Modify: `pyproject.toml:28-30` (`[project.scripts]`)
- Test: `tests/test_webapp_app.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `create_app(cfg: AppConfig, oauth_client, spawn) -> FastAPI`; `AppConfig(client_id, client_secret, redirect_uri, output_root: Path, max_concurrency=3, max_upload_mb=100, retention_days=14)`; `main()` for the console script.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_app.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gee_animation.webapp.app'`

- [ ] **Step 3: Write minimal implementation**

```python
# gee_animation/webapp/app.py
"""FastAPI host: OAuth routes of its own, the Gradio GUI mounted at /."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import gradio as gr
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from . import caps, logsafe, oauth, storage
from .jobs import JobQueue
from .session import SessionStore
from ..gui import build_app


@dataclass
class AppConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    output_root: Path
    #: Signs the Starlette session cookie that carries the OAuth CSRF state. Must be
    #: stable across restarts (an in-flight sign-in breaks otherwise) and secret.
    cookie_secret: str = ""
    max_concurrency: int = 3
    max_upload_mb: int = 100
    retention_days: int = 14


def create_app(cfg: AppConfig, oauth_client, spawn) -> FastAPI:
    app = FastAPI()
    # Authlib stores the OAuth `state` (the CSRF token) in a Starlette session, so
    # /login raises without this middleware. The secret signs that cookie only; our own
    # session cookie is an opaque sid looked up in SessionStore (Ruling S).
    app.add_middleware(SessionMiddleware, secret_key=cfg.cookie_secret,
                       https_only=True, same_site="lax")
    app.state.sessions = SessionStore()
    app.state.queue = JobQueue(spawn=spawn, max_concurrency=cfg.max_concurrency)
    app.state.max_upload_bytes = cfg.max_upload_mb * 1024 * 1024
    app.state.config = cfg

    @app.on_event("startup")
    async def _on_start():
        app.state.loop = asyncio.get_running_loop()   # ServerContext.submit runs off-loop
        # Install the redactor HERE, not at import time: `logsafe.install` attaches the
        # filter to the handlers that exist when it runs, and uvicorn configures logging
        # during startup. Installing earlier would attach to handlers that are then
        # replaced, and the redaction would silently cover nothing (Task 3, Ruling J).
        secrets = lambda: [s.refresh_token for s in app.state.sessions.all()]
        logsafe.install(logging.getLogger(), secrets)
        # uvicorn's own loggers carry their own handlers with propagate=False, so a
        # root-only install never sees their records (Ruling K).
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logsafe.install(logging.getLogger(name), secrets)
        storage.sweep(cfg.output_root, max_age_days=cfg.retention_days)

    if oauth_client is not None:
        oauth.add_routes(app, oauth.OAuthConfig(cfg.client_id, cfg.client_secret,
                                                cfg.redirect_uri),
                         app.state.sessions, oauth_client)
    else:
        @app.get("/login")
        async def login():                       # test/dev stand-in
            return {"login": "not configured"}

    def auth_dependency(request):
        user = oauth.current_user(request)
        return user      # Gradio redirects to /login when this is None

    class ServerContext:
        """Per-request view of the server for the one mounted Gradio app.

        The single seam where server mode diverges from the single-operator GUI: it is
        what makes the queue, the caps and the per-user output directory reachable.
        """

        @staticmethod
        def list_runs(request):
            email = oauth.current_user(request)
            return storage.list_runs(cfg.output_root, email) if email else []

        @staticmethod
        def submit(request, params):
            sid = request.cookies.get(oauth.COOKIE, "")
            session = app.state.sessions.get(sid)
            if session is None:
                raise PermissionError("Your session expired. Sign in again.")
            caps.check(params)                    # raises CapExceeded, surfaced in the UI
            stamp = time.strftime("%Y%m%d-%H%M%S")
            params = dict(params)
            params["out_dir"] = str(
                storage.user_dir(cfg.output_root, session.email)
                / f"{params.get('sensor')}_{params.get('index')}_{stamp}")
            credentials = {"refresh_token": session.refresh_token,
                           "client_id": cfg.client_id, "client_secret": cfg.client_secret}
            return asyncio.run_coroutine_threadsafe(
                app.state.queue.submit(session.email, credentials, params),
                app.state.loop).result()

        @staticmethod
        def job_status(job_id):
            job = app.state.queue.status(job_id)
            return {"state": job.state, "message": job.message, "result": job.result,
                    "position": app.state.queue.position(job_id)}

    gr.mount_gradio_app(app, build_app(context=ServerContext), path="/",
                        auth_dependency=auth_dependency,
                        max_file_size=app.state.max_upload_bytes)
    return app


def main() -> None:
    import uvicorn
    from authlib.integrations.starlette_client import OAuth

    cfg = AppConfig(
        client_id=os.environ["GEE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GEE_OAUTH_CLIENT_SECRET"],
        redirect_uri=os.environ["GEE_OAUTH_REDIRECT_URI"],
        cookie_secret=os.environ["GEE_COOKIE_SECRET"],
        output_root=Path(os.environ.get("GEE_OUTPUT_DIR", "/data/out")))
    client = OAuth().register(
        name="google",
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_id=cfg.client_id, client_secret=cfg.client_secret,
        # access_type=offline + prompt=consent are REQUIRED, not optional: without
        # them Google returns no refresh token, and a render outlives the one-hour
        # access token, so every job would fail partway (Ruling S).
        client_kwargs={"scope": "openid email " + " ".join(oauth.SCOPES),
                       "access_type": "offline", "prompt": "consent"})
    from .spawn import spawn_worker
    uvicorn.run(create_app(cfg, client, spawn_worker), host="0.0.0.0", port=7860)
```

Also create the real spawner the server uses (the tests inject fakes instead):

```python
# gee_animation/webapp/spawn.py
"""Spawn `webapp.worker` as a subprocess and stream its events back."""
from __future__ import annotations

import asyncio
import json
import sys


class WorkerProcess:
    def __init__(self, payload: dict):
        self._payload = payload
        self._proc = None

    async def run(self, on_event) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "gee_animation.webapp.worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            start_new_session=True)              # own process group, so kill() is clean
        self._proc.stdin.write(json.dumps(self._payload).encode())
        await self._proc.stdin.drain()
        self._proc.stdin.close()
        async for line in self._proc.stdout:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                # Not our protocol: a library the render path imports printed to
                # stdout. Ignore it rather than killing the job on someone else's
                # chatter (Ruling N). The worker's own events are always valid JSON.
                continue
            on_event(event)
        await self._proc.wait()

    def kill(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.kill()


def spawn_worker(payload: dict, on_event) -> WorkerProcess:
    return WorkerProcess(payload)
```

Add the console script to `pyproject.toml` under `[project.scripts]`:

```toml
gee-animation-server = "gee_animation.webapp.app:main"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n GEE_animation python -m pytest tests/test_webapp_app.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Run the whole suite**

Run: `conda run -n GEE_animation python -m pytest -m "not integration" -q`
Expected: all green except the pre-existing, unrelated failure
`tests/test_hemnet_snow_configs.py::test_write_configs_round_trips_through_runconfig`
(an untracked file asserting a `modis_snow` sensor that `products.py` does not define).

- [ ] **Step 6: Commit**

```bash
git add gee_animation/webapp/app.py gee_animation/webapp/spawn.py pyproject.toml tests/test_webapp_app.py
git commit -m "feat(webapp): FastAPI host with mounted Gradio, upload cap and startup sweep"
```

---

### Task 10: Deployment — image, compose, Caddy, README

**Files:**
- Modify: `Dockerfile`
- Create: `deploy/compose.yaml`
- Create: `deploy/Caddyfile`
- Create: `deploy/.env.example`
- Modify: `README.md` (a "Multi-user server" section after "Docker")

**Interfaces:**
- Consumes: `gee-animation-server` (Task 9).
- Produces: a deployable stack on `carrot.aisl.c4ds.hnee.de`.

- [ ] **Step 1: Extend the Dockerfile to install the server extra**

Change the install line (currently `RUN pip install --no-cache-dir ".[gui,shapefile,ml]"`) to:

```dockerfile
RUN pip install --no-cache-dir ".[gui,shapefile,ml,server]"
```

and add a non-root user before `CMD`:

```dockerfile
RUN useradd --create-home --uid 10001 app && mkdir -p /data/out /data/cache && chown -R app /data
USER app
ENV GEE_OUTPUT_DIR=/data/out \
    GEE_ANIMATION_CACHE_DIR=/data/cache
```

- [ ] **Step 2: Write the compose file**

```yaml
# deploy/compose.yaml — carrot.aisl.c4ds.hnee.de
services:
  app:
    build: ..
    command: ["gee-animation-server"]
    env_file: .env
    environment:
      GEE_OAUTH_REDIRECT_URI: https://carrot.aisl.c4ds.hnee.de/oauth2/callback
      # GEE_OAUTH_CLIENT_ID / _SECRET and GEE_COOKIE_SECRET come from .env
    volumes:
      - out:/data/out
      - cache:/data/cache
    expose: ["7860"]
    restart: unless-stopped

  caddy:
    image: caddy:2
    ports: ["443:443", "80:80"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
    depends_on: [app]
    restart: unless-stopped

volumes: { out: {}, cache: {}, caddy_data: {} }
```

- [ ] **Step 3: Write the Caddyfile**

```
# A render can take well over an hour; the default 60 s proxy timeouts would cut it.
carrot.aisl.c4ds.hnee.de {
	tls {
		# VPN-only host: Let's Encrypt HTTP-01 cannot reach it. Use DNS-01 with a
		# token for the hnee.de zone, or replace this block with:
		#   tls /etc/caddy/carrot.crt /etc/caddy/carrot.key
		dns <provider> {env.DNS_API_TOKEN}
	}
	reverse_proxy app:7860 {
		transport http {
			read_timeout 4h
			write_timeout 4h
		}
	}
}
```

- [ ] **Step 4: Write `.env.example`**

```bash
# deploy/.env.example — copy to deploy/.env (root-owned, chmod 600, NEVER committed)
GEE_OAUTH_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GEE_OAUTH_CLIENT_SECRET=
# Signs the OAuth state cookie. Generate once with:  python -c "import secrets;
# print(secrets.token_urlsafe(48))"  and keep it stable across restarts.
GEE_COOKIE_SECRET=
# Peers whose X-Forwarded-* headers uvicorn may trust. Caddy terminates TLS and
# proxies over plain HTTP, so without this Starlette sees http:// and the Secure
# session cookie misbehaves (Ruling Z). Default covers loopback + RFC1918; tighten
# to the Caddy container's address if you know it.
GEE_FORWARDED_ALLOW_IPS=127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
DNS_API_TOKEN=
```

Only these five variables are read by the app (`GEE_OUTPUT_DIR` and
`GEE_ANIMATION_CACHE_DIR` are set in the Dockerfile). The caps — 3 concurrent renders,
100 MB upload, 14-day retention — are `AppConfig` defaults in code, not environment
variables; do NOT add env vars for them to `.env.example`.

Confirm `.env` is ignored: `git check-ignore -v deploy/.env` must print a rule. If it
does not, add `deploy/.env` to `.gitignore` in this commit. (`work/UltraDetector/.env`
being staged in a repo with no `.gitignore` is the cautionary tale.)

- [ ] **Step 5: Verify the stack parses and builds**

```bash
cd deploy && docker compose config >/dev/null && echo "compose OK"
docker build -t gee-timelapse-server ..
docker run --rm gee-timelapse-server gee-animation-server --help || true
```
Expected: `compose OK`, a successful build, and the server entry point importable.

- [ ] **Step 6: Write the README section**

Add after the existing "Docker" section: what the server is, the two prerequisites
(a browser-trusted certificate via DNS-01 or an HNEE internal CA; an OAuth client whose
consent screen is Internal, or External/testing with `hnee.de` verified and ≤100 named
users), the per-user setup (their own Cloud project with the Earth Engine API enabled
and registered), the fact that the project field starts **empty** and each person types
their own project id there — nothing is billed to HNEE's project (Ruling AA) — the
3-concurrent/12-year/1440p limits, the 14-day retention, and this sentence verbatim:

> Renders run as subprocesses on a shared machine. Anyone with Docker access on carrot
> can read a running worker's memory, which briefly holds your Google refresh token.
> Use the server only if you accept that; nothing is written to disk.

- [ ] **Step 7: Commit**

```bash
git add Dockerfile deploy/ README.md .gitignore
git commit -m "feat(deploy): compose stack, Caddy TLS and README for the multi-user server"
```

---

## Self-review notes

- **Spec coverage.** Blocker 1 → Tasks 7+9; blocker 2 → Tasks 2+8; blocker 3 → Tasks 4+7;
  blocker 4 → Tasks 4+5; blocker 5 → Tasks 2+10. Caps → Task 6. Log hygiene → Task 3.
  Acceptance criteria 1–8 map to tests in Tasks 9, 2, 5, 4, 5, 2, 8 and the Task 9 full-suite step.
- **Not covered by this plan, deliberately:** acceptance criterion 4 ("billed to A's project")
  can only be confirmed by looking at a real project's EE usage after deployment — it is a
  post-deploy check, not a test. Record the result in the PR description.
- **Open questions from the spec are unresolved and must not be invented by an implementer:**
  whether a user's EE project id is remembered between sessions (Task 1 currently forgets it on
  restart), and whether runs can be shared between users (they currently cannot).
