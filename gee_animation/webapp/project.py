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


def remember(store, sid: str, project_id: str) -> None:
    """Record the validated project on the session.

    The session can vanish between sign-in and entering the project id (expiry,
    server restart); `SessionStore.set_project` would raise a bare KeyError, so say
    what happened and what to do instead.
    """
    if store.get(sid) is None:
        raise ProjectError("Your session expired. Please sign in again.")
    store.set_project(sid, project_id.strip())
