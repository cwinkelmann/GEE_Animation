import pytest

from gee_animation.webapp.caps import CapExceeded, check


def test_a_run_inside_the_limits_passes():
    check({"start": "2020-01-01", "end": "2025-01-01", "preset": "1080p"})


def test_too_many_years_is_refused_with_the_limit_named():
    with pytest.raises(CapExceeded, match="12 years"):
        check({"start": "2000-01-01", "end": "2026-01-01", "preset": "1080p"})


def test_too_large_a_preset_is_refused():
    with pytest.raises(CapExceeded, match="1440p"):
        check({"start": "2024-01-01", "end": "2025-01-01", "preset": "4k"})


def test_the_native_preset_is_allowed():
    check({"start": "2024-01-01", "end": "2025-01-01", "preset": "native (no upscaling)"})


def test_a_missing_or_blank_preset_is_allowed():
    check({"start": "2024-01-01", "end": "2025-01-01", "preset": ""})


def test_an_unparsable_date_is_refused_rather_than_ignored():
    with pytest.raises(CapExceeded):
        check({"start": "not-a-date", "end": "2025-01-01", "preset": "1080p"})


def test_an_unknown_preset_is_refused_and_named():
    with pytest.raises(CapExceeded, match="'8k'"):
        check({"start": "2024-01-01", "end": "2025-01-01", "preset": "8k"})


def test_the_unknown_preset_refusal_lists_the_allowed_presets():
    with pytest.raises(CapExceeded, match="allowed presets are .*720p.*native \\(no upscaling\\)"):
        check({"start": "2024-01-01", "end": "2025-01-01", "preset": "8k"})


def test_a_reversed_date_range_is_refused():
    with pytest.raises(CapExceeded, match="is not after the start date 2025-01-01"):
        check({"start": "2025-01-01", "end": "2024-01-01", "preset": "1080p"})


def test_an_end_equal_to_the_start_is_refused():
    with pytest.raises(CapExceeded, match="is not after the start date 2024-01-01"):
        check({"start": "2024-01-01", "end": "2024-01-01", "preset": "1080p"})
