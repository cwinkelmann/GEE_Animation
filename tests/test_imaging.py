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


def test_ndvi_clipped_to_unit_range():
    out = ndvi(np.array([1.0]), np.array([-1.0]))  # would exceed 1
    assert out.max() <= 1.0 and out.min() >= -1.0


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
