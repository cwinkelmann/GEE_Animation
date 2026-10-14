import os
import time
from pathlib import Path

from gee_animation.webapp import storage


def _run(root, email, name, age_days=0):
    d = storage.user_dir(root, email) / name
    d.mkdir(parents=True)
    mp4 = d / f"{name}.mp4"
    mp4.write_bytes(b"\x00\x00mp4")
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(mp4, (old, old))
        os.utime(d, (old, old))
    return mp4


def test_user_dir_is_stable_and_does_not_contain_the_email(tmp_path):
    a = storage.user_dir(tmp_path, "alice@hnee.de")
    assert a == storage.user_dir(tmp_path, "alice@hnee.de")      # stable
    assert a != storage.user_dir(tmp_path, "bob@hnee.de")
    assert "alice" not in str(a)        # the directory name is not an identity leak


def test_list_runs_never_returns_another_users_run(tmp_path):
    _run(tmp_path, "alice@hnee.de", "alice_ndvi")
    _run(tmp_path, "bob@hnee.de", "bob_lst")
    alice = storage.list_runs(tmp_path, "alice@hnee.de")
    bobs_dir = str(storage.user_dir(tmp_path, "bob@hnee.de"))
    assert len(alice) == 1 and "alice_ndvi" in alice[0][0]
    assert all(bobs_dir not in path for _, path in alice)


def test_same_run_name_from_two_users_does_not_collide(tmp_path):
    """Review Focus 3: two users upload 'aoi.geojson' and name the run the same."""
    a = _run(tmp_path, "alice@hnee.de", "sentinel2_ndvi")
    b = _run(tmp_path, "bob@hnee.de", "sentinel2_ndvi")
    assert a != b and a.exists() and b.exists()
    assert len(storage.list_runs(tmp_path, "alice@hnee.de")) == 1
    assert len(storage.list_runs(tmp_path, "bob@hnee.de")) == 1


def test_sweep_deletes_old_runs_and_keeps_new_ones(tmp_path):
    old = _run(tmp_path, "alice@hnee.de", "ancient", age_days=20)
    new = _run(tmp_path, "alice@hnee.de", "fresh", age_days=1)
    deleted = storage.sweep(tmp_path, max_age_days=14)
    assert not old.exists() and new.exists()
    assert [p.name for p in deleted] == ["ancient"]


def test_sweep_on_a_missing_root_is_a_noop(tmp_path):
    assert storage.sweep(tmp_path / "nope", max_age_days=14) == []


def _old(path, days=30):
    stamp = time.time() - days * 86400
    for p in [path, *path.rglob("*")]:
        os.utime(p, (stamp, stamp))


def test_sweep_reclaims_a_failed_run_that_holds_only_its_copied_aoi(tmp_path):
    """`submit` copies the AOI into <run>/aoi/ before the job starts, so every errored,
    timed-out, OOM-killed or restart-interrupted render leaves a run with no media."""
    run = storage.user_dir(tmp_path, "alice@hnee.de") / "sentinel2_ndvi_20260101-000000-abc"
    (run / "aoi").mkdir(parents=True)
    (run / "aoi" / "area.geojson").write_text("{}")
    fresh = storage.user_dir(tmp_path, "alice@hnee.de") / "just_failed"
    (fresh / "aoi").mkdir(parents=True)
    (fresh / "aoi" / "area.geojson").write_text("{}")
    _old(run)

    deleted = storage.sweep(tmp_path, max_age_days=14)

    assert deleted == [run] and not run.exists()
    assert fresh.exists()                      # a recent failure is kept for 14 days too


def test_sweep_never_removes_the_output_root_or_a_user_directory(tmp_path):
    """Ruling H: only run subdirectories are ever deleted, however old and however empty."""
    root = tmp_path / "out"
    user = storage.user_dir(root, "alice@hnee.de")
    user.mkdir(parents=True)                   # an old, empty user directory
    other = storage.user_dir(root, "bob@hnee.de")
    (other / "run").mkdir(parents=True)
    (other / "stray.txt").write_text("x")      # a loose file beside the runs
    _old(user)
    _old(other)
    _old(root)

    deleted = storage.sweep(root, max_age_days=14)

    assert root.is_dir() and user.is_dir() and other.is_dir()
    assert (other / "stray.txt").exists()
    assert deleted == [other / "run"]


def test_list_runs_still_shows_only_finished_renders(tmp_path):
    """The listing and the sweep deliberately use different definitions of a run."""
    _run(tmp_path, "alice@hnee.de", "done_run")
    failed = storage.user_dir(tmp_path, "alice@hnee.de") / "failed_run" / "aoi"
    failed.mkdir(parents=True)
    (failed / "a.geojson").write_text("{}")
    labels = [label for label, _ in storage.list_runs(tmp_path, "alice@hnee.de")]
    assert len(labels) == 1 and "done_run" in labels[0]


def test_sweep_temp_reclaims_stale_entries_but_never_the_directory_itself(tmp_path):
    temp = tmp_path / "gradio-tmp"
    stale_dir = temp / "abc123"
    stale_dir.mkdir(parents=True)
    (stale_dir / "frame.png").write_bytes(b"png")
    stale_file = temp / "loose.zip"
    stale_file.write_bytes(b"zip")
    fresh = temp / "fresh"
    fresh.mkdir()
    (fresh / "frame.png").write_bytes(b"png")
    _old(stale_dir)
    _old(stale_file)

    deleted = storage.sweep_temp(temp, max_age_days=14)

    assert sorted(p.name for p in deleted) == ["abc123", "loose.zip"]
    assert temp.is_dir() and fresh.exists()
    assert storage.sweep_temp(tmp_path / "missing") == []


def test_sweep_uses_the_injected_clock(tmp_path):
    """Review finding 2: an explicit `now` decides the cutoff, including now=0.0."""
    mp4 = _run(tmp_path, "alice@hnee.de", "dated")
    run = mp4.parent
    mtime = 1000.0
    for p in (run, mp4):
        os.utime(p, (mtime, mtime))

    # now=0.0 is a real epoch, so the cutoff is far in the past and nothing is old enough.
    # Treating 0.0 as unset would fall back to the wall clock and delete the run.
    assert storage.sweep(tmp_path, max_age_days=14, now=0.0) == []
    assert run.exists()

    # One day past the 14-day cutoff from the run's mtime: now the run is old.
    assert storage.sweep(tmp_path, max_age_days=14, now=mtime + 15 * 86400) == [run]
    assert not run.exists()


def test_a_run_in_flight_with_an_old_copied_aoi_is_not_swept(tmp_path):
    """`shutil.copy2` keeps the source's mtime, so an AOI copied from an old preset file
    is ancient the moment it lands. The directory's own (fresh) mtime must protect a run
    that is still rendering, or the sweep would delete it under the worker."""
    run = storage.user_dir(tmp_path, "alice@hnee.de") / "running"
    (run / "aoi").mkdir(parents=True)
    aoi = run / "aoi" / "preset.geojson"
    aoi.write_text("{}")
    old = time.time() - 90 * 86400
    os.utime(aoi, (old, old))
    os.utime(run / "aoi", (old, old))

    assert storage.sweep(tmp_path, max_age_days=14) == []
    assert run.exists()
