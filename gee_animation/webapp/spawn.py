# gee_animation/webapp/spawn.py
"""Spawn `webapp.worker` as a subprocess and stream its events back."""
from __future__ import annotations

import asyncio
import json
import sys


class WorkerProcess:
    def __init__(self, payload: dict):
        self._payload = payload
        self._proc = None

    async def run(self, on_event) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "gee_animation.webapp.worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            start_new_session=True)              # own process group, so kill() is clean
        self._proc.stdin.write(json.dumps(self._payload).encode())
        await self._proc.stdin.drain()
        self._proc.stdin.close()
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
        await self._proc.wait()

    def kill(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.kill()


def spawn_worker(payload: dict, on_event) -> WorkerProcess:
    return WorkerProcess(payload)
