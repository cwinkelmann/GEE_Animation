import numpy as np
from gee_animation.imaging import ndvi, colorize, distrad_sharpen, _block_mean


def test_distrad_sharpen_is_conservative():
    # P1-2 acceptance: aggregating the sharpened output back to the coarse grid must
    # reproduce the input coarse LST to float tolerance (residual correction).
    rng = np.random.default_rng(0)
    factor = 3
    lst_coarse = 15 + 12 * rng.random((8, 10))                 # deg C
    pred_fine = rng.random((8 * factor, 10 * factor))          # e.g. NIRv at fine scale
    sharp = distrad_sharpen(lst_coarse, pred_fine, factor)
    assert sharp.shape == (24, 30)
    np.testing.assert_allclose(_block_mean(sharp, factor), lst_coarse, atol=1e-9)


def test_distrad_sharpen_injects_fine_structure():
    # LST correlates with the predictor across coarse cells (fit a=8, b=12), so
    # within-cell predictor variation gives the sharpened LST fine structure.
    pred = np.array([[0.0, 1.0, 0.0, 0.0],
                     [0.0, 0.0, 0.0, 0.0],
                     [1.0, 1.0, 1.0, 1.0],
                     [1.0, 1.0, 1.0, 1.0]])
    lst_coarse = 8 * _block_mean(pred, 2) + 12                 # perfectly linear
    sharp = distrad_sharpen(lst_coarse, pred, 2)
    assert sharp[0, 1] > sharp[0, 0]                           # brighter predictor -> warmer
    np.testing.assert_allclose(_block_mean(sharp, 2), lst_coarse, atol=1e-9)


def test_distrad_flat_predictor_returns_coarse_value():
    # no predictor gradient -> flat fit -> every fine pixel == the coarse LST
    sharp = distrad_sharpen(np.array([[25.0, 30.0]]), np.ones((2, 4)), 2)
    np.testing.assert_allclose(sharp, [[25, 25, 30, 30], [25, 25, 30, 30]], atol=1e-9)


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
