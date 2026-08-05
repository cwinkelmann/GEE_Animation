"""Thumbnail cache: key construction, hit/miss behaviour, corruption tolerance.

Network-free: `urlopen` is faked at the render seam and every cfg points
`cache_dir` at a pytest `tmp_path`, so the real user cache is never touched.
"""
import io
import types

import numpy as np
import pytest
from PIL import Image

from gee_animation import cache
from gee_animation.render import _fetch_thumbnail, _thumb_params


def _png_bytes(value=128, size=(8, 8), mode="LA"):
    img = Image.new(mode, size, (value, 255) if mode == "LA" else (value, value, value, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeImage:
    """Stands in for an ee.Image: select() chains, getThumbURL() returns a URL."""

    def select(self, bands):
        return self

    def getThumbURL(self, params):
        return "https://earthengine.invalid/thumb.png"


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def urlopen_counter(monkeypatch):
    """Replace render.urlopen with a counting fake. Returns the call-count list."""
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        return _FakeResponse(_png_bytes())

    monkeypatch.setattr("gee_animation.render.urlopen", fake_urlopen)
    return calls


def _cfg(tmp_path, **over):
    base = dict(
        name="anim", project="proj", out_dir=str(tmp_path / "out"),
        frame_aoi={"bbox": [0, 0, 1, 1]}, region_aoi={"bbox": [0, 0, 1, 1]},
        start="2022-01-01", end="2022-12-31",
        sensor="sentinel2", index="ndvi", cadence="monthly",
        max_cloud_percent=40.0, region_max_cloud_percent=10.0,
        viz_min=-0.2, viz_max=0.9, palette=["#000000", "#ffffff"],
        fps=2.0, scale=20.0, dimensions=64, crs="EPSG:32633",
        preset=None, aspect=None, upscale="lanczos",
        anomaly=None, baseline_years=None, pool_years=None,
        pool_strategy="least_cloudy", metadata=False, draw_region=True,
        region_line_width=None, missions=None, min_scenes=1,
        allow_upsample=False, debug_month=None,
        workers=4, cache=True, cache_dir=str(tmp_path / "cache"),
    )
    base.update(over)
    return types.SimpleNamespace(**base)


# --------------------------------------------------------------------------
# palette verification: it must not reach Earth Engine at all
# --------------------------------------------------------------------------

def test_thumb_params_never_sends_the_palette(tmp_path):
    cfg = _cfg(tmp_path)
    params = _thumb_params(cfg, geometry="GEOM")
    assert set(params) == {"min", "max", "dimensions", "region", "format", "crs"}
    assert "palette" not in params
    # and no param value smuggles the palette through
    assert not any(cfg.palette == v or cfg.palette[0] in repr(v) for v in params.values())


# --------------------------------------------------------------------------
# hit / miss
# --------------------------------------------------------------------------

def test_second_identical_run_is_a_pure_cache_hit(tmp_path, urlopen_counter):
    cfg = _cfg(tmp_path)
    a = _fetch_thumbnail(_FakeImage(), cfg, "GEOM")
    assert len(urlopen_counter) == 1
    b = _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")
    assert len(urlopen_counter) == 1, "identical config must not refetch"
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


@pytest.mark.parametrize("field,value", [
    ("viz_min", -0.5),
    ("dimensions", 128),
    ("crs", "EPSG:32632"),
    ("start", "2021-01-01"),
    ("index", "ndmi"),
    ("pool_years", [2019, 2022]),
])
def test_pixel_affecting_change_is_a_cache_miss(tmp_path, urlopen_counter, field, value):
    _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")
    _fetch_thumbnail(_FakeImage(), _cfg(tmp_path, **{field: value}), "GEOM")
    assert len(urlopen_counter) == 2, f"{field} must invalidate the cache"


def test_frame_source_is_part_of_the_key(tmp_path, urlopen_counter):
    # Same label, different borrowed year -> a different EE image -> must refetch.
    with cache.frame_identity("2022-05", None):
        _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")
    with cache.frame_identity("2022-05", 2019):
        _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")
    assert len(urlopen_counter) == 2
    # ...and re-running the 2019-sourced frame hits.
    with cache.frame_identity("2022-05", 2019):
        _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")
    assert len(urlopen_counter) == 2


def test_frame_label_is_part_of_the_key(tmp_path, urlopen_counter):
    with cache.frame_identity("2022-05", None):
        _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")
    with cache.frame_identity("2022-06", None):
        _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")
    assert len(urlopen_counter) == 2


@pytest.mark.parametrize("field,value", [
    ("palette", ["#ff0000", "#00ff00"]),
    ("fps", 12.0),
    ("region_line_width", 9),
    ("preset", "1080p"),
    ("name", "other-name"),
    ("out_dir", "somewhere-else"),
])
def test_client_side_change_still_hits_the_cache(tmp_path, urlopen_counter, field, value):
    _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")
    _fetch_thumbnail(_FakeImage(), _cfg(tmp_path, **{field: value}), "GEOM")
    assert len(urlopen_counter) == 1, f"{field} is client-side and must not refetch"


def test_unknown_new_config_field_invalidates_by_default(tmp_path, urlopen_counter):
    # Fail-safe: a field nobody allowlisted must change the key, not be ignored.
    _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")
    _fetch_thumbnail(_FakeImage(), _cfg(tmp_path, some_future_knob="x"), "GEOM")
    assert len(urlopen_counter) == 2


def test_cache_version_is_in_the_key(tmp_path, monkeypatch, urlopen_counter):
    _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")
    monkeypatch.setattr(cache, "CACHE_VERSION", cache.CACHE_VERSION + 1)
    _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")
    assert len(urlopen_counter) == 2


# --------------------------------------------------------------------------
# robustness
# --------------------------------------------------------------------------

def test_corrupt_cache_entry_is_a_miss_not_a_crash(tmp_path, urlopen_counter):
    cfg = _cfg(tmp_path)
    expected = _fetch_thumbnail(_FakeImage(), cfg, "GEOM")
    entries = list(cache.cache_dir(cfg).rglob("*.bin"))
    assert len(entries) == 1
    entries[0].write_bytes(b"not a png at all")
    got = _fetch_thumbnail(_FakeImage(), cfg, "GEOM")      # must not raise
    assert len(urlopen_counter) == 2, "corrupt entry must be refetched"
    assert np.array_equal(got[0], expected[0])


def test_unreadable_cache_dir_does_not_break_the_run(tmp_path, urlopen_counter):
    # cache_dir points at an existing *file* -> stores/loads fail; the run continues.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am not a directory")
    cfg = _cfg(tmp_path, cache_dir=str(blocker))
    arr, valid = _fetch_thumbnail(_FakeImage(), cfg, "GEOM")
    assert arr.shape == (8, 8) and len(urlopen_counter) == 1


def test_cache_disabled_always_refetches(tmp_path, urlopen_counter):
    _fetch_thumbnail(_FakeImage(), _cfg(tmp_path, cache=False), "GEOM")
    _fetch_thumbnail(_FakeImage(), _cfg(tmp_path, cache=False), "GEOM")
    assert len(urlopen_counter) == 2
    assert not list(cache.cache_dir(_cfg(tmp_path)).rglob("*.bin"))


def test_writes_are_atomic_leaving_no_temp_files(tmp_path, urlopen_counter):
    cfg = _cfg(tmp_path)
    _fetch_thumbnail(_FakeImage(), cfg, "GEOM")
    leftovers = [p for p in cache.cache_dir(cfg).rglob("*") if p.is_file()
                 and p.suffix != ".bin"]
    assert leftovers == []


# --------------------------------------------------------------------------
# location + clearing
# --------------------------------------------------------------------------

def test_cache_dir_precedence_config_then_env_then_default(tmp_path, monkeypatch):
    monkeypatch.delenv(cache.ENV_CACHE_DIR, raising=False)
    cfg = _cfg(tmp_path, cache_dir=None)
    default = cache.cache_dir(cfg)
    assert default.name == "thumbs" and "gee_animation" in str(default)
    assert "out" not in default.parts, "must never default inside the deliverable dir"

    monkeypatch.setenv(cache.ENV_CACHE_DIR, str(tmp_path / "from-env"))
    assert cache.cache_dir(cfg) == tmp_path / "from-env"

    cfg.cache_dir = str(tmp_path / "from-cfg")     # explicit config beats ambient env
    assert cache.cache_dir(cfg) == tmp_path / "from-cfg"


def test_clear_removes_entries(tmp_path, urlopen_counter):
    cfg = _cfg(tmp_path)
    _fetch_thumbnail(_FakeImage(), cfg, "GEOM")
    assert cache.clear(cfg) == 1
    assert not list(cache.cache_dir(cfg).rglob("*.bin"))
    _fetch_thumbnail(_FakeImage(), cfg, "GEOM")
    assert len(urlopen_counter) == 2
