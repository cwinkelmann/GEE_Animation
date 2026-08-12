from gee_animation.labels import generated_text, observed_text, period_text


# --- period_text -----------------------------------------------------------

def test_period_text_monthly():
    assert period_text("2022-05") == "May 2022"


def test_period_text_submonthly():
    assert period_text("2022-05-11") == "11 May 2022"


# --- observed_text -----------------------------------------------------------

def test_observed_text_plural_passes():
    assert observed_text("2022-05", 3, None) == "May 2022 · 3 passes"


def test_observed_text_singular_pass():
    assert observed_text("2022-05", 1, None) == "May 2022 · 1 pass"


def test_observed_text_borrowed_year_and_singular_pass():
    # Segment order is pinned: period, then provenance (source), then count.
    assert observed_text("2022-09", 1, 2018) == "September 2022 · image from 2018 · 1 pass"


def test_observed_text_omits_passes_segment_when_n_scenes_none():
    # No dangling separator when the count is unknown (see debug.py's per-scene
    # frames, which leave n_scenes=None deliberately).
    assert observed_text("2022-05", None, None) == "May 2022"


def test_observed_text_borrowed_year_without_count():
    assert observed_text("2022-09", None, 2018) == "September 2022 · image from 2018"


def test_observed_text_borrowed_year_range_source():
    # compositing.gap_fill's multi-year pool source is a "YYYY–YYYY" string, not
    # an int; observed_text must not assume source is a year number.
    assert observed_text("2022-05", 4, "2019–2021") == "May 2022 · image from 2019–2021 · 4 passes"


# --- generated_text -----------------------------------------------------------

def test_generated_text_monthly_same_year():
    assert generated_text("2022-05", "2022-06", 36) == "between May and June 2022 · 36%"


def test_generated_text_monthly_year_boundary():
    # Crossing a year boundary: the year is ambiguous per side, so both are shown.
    assert generated_text("2021-12", "2022-01", 50) == "between December 2021 and January 2022 · 50%"


def test_generated_text_submonthly_same_month():
    assert generated_text("2022-05-11", "2022-05-21", 50) == "between 11 May and 21 May 2022 · 50%"


def test_generated_text_submonthly_cross_month_same_year():
    # Not explicitly pinned by the brief: a sub-monthly gap can still cross a
    # month boundary while staying inside one year (e.g. semimonthly/10day
    # cadences). Chosen wording mirrors the monthly cross-boundary case — each
    # side keeps its own day+month, the shared year appears once at the end.
    assert generated_text("2022-05-21", "2022-06-01", 50) == "between 21 May and 1 June 2022 · 50%"


def test_generated_text_submonthly_year_boundary():
    assert generated_text("2021-12-21", "2022-01-01", 50) == "between 21 December 2021 and 1 January 2022 · 50%"


def test_period_text_quarterly_spells_the_month_range():
    assert period_text("2022-Q1") == "January–March 2022"
    assert period_text("2022-Q4") == "October–December 2022"


def test_observed_text_quarterly():
    assert observed_text("2022-Q3", 5, None) == "July–September 2022 · 5 passes"
    assert (observed_text("2022-Q1", 1, 2019)
            == "January–March 2022 · image from 2019 · 1 pass")


def test_generated_text_quarterly_shares_or_splits_the_year_like_months():
    assert (generated_text("2022-Q1", "2022-Q2", 36)
            == "between January–March and April–June 2022 · 36%")
    assert (generated_text("2022-Q4", "2023-Q1", 50)
            == "between October–December 2022 and January–March 2023 · 50%")
