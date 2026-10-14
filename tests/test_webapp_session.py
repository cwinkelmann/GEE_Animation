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
