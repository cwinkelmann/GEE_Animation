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


async def _wait_bounded(q, job_id, seconds=5):
    """`q.wait`, but a queue that never finishes the job FAILS this test in `seconds`
    instead of hanging the suite. `asyncio.wait`, not `wait_for`: wait_for's timeout path
    cancels and then awaits the cancellation, which a task that swallows it never ends."""
    waiter = asyncio.ensure_future(q.wait(job_id))
    _, pending = await asyncio.wait({waiter}, timeout=seconds)
    if pending:
        waiter.cancel()
        await q.cancel(job_id)
        pytest.fail(f"job {job_id} was still {q.status(job_id).state!r} after {seconds}s: "
                    "the queue's wall-clock timeout did not fire")


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
    procs = []

    def spawn(job, on_event):
        procs.append(FakeProc([], hang=True))
        return procs[0]

    q = JobQueue(spawn=spawn, max_concurrency=1, timeout_s=0.05)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await _wait_bounded(q, job_id)
    job = q.status(job_id)
    assert job.state == "error" and "timed out" in job.message.lower()
    assert procs[0].killed


@pytest.mark.asyncio
async def test_a_spawn_failure_fails_the_job_without_leaking_the_error_text_and_frees_the_slot():
    calls = []

    def spawn(job, on_event):
        calls.append(job["id"])
        if len(calls) == 1:
            raise OSError("boom refresh_token=SECRET")
        return FakeProc([json.dumps({"event": "done", "mp4": "/out/y.mp4", "status": "ok"})])

    q = JobQueue(spawn=spawn, max_concurrency=1)
    first = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    second = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await q.wait(first)
    await q.wait(second)
    assert q.status(first).state == "error"
    assert "SECRET" not in q.status(first).message and "boom" not in q.status(first).message
    assert q.status(second).state == "done"           # the slot was freed


@pytest.mark.asyncio
async def test_a_done_job_is_not_turned_into_an_error_by_a_lingering_process():
    class Lingering(FakeProc):
        async def run(self, on_event):
            on_event({"event": "done", "mp4": "/out/z.mp4", "status": "ok"})
            await asyncio.sleep(3600)

    q = JobQueue(spawn=lambda job, on_event: Lingering([]), max_concurrency=1, timeout_s=0.05)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await _wait_bounded(q, job_id)
    assert q.status(job_id).state == "done"
    assert q.status(job_id).result["mp4"] == "/out/z.mp4"


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


def test_the_default_job_timeout_is_four_hours_not_ninety_minutes():
    """caps allow 12 years at a 10-day cadence; the Caddyfile and README promise 4 h."""
    assert JobQueue(spawn=None)._timeout == 14400


@pytest.mark.asyncio
async def test_an_unknown_job_id_is_a_described_error_not_a_bare_keyerror():
    from gee_animation.webapp.jobs import UnknownJobError
    q = JobQueue(spawn=_spawn_ok())
    with pytest.raises(UnknownJobError, match="not known"):
        q.status("nope")


# --- review round B: cancel reaches production, and only for its owner ------------------

@pytest.mark.asyncio
async def test_cancel_for_stops_only_that_users_jobs_and_frees_the_slot():
    """The ownership rule at the queue layer: `cancel_for` selects by email only, so
    there is no job id for a client to supply. B asking to cancel must not touch A."""
    procs = []

    def spawn(job, on_event):
        p = FakeProc([], hang=True)
        procs.append(p)
        return p

    q = JobQueue(spawn=spawn, max_concurrency=1)
    a_job = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    b_job = await q.submit("b@hnee.de", {"refresh_token": "r"}, {})
    await asyncio.sleep(0.05)
    assert q.status(a_job).state == "running" and q.status(b_job).state == "queued"

    assert await q.cancel_for("b@hnee.de") == 1          # B's own queued job
    await asyncio.sleep(0.05)
    assert q.status(b_job).state == "cancelled"
    assert q.status(a_job).state == "running"            # A's render is untouched
    assert not procs[0].killed

    assert await q.cancel_for("a@hnee.de") == 1
    await asyncio.sleep(0.05)
    assert procs[0].killed and q.status(a_job).state == "cancelled"
    assert "Cancelled" in q.status(a_job).message


