import numpy as np
from gee_animation.imaging import ndvi, colorize, distrad_sharpen, _block_mean, _hex_to_rgb
from gee_animation.products import INDICES


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


# --- NDVI default palette: CVD-safe, water-blue bottom stop (review H2/H3) -------
# imaging.colorize interpolates linearly across EVENLY SPACED stops (verified
# above: K stops -> K-1 equal-width segments over vmin..vmax). The ndvi default_viz
# is (-0.2, 1.0, 7 stops): a dark and a pale water-blue stop (-0.2, 0), followed by
# the unmodified 5-stop ColorBrewer BrBG land ramp (0, 0.2, 0.4, 0.6, 0.8, 1.0).
# Two water stops (not one) matter: a single stop spanning the whole negative range
# was verified to fail -- water as shallow as NDVI=-0.1 already blended into brown
# (see products.py comment). The paler second stop keeps the entire negative range
# blue while the ramp still turns brown by NDVI~0.02-0.05.

def test_ndvi_default_viz_water_stays_in_blue_family():
    vmin, vmax, palette = INDICES["ndvi"].default_viz
    values = np.linspace(vmin, -0.001, 8).reshape(1, -1)   # vmin .. just below zero
    rgb = colorize(values, vmin, vmax, palette).astype(int)
    reds, blues = rgb[..., 0], rgb[..., 2]
    assert np.all(reds < blues), f"expected blue-family (R<B) for every water pixel, got {rgb}"


def test_ndvi_default_viz_bare_soil_is_brown_family():
    vmin, vmax, palette = INDICES["ndvi"].default_viz
    rgb = colorize(np.array([[0.05]]), vmin, vmax, palette).astype(int)
    r, _, b = rgb[0, 0]
    assert r > b, f"expected brown-family (R>B) at NDVI=0.05, got {rgb[0, 0]}"


def test_ndvi_default_viz_low_label_is_water():
    assert INDICES["ndvi"].low_label == "water"
    assert INDICES["ndvi"].high_label == "dense vegetation"


# --- Deuteranopia simulation (Vienot, Brettel & Mollon 1999) --------------------
# A minimal linear-RGB -> LMS -> deuteranope-projection -> LMS -> linear-RGB
# pipeline, used only as a test helper to catch colour-vision-deficiency
# regressions in the palette (review finding H2: the old brown/green ramp's
# endpoints were near-indistinguishable to deuteranopes).
_RGB2LMS = np.array([
    [17.8824, 43.5161, 4.11935],
    [3.45565, 27.1554, 3.86714],
    [0.0299566, 0.184309, 1.46709],
])
_LMS2RGB = np.linalg.inv(_RGB2LMS)
# Deuteranope (missing M-cone) projection in LMS space: L and S pass through
# unchanged, M is reconstructed as a linear combination of L and S.
_DEUTERANOPE_LMS = np.array([
    [1.0, 0.0, 0.0],
    [0.494207, 0.0, 1.24827],
    [0.0, 0.0, 1.0],
])


def _srgb_to_linear(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def _simulate_deuteranopia(rgb255):
    lin = _srgb_to_linear(np.asarray(rgb255, dtype=float))
    lms = lin @ _RGB2LMS.T
    lms_sim = lms @ _DEUTERANOPE_LMS.T
    lin_sim = lms_sim @ _LMS2RGB.T
    return np.clip(_linear_to_srgb(lin_sim) * 255.0, 0, 255)


def _deuteranope_endpoint_separation(hex_a, hex_b):
    a = _simulate_deuteranopia(np.array(_hex_to_rgb(hex_a), dtype=float))
    b = _simulate_deuteranopia(np.array(_hex_to_rgb(hex_b), dtype=float))
    return float(np.linalg.norm(a - b))


def test_ndvi_new_palette_endpoints_separate_under_deuteranopia():
    _, _, palette = INDICES["ndvi"].default_viz
    sep = _deuteranope_endpoint_separation(palette[0], palette[-1])
    assert sep > 60.0, f"new palette endpoints only separate by {sep:.1f}/255 under deuteranopia"


def test_old_ndvi_palette_endpoints_fail_deuteranopia_check():
    # Regression teeth: prove the helper actually detects the problem it guards
    # against, using the OLD brown/green palette the review flagged (H2).
    sep = _deuteranope_endpoint_separation("#a1622f", "#3b7a2a")
    assert sep <= 60.0, f"expected the old palette to fail the separation check, got {sep:.1f}/255"
