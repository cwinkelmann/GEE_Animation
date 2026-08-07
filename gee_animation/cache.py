"""On-disk cache for raw Earth Engine thumbnail bytes.

Every run recomputes and refetches every frame, even when only client-side styling
changed (colorbar, outline width, palette). The payloads are tiny — ~10 KB for a
Landsat LST frame — but each one costs a full EE compute + stream round trip, so
caching them turns a re-render into a local operation.

What is stored are the **raw downloaded bytes**, before decoding, so the decode
path in `render._fetch_thumbnail` stays untouched and a cached run produces
byte-identical pixels.

Key construction is fail-safe: the key is derived from *all* config fields except
an explicit `CLIENT_SIDE_FIELDS` allowlist of ones that provably cannot change the
returned bytes. A config field added later therefore invalidates the cache by
default rather than silently serving stale pixels.

Clearing the cache::

    python -c "from gee_animation import cache; print(cache.clear(), 'entries removed')"
    # or just:  rm -rf ~/.cache/gee_animation/thumbs      (~/Library/Caches on macOS)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

#: Bumped whenever the *stored representation* changes (e.g. if we ever store a
#: decoded array instead of PNG bytes), so old entries can never be misread.
CACHE_VERSION = 1

#: Env var overriding the cache location (see `cache_dir`).
ENV_CACHE_DIR = "GEE_ANIMATION_CACHE_DIR"

#: Config fields that are purely client-side: they change how the downloaded
#: pixels are *drawn*, never what Earth Engine is asked for or what it returns.
#: Everything NOT listed here goes into the key — a deliberately conservative
#: default so a new config knob invalidates rather than silently reuses.
#:
#: `palette` belongs here because `render._thumb_params` sends only min/max/
#: dimensions/region/format/crs: for a single-band index EE returns greyscale and
#: `imaging.colorize` applies the palette locally; for a composite EE returns
#: colour and the palette is unused. Re-rendering with a new palette is a hit.
CLIENT_SIDE_FIELDS = frozenset({
    "name", "out_dir", "fps", "preset", "aspect", "upscale",
    "region_line_width", "draw_region", "metadata", "workers",
    "palette", "debug_month", "allow_upsample",
    # header text: drawn locally into the label margins (render._header_text), never
    # sent to EE — retitling a run must not recompute every frame.
    "title", "subtitle",
    # the cache controls themselves: where entries live and whether they are used
    # cannot change what EE computes.
    "cache", "cache_dir",
})

# The frame being fetched, published per-thread so `_fetch_thumbnail` can key on
# it without changing the `fetch(image, cfg, geometry)` seam that render() and
# every test rely on. Set inside the worker thread by `render._fetch_one`.
_local = threading.local()


@contextmanager
def frame_identity(label, source):
    """Publish the current frame's identity to the cache key for this thread.

    `source` matters as much as `label`: a gap-filled "2022-05" borrowing 2019 is
    a different EE image from the real 2022 one with the same label.
    """
    prev = getattr(_local, "identity", None)
    _local.identity = (label, source)
    try:
        yield
    finally:
        _local.identity = prev


def enabled(cfg) -> bool:
    return bool(getattr(cfg, "cache", True))


def _default_root() -> Path:
    """Platform user cache dir — never inside `out/`, which is the deliverable."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "gee_animation"
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "gee_animation"


def cache_dir(cfg=None) -> Path:
    """Cache location: explicit `render.cache_dir` beats $GEE_ANIMATION_CACHE_DIR
    beats the platform user cache dir."""
    explicit = getattr(cfg, "cache_dir", None) if cfg is not None else None
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_CACHE_DIR)
    if env:
        return Path(env).expanduser()
    return _default_root() / "thumbs"


def thumb_key(cfg, params, composite: bool) -> str:
    """SHA-256 over everything that can change the returned bytes.

    Covers (a) what EE is asked to compute — every config field except
    `CLIENT_SIDE_FIELDS`, which includes sensor/index/dates/cadence/cloud
    thresholds/missions/pooling/anomaly/both AOIs; (b) the frame's own identity
    (label + source); and (c) the thumbnail request itself — dimensions, the
    already-resolved crs, viz min/max, and whether this is a composite.

    `params["region"]` is dropped: it is a live ee.Geometry with no stable
    repr, and it is derived from `cfg.frame_aoi`, which is already in the key.
    """
    label, source = getattr(_local, "identity", None) or (None, None)
    payload = {
        "v": CACHE_VERSION,
        "cfg": {k: v for k, v in vars(cfg).items() if k not in CLIENT_SIDE_FIELDS},
        "req": {k: v for k, v in params.items() if k != "region"},
        "composite": bool(composite),
        "frame": {"label": label, "source": source},
    }
    blob = json.dumps(payload, sort_keys=True, default=repr)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _path(cfg, key: str) -> Path:
    # two-char fan-out keeps any one directory from growing unbounded
    return cache_dir(cfg) / key[:2] / f"{key}.bin"


def load(cfg, key: str) -> bytes | None:
    """Cached bytes, or None on a miss. Never raises — a broken cache is a miss."""
    if not enabled(cfg):
        return None
    try:
        return _path(cfg, key).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.debug("thumbnail cache unreadable (%s); refetching", exc)
        return None


def store(cfg, key: str, data: bytes) -> None:
    """Write `data` atomically (temp file + os.replace) so concurrent worker
    threads can never publish a half-written entry. Never raises."""
    if not enabled(cfg):
        return
    dest = _path(cfg, key)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dest)              # atomic within the same filesystem
        except BaseException:
            os.unlink(tmp)
            raise
    except OSError as exc:
        log.debug("could not write thumbnail cache entry (%s); continuing", exc)


def discard(cfg, key: str) -> None:
    """Drop a single entry (used when cached bytes turn out to be undecodable)."""
    try:
        _path(cfg, key).unlink()
    except OSError:
        pass


def clear(cfg=None) -> int:
    """Delete every cached thumbnail. Returns the number of entries removed."""
    root = cache_dir(cfg)
    if not root.is_dir():
        return 0
    n = len(list(root.rglob("*.bin")))
    shutil.rmtree(root, ignore_errors=True)
    return n
