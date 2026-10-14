import asyncio
import json

import pytest

from gee_animation.webapp.jobs import JobQueue


class FakeProc:
    """Stands in for an asyncio subprocess: emits events, then exits."""
    def __init__(self, events, hang=False):
        self._events, self._hang, self.killed = events, hang, False
        self.returncode = None

    async def run(self, on_event):
        if self._hang:
            await asyncio.sleep(3600)
        for e in self._events:
            on_event(json.loads(e))
        self.returncode = 0

    def kill(self):
        self.killed = True


def _spawn_ok(mp4="/out/x.mp4"):
    def spawn(job, on_event):
        return FakeProc([json.dumps({"event": "done", "mp4": mp4, "status": "ok"})])
    return spawn


@pytest.mark.asyncio
async def test_a_submitted_job_runs_and_reports_its_result():
    q = JobQueue(spawn=_spawn_ok(), max_concurrency=3)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {"index": "ndvi"})
    await q.wait(job_id)
    job = q.status(job_id)
    assert job.state == "done" and job.result["mp4"] == "/out/x.mp4"


@pytest.mark.asyncio
async def test_only_three_run_at_once_and_the_fourth_reports_its_position():
    started = []

    def spawn(job, on_event):
        started.append(job["id"])
        return FakeProc([], hang=True)

    q = JobQueue(spawn=spawn, max_concurrency=3)
    ids = [await q.submit("a@hnee.de", {"refresh_token": "r"}, {}) for _ in range(4)]
    await asyncio.sleep(0.05)
    assert len(started) == 3
    assert q.status(ids[3]).state == "queued"
    assert q.position(ids[3]) == 1
    for i in ids:
        await q.cancel(i)


@pytest.mark.asyncio
async def test_cancel_kills_the_worker_and_frees_the_slot():
    procs = []

    def spawn(job, on_event):
        p = FakeProc([], hang=True)
        procs.append(p)
        return p

    q = JobQueue(spawn=spawn, max_concurrency=1)
    first = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    second = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await asyncio.sleep(0.05)
    await q.cancel(first)
    await asyncio.sleep(0.05)
    assert procs[0].killed and q.status(first).state == "cancelled"
    assert q.status(second).state == "running"      # the slot was freed
    await q.cancel(second)


@pytest.mark.asyncio
async def test_a_stalled_job_is_killed_at_the_timeout_and_says_so():
    q = JobQueue(spawn=lambda job, on_event: FakeProc([], hang=True),
                 max_concurrency=1, timeout_s=0.05)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await q.wait(job_id)
    job = q.status(job_id)
    assert job.state == "error" and "timed out" in job.message.lower()


@pytest.mark.asyncio
async def test_a_finished_job_stays_retrievable_after_the_queue_moves_on():
    """The browser closes; later jobs come and go; the first result is still there."""
    mp4s = iter(["/out/first.mp4", "/out/second.mp4", "/out/third.mp4"])

    def spawn(job, on_event):
        mp4 = next(mp4s)
        return FakeProc([json.dumps({"event": "done", "mp4": mp4, "status": "ok"})])

    q = JobQueue(spawn=spawn, max_concurrency=1)
    first = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await q.wait(first)
    for _ in range(2):
        later = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
        await q.wait(later)
    assert q.status(first).state == "done"
    assert q.status(first).result["mp4"] == "/out/first.mp4"
    assert q.status(later).result["mp4"] == "/out/third.mp4"


@pytest.mark.asyncio
async def test_a_process_that_exits_without_any_event_becomes_a_failed_job():
    q = JobQueue(spawn=lambda job, on_event: FakeProc([]), max_concurrency=1)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await q.wait(job_id)
    job = q.status(job_id)
    assert job.state == "error"
    assert "stopped unexpectedly" in job.message


@pytest.mark.asyncio
async def test_cancelling_a_queued_job_means_it_never_starts():
    started = []

    def spawn(job, on_event):
        started.append(job["id"])
        return FakeProc([], hang=True)

    q = JobQueue(spawn=spawn, max_concurrency=1)
    first = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    second = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await asyncio.sleep(0.05)
    assert q.status(second).state == "queued"
    await q.cancel(second)
    await q.cancel(first)                      # frees the slot the second would take
    await asyncio.sleep(0.05)
    assert q.status(second).state == "cancelled"
    assert started == [first]


@pytest.mark.asyncio
async def test_a_worker_error_event_becomes_a_failed_job():
    def spawn(job, on_event):
        return FakeProc([json.dumps({"event": "error", "message": "sign in again"})])

    q = JobQueue(spawn=spawn, max_concurrency=1)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await q.wait(job_id)
    assert q.status(job_id).state == "error"
    assert "sign in again" in q.status(job_id).message
