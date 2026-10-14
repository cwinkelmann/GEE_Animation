"""A small async queue: at most `max_concurrency` worker processes at a time.

Jobs are owned by the queue, not by the browser tab that submitted them — a user
who closes the page comes back to a finished render in their run browser.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

#: How long a finished job's record is kept. `Job.result` holds the whole `done` event,
#: including every frame path (hundreds for a 10-day cadence), so records kept for the
#: process lifetime are megabytes at 20 users. An hour is far longer than the poll loop
#: needs to report the result, and the user's video lives in their run directory, not
#: here -- a pruned job id simply reads as "not known to the server".
RETAIN_FINISHED_S = 3600

_TERMINAL = ("done", "error", "cancelled")


class UnknownJobError(LookupError):
    """No job with this id (never submitted, pruned, or from before a restart)."""


@dataclass
class Job:
    id: str
    email: str
    state: Literal["queued", "running", "done", "error", "cancelled"] = "queued"
    message: str = ""
    result: dict | None = None
    proc: object | None = field(default=None, repr=False)
    #: `time.monotonic()` when the job reached a terminal state; None while unfinished.
    finished: float | None = None


class JobQueue:
    def __init__(self, spawn, max_concurrency: int = 3, timeout_s: float = 14400,
                 retain_finished_s: float = RETAIN_FINISHED_S):
        self._spawn = spawn
        self._sem = asyncio.Semaphore(max_concurrency)
        self._timeout = timeout_s
        self._retain = retain_finished_s
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        #: Ids still WAITING for a slot, in arrival order -- not every job ever
        #: submitted, so `position` stays O(queue) on a poll every 2 s per waiting user.
        self._order: list[str] = []
        #: job id -> refresh token, for jobs whose payload this process may still hold.
        self._inflight: dict[str, str] = {}

    async def submit(self, email: str, credentials: dict, params: dict) -> str:
        self._prune()
        job_id = secrets.token_urlsafe(16)
        self._jobs[job_id] = Job(id=job_id, email=email)
        self._order.append(job_id)
        payload = {"id": job_id, **credentials, "params": params}
        token = credentials.get("refresh_token")
        if token:
            self._inflight[job_id] = token
        self._tasks[job_id] = asyncio.create_task(self._run(job_id, payload))
        return job_id

    def live_secrets(self) -> list[str]:
        """Refresh tokens this process may still hold in an undelivered worker payload.

        The log redactor's secret list is built from live sessions, and a session can
        now be dropped while a job it started is still in flight (expiry, a re-sign-in,
        a logout), which would silently stop redacting that token. The redactor takes
        the union of both lists instead.

        Deliberately conservative: an entry is kept until the job reaches a terminal
        state, which is at or after the moment `WorkerProcess.run` drains the payload
        and drops its reference. Listing a token slightly longer than it is reachable
        costs nothing; one missing from the list is a token a log line could print.
        """
        return list(self._inflight.values())

    def status(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise UnknownJobError(
                "That render job is not known to the server (it may predate a restart). "
                "Submit it again.") from None

    def position(self, job_id: str) -> int:
        return self._order.index(job_id) + 1 if job_id in self._order else 0

    def _prune(self) -> None:
        """Forget jobs that finished more than `self._retain` ago."""
        cutoff = time.monotonic() - self._retain
        for job_id in [i for i, job in self._jobs.items()
                       if job.finished is not None and job.finished < cutoff]:
            self._jobs.pop(job_id, None)
            self._tasks.pop(job_id, None)

    async def wait(self, job_id: str) -> None:
        await asyncio.shield(asyncio.gather(self._tasks[job_id], return_exceptions=True))

    async def cancel(self, job_id: str) -> None:
        job = self._jobs[job_id]
        if job.state in _TERMINAL:
            return                              # already finished: a no-op, not an error
        job.state = "cancelled"
        job.message = "Cancelled at your request."
        if job.proc is not None:
            job.proc.kill()
        self._tasks[job_id].cancel()

    async def cancel_for(self, email: str) -> int:
        """Cancel every unfinished job owned by `email`; return how many were cancelled.

        Ownership is the ONLY selector. The caller resolves `email` from the server-side
        session, so no job id crosses the wire and there is nothing for a client to
        tamper with -- the same lesson as the AOI path: an identifier from the browser
        is not proof of ownership. Fails closed: an empty email cancels nothing.
        """
        if not email:
            return 0
        mine = [i for i, job in self._jobs.items()
                if job.email == email and job.state not in _TERMINAL]
        for job_id in mine:
            await self.cancel(job_id)
        return len(mine)

    async def _run(self, job_id: str, payload: dict) -> None:
        try:
            await self._run_guarded(job_id, payload)
        finally:
            payload = None                 # see the note in `_run_guarded`
            # One place for every terminal path (done, error, timeout, cancel -- even a
            # cancel while still queued, which raises out of the semaphore): the job is
            # over, so its token is no longer in flight and its record can age out.
            self._inflight.pop(job_id, None)
            self._discard_from_queue(job_id)
            job = self._jobs.get(job_id)
            if job is not None and job.finished is None:
                job.finished = time.monotonic()

    def _discard_from_queue(self, job_id: str) -> None:
        try:
            self._order.remove(job_id)
        except ValueError:
            pass                           # already running, or already finished

    async def _run_guarded(self, job_id: str, payload: dict) -> None:
        job = self._jobs[job_id]
        async with self._sem:
            self._discard_from_queue(job_id)   # it has a slot: no longer waiting for one
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
                # Hand the credentials over and stop referencing them here. The worker
                # object holds the payload until it has written it to the subprocess's
                # stdin, then drops it too -- so the token's reachable lifetime in this
                # process is the handover, not the whole render.
                payload = None
                await asyncio.wait_for(proc.run(on_event), timeout=self._timeout)
            except asyncio.TimeoutError:
                proc.kill()
                if job.state == "running":    # a job that already reported done stays done
                    job.state = "error"
                    job.message = (f"Render timed out after {self._timeout / 60:.0f} minutes "
                                   "and was stopped. Try a shorter date range.")
            except asyncio.CancelledError:
                # `cancel` kills the process group first, but a cancellation can also
                # land while `proc.run` is still starting the subprocess (before
                # `job.proc` was ever set, or before kill saw a pid): kill again here so
                # a cancelled job can never leave a worker -- or its ffmpeg child --
                # alive. `kill` is idempotent and safe on an already-gone process.
                if proc is not None:
                    proc.kill()
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
