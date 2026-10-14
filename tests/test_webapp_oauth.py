import types

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from gee_animation.webapp import oauth
from gee_animation.webapp.session import SessionStore

RT = "1//REFRESH-SECRET"


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


# ---- routes: fake Authlib client, no network ------------------------------

class FakeClient:
    def __init__(self, token):
        self.token = token
        self.redirect_uri = None

    async def authorize_redirect(self, request, redirect_uri):
        self.redirect_uri = redirect_uri
        return RedirectResponse("https://accounts.google.test/auth?state=abc")

    async def authorize_access_token(self, request):
        return self.token


def _app(token):
    store = SessionStore()
    client = FakeClient(token)
    app = FastAPI()
    app.state.sessions = store
    oauth.add_routes(app, oauth.OAuthConfig("id", "sec", "https://x/oauth2/callback"),
                     store, client)
    return TestClient(app, follow_redirects=False), store, client


def _good():
    return {"userinfo": {"email": "a@hnee.de"}, "refresh_token": RT,
            "access_token": "AT-SECRET"}


def _sid(response):
    return response.headers["set-cookie"].split(";")[0].split("=", 1)[1]


def test_login_redirects_to_the_client_with_the_configured_redirect_uri():
    tc, _, client = _app(_good())
    r = tc.get("/login")
    assert r.headers["location"] == "https://accounts.google.test/auth?state=abc"
    assert client.redirect_uri == "https://x/oauth2/callback"


def test_callback_sets_a_hardened_cookie_holding_only_the_opaque_sid():
    tc, store, _ = _app(_good())
    r = tc.get("/oauth2/callback")
    cookie = r.headers["set-cookie"]
    sid = _sid(r)
    assert store.get(sid).email == "a@hnee.de"
    assert store.get(sid).refresh_token == RT
    low = cookie.lower()
    assert "httponly" in low and "secure" in low and "samesite=lax" in low
    assert "a@hnee.de" not in cookie and RT not in cookie


def test_callback_leaks_no_token_into_body_or_headers():
    tc, _, _ = _app(_good())
    r = tc.get("/oauth2/callback")
    assert r.headers["location"] == "/"
    blob = r.text + repr(dict(r.headers))
    assert RT not in blob and "AT-SECRET" not in blob


def test_callback_without_a_refresh_token_fails_clearly():
    tc, store, _ = _app({"userinfo": {"email": "a@hnee.de"}})
    r = tc.get("/oauth2/callback")
    assert r.status_code == 400
    assert "sign in again" in r.text.lower()
    assert "set-cookie" not in r.headers
    assert store.all() == []


def test_logout_drops_the_session_and_clears_the_cookie():
    tc, store, _ = _app(_good())
    sid = _sid(tc.get("/oauth2/callback"))
    tc.cookies.set("sid", sid)
    r = tc.get("/logout")
    assert r.headers["location"] == "/login"
    assert store.get(sid) is None
    assert "max-age=0" in r.headers["set-cookie"].lower()
    request = types.SimpleNamespace(cookies={"sid": sid}, app=types.SimpleNamespace(
        state=types.SimpleNamespace(sessions=store)))
    assert oauth.current_user(request) is None
