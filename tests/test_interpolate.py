import numpy as np
import pytest

from gee_animation.interpolate import blend, expand


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


def _items(*labels):
    import numpy as np
    ok = np.ones((1, 1), dtype=bool)
    return [(np.array([[float(i)]]), ok, lab) for i, lab in enumerate(labels)]


def _adjacent(a, b):        # every pair is one period apart
    return 1


def test_expand_inserts_steps_between_each_pair():
    out = list(expand(_items("2022-05", "2022-06"), steps=3, period_gap=_adjacent))
    assert [lab for _v, _ok, lab, _real in out] == [
        "2022-05",
        "2022-05 -> 2022-06  25%",
        "2022-05 -> 2022-06  50%",
        "2022-05 -> 2022-06  75%",
        "2022-06",
    ]
    assert [real for *_r, real in out] == [True, False, False, False, True]


def test_expand_spacing_is_proportional_to_the_gap():
    # A two-period gap gets twice the generated frames of a one-period gap, so
    # playback speed tracks elapsed time rather than frame index.
    gaps = {("a", "b"): 1, ("b", "c"): 2}
    out = list(expand(_items("a", "b", "c"), steps=2, period_gap=lambda x, y: gaps[(x, y)]))
    generated = [lab for _v, _ok, lab, real in out if not real]
    assert sum(lab.startswith("a ->") for lab in generated) == 2
    assert sum(lab.startswith("b ->") for lab in generated) == 4


def test_expand_with_zero_steps_yields_only_observations():
    out = list(expand(_items("x", "y"), steps=0, period_gap=_adjacent))
    assert [lab for _v, _ok, lab, _r in out] == ["x", "y"]
    assert all(real for *_r, real in out)


def test_expand_single_frame_is_unchanged():
    out = list(expand(_items("only"), steps=5, period_gap=_adjacent))
    assert [lab for _v, _ok, lab, _r in out] == ["only"]


def test_expand_generated_values_lie_between_the_endpoints():
    out = list(expand(_items("a", "b"), steps=1, period_gap=_adjacent))
    mid = [v for v, _ok, _lab, real in out if not real][0]
    assert 0.0 < float(mid.item()) < 1.0