@pytest.mark.asyncio
async def test_cancel_for_an_unknown_or_empty_email_cancels_nothing():
    """Fails closed: an unrecognised caller must not become a wildcard."""
    q = JobQueue(spawn=lambda job, on_event: FakeProc([], hang=True), max_concurrency=2)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await asyncio.sleep(0.05)
    assert await q.cancel_for("") == 0
    assert await q.cancel_for(None) == 0
    assert await q.cancel_for("nobody@hnee.de") == 0
    assert q.status(job_id).state == "running"
    await q.cancel(job_id)


@pytest.mark.asyncio
async def test_cancelling_an_already_finished_job_is_a_no_op_not_an_error():
    q = JobQueue(spawn=_spawn_ok(), max_concurrency=1)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await _wait_bounded(q, job_id)
    assert q.status(job_id).state == "done"
    assert await q.cancel_for("a@hnee.de") == 0       # nothing active to cancel
    await q.cancel(job_id)                            # and the direct call is harmless
    assert q.status(job_id).state == "done" and q.status(job_id).result["mp4"]


@pytest.mark.asyncio
async def test_a_cancellation_arriving_while_the_worker_starts_still_kills_it():
    """`cancel` kills the group before cancelling the task, but a cancellation can land
    while `proc.run` is still starting the subprocess -- before `job.proc` was set. The
    CancelledError branch kills again so no worker (nor an ffmpeg child) is left."""
    procs = []

    class SlowStart(FakeProc):
        async def run(self, on_event):
            await asyncio.sleep(3600)        # "still starting the subprocess"

    def spawn(job, on_event):
        p = SlowStart([])
        procs.append(p)
        return p

    q = JobQueue(spawn=spawn, max_concurrency=1)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await asyncio.sleep(0.05)
    q._jobs[job_id].proc = None                 # as if cancel arrived a moment earlier
    assert await q.cancel_for("a@hnee.de") == 1
    await asyncio.sleep(0.05)
    assert procs[0].killed                      # killed by the CancelledError branch
    assert q.status(job_id).state == "cancelled"


# --- review round C: the redactor's view, and pruning -----------------------------------

@pytest.mark.asyncio
async def test_a_jobs_token_is_listed_while_in_flight_and_dropped_when_it_ends():
    """The redactor unions this with the live sessions, so an entry missing here is a
    token that stops being redacted the moment its session is dropped."""
    q = JobQueue(spawn=lambda job, on_event: FakeProc([], hang=True), max_concurrency=1)
    job_id = await q.submit("a@hnee.de", {"refresh_token": "tok-live"}, {})
    await asyncio.sleep(0.05)
    assert q.live_secrets() == ["tok-live"]
    await q.cancel(job_id)
    await asyncio.sleep(0.05)
    assert q.live_secrets() == []                 # cancelled: no longer held


@pytest.mark.asyncio
async def test_a_tokenless_job_adds_nothing_and_a_queued_jobs_token_is_listed_too():
    q = JobQueue(spawn=lambda job, on_event: FakeProc([], hang=True), max_concurrency=1)
    running = await q.submit("a@hnee.de", {"refresh_token": "tok-a"}, {})
    queued = await q.submit("b@hnee.de", {"refresh_token": "tok-b"}, {})
    nothing = await q.submit("c@hnee.de", {}, {})          # no credentials at all
    await asyncio.sleep(0.05)
    assert q.status(queued).state == "queued"
    assert sorted(q.live_secrets()) == ["tok-a", "tok-b"]
    for i in (running, queued, nothing):
        await q.cancel(i)
    await asyncio.sleep(0.05)
    assert q.live_secrets() == []


