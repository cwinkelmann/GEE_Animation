"""`overlay`: iso-lines of an external raster (e.g. fallen-stem density) drawn
client-side over every frame, like the region outline."""
import numpy as np
import pytest

from gee_animation import overlay
from gee_animation.config import ConfigError

rasterio = pytest.importorskip("rasterio")


def _density_tif(path, bounds=(0.0, 0.0, 1.0, 1.0), crs="EPSG:4326", n=10, block=(3, 7), value=50.0):
    """n×n raster over `bounds` with a square block of `value` (rows/cols block[0]:block[1])."""
    from rasterio.transform import from_bounds
    a = np.zeros((n, n), "float32"); a[block[0]:block[1], block[0]:block[1]] = value
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32",
                       crs=crs, transform=from_bounds(*bounds, n, n), nodata=-9999) as d:
        d.write(a, 1)
    return str(path)


def test_parse_fills_defaults_and_keeps_levels_ascending(tmp_path):
    spec = overlay.parse({"raster": _density_tif(tmp_path / "d.tif"), "levels": [20, 60]})
    assert spec["levels"] == [20.0, 60.0]
    assert len(spec["colors"]) == 2 and all(c.startswith("#") for c in spec["colors"])
    assert spec["line_px"] == overlay.DEFAULT_LINE_PX and spec["alpha"] == overlay.DEFAULT_ALPHA
    assert overlay.parse(None) is None


@pytest.mark.parametrize("raw, match", [
    ({"levels": [20]}, "overlay.raster"),
    ({"raster": "/nowhere/x.tif", "levels": [20]}, "not found"),
    ({"raster": "RASTER", "levels": []}, "overlay.levels"),
    ({"raster": "RASTER", "levels": [60, 20]}, "ascending"),
    ({"raster": "RASTER", "levels": [20, 60], "colors": ["#fff"]}, "one colour per level"),
])
def test_parse_refuses_bad_specs(tmp_path, raw, match):
    if raw.get("raster") == "RASTER":
        raw["raster"] = _density_tif(tmp_path / "d.tif")
    with pytest.raises(ConfigError, match=match):
        overlay.parse(raw)


def test_level_masks_outline_the_cells_above_each_level(tmp_path):
    path = _density_tif(tmp_path / "d.tif")             # block rows/cols 3..6 of a 10x10 grid = 50
    spec = overlay.parse({"raster": path, "levels": [20, 60], "line_px": 1})
    masks = overlay.level_masks(spec, (0.0, 0.0, 1.0, 1.0), None, (200, 200))
    assert [c for _, c in masks] == [overlay._hex_to_rgb(c) for c in spec["colors"]]
    m20, m60 = masks[0][0], masks[1][0]
    assert m20.shape == (200, 200) and m20.dtype == np.float32
    assert m60.sum() == 0                                  # nothing reaches 60
    # 10 cells over 200 px => 20 px per cell; the block spans px 60..140 on both axes
    assert m20[100, 100] == 0                              # block interior: no line
    assert m20[20, 20] == 0                                # far outside: no line
    assert m20[60:62, 100].any() and m20[100, 139:141].any()   # its left and right edges
    assert 0 < m20.mean() < 0.1                            # thin outline, not a wash


def test_level_masks_reproject_a_metric_raster_onto_a_lonlat_frame(tmp_path):
    # The raster is in UTM 33N; the frame is lon/lat (cfg.crs None). The block must
    # still land on the output pixels that cover its ground footprint.
    from pyproj import Transformer
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32633", always_xy=True)
    x0, y0 = to_utm.transform(13.20, 52.57); x1, y1 = to_utm.transform(13.30, 52.62)
    path = _density_tif(tmp_path / "utm.tif", bounds=(x0, y0, x1, y1), crs="EPSG:32633")
    spec = overlay.parse({"raster": path, "levels": [20]})
    (mask, _), = overlay.level_masks(spec, (13.20, 52.57, 13.30, 52.62), None, (100, 100))
    assert mask.any() and mask[50, 50] == 0 and mask[2, 2] == 0


def test_parse_mode_and_label(tmp_path):
    path = _density_tif(tmp_path / "d.tif")
    spec = overlay.parse({"raster": path, "levels": [20]})
    assert spec["mode"] == "lines" and spec["label"] is None
    spec = overlay.parse({"raster": path, "levels": [20, 60], "mode": "fill", "label": "fallen stems, m/ha"})
    assert spec["mode"] == "fill" and spec["label"] == "fallen stems, m/ha"
    with pytest.raises(ConfigError, match="overlay.mode"):
        overlay.parse({"raster": path, "levels": [20], "mode": "glow"})


def test_fill_mode_masks_are_bands_between_levels(tmp_path):
    # Band i covers level_i <= value < level_{i+1}; the last band is open-ended. A
    # block of 50 therefore fills the [20, 60) band completely and the >= 60 band not
    # at all — the interior is painted, unlike the lines mode.
    path = _density_tif(tmp_path / "d.tif")
    spec = overlay.parse({"raster": path, "levels": [20, 60], "mode": "fill"})
    (m20, _), (m60, _) = overlay.level_masks(spec, (0.0, 0.0, 1.0, 1.0), None, (200, 200))
    assert m20[100, 100] == 1 and m20[61:139, 61:139].all()     # interior filled
    assert m20[20, 20] == 0 and m60.sum() == 0
    assert abs(m20.mean() - 0.16) < 0.01                           # 4x4 of 10x10 cells = 16 %


def test_level_masks_need_rasterio(monkeypatch, tmp_path):
    import builtins
    real_import = builtins.__import__

    def no_rasterio(name, *a, **k):
        if name == "rasterio" or name.startswith("rasterio."):
            raise ImportError("no rasterio")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_rasterio)
    with pytest.raises(RuntimeError, match="ml"):
        overlay.level_masks({"raster": "x.tif", "levels": [1.0], "colors": ["#fff"], "line_px": 1, "alpha": 0.9},
                            (0, 0, 1, 1), None, (10, 10))
