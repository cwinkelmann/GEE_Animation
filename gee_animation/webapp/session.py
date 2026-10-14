"""In-memory session store: sid -> who you are, your EE project, your refresh token.

Deliberately NOT persistent. A restart logs everyone out, which for a group this
size is the right trade for never writing a refresh token to disk.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

#: How long a session lives. Seven days, chosen against the two forces that matter:
#: a render may now run four hours (AppConfig.job_timeout_s) and the person expects to
#: close the tab, come back the next morning and still find their video in the run
#: browser -- which needs a live session, because `list_runs` is scoped by it. So the
#: window has to be comfortably longer than one render plus a night, and a weekend too.
#: It must also be much SHORTER than the process lifetime: the container runs
#: `restart: unless-stopped`, so without an expiry an abandoned browser's refresh token
#: sits in memory for months. A week satisfies both; a user who waits longer signs in
#: again, which costs one click.
DEFAULT_MAX_AGE_S = 7 * 86400


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
    def __init__(self, max_age_s: float = DEFAULT_MAX_AGE_S, now=time.time):
        self._sessions: dict[str, Session] = {}
        self._max_age_s = max_age_s
        self._now = now                       # injected so tests never sleep

    def new(self, email: str, refresh_token: str) -> str:
        # One live session per user: signing in again drops the previous one. The
        # trade-off is explicit -- a second browser logs the first one out -- and it is
        # the right default for a session that holds a refresh token: otherwise every
        # re-sign-in leaves another copy of a live credential in memory, reachable until
        # the process restarts, with no way for the user to revoke it.
        key = self._key(email)
        for sid in [s for s, session in self._sessions.items()
                    if self._key(session.email) == key]:
            self._sessions.pop(sid, None)
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = Session(email=email, refresh_token=refresh_token,
                                      created=self._now())
        return sid

    def get(self, sid: str) -> Session | None:
        session = self._sessions.get(sid)
        if session is None:
            return None
        if self._expired(session):
            # Drop it here rather than only hiding it: an expired session's refresh
            # token must not stay reachable from the store.
            self._sessions.pop(sid, None)
            return None
        return session

    def set_project(self, sid: str, project: str) -> None:
        self._sessions[sid].project = project

    def drop(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    def all(self) -> list:
        """Every live session. Used by the log redactor to know the current secrets."""
        for sid in [s for s, session in self._sessions.items() if self._expired(session)]:
            self._sessions.pop(sid, None)      # nothing else ever looks up an abandoned sid
        return list(self._sessions.values())

    def _expired(self, session: Session) -> bool:
        return self._now() - session.created >= self._max_age_s

    @staticmethod
    def _key(email: str) -> str:
        return (email or "").strip().lower()
