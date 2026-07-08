from pathlib import Path
import numpy as np
import types
from gee_animation.render import annotate, assemble, render
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
    # fetch returns a tiny NDVI array per frame
    def fake_fetch(image, cfg, geometry=None):
        return np.array([[0.5, -0.1], [0.9, 0.0]])
    frames = [Frame("2022-01", object()), Frame("2022-02", object())]
    paths = render(frames, cfg, fetch=fake_fetch, geometry=None)
    assert any(p.suffix == ".gif" and p.exists() for p in paths)
