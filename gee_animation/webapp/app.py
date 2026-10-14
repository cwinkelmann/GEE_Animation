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
import concurrent.futures
import re
import secrets
import shutil
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from . import caps, logsafe, oauth, project, storage
from .jobs import JobQueue, UnknownJobError
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
    #: Wall-clock limit for one render, after which its worker is killed. 4 h, matching
    #: deploy/Caddyfile's proxy timeouts and the README: caps allow 12 years at a 10-day
    #: cadence with lst_rf, which is far past an hour.
    job_timeout_s: float = 14400


#: How long a Gradio worker thread waits for the event loop to accept a job before telling
#: the user the server is busy, rather than hanging forever on a wedged loop.
SUBMIT_TIMEOUT_S = 30.0
#: The same bound for a cancel. Shorter than submit's because the work is a `killpg` and
#: a `Task.cancel()` with nothing to await, and the spec asks for the worker to be dead
#: within 5 s -- but still bounded, because it crosses the same thread/loop boundary.
CANCEL_TIMEOUT_S = 10.0
#: How long shutdown waits for the sweeper to stop before giving up on it (a wedged
#: sweeper must never hang the server's shutdown).
SHUTDOWN_TIMEOUT_S = 5.0
#: Retention is enforced at startup and then this often, so an always-up server keeps
#: its promise. A module constant so tests can shorten it.
SWEEP_INTERVAL_S = 24 * 3600

log = logging.getLogger(__name__)


