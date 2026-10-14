"""Per-user output directories, user-scoped run listing, and retention.

Blocker 2 of the spec is fixed HERE rather than in the UI: hiding the run-browser
control would leave the paths reachable, whereas a user-scoped listing has nothing
to hide.
"""
from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

from ..gui import list_previous_runs


def user_dir(root: Path, email: str) -> Path:
    """`root/<16 hex chars>` — stable per user, and not itself an identity leak."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]
    return Path(root) / digest


def list_runs(root: Path, email: str) -> list[tuple[str, str]]:
    """[(label, media_path)] for THIS user only."""
    return list_previous_runs(base_dir=str(user_dir(root, email)))


def _newest_mtime(path: Path) -> float:
    """Newest modification time under `path` (itself included), so a directory still
    being written to is never mistaken for an old one."""
    times = [path.stat().st_mtime]
    for f in path.rglob("*"):
        try:
            times.append(f.stat().st_mtime)
        except OSError:                     # vanished between listing and stat
            pass
    return max(times)


def sweep(root: Path, max_age_days: int = 14, now: float | None = None) -> list[Path]:
    """Delete run directories whose newest file is older than `max_age_days`.

    A run directory is an immediate child directory of a user directory, whatever it
    contains. Reclaiming disk must not depend on a render having succeeded: `submit`
    copies the AOI into `<run>/aoi/` before the job starts, so a render that errors,
    times out, is OOM-killed or is interrupted by a restart leaves a directory with no
    media, and that is the common failure case. (`list_runs`, which shows a user their
    finished renders, deliberately keeps the stricter media-only definition.)

    Only run directories are ever deleted: never the output root, never a user directory.
    """
    root = Path(root)
    if not root.exists():
        return []
    cutoff = (time.time() if now is None else now) - max_age_days * 86400
    deleted = []
    for user in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        for run in sorted(p for p in user.iterdir() if p.is_dir() and not p.is_symlink()):
            if _newest_mtime(run) < cutoff:
                shutil.rmtree(run)
                deleted.append(run)
    return deleted


def sweep_temp(temp_dir: Path, max_age_days: int = 14, now: float | None = None) -> list[Path]:
    """Reclaim stale entries directly inside Gradio's temp/cache directory.

    Gradio copies every returned file (mp4, gif, zip, each frame PNG) there. The
    directory itself is never removed, only its entries.
    """
    temp_dir = Path(temp_dir)
    if not temp_dir.is_dir():
        return []
    cutoff = (time.time() if now is None else now) - max_age_days * 86400
    deleted = []
    for entry in sorted(temp_dir.iterdir()):
        if _newest_mtime(entry) < cutoff:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            deleted.append(entry)
    return deleted