def test_submit_has_no_suspension_points_before_the_job_is_recorded():
    """The premise `ServerContext.submit`'s cleanup rests on, pinned.

    When a submit times out, the web process cannot know whether a job was created, so
    it chooses the id up front and then asks the queue to undo that id. That only works
    because `JobQueue.submit` cannot be suspended part-way: once it starts it either
    creates the whole job record or nothing. An `await` added ahead of
    `self._jobs[job_id] = Job(...)` would reopen exactly the hole two review rounds went
    into closing -- a render with no id in the browser, uncancellable, holding a slot
    for four hours and spending the user's Earth Engine quota.

    If you genuinely need to await in here, DO NOT just delete this test: make the
    cleanup robust against a half-created job first (e.g. record the id before the
    await, or have the cleanup remember unresolved ids), then retire it deliberately.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(JobQueue.submit)))
    suspensions = [node for node in ast.walk(tree)
                   if isinstance(node, (ast.Await, ast.AsyncWith, ast.AsyncFor))]
    assert suspensions == [], (
        "JobQueue.submit gained a suspension point at line(s) "
        f"{[n.lineno for n in suspensions]}; read this test's docstring")


@pytest.mark.asyncio
async def test_submit_never_silently_replaces_the_id_the_caller_chose():
    """`or` would re-id a falsy one, and the caller's cleanup would then look for an id
    that does not exist. Unreachable today (the one caller passes a generated id); this
    keeps it unreachable by construction rather than by luck."""
    q = JobQueue(spawn=_spawn_ok(), max_concurrency=1)
    assert await q.submit("a@hnee.de", {"refresh_token": "r"}, {}, job_id="") == ""
    assert q.status("").email == "a@hnee.de"
    await _wait_bounded(q, "")


@pytest.mark.asyncio
async def test_cancel_if_present_undoes_one_job_and_shrugs_at_an_id_that_never_existed():
    """The submit-timeout cleanup path: the caller does not know whether its job was
    ever created, so an unknown id must be False rather than an exception -- and it
    must touch nothing else the same user has running."""
    q = JobQueue(spawn=lambda job, on_event: FakeProc([], hang=True), max_concurrency=2)
    mine = await q.submit("a@hnee.de", {"refresh_token": "r"}, {}, job_id="chosen-id")
    other = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await asyncio.sleep(0.05)
    assert mine == "chosen-id"                      # the caller's id was honoured
    assert await q.cancel_if_present("never-created") is False
    assert await q.cancel_if_present("chosen-id") is True
    await asyncio.sleep(0.05)
    assert q.status(mine).state == "cancelled"
    assert q.status(other).state == "running"       # the user's other render survives
    await q.cancel(other)


@pytest.mark.asyncio
async def test_a_finished_jobs_record_is_dropped_once_it_has_aged_out():
    """Finding 11: nothing ever pruned, and `Job.result` holds the whole done event."""
    from gee_animation.webapp.jobs import UnknownJobError
    q = JobQueue(spawn=_spawn_ok(), max_concurrency=1, retain_finished_s=-1)
    old = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await _wait_bounded(q, old)
    assert q.status(old).state == "done"                   # still there until a submit
    fresh = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    with pytest.raises(UnknownJobError, match="not known"):
        q.status(old)
    assert old not in q._tasks                             # the task record went too
    await _wait_bounded(q, fresh)
    assert q.status(fresh).state == "done"


@pytest.mark.asyncio
async def test_a_job_within_the_retention_window_is_kept():
    q = JobQueue(spawn=_spawn_ok(), max_concurrency=1)     # default: one hour
    old = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await _wait_bounded(q, old)
    await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    assert q.status(old).state == "done" and q.status(old).result["mp4"]


@pytest.mark.asyncio
async def test_position_counts_only_the_waiting_queue_not_every_job_ever_submitted():
    """`position` is polled every 2 s by every waiting user, so it must not scan the
    history. It also has to stay correct as jobs ahead start and finish."""
    q = JobQueue(spawn=lambda job, on_event: FakeProc([], hang=True), max_concurrency=1)
    running = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    first = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    second = await q.submit("a@hnee.de", {"refresh_token": "r"}, {})
    await asyncio.sleep(0.05)
    assert q.position(running) == 0                 # running: no position
    assert (q.position(first), q.position(second)) == (1, 2)
    assert len(q._order) == 2                       # only the two that are waiting

    await q.cancel(running)
    await asyncio.sleep(0.05)
    assert q.status(first).state == "running" and q.position(first) == 0
    assert q.position(second) == 1                  # moved up as the queue drained
    await q.cancel(first)
    await q.cancel(second)
    await asyncio.sleep(0.05)
    assert q._order == [] and q.position(second) == 0
