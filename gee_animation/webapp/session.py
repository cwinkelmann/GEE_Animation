"""In-memory session store: sid -> who you are, your EE project, your refresh token.

Deliberately NOT persistent. A restart logs everyone out, which for a group this
size is the right trade for never writing a refresh token to disk.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    email: str
    # repr=False so the token cannot reach a log line or a traceback frame summary.
    refresh_token: str = field(repr=False)
    project: str | None = None
    created: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return repr(self)


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def new(self, email: str, refresh_token: str) -> str:
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = Session(email=email, refresh_token=refresh_token)
        return sid

    def get(self, sid: str) -> Session | None:
        return self._sessions.get(sid)

    def set_project(self, sid: str, project: str) -> None:
        self._sessions[sid].project = project

    def drop(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    def all(self) -> list:
        """Every live session. Used by the log redactor to know the current secrets."""
        return list(self._sessions.values())
