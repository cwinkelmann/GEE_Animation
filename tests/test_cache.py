"""Thumbnail cache: key construction, hit/miss behaviour, corruption tolerance.

Network-free: `urlopen` is faked at the render seam and every cfg points
`cache_dir` at a pytest `tmp_path`, so the real user cache is never touched.
"""
import io
import logging
import types
import urllib.error

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
        title=None, subtitle=None, credit=None,
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
    # the header text is drawn locally into the margins; retitling a run must not
    # cost a full Earth Engine recompute of every frame.
    ("title", "Grumsiner Forst"),
    ("subtitle", "Brandenburg, Germany"),
    # the attribution line is drawn locally too (render._default_credit); changing
    # or overriding it must not force a refetch of every frame.
    ("credit", "Custom credit line"),
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


# --------------------------------------------------------------------------
# transient-failure retry: a single 503/429/network blip must not kill an
# otherwise-healthy render (see gee_animation.render._fetch_url)
# --------------------------------------------------------------------------

@pytest.fixture
def no_sleep(monkeypatch):
    """Replace render.time.sleep with a recording no-op — retry tests never wait."""
    sleeps = []
    monkeypatch.setattr("gee_animation.render.time.sleep", lambda s: sleeps.append(s))
    return sleeps


def _http_error(code, url="https://earthengine.invalid/thumb.png"):
    return urllib.error.HTTPError(url, code, f"HTTP {code}", None, None)


def test_transient_503_retries_then_succeeds(tmp_path, monkeypatch, no_sleep, caplog):
    calls = []

    def flaky_urlopen(url, timeout=None):
        calls.append(url)
        if len(calls) <= 2:
            raise _http_error(503)
        return _FakeResponse(_png_bytes())

    monkeypatch.setattr("gee_animation.render.urlopen", flaky_urlopen)
    with caplog.at_level(logging.WARNING):
        arr, valid = _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")

    assert len(calls) == 3, "two failures + one success == 3 urlopen calls"
    assert no_sleep == [2.0, 6.0], "backoff must be the two configured sleeps, in order"
    warnings = [rec.message for rec in caplog.records if rec.levelno == logging.WARNING]
    assert sum("retrying" in w for w in warnings) == 2, "one warning per retry"
    assert arr.shape == (8, 8)


def test_transient_503_exhausts_retries_and_raises(tmp_path, monkeypatch, no_sleep):
    calls = []

    def always_503(url, timeout=None):
        calls.append(url)
        raise _http_error(503)

    monkeypatch.setattr("gee_animation.render.urlopen", always_503)
    with pytest.raises(urllib.error.HTTPError) as exc:
        _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")

    assert exc.value.code == 503
    assert len(calls) == 3, "exactly 3 attempts total, then give up"
    assert no_sleep == [2.0, 6.0]


def test_404_is_not_retried(tmp_path, monkeypatch, no_sleep):
    calls = []

    def not_found(url, timeout=None):
        calls.append(url)
        raise _http_error(404)

    monkeypatch.setattr("gee_animation.render.urlopen", not_found)
    with pytest.raises(urllib.error.HTTPError) as exc:
        _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")

    assert exc.value.code == 404
    assert len(calls) == 1, "a real error must not be retried"
    assert no_sleep == []


@pytest.mark.parametrize("code", sorted({429, 500, 502, 503, 504}))
def test_retryable_http_codes_recover_on_second_attempt(tmp_path, monkeypatch, no_sleep, code):
    calls = []

    def flaky_urlopen(url, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            raise _http_error(code)
        return _FakeResponse(_png_bytes())

    monkeypatch.setattr("gee_animation.render.urlopen", flaky_urlopen)
    _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")

    assert len(calls) == 2
    assert no_sleep == [2.0]


def test_url_error_is_retried(tmp_path, monkeypatch, no_sleep):
    calls = []

    def flaky_urlopen(url, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.URLError("connection reset")
        return _FakeResponse(_png_bytes())

    monkeypatch.setattr("gee_animation.render.urlopen", flaky_urlopen)
    arr, valid = _fetch_thumbnail(_FakeImage(), _cfg(tmp_path), "GEOM")

    assert len(calls) == 2
    assert no_sleep == [2.0]
    assert arr.shape == (8, 8)
