import types

import pytest

from gee_animation.webapp.project import ProjectError, remember, validate
from gee_animation.webapp.session import SessionStore


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


def test_remember_stores_the_project_on_a_live_session():
    store = SessionStore()
    sid = store.new("a@hnee.de", "r")
    remember(store, sid, " my-proj ")
    assert store.get(sid).project == "my-proj"


def test_an_expired_session_says_sign_in_again_not_keyerror():
    """Session lost (expiry/restart) between sign-in and entering the project id."""
    store = SessionStore()
    sid = store.new("a@hnee.de", "r")
    store.drop(sid)
    with pytest.raises(ProjectError) as e:
        remember(store, sid, "my-proj")
    assert "session expired" in str(e.value)
    assert "sign in again" in str(e.value)
    assert not isinstance(e.value, KeyError)
