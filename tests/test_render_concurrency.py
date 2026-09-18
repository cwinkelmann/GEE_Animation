"""Concurrent frame fetching in render(): order, identity with the serial path,
error propagation and the bounded lookahead.

Network-free — every test injects `fetch`, which is the seam render() promises.
"""
import threading
import time
import types

import numpy as np
import pytest

from gee_animation.compositing import Frame
from gee_animation.render import render


def _cfg(tmp_path, workers, name="anim"):
    return types.SimpleNamespace(
        name=name, out_dir=str(tmp_path), index="ndvi",
        viz_min=-0.2, viz_max=0.9, palette=["#000000", "#ffffff"],
        fps=2, scale=20, dimensions=32, frame_aoi={"bbox": [0, 0, 1, 1]},
        draw_region=False, workers=workers, cache=False, cache_dir=str(tmp_path / "c"),
    )


class _Img:
    """A stand-in EE image that tests can tag (e.g. `.boom = True`)."""
    boom = False


def _frames(n):
    # Real consecutive period keys, rolling into the next year past December: these
    # runs go well past 12 frames, and render() now renders the label as plain
    # language (labels.period_text), which has no month 13.
    return [Frame(f"{2022 + i // 12}-{i % 12 + 1:02d}", _Img(), i + 1) for i in range(n)]


def _shaded_fetch(delay=0.0):
    """Each frame gets a distinct constant value, so order is visible in the pixels."""
    def fetch(image, cfg, geometry):
        idx = fetch.order.index(image)
        if delay:
            time.sleep(delay * ((idx % 3) + 1))       # out-of-order completion
        arr = np.full((16, 16), -0.2 + idx * 0.05, dtype=float)
        return arr, np.ones((16, 16), bool)
    return fetch


def _png_bytes(paths):
    return {p.name: p.read_bytes() for p in paths if p.suffix == ".png"}


def test_concurrent_output_is_byte_identical_to_serial(tmp_path):
    frames = _frames(8)
    images = [f.image for f in frames]

    def run(workers, out):
        cfg = _cfg(out, workers)
        fetch = _shaded_fetch(delay=0.005 if workers > 1 else 0.0)
        fetch.order = images
        return _png_bytes(render(frames, cfg, fetch=fetch, geometry=None))

    serial = run(1, tmp_path / "serial")
    conc = run(4, tmp_path / "conc")
    assert serial.keys() == conc.keys()
    for name in serial:
        assert serial[name] == conc[name], f"{name} differs between serial and concurrent"


def test_concurrent_fetch_preserves_frame_order(tmp_path):
    frames = _frames(6)
    fetch = _shaded_fetch(delay=0.01)
    fetch.order = [f.image for f in frames]
    cfg = _cfg(tmp_path, workers=4)
    paths = render(frames, cfg, fetch=fetch, geometry=None)
    pngs = sorted(p for p in paths if p.suffix == ".png")
    assert [p.stem.split("_")[-1] for p in pngs] == [f.label for f in frames]


def test_workers_one_takes_a_genuinely_serial_path(tmp_path):
    """With workers=1 nothing is ever in flight concurrently and no pool is used."""
    inflight, peak, lock = 0, [0], threading.Lock()
    main = threading.current_thread()
    threads = set()

    def fetch(image, cfg, geometry):
        nonlocal inflight
        threads.add(threading.current_thread())
        with lock:
            inflight += 1
            peak[0] = max(peak[0], inflight)
        time.sleep(0.005)
        with lock:
            inflight -= 1
        return np.zeros((16, 16)), np.ones((16, 16), bool)

    render(_frames(5), _cfg(tmp_path, workers=1), fetch=fetch, geometry=None)
    assert peak[0] == 1
    assert threads == {main}, "workers=1 must run inline, not on a pool thread"


@pytest.mark.parametrize("workers", [2, 3, 4])
def test_in_flight_fetches_never_exceed_the_bound(tmp_path, workers):
    inflight, peak, lock = 0, [0], threading.Lock()

    def fetch(image, cfg, geometry):
        nonlocal inflight
        with lock:
            inflight += 1
            peak[0] = max(peak[0], inflight)
        time.sleep(0.01)
        with lock:
            inflight -= 1
        return np.zeros((16, 16)), np.ones((16, 16), bool)

    render(_frames(20), _cfg(tmp_path, workers), fetch=fetch, geometry=None)
    assert peak[0] <= workers, f"peak {peak[0]} exceeded {workers} workers"
    assert peak[0] > 1, "concurrency should actually happen"


def test_lookahead_is_bounded_not_the_whole_run(tmp_path):
    """Frames must not all be fetched before drawing starts (memory bound)."""
    from gee_animation import render as render_mod

    started, drawn = [], []
    lock = threading.Lock()

    def fetch(image, cfg, geometry):
        with lock:
            started.append(len(started))
        time.sleep(0.005)
        return np.zeros((16, 16)), np.ones((16, 16), bool)

    real_colorize = render_mod.colorize

    def spy_colorize(*a, **k):
        with lock:
            # how many fetches had started by the time this frame was drawn
            drawn.append(len(started))
        return real_colorize(*a, **k)

    render_mod.colorize = spy_colorize
    try:
        render(_frames(24), _cfg(tmp_path, workers=4), fetch=fetch, geometry=None)
    finally:
        render_mod.colorize = real_colorize
    # When the FIRST frame is drawn, only a bounded window may have been fetched...
    assert drawn[0] <= 4 + 2, f"lookahead {drawn[0]} is not bounded"
    # ...and halfway through the run the tail is still unfetched (nothing is
    # materialised up front). `drawn` has >1 entry per frame (the colorbar also
    # colorizes), so index by position rather than assuming a count.
    assert drawn[len(drawn) // 2] < 24, "whole run was prefetched before drawing"
    assert drawn[-1] == 24 and drawn == sorted(drawn)


def test_fetch_error_names_the_frame_and_does_not_hang(tmp_path):
    def fetch(image, cfg, geometry):
        if getattr(image, "boom", False):
            raise ValueError("EE said no")
        return np.zeros((16, 16)), np.ones((16, 16), bool)

    frames = _frames(6)
    frames[3].image.boom = True

    for workers in (1, 4):
        with pytest.raises(RuntimeError) as exc:
            render(frames, _cfg(tmp_path / f"w{workers}", workers), fetch=fetch, geometry=None)
        assert frames[3].label in str(exc.value)
        assert "EE said no" in str(exc.value)


def test_error_surfaces_even_when_a_later_frame_fails(tmp_path):
    def fetch(image, cfg, geometry):
        if getattr(image, "boom", False):
            raise RuntimeError("late failure")
        return np.zeros((16, 16)), np.ones((16, 16), bool)

    frames = _frames(10)
    frames[9].image.boom = True
    with pytest.raises(RuntimeError, match="2022-10"):
        render(frames, _cfg(tmp_path, workers=4), fetch=fetch, geometry=None)


def test_workers_defaults_to_four_when_absent_from_cfg(tmp_path):
    """Legacy cfgs without `workers` still render (and use the default)."""
    cfg = _cfg(tmp_path, workers=4)
    del cfg.workers
    fetch = _shaded_fetch()
    frames = _frames(3)
    fetch.order = [f.image for f in frames]
    paths = render(frames, cfg, fetch=fetch, geometry=None)
    assert len([p for p in paths if p.suffix == ".png"]) == 3
