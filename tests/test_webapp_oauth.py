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
