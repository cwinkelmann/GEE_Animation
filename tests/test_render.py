from pathlib import Path
import numpy as np
import types
from gee_animation.render import (
    add_colorbar,
    annotate,
    apply_nodata,
    assemble,
    render,
)
from gee_animation.compositing import Frame


def _cfg(tmp_path, name="anim", fps=2):
    return types.SimpleNamespace(
        name=name, out_dir=str(tmp_path),
        ndvi_min=-0.2, ndvi_max=0.9, palette=["#000000", "#ffffff"],
        fps=fps, scale=20, dimensions=64,
    )


def test_annotate_keeps_shape_and_type():
    rgb = np.zeros((32, 32, 3), dtype=np.uint8)
    out = annotate(rgb, "2022-06")
    assert out.shape == (32, 32, 3)
    assert out.dtype == np.uint8
    # some pixels changed (text/bar drawn)
    assert out.sum() > 0


def test_assemble_writes_gif_and_mp4(tmp_path):
    cfg = _cfg(tmp_path)
    frames = [np.zeros((16, 16, 3), np.uint8), np.full((16, 16, 3), 255, np.uint8)]
    paths = assemble(frames, cfg)
    suffixes = {p.suffix for p in paths}
    assert suffixes == {".mp4", ".gif"}
    assert all(p.exists() for p in paths)


def test_assemble_encodes_mp4_for_odd_dimension_frames(tmp_path):
    # libx264 requires even width AND height; frames from arbitrary AOIs are
    # often odd (e.g. 768x577). The MP4 must still be produced, not dropped.
    cfg = _cfg(tmp_path)
    frames = [np.zeros((15, 16, 3), np.uint8), np.full((15, 16, 3), 200, np.uint8)]
    paths = assemble(frames, cfg)
    suffixes = {p.suffix for p in paths}
    assert ".mp4" in suffixes, "MP4 should be produced for odd-dimension frames"
    mp4 = next(p for p in paths if p.suffix == ".mp4")
    assert mp4.exists() and mp4.stat().st_size > 0


def test_assemble_falls_back_to_gif_when_mp4_fails(tmp_path, monkeypatch):
    import gee_animation.render as r
    cfg = _cfg(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("no ffmpeg")
    monkeypatch.setattr(r, "_write_mp4", boom)
    frames = [np.zeros((16, 16, 3), np.uint8)]
    paths = assemble(frames, cfg)
    assert all(p.suffix == ".gif" for p in paths)
    assert paths[0].exists()


def test_render_pipeline_with_injected_fetch(tmp_path):
    cfg = _cfg(tmp_path)
    # fetch returns a tiny (ndvi_array, valid_mask) tuple per frame
    def fake_fetch(image, cfg, geometry=None):
        arr = np.array([[0.5, -0.1], [0.9, 0.0]])
        valid = np.ones(arr.shape, dtype=bool)
        return arr, valid
    frames = [Frame("2022-01", object()), Frame("2022-02", object())]
    paths = render(frames, cfg, fetch=fake_fetch, geometry=None)
    assert any(p.suffix == ".gif" and p.exists() for p in paths)


def _cfg_ns():
    return types.SimpleNamespace(
        ndvi_min=-0.2, ndvi_max=0.9, palette=["#000000", "#ffffff"],
    )


def test_apply_nodata_paints_invalid_pixels():
    rgb = np.zeros((1, 2, 3), np.uint8)
    valid = np.array([[True, False]])
    out = apply_nodata(rgb, valid)
    assert out[0, 0].tolist() == [0, 0, 0]          # valid untouched
    assert out[0, 1].tolist() == list((240, 240, 240))  # invalid -> no-data colour


def test_add_colorbar_preserves_shape_and_draws():
    rgb = np.zeros((40, 60, 3), np.uint8)
    out = add_colorbar(rgb, _cfg_ns())
    assert out.shape == (40, 60, 3) and out.dtype == np.uint8
    assert out.sum() > 0
