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


def list_runs(root: Path, email: str) -> list:
    """[(label, media_path)] for THIS user only."""
    return list_previous_runs(base_dir=str(user_dir(root, email)))


def sweep(root: Path, max_age_days: int = 14, now: float | None = None) -> list:
    """Delete run directories whose newest file is older than `max_age_days`."""
    root = Path(root)
    if not root.exists():
        return []
    cutoff = (now or time.time()) - max_age_days * 86400
    deleted = []
    for user in sorted(p for p in root.iterdir() if p.is_dir()):
        for run in sorted(p for p in user.iterdir() if p.is_dir()):
            newest = max((f.stat().st_mtime for f in run.rglob("*") if f.is_file()),
                         default=run.stat().st_mtime)
            if newest < cutoff:
                shutil.rmtree(run)
                deleted.append(run)
    return deleted
