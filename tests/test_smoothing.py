"""Harmonic smoothing: the model maths, and the guards around it."""
import math
import types

import pytest

from gee_animation import smoothing
from gee_animation.compositing import Frame
from gee_animation.config import ConfigError, RunConfig


def test_predictor_names_and_values_agree_in_order():
    # linearRegression consumes the first numX bands as predictors and returns
    # coefficients in the SAME order, so names and values must never drift apart.
    for k in (1, 2, 3):
        names = smoothing.predictor_names(k)
        values = smoothing.predictor_values(0.3, k)
        assert len(names) == len(values) == 2 + 2 * k
        assert names[:2] == ["constant", "t"]
        assert names[2:] == [n for j in range(1, k + 1) for n in (f"sin{j}", f"cos{j}")]


def test_model_reproduces_a_known_seasonal_curve():
    # Evaluating the design row against known coefficients must reproduce the
    # curve exactly — this is the model itself, checked without Earth Engine.
    coeffs = [10.0, 0.5, 4.0, -2.0]          # constant, trend, sin1, cos1
    def value(t):
        return sum(c * v for c, v in zip(coeffs, smoothing.predictor_values(t, 1)))
    # at t=0: constant + cos1 term (cos 0 = 1)
    assert value(0.0) == pytest.approx(10.0 - 2.0)
    # a quarter year later sin1 peaks and cos1 vanishes, plus a quarter of trend
    assert value(0.25) == pytest.approx(10.0 + 0.125 + 4.0)
    # one full year on, the seasonal terms return and only the trend has moved
    assert value(1.0) - value(0.0) == pytest.approx(0.5)


def test_second_harmonic_completes_two_cycles_per_year():
    # k=2 must repeat twice a year — that is what captures green-up/senescence
    # asymmetry rather than a single symmetric sine.
    at0 = smoothing.predictor_values(0.0, 2)
    half = smoothing.predictor_values(0.5, 2)
    assert half[4] == pytest.approx(at0[4], abs=1e-9)   # sin2 back to its start
    assert half[5] == pytest.approx(at0[5], abs=1e-9)   # cos2 likewise
    assert half[3] == pytest.approx(-at0[3])            # cos1 is inverted, though


@pytest.mark.parametrize("label,expected", [
    ("2022-07", "2022-07-01"),
    ("2022-Q3", "2022-07-01"),
    ("2022-Q1", "2022-01-01"),
    ("2022-07-11", "2022-07-11"),
])
def test_frame_dates_resolve_for_every_label_format(label, expected):
    assert smoothing._frame_date(label) == expected


def test_apply_is_a_noop_without_smoothing_configured():
    frames = [Frame("2022-07", "IMG", 3)]
    cfg = types.SimpleNamespace(smooth=None)
    assert smoothing.apply(frames, "COLL", cfg) is frames


def test_apply_replaces_imagery_but_keeps_provenance(monkeypatch):
    # Only the pixels are modelled; the label and scene count still describe the
    # real data the curve was fitted through, so the frame text stays truthful.
    monkeypatch.setattr(smoothing, "fit", lambda coll, k, ee_module: "COEFFS")
    monkeypatch.setattr(smoothing, "evaluate",
                        lambda c, when, k, ee_module: types.SimpleNamespace(
                            set=lambda *a: f"FITTED@{when}"))
    ee = types.SimpleNamespace(Date=lambda d: types.SimpleNamespace(millis=lambda: 0))
    frames = [Frame("2022-07", "IMG", 3, 2019)]
    cfg = types.SimpleNamespace(smooth="harmonic", harmonics=2)
    out = smoothing.apply(frames, "COLL", cfg, ee_module=ee)
    assert out[0].image == "FITTED@2022-07-01"
    assert (out[0].label, out[0].n_scenes, out[0].source) == ("2022-07", 3, 2019)


def test_apply_rejects_an_unknown_mode():
    cfg = types.SimpleNamespace(smooth="whittaker")
    with pytest.raises(ValueError, match="whittaker"):
        smoothing.apply([], "COLL", cfg)


# --- guards: a model must not masquerade as a measurement ------------------

def _cfg(tmp_path, extra):
    import textwrap
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent(
        "name: t\nproject: p\n"
        "aoi:\n  frame: {shapefile: f.shp}\n  region: {shapefile: r.shp}\n"
        'start: "2018-01-01"\nend: "2023-01-01"\n'
        "sensor: landsat\ncadence: monthly\nmax_cloud_percent: 60\n"
        "render: {fps: 4, scale: 30, dimensions: 768}\n" + extra))
    return p


def test_harmonic_cannot_be_combined_with_metadata(tmp_path):
    # Every frame would be a curve evaluated at a date, so the stats table would
    # describe the model rather than the landscape.
    with pytest.raises(ConfigError, match="model output"):
        RunConfig.from_yaml(_cfg(tmp_path, "index: lst\nsmooth: harmonic\nmetadata: true\n"))


def test_harmonic_rejects_composites_and_classified_products(tmp_path):
    with pytest.raises(ConfigError, match="composite"):
        RunConfig.from_yaml(_cfg(tmp_path, "index: rgb\nsmooth: harmonic\n").with_suffix(".yaml")
                            if False else _cfg(tmp_path, "index: rgb\nsmooth: harmonic\n"))


def test_harmonic_bounds_the_harmonic_count(tmp_path):
    with pytest.raises(ConfigError, match="between 1 and 5"):
        RunConfig.from_yaml(_cfg(tmp_path, "index: lst\nsmooth: harmonic\nharmonics: 9\n"))
    cfg = RunConfig.from_yaml(_cfg(tmp_path, "index: lst\nsmooth: harmonic\nharmonics: 3\n"))
    assert cfg.smooth == "harmonic" and cfg.harmonics == 3


def test_unknown_smooth_mode_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="harmonic"):
        RunConfig.from_yaml(_cfg(tmp_path, "index: lst\nsmooth: whittaker\n"))


def test_smoothed_frames_say_so_in_the_header():
    # The strongest claim on the frame and the least visible one: a smooth,
    # hole-free picture looks exactly like a well-observed one.
    from gee_animation.render import _caveats
    cfg = types.SimpleNamespace(pool_years=None, interpolate=0,
                                smooth="harmonic", harmonics=2)
    assert "modelled" in _caveats(cfg) and "harmonic" in _caveats(cfg)
    cfg.harmonics = 1
    assert "1 harmonic," in _caveats(cfg) + ","      # singular, not "1 harmonics"
    cfg.smooth = None
    assert "modelled" not in _caveats(cfg)
