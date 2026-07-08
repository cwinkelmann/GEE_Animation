"""Earth Engine authentication + initialization."""
from __future__ import annotations

import ee


def init(project: str, ee_module=ee) -> None:
    try:
        ee_module.Initialize(project=project)
    except Exception:
        ee_module.Authenticate()
        ee_module.Initialize(project=project)
