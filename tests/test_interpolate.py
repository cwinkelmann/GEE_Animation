import numpy as np
import pytest

from gee_animation.interpolate import blend


def test_blend_midpoint_of_two_valid_frames():
    a = np.array([[0.0, 10.0]])
    b = np.array([[10.0, 20.0]])
    ok = np.ones((1, 2), dtype=bool)
    vals, valid = blend(a, ok, b, ok, 0.5)
    assert np.allclose(vals, [[5.0, 15.0]])
    assert valid.all()


def test_blend_endpoints_are_exact():
    a = np.array([[1.0]]); b = np.array([[2.0]])
    ok = np.ones((1, 1), dtype=bool)
    assert blend(a, ok, b, ok, 0.0)[0] == pytest.approx(1.0)
    assert blend(a, ok, b, ok, 1.0)[0] == pytest.approx(2.0)


def test_blend_holds_the_valid_endpoint_instead_of_fading_to_grey():
    # A cloud hole present in one observation and absent in the next must not
    # produce a grey pulse: the generated pixel holds the valid endpoint's value.
    a = np.array([[5.0]]); b = np.array([[99.0]])
    a_ok = np.array([[True]]); b_ok = np.array([[False]])
    vals, valid = blend(a, a_ok, b, b_ok, 0.5)
    assert vals[0, 0] == pytest.approx(5.0)      # A's value, not a blend toward B
    assert valid[0, 0]                            # and it counts as data

    vals, valid = blend(b, b_ok, a, a_ok, 0.5)   # mirror: B invalid on the left
    assert vals[0, 0] == pytest.approx(5.0)
    assert valid[0, 0]


def test_blend_marks_pixels_invalid_only_when_both_endpoints_are():
    a = np.array([[1.0]]); b = np.array([[2.0]])
    no = np.array([[False]])
    _vals, valid = blend(a, no, b, no, 0.5)
    assert not valid[0, 0]


def test_blend_handles_colour_frames():
    a = np.zeros((1, 1, 3)); b = np.full((1, 1, 3), 10.0)
    ok = np.ones((1, 1), dtype=bool)
    vals, _valid = blend(a, ok, b, ok, 0.5)
    assert np.allclose(vals, 5.0)
