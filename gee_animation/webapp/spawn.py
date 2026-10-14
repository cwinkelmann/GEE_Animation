# gee_animation/webapp/spawn.py
"""Spawn `webapp.worker` as a subprocess and stream its events back."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys

#: asyncio's default StreamReader line limit is 64 KiB, and the worker's `done` event
#: carries the whole frame-path list (a multi-year daily render easily exceeds that).
STDOUT_LIMIT = 32 * 1024 * 1024

#: Server-only secrets a render subprocess has no business holding. (The user's refresh
#: token is never in the environment at all: it travels on stdin.)
_SCRUBBED_PREFIXES = ("GEE_OAUTH_",)
_SCRUBBED_NAMES = ("GEE_COOKIE_SECRET",)


def worker_env(environ=None) -> dict:
    """The parent's environment minus the server's own secrets."""
    environ = os.environ if environ is None else environ
    return {k: v for k, v in environ.items()
            if k not in _SCRUBBED_NAMES and not k.startswith(_SCRUBBED_PREFIXES)}


class WorkerProcess:
    def __init__(self, payload: dict):
        self._payload = payload
        self._proc = None

    async def run(self, on_event) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "gee_animation.webapp.worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            limit=STDOUT_LIMIT, env=worker_env(),
            start_new_session=True)              # own process group: kill() signals the group
        self._proc.stdin.write(json.dumps(self._payload).encode())
        # The credentials have been handed over; a finished job must not keep holding
        # the refresh token in this object.
        self._payload = None
        await self._proc.stdin.drain()
        self._proc.stdin.close()
        try:
            async for line in self._proc.stdout:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    # Not our protocol: a library the render path imports printed to
                    # stdout. Ignore it rather than killing the job on someone else's
                    # chatter (Ruling N). The worker's own events are always valid JSON.
                    continue
                on_event(event)
        except (ValueError, asyncio.LimitOverrunError):
            # A single line beyond STDOUT_LIMIT: the stream is unusable from here.
            self.kill()
            on_event({"event": "error", "message": (
                "The render finished with a result too large to read back, so it was "
                "stopped. Try a coarser cadence or a shorter date range.")})
            return
        await self._proc.wait()

    def kill(self) -> None:
        """SIGKILL the worker's whole process group, so children (ffmpeg) die too."""
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass                                 # already gone


def spawn_worker(payload: dict, on_event) -> WorkerProcess:
    return WorkerProcess(payload)
