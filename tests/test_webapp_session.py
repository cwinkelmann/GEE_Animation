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


# --- review round B: expiry is enforced, and a re-sign-in drops the old session --------

def _clock(start=1000.0):
    now = {"t": start}
    return now, (lambda: now["t"])


def test_a_session_older_than_the_window_is_rejected_and_a_fresh_one_is_not():
    """`Session.created` was written and never read, so a session lived until the
    process restarted."""
    now, clock = _clock()
    store = SessionStore(max_age_s=100, now=clock)
    sid = store.new("a@hnee.de", "tok")
    now["t"] += 99
    assert store.get(sid) is not None            # still inside the window
    now["t"] += 2
    assert store.get(sid) is None                # expired, and rejected


def test_an_expired_sessions_token_is_dropped_not_merely_hidden():
    """Hiding it would leave a live refresh token reachable from the store (and in the
    redactor's secret list) for the life of the process."""
    now, clock = _clock()
    store = SessionStore(max_age_s=100, now=clock)
    store.new("a@hnee.de", "tok-expiring")
    live = store.new("b@hnee.de", "tok-live")
    now["t"] += 101
    assert store.get(live) is None               # both are past the window
    store.new("c@hnee.de", "tok-fresh")
    assert [s.refresh_token for s in store.all()] == ["tok-fresh"]
    assert store._sessions and len(store._sessions) == 1


def test_the_default_window_outlives_a_four_hour_render_but_not_the_process():
    """Chosen deliberately: longer than one render plus a night (so the person comes
    back to their video), far shorter than the container's uptime."""
    from gee_animation.webapp.session import DEFAULT_MAX_AGE_S
    assert DEFAULT_MAX_AGE_S == 7 * 86400
    assert DEFAULT_MAX_AGE_S > 4 * 3600 + 86400


def test_signing_in_again_invalidates_the_previous_session():
    """Otherwise every sign-in leaves another live refresh token in memory, with no way
    for the user to revoke it. The trade-off: a second browser logs the first out."""
    store = SessionStore()
    old = store.new("a@hnee.de", "tok-1")
    other = store.new("b@hnee.de", "tok-b")
    new = store.new("A@HNEE.de ", "tok-2")       # same person, as Google might spell it
    assert store.get(old) is None and new != old
    assert store.get(new).refresh_token == "tok-2"
    assert store.get(other).refresh_token == "tok-b"      # nobody else is touched
    # The dropped session's token must not stay reachable -- including via the redactor.
    assert sorted(s.refresh_token for s in store.all()) == ["tok-2", "tok-b"]
