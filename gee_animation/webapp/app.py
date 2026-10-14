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
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from . import caps, logsafe, oauth, project, storage
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


class _LoginRedirect:
    """Send an anonymous browser to /login instead of showing Gradio's bare 401.

    Gradio answers an unauthenticated page load with 401 JSON, which a browser renders
    as an error page. Pure ASGI rather than `BaseHTTPMiddleware` so Gradio's streaming
    queue responses are never buffered. Only the page itself ("/" or any request that
    asks for HTML) is redirected; API calls keep their 401.
    """

    _OPEN = ("/login", "/oauth2/callback", "/logout")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "GET" \
                and not scope["path"].startswith(self._OPEN):
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

    @asynccontextmanager
    async def lifespan(_app):
        await _on_start()
        yield

    app = FastAPI(lifespan=lifespan)
    # Authlib stores the OAuth `state` (the CSRF token) in a Starlette session, so
    # /login raises without this middleware. The secret signs that cookie only; our own
    # session cookie is an opaque sid looked up in SessionStore (Ruling S).
    app.add_middleware(SessionMiddleware, secret_key=cfg.cookie_secret,
                       https_only=True, same_site="lax")
    app.add_middleware(_LoginRedirect)
    app.state.sessions = SessionStore()
    app.state.queue = JobQueue(spawn=spawn, max_concurrency=cfg.max_concurrency)
    app.state.max_upload_bytes = cfg.max_upload_mb * 1024 * 1024
    app.state.config = cfg

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
            stamp = time.strftime("%Y%m%d-%H%M%S")
            params = dict(params)
            # The worker needs the user's project; the GUI's textbox is where they type
            # it. `remember` (not set_project) so a lost session is an actionable error.
            project_id = (params.get("project") or session.project or "").strip()
            if not project_id:
                raise project.ProjectError(
                    "Enter your Google Cloud project id (e.g. my-ee-project).")
            project.remember(app.state.sessions, sid, project_id)
            run_dir = (storage.user_dir(cfg.output_root, session.email)
                       / f"{params.get('sensor')}_{params.get('index')}_{stamp}")
            params["out_dir"] = str(run_dir)
            # The render runs in another process, later, possibly after Gradio has
            # cleaned its upload cache: hand it a copy that lives with the run (and is
            # swept with it), not Gradio's temp path.
            aoi_src = params.get("aoi_path")
            if aoi_src:
                aoi_dir = run_dir / "aoi"
                aoi_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(aoi_src, aoi_dir / Path(aoi_src).name)
                params["aoi_path"] = str(aoi_dir / Path(aoi_src).name)
            credentials = {"refresh_token": session.refresh_token,
                           "client_id": cfg.client_id, "client_secret": cfg.client_secret,
                           "project": project_id}
            return asyncio.run_coroutine_threadsafe(
                app.state.queue.submit(session.email, credentials, params),
                app.state.loop).result()

        @staticmethod
        def job_status(job_id):
            job = app.state.queue.status(job_id)
            return {"state": job.state, "message": job.message, "result": job.result,
                    "position": app.state.queue.position(job_id)}

    app.state.context = ServerContext
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