def _safe_part(value, limit: int = 32) -> str:
    """A path component built only from [A-Za-z0-9_-] (the value is client-supplied)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(value))[:limit] or "x"


def gradio_temp_dir() -> Path | None:
    """Where Gradio copies returned files, if the deployment pointed it somewhere of its
    own (`GRADIO_TEMP_DIR`, set by the server's compose service to a path inside the
    cache volume). Unset -- the single-operator container -- means Gradio's own default
    under the system temp dir, which this server must not sweep: it may be shared, and
    only the server has a sweeper at all."""
    configured = os.environ.get("GRADIO_TEMP_DIR")
    return Path(configured) if configured else None


def allowed_aoi_roots() -> list[Path]:
    """Where a legitimate AOI can live: Gradio's upload cache and our staged presets.

    Evaluated per call (GRADIO_TEMP_DIR is read from the environment by Gradio too).
    """
    from gradio.utils import get_upload_folder
    return [Path(get_upload_folder()).resolve()] + [
        p.resolve() for p in Path(tempfile.gettempdir()).glob("gee_preset_*") if p.is_dir()]


def _contained_aoi(path) -> Path:
    """Resolve a client-named AOI path and require it to sit under an allowed root.

    The path arrives in the client's FileData payload and Gradio only checks the
    extension, so without this a user could name any .json on the server (another
    user's run directory, credentials) and have it copied somewhere they can read.
    """
    try:
        resolved = Path(str(path)).resolve()
    except (OSError, ValueError, RuntimeError):
        resolved = None
    if resolved is None or not resolved.is_file() or not any(
            resolved.is_relative_to(root) for root in allowed_aoi_roots()):
        raise PermissionError("That AOI file was not uploaded through this page. "
                              "Please upload it again.")
    return resolved


class _LoginRedirect:
    """Send an anonymous browser to /login instead of showing Gradio's bare 401.

    Gradio answers an unauthenticated page load with 401 JSON, which a browser renders
    as an error page. Pure ASGI rather than `BaseHTTPMiddleware` so Gradio's streaming
    queue responses are never buffered. Only the page itself ("/" or any request that
    asks for HTML) is redirected; API calls keep their 401.
    """

    #: Reachable without a session. Matched EXACTLY, not by prefix: a prefix rule
    #: exempts every future path that merely starts with one of these strings
    #: (`/logout-everyone`, `/login-as-admin`) from the authentication redirect, which
    #: is how `/login-failed` came to work -- by luck, not by design.
    _OPEN = frozenset({"/login", oauth.SIGN_IN_FAILED_PATH, "/oauth2/callback", "/logout"})
    #: Deliberate prefix exemptions, for a subtree such as a static-asset mount. Empty
    #: today; an entry here is a decision, and `startswith(())` is False, so nothing is
    #: exempt by accident.
    _OPEN_PREFIXES: tuple = ()

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "GET" \
                and scope["path"] not in self._OPEN \
                and not scope["path"].startswith(self._OPEN_PREFIXES):
            accept = dict(scope["headers"]).get(b"accept", b"").decode()
            if scope["path"] == "/" or "text/html" in accept:
                from starlette.requests import Request
                if oauth.current_user(Request(scope)) is None:
                    await RedirectResponse("/login")(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def create_app(cfg: AppConfig, oauth_client, spawn) -> FastAPI:
    if not cfg.cookie_secret:
        raise ValueError("AppConfig.cookie_secret must be set (and stable across restarts).")

    secrets_now = lambda: [s.refresh_token for s in app.state.sessions.all()]

    async def _sweep_once():
        """One retention pass. A transient filesystem error must not end retention for
        the rest of uptime (or stop the server starting): log it and carry on."""
        try:
            await asyncio.to_thread(storage.sweep, cfg.output_root,
                                    max_age_days=cfg.retention_days)
        except Exception:
            log.exception("retention sweep failed; will retry")
        # Gradio's own copy of every returned file lives outside output_root. Only swept
        # when GRADIO_TEMP_DIR points at a dedicated directory (the image sets it): the
        # default, the system temp dir's /gradio, may be shared with other processes.
        temp_dir = gradio_temp_dir()
        if temp_dir is not None:
            try:
                await asyncio.to_thread(storage.sweep_temp, temp_dir,
                                        max_age_days=cfg.retention_days)
            except Exception:
                log.exception("gradio temp sweep failed; will retry")

    async def _sweeper():
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_S)
            await _sweep_once()

    @asynccontextmanager
    async def lifespan(_app):
        # `logging.lastResort` is process-global: pair the install with its removal in
        # one try/finally, so a failed startup cannot leak the filter into the process.
        last_resort = None
        if logging.lastResort is not None:
            # With no handler on the root logger (the uvicorn case) a child logger's
            # record falls through to lastResort, a handler with no filter at all.
            last_resort = logsafe.RedactingFilter(secrets_now)
            logging.lastResort.addFilter(last_resort)
        try:
            await _on_start()
            await _sweep_once()
            app.state.sweeper = asyncio.create_task(_sweeper())
            try:
                yield
            finally:
                app.state.sweeper.cancel()
                # asyncio.wait, not wait_for: wait_for cancels on timeout and then waits
                # for the cancellation to finish, which a task that swallows
                # CancelledError would never do -- the very hang being prevented.
                _, pending = await asyncio.wait({app.state.sweeper},
                                                timeout=SHUTDOWN_TIMEOUT_S)
                if pending:
                    log.error("retention sweeper did not stop within %.0fs of shutdown",
                              SHUTDOWN_TIMEOUT_S)
        finally:
            if last_resort is not None and logging.lastResort is not None:
                logging.lastResort.removeFilter(last_resort)

    app = FastAPI(lifespan=lifespan)
    # Authlib stores the OAuth `state` (the CSRF token) in a Starlette session, so
    # /login raises without this middleware. The secret signs that cookie only; our own
    # session cookie is an opaque sid looked up in SessionStore (Ruling S).
    app.add_middleware(SessionMiddleware, secret_key=cfg.cookie_secret,
                       https_only=True, same_site="lax")
    app.add_middleware(_LoginRedirect)
    app.state.sessions = SessionStore()
    app.state.queue = JobQueue(spawn=spawn, max_concurrency=cfg.max_concurrency,
                                  timeout_s=cfg.job_timeout_s)
    app.state.max_upload_bytes = cfg.max_upload_mb * 1024 * 1024
    app.state.config = cfg

    async def _on_start():
        app.state.loop = asyncio.get_running_loop()   # ServerContext.submit runs off-loop
        # Install the redactor HERE, not at import time: `logsafe.install` attaches the
        # filter to the handlers that exist when it runs, and uvicorn configures logging
        # during startup. Installing earlier would attach to handlers that are then
        # replaced, and the redaction would silently cover nothing (Task 3, Ruling J).
        secrets = secrets_now
        logsafe.install(logging.getLogger(), secrets)
        # uvicorn's own loggers carry their own handlers with propagate=False, so a
        # root-only install never sees their records (Ruling K).
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logsafe.install(logging.getLogger(name), secrets)

    if oauth_client is not None:
        oauth.add_routes(app, oauth.OAuthConfig(cfg.client_id, cfg.client_secret,
                                                cfg.redirect_uri),
                         app.state.sessions, oauth_client)
    else:
        @app.get("/login")
        async def login():                       # test/dev stand-in
            return {"login": "not configured"}

    def user_of(request):
        """Who is this request? Looks in OUR store, not `request.app.state`: Gradio
        runs these callbacks on its own sub-app, whose `.state` has no `sessions`."""
        session = app.state.sessions.get(request.cookies.get(oauth.COOKIE, ""))
        return session.email if session else None

    def auth_dependency(request):
        user = user_of(request)
        return user      # None -> Gradio answers 401; _LoginRedirect covers the page load

    class ServerContext:
        """Per-request view of the server for the one mounted Gradio app.

        The single seam where server mode diverges from the single-operator GUI: it is
        what makes the queue, the caps and the per-user output directory reachable.
        """

        #: The GUI derives its poll bound from this, so it never gives up on a job the
        #: queue would still let run.
        job_timeout_s = cfg.job_timeout_s

        @staticmethod
        def list_runs(request):
            email = user_of(request)
            return storage.list_runs(cfg.output_root, email) if email else []

        @staticmethod
        def submit(request, params):
            sid = request.cookies.get(oauth.COOKIE, "")
            session = app.state.sessions.get(sid)
            if session is None:
                raise PermissionError("Your session expired. Sign in again.")
            caps.check(params)                    # raises CapExceeded, surfaced in the UI
            # Second resolution plus a double-click would share a directory, and
            # sensor/index are client-supplied: random suffix, restricted charset.
            stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
            params = dict(params)
            # The worker needs the user's project; the GUI's textbox is where they type
            # it. `remember` (not set_project) so a lost session is an actionable error.
            project_id = (params.get("project") or session.project or "").strip()
            if not project_id:
                raise project.ProjectError(
                    "Enter your own Google Cloud project id, the one you registered for "
                    "Earth Engine, in the project field (e.g. my-ee-project).")
            project.remember(app.state.sessions, sid, project_id)
            run_dir = (storage.user_dir(cfg.output_root, session.email)
                       / f"{_safe_part(params.get('sensor'))}_{_safe_part(params.get('index'))}_{stamp}")
            params["out_dir"] = str(run_dir)
            # The render runs in another process, later, possibly after Gradio has
            # cleaned its upload cache: hand it a copy that lives with the run (and is
            # swept with it), not Gradio's temp path.
            aoi_src = params.get("aoi_path")
            if aoi_src:
                source = _contained_aoi(aoi_src)           # before anything is created
                aoi_dir = run_dir / "aoi"
                aoi_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, aoi_dir / source.name)
                params["aoi_path"] = str(aoi_dir / source.name)
            credentials = {"refresh_token": session.refresh_token,
                           "client_id": cfg.client_id, "client_secret": cfg.client_secret,
                           "project": project_id}
            future = asyncio.run_coroutine_threadsafe(
                app.state.queue.submit(session.email, credentials, params),
                app.state.loop)
            try:
                return future.result(timeout=SUBMIT_TIMEOUT_S)
            except concurrent.futures.TimeoutError:
                future.cancel()
                raise RuntimeError(
                    "The server is busy right now. Please try again in a minute.") from None

        @staticmethod
        def cancel(request):
            """Stop the caller's own unfinished renders. Returns how many were stopped.

            No job id is accepted. The queue is asked for the jobs owned by the email
            in THIS request's session, so a client cannot name someone else's render --
            and a request with no session cancels nothing (it raises).
            """
            email = user_of(request)
            if email is None:
                raise PermissionError("Your session expired. Sign in again.")
            # Same boundary as submit: a Gradio worker thread handing work to the loop
            # the server captured at startup, and bounded so a wedged loop cannot pin
            # the thread forever.
            future = asyncio.run_coroutine_threadsafe(
                app.state.queue.cancel_for(email), app.state.loop)
            try:
                return future.result(timeout=CANCEL_TIMEOUT_S)
            except concurrent.futures.TimeoutError:
                future.cancel()
                raise RuntimeError("The server did not answer the stop request. Your "
                                   "render may still be going; try again.") from None

        @staticmethod
        def job_status(request, job_id):
            """This job's state -- for its owner only.

            The id is 128 bits, so guessing one is not a practical attack, but it is the
            one client-reachable identifier that indexes server state, and `cancel`
            makes a mistake here matter. An id belonging to someone else is answered
            exactly like an id that does not exist, so the error is not an oracle.
            """
            job = app.state.queue.status(job_id)          # raises UnknownJobError
            if job.email != user_of(request):             # None never equals an email
                raise UnknownJobError(
                    "That render job is not known to the server (it may predate a "
                    "restart). Submit it again.")
            return {"state": job.state, "message": job.message, "result": job.result,
                    "position": app.state.queue.position(job_id)}

    app.state.context = ServerContext
    gr.mount_gradio_app(app, build_app(context=ServerContext), path="/",
                        auth_dependency=auth_dependency,
                        max_file_size=app.state.max_upload_bytes)
    return app


#: Sent on the authorize URL. REQUIRED, not optional: without access_type=offline Google
#: returns no refresh token, and a render outlives the one-hour access token, so every
#: job would fail partway. They must go in `authorize_params`: Authlib silently drops
#: unknown keys from `client_kwargs` (access_type vanished; only prompt survived).
AUTHORIZE_PARAMS = {"access_type": "offline", "prompt": "consent"}


def build_oauth_client(cfg: AppConfig, server_metadata: dict | None = None):
    from authlib.integrations.starlette_client import OAuth
    extra = ({"server_metadata_url":
              "https://accounts.google.com/.well-known/openid-configuration"}
             if server_metadata is None else server_metadata)   # dict injected by tests
    return OAuth().register(
        name="google", client_id=cfg.client_id, client_secret=cfg.client_secret,
        client_kwargs={"scope": "openid email " + " ".join(oauth.SCOPES)},
        authorize_params=AUTHORIZE_PARAMS, **extra)


def main() -> None:
    import uvicorn
    cfg = AppConfig(
        client_id=os.environ["GEE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GEE_OAUTH_CLIENT_SECRET"],
        redirect_uri=os.environ["GEE_OAUTH_REDIRECT_URI"],
        cookie_secret=os.environ["GEE_COOKIE_SECRET"],
        output_root=Path(os.environ.get("GEE_OUTPUT_DIR", "/data/out")),
        job_timeout_s=float(os.environ.get("GEE_JOB_TIMEOUT_S", 14400)))
    client = build_oauth_client(cfg)
    from .spawn import spawn_worker
    # Caddy terminates TLS and talks plain HTTP to us; without trusted forwarded headers
    # Starlette sees http:// and the https-only cookie / OAuth redirect break. The default
    # trusts loopback and the private (RFC1918) ranges a compose network allocates from,
    # not a wildcard: a client outside them cannot spoof X-Forwarded-*. Tighten it to the
    # proxy's address with GEE_FORWARDED_ALLOW_IPS (comma-separated IPs/CIDRs).
    allow = os.environ.get("GEE_FORWARDED_ALLOW_IPS",
                           "127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
    uvicorn.run(create_app(cfg, client, spawn_worker), host="0.0.0.0", port=7860,
                proxy_headers=True, forwarded_allow_ips=allow)
