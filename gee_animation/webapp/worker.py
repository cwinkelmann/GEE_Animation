"""One render, one process, one user's Earth Engine identity.

Forced by `ee._state._state` being a process-wide global: the EE client can hold
exactly one set of credentials, so a job that runs under a user's identity gets a
process of its own. Credentials arrive on **stdin** — never argv (world-readable in
/proc) and never the environment.
"""
from __future__ import annotations

import json
import math
import sys
import types

import ee
from google.oauth2.credentials import Credentials

from . import project
from .. import gui

TOKEN_URI = "https://oauth2.googleapis.com/token"
_REAUTH_HINT = ("Your Google sign-in expired or was revoked. Sign in again and "
                "re-run; nothing was charged to your project.")


def _number(value):
    """A JSON-safe float, or None. NaN/inf are not JSON (`json.loads` of a bare NaN
    works in Python but not for strict parsers), and numpy scalars are not serialisable."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _series_payload(series) -> list:
    """`(period, inside, outside)` tuples as plain JSON lists (never pickled)."""
    return [[str(period), _number(inside), _number(outside)]
            for period, inside, outside in (series or [])]


def _emit(stream, **event) -> None:
    stream.write(json.dumps(event, allow_nan=False) + "\n")
    stream.flush()


def main(stdin=None, stdout=None, ee_module=ee, runner=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    runner = runner or gui.run_animation
    job = json.loads(stdin.read())
    token = job["refresh_token"]
    try:
        creds = Credentials(
            token=None, refresh_token=token, token_uri=TOKEN_URI,
            client_id=job["client_id"], client_secret=job["client_secret"],
            scopes=["https://www.googleapis.com/auth/earthengine",
                    "https://www.googleapis.com/auth/cloud-platform"])
        # `project.validate`, not a bare `ee_module.Initialize`: it initialises AND
        # makes one trivial getInfo call, so the commonest setup error by far -- a Cloud
        # project that was never registered for Earth Engine -- is reported with the
        # "enable the API here" link instead of Earth Engine's raw text, and before any
        # imagery work starts. It runs HERE, in the worker, and not in `submit`: it
        # needs the network and it calls `ee.Initialize`, and `ee._state._state` is
        # process-wide, so doing it in the shared web process would clobber every other
        # user. Its ProjectError falls into the handler below, which classifies the
        # expired-sign-in case and scrubs the token exactly as for any other failure.
        project.validate(creds, job["project"], ee_module=ee_module)
        _emit(stdout, event="progress", message="Earth Engine initialised")
        # EE is already live under the user's identity, so run_animation must NOT
        # re-authenticate: its deps.init becomes a no-op (see gui.py:448).
        deps = types.SimpleNamespace(**vars(gui.DEFAULT_DEPS))
        deps.init = lambda project=None: None
        # The job's project is the authoritative one (the user's own, as typed into the
        # project field, and checked by `project.validate` above); gui's params carry a
        # `project` too, so drop it or `runner` gets the keyword twice.
        params = {k: v for k, v in job["params"].items() if k != "project"}
        mp4, gif, pngs, zip_path, status, series = runner(
            project=job["project"], deps=deps, **params)
        _emit(stdout, event="done", mp4=mp4, gif=gif, frames=pngs,
              zip=zip_path, status=status, series=_series_payload(series))
        return 0
    except Exception as exc:                       # reported, never raised at the user
        message = str(exc)
        # Classify before scrubbing: scrubbing a short token would mangle the markers.
        if "invalid_grant" in message or "expired or revoked" in message:
            message = _REAUTH_HINT
        elif token:
            message = message.replace(token, "***")
        _emit(stdout, event="error", message=message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
