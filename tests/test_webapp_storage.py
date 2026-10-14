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


def test_sweep_leaves_old_directories_without_media_alone(tmp_path):
    """Review finding 1: an old uploads folder beside a run is not a run."""
    uploads = storage.user_dir(tmp_path, "alice@hnee.de") / "uploads"
    uploads.mkdir(parents=True)
    aoi = uploads / "aoi.geojson"
    aoi.write_text("{}")
    old = time.time() - 30 * 86400
    os.utime(aoi, (old, old))
    os.utime(uploads, (old, old))
    old_run = _run(tmp_path, "alice@hnee.de", "ancient", age_days=20)

    deleted = storage.sweep(tmp_path, max_age_days=14)

    assert uploads.exists() and aoi.exists()
    assert not old_run.exists()
    assert [p.name for p in deleted] == ["ancient"]


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
