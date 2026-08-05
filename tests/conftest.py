"""Suite-wide safety nets.

The thumbnail cache defaults to the *user's real* cache directory. A unit test
that reached it would both pollute a developer's machine and, worse, be able to
pass on a stale entry. Redirect it to a per-test tmp dir for the whole suite;
tests that care about cache behaviour set `cfg.cache_dir` explicitly, which takes
precedence over this env var anyway.
"""
import pytest

from gee_animation import cache


@pytest.fixture(autouse=True)
def _isolate_thumbnail_cache(tmp_path_factory, monkeypatch):
    monkeypatch.setenv(cache.ENV_CACHE_DIR,
                       str(tmp_path_factory.mktemp("thumb-cache")))
