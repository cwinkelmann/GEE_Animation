"""A small async queue: at most `max_concurrency` worker processes at a time.

Jobs are owned by the queue, not by the browser tab that submitted them — a user
who closes the page comes back to a finished render in their run browser.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Job:
    id: str
    email: str
    state: Literal["queued", "running", "done", "error", "cancelled"] = "queued"
    message: str = ""
    result: dict | None = None
    proc: object | None = field(default=None, repr=False)


class JobQueue:
    def __init__(self, spawn, max_concurrency: int = 3, timeout_s: float = 5400):
        self._spawn = spawn
        self._sem = asyncio.Semaphore(max_concurrency)
        self._timeout = timeout_s
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._order: list[str] = []

    async def submit(self, email: str, credentials: dict, params: dict) -> str:
        job_id = secrets.token_urlsafe(16)
        self._jobs[job_id] = Job(id=job_id, email=email)
        self._order.append(job_id)
        payload = {"id": job_id, **credentials, "params": params}
        self._tasks[job_id] = asyncio.create_task(self._run(job_id, payload))
        return job_id

    def status(self, job_id: str) -> Job:
        return self._jobs[job_id]

    def position(self, job_id: str) -> int:
        queued = [i for i in self._order if self._jobs[i].state == "queued"]
        return queued.index(job_id) + 1 if job_id in queued else 0

    async def wait(self, job_id: str) -> None:
        await asyncio.shield(asyncio.gather(self._tasks[job_id], return_exceptions=True))

    async def cancel(self, job_id: str) -> None:
        job = self._jobs[job_id]
        if job.state in ("done", "error", "cancelled"):
            return
        job.state = "cancelled"
        if job.proc is not None:
            job.proc.kill()
        self._tasks[job_id].cancel()

    async def _run(self, job_id: str, payload: dict) -> None:
        job = self._jobs[job_id]
        async with self._sem:
            # Not expected to fire: asyncio raises CancelledError inside
            # Semaphore.acquire, before this point. Kept deliberately so that if a
            # future change adds an await ahead of this line, a cancelled job still
            # cannot spawn a worker.
            if job.state == "cancelled":
                return
            job.state = "running"

            def on_event(event):
                if event["event"] == "done":
                    job.state, job.result = "done", event
                elif event["event"] == "error":
                    job.state, job.message = "error", event["message"]
                else:
                    job.message = event.get("message", "")

            proc = None
            try:
                proc = self._spawn(payload, on_event)
                job.proc = proc
                await asyncio.wait_for(proc.run(on_event), timeout=self._timeout)
            except asyncio.TimeoutError:
                proc.kill()
                if job.state == "running":    # a job that already reported done stays done
                    job.state = "error"
                    job.message = (f"Render timed out after {self._timeout / 60:.0f} minutes "
                                   "and was stopped. Try a shorter date range.")
            except asyncio.CancelledError:
                job.state = "cancelled"
            except Exception:
                # Generic message on purpose: exception text may carry credentials.
                if proc is not None:
                    proc.kill()
                if job.state == "running":
                    job.state = "error"
                    job.message = "The render failed to start."
            if job.state == "running":            # process exited without a done event
                job.state, job.message = "error", "The render stopped unexpectedly."
