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
