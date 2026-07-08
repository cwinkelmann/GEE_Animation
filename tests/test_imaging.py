import numpy as np
from gee_animation.imaging import ndvi, colorize


def test_ndvi_basic():
    nir = np.array([[0.5, 0.2]])
    red = np.array([[0.1, 0.2]])
    out = ndvi(nir, red)
    # (0.5-0.1)/(0.5+0.1)=0.666..., (0.2-0.2)/0.4=0.0
    assert np.allclose(out, [[0.4 / 0.6, 0.0]])


def test_ndvi_zero_denominator_is_zero():
    out = ndvi(np.array([0.0]), np.array([0.0]))
    assert out.tolist() == [0.0]


def test_ndvi_clips_out_of_range_values():
    # non-zero denominator, NDVI magnitude > 1 -> must clip to the bound
    hi = ndvi(np.array([1.0]), np.array([-0.5]))   # (1.0-(-0.5))/(1.0-0.5)=3.0 -> 1.0
    lo = ndvi(np.array([-0.5]), np.array([1.0]))   # (-0.5-1.0)/(0.5)= -3.0 -> -1.0
    assert hi.tolist() == [1.0]
    assert lo.tolist() == [-1.0]


def test_colorize_shape_and_endpoints():
    arr = np.array([[-0.2, 0.9]])
    rgb = colorize(arr, -0.2, 0.9, ["#000000", "#ffffff"])
    assert rgb.shape == (1, 2, 3)
    assert rgb.dtype == np.uint8
    assert rgb[0, 0].tolist() == [0, 0, 0]        # vmin -> first colour
    assert rgb[0, 1].tolist() == [255, 255, 255]  # vmax -> last colour


def test_colorize_clamps_out_of_range():
    arr = np.array([[-5.0, 5.0]])
    rgb = colorize(arr, -0.2, 0.9, ["#000000", "#ffffff"])
    assert rgb[0, 0].tolist() == [0, 0, 0]
    assert rgb[0, 1].tolist() == [255, 255, 255]


def test_colorize_interpolates_midpoint():
    # black->white at the midpoint: 255*0.5 = 127.5 -> rounds to 128
    rgb = colorize(np.array([[0.5]]), 0.0, 1.0, ["#000000", "#ffffff"])
    assert rgb[0, 0].tolist() == [128, 128, 128]


def test_colorize_three_stop_palette_hits_middle_stop():
    # K=3: midpoint lands exactly on the middle stop (red)
    rgb = colorize(np.array([[0.5]]), 0.0, 1.0, ["#000000", "#ff0000", "#ffffff"])
    assert rgb[0, 0].tolist() == [255, 0, 0]


def test_colorize_rejects_vmax_not_greater_than_vmin():
    import pytest
    with pytest.raises(ValueError):
        colorize(np.array([[0.0]]), 1.0, 1.0, ["#000000", "#ffffff"])
