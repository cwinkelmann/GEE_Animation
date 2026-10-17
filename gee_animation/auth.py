"""Earth Engine authentication + initialization."""
from __future__ import annotations

import json
import os
from pathlib import Path

import ee


def _service_account_email(key_path: str) -> str | None:
    """The `client_email` from a service-account JSON key file."""
    try:
        return json.loads(Path(key_path).read_text()).get("client_email")
    except (OSError, ValueError):
        return None


def init(project: str, ee_module=ee) -> None:
    """Initialize Earth Engine.

    For headless/containerized use set `EE_SERVICE_ACCOUNT_KEY` to a service-account
    JSON key path (and optionally `EE_SERVICE_ACCOUNT` for the email); otherwise the
    cached interactive credentials are used, prompting `Authenticate()` if needed.
    """
    if not project or not str(project).strip():
        raise ValueError(
            "an Earth Engine project id is required — pass your own Google Cloud "
            "project id (registered for Earth Engine, see the setup notebook)")
    key = os.environ.get("EE_SERVICE_ACCOUNT_KEY")
    if key:
        email = os.environ.get("EE_SERVICE_ACCOUNT") or _service_account_email(key)
        ee_module.Initialize(ee_module.ServiceAccountCredentials(email, key), project=project)
        return
    try:
        ee_module.Initialize(project=project)
    except Exception:
        ee_module.Authenticate()
        ee_module.Initialize(project=project)
