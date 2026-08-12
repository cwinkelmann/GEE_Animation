import textwrap
from pathlib import Path

import pytest
from gee_animation.config import RunConfig, ConfigError

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(tmp_path, body: str):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def _base(extra: str) -> str:
    return (
        "name: t\nproject: p\n"
        "aoi:\n  frame: {shapefile: frame.shp}\n  region: {shapefile: region.shp}\n"
        'start: "2022-01-01"\nend: "2023-01-01"\n'
        "sensor: landsat\ncadence: monthly\nmax_cloud_percent: 60\n"
        "render: {fps: 4, scale: 30, dimensions: 768}\n" + extra)


def test_climatology_anomaly_uses_diverging_default_and_needs_baseline(tmp_path):
    cfg = RunConfig.from_yaml(_write(tmp_path, _base(
        "index: lst\nanomaly: climatology\nbaseline_years: [2015, 2024]\n")))
    assert cfg.anomaly == "climatology" and cfg.baseline_years == [2015, 2024]
    assert (cfg.viz_min, cfg.viz_max) == (-3.0, 3.0)          # diverging default applied
    # missing baseline_years is rejected
    with pytest.raises(ConfigError, match="baseline_years"):
        RunConfig.from_yaml(_write(tmp_path, _base("index: lst\nanomaly: climatology\n")))


def test_reference_anomaly_is_thermal_only(tmp_path):
    with pytest.raises(ConfigError, match="thermal-only"):
        RunConfig.from_yaml(_write(tmp_path, _base("index: ndvi\nanomaly: reference\n")))
    cfg = RunConfig.from_yaml(_write(tmp_path, _base("index: lst_smw\nanomaly: reference\n")))
    assert cfg.anomaly == "reference"


def test_validate_rejects_anomaly_with_sub_monthly_cadence(tmp_path):
    # anomaly divides a period mean by a *monthly* climatology sigma; a semimonthly
    # slice would silently produce inflated z-scores, so this must be a hard reject.
    body = _base(
        "index: lst\nanomaly: climatology\nbaseline_years: [2015, 2024]\n"
    ).replace("cadence: monthly", "cadence: semimonthly")
    with pytest.raises(ConfigError, match="cadence"):
        RunConfig.from_yaml(_write(tmp_path, body))


def test_accepts_shapefile_aois(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {shapefile: frame.shp}
          region: {shapefile: region.shp}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.frame_aoi == {"shapefile": "frame.shp"}
    assert cfg.region_aoi == {"shapefile": "region.shp"}


def test_rejects_null_aoi_with_clean_error(tmp_path):
    # A present-but-null `aoi:` must raise ConfigError, not a raw TypeError.
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError):
        RunConfig.from_yaml(p)


def test_from_yaml_loads_valid_config(tmp_path):
    p = _write(tmp_path, """
        name: test
        project: hnee-331218
        aoi:
          frame:
            bbox: [13.7, 52.8, 13.9, 52.95]
          region:
            bbox: [13.7, 52.8, 13.9, 52.95]
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render:
          fps: 4
          scale: 20
          dimensions: 768
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.name == "test"
    assert cfg.project == "hnee-331218"
    assert cfg.frame_aoi == {"bbox": [13.7, 52.8, 13.9, 52.95]}
    assert cfg.region_aoi == {"bbox": [13.7, 52.8, 13.9, 52.95]}
    assert cfg.viz_min == -0.2 and cfg.viz_max == 1.0
    assert cfg.palette == ["#4575b4", "#aeaec7", "#8c510a", "#d8b365", "#f6e8c3", "#5ab4ac", "#01665e"]
    assert cfg.fps == 4 and cfg.dimensions == 768
    assert cfg.out_dir == "out"


def test_index_viz_defaults_from_index(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {frame: {bbox: [0,0,1,1]}, region: {bbox: [0,0,1,1]}}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.index == "ndvi"
    assert cfg.viz_min == -0.2 and cfg.viz_max == 1.0
    assert cfg.palette == ["#4575b4", "#aeaec7", "#8c510a", "#d8b365", "#f6e8c3", "#5ab4ac", "#01665e"]


def test_viz_block_overrides_defaults(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {frame: {bbox: [0,0,1,1]}, region: {bbox: [0,0,1,1]}}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: landsat
        index: lst
        cadence: monthly
        max_cloud_percent: 60
        viz: {min: 5, max: 35, palette: ["#000000", "#ffffff"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.viz_min == 5 and cfg.viz_max == 35 and cfg.palette == ["#000000", "#ffffff"]


def test_crs_defaults_to_auto_when_yaml_omits_it(tmp_path):
    # Regression guard: an absent render.crs key must NOT pass an explicit
    # None into RunConfig (which would override the dataclass default).
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {frame: {bbox: [0,0,1,1]}, region: {bbox: [0,0,1,1]}}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.crs == "auto"


def test_explicit_crs_in_yaml_is_preserved(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {frame: {bbox: [0,0,1,1]}, region: {bbox: [0,0,1,1]}}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768, crs: "EPSG:4326"}
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.crs == "EPSG:4326"


def test_region_line_width_defaults_to_none(tmp_path):
    cfg = RunConfig.from_yaml(_write(tmp_path, _base("index: ndvi\n")))
    assert cfg.region_line_width is None


def test_region_line_width_read_from_render_block(tmp_path):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, region_line_width: 5}"))
    cfg = RunConfig.from_yaml(p)
    assert cfg.region_line_width == 5


def test_rejects_non_positive_region_line_width(tmp_path):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, region_line_width: 0}"))
    with pytest.raises(ConfigError, match="region_line_width"):
        RunConfig.from_yaml(p)


def test_workers_defaults_to_four(tmp_path):
    cfg = RunConfig.from_yaml(_write(tmp_path, _base("index: ndvi\n")))
    assert cfg.workers == 4


def test_workers_read_from_render_block(tmp_path):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, workers: 8}"))
    assert RunConfig.from_yaml(p).workers == 8


def test_rejects_non_positive_workers(tmp_path):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, workers: 0}"))
    with pytest.raises(ConfigError, match="workers"):
        RunConfig.from_yaml(p)


def test_cache_defaults_on_and_dir_unset(tmp_path):
    cfg = RunConfig.from_yaml(_write(tmp_path, _base("index: ndvi\n")))
    assert cfg.cache is True and cfg.cache_dir is None


def test_cache_can_be_disabled_and_redirected(tmp_path):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, cache: false, cache_dir: /tmp/x}"))
    cfg = RunConfig.from_yaml(p)
    assert cfg.cache is False and cfg.cache_dir == "/tmp/x"


def test_rejects_unsupported_sensor_index_pair(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {frame: {bbox: [0,0,1,1]}, region: {bbox: [0,0,1,1]}}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: lst
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="not available"):
        RunConfig.from_yaml(p)


def test_rejects_end_before_start(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {bbox: [0, 0, 1, 1]}
          region: {bbox: [0, 0, 1, 1]}
        start: "2023-01-01"
        end: "2022-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="end.*after.*start"):
        RunConfig.from_yaml(p)


def test_rejects_unsupported_cadence(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {bbox: [0, 0, 1, 1]}
          region: {bbox: [0, 0, 1, 1]}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: weekly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="cadence"):
        RunConfig.from_yaml(p)


def test_warns_on_sub_monthly_cadence_with_landsat(tmp_path, caplog):
    # Landsat's 16-day repeat leaves most 10-day bins empty — warn, don't reject.
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {bbox: [0, 0, 1, 1]}
          region: {bbox: [0, 0, 1, 1]}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: landsat
        index: lst
        cadence: 10day
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with caplog.at_level("WARNING"):
        cfg = RunConfig.from_yaml(p)
    assert cfg.cadence == "10day"
    assert "landsat" in caplog.text.lower() and "10day" in caplog.text


def test_rejects_viz_max_not_greater_than_min(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {bbox: [0, 0, 1, 1]}
          region: {bbox: [0, 0, 1, 1]}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        viz: {min: 0.9, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="viz.max"):
        RunConfig.from_yaml(p)


def test_rejects_empty_palette(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {bbox: [0, 0, 1, 1]}
          region: {bbox: [0, 0, 1, 1]}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        viz: {min: -0.2, max: 0.9, palette: []}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="palette"):
        RunConfig.from_yaml(p)


def test_from_yaml_loads_two_aois(tmp_path):
    p = _write(tmp_path, """
        name: test
        project: hnee-331218
        aoi:
          frame:
            bbox: [13.7, 52.8, 13.9, 52.95]
          region:
            geojson:
              type: Polygon
              coordinates: [[[13.75, 52.83], [13.85, 52.83], [13.85, 52.90], [13.75, 52.83]]]
          region_max_cloud_percent: 10
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.frame_aoi == {"bbox": [13.7, 52.8, 13.9, 52.95]}
    assert cfg.region_aoi["geojson"]["type"] == "Polygon"
    assert cfg.region_max_cloud_percent == 10


def test_region_max_cloud_percent_defaults_to_10(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {bbox: [0, 0, 1, 1]}
          region: {bbox: [0, 0, 1, 1]}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    assert RunConfig.from_yaml(p).region_max_cloud_percent == 10


def test_rejects_missing_frame_or_region(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {}
          region: {bbox: [0, 0, 1, 1]}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="frame"):
        RunConfig.from_yaml(p)


def test_rejects_region_cloud_percent_out_of_range(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {bbox: [0, 0, 1, 1]}
          region: {bbox: [0, 0, 1, 1]}
          region_max_cloud_percent: 150
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="region_max_cloud_percent"):
        RunConfig.from_yaml(p)


def test_rejects_unknown_sensor(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {frame: {bbox: [0,0,1,1]}, region: {bbox: [0,0,1,1]}}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: viirs
        index: ndvi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="sensor"):
        RunConfig.from_yaml(p)


def test_rejects_unknown_index_cleanly(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {frame: {bbox: [0,0,1,1]}, region: {bbox: [0,0,1,1]}}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        index: savi
        cadence: monthly
        max_cloud_percent: 60
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="index"):
        RunConfig.from_yaml(p)


def test_pool_years_defaults_off_and_loads(tmp_path):
    # absent -> off, and the strategy default is least_cloudy
    plain = RunConfig.from_yaml(_write(tmp_path, _base("index: ndvi\n")))
    assert plain.pool_years is None and plain.pool_strategy == "least_cloudy"
    cfg = RunConfig.from_yaml(_write(tmp_path, _base(
        "index: ndvi\npool_years: [2019, 2024]\npool_strategy: median\n"
        "missions: [L8, L9]\n")))
    assert cfg.pool_years == [2019, 2024] and cfg.pool_strategy == "median"


def test_validate_rejects_anomaly_with_pool_years(tmp_path):
    # the anomaly baseline is itself multi-year, so a borrowed year would be scored
    # against a climatology that already contains it
    with pytest.raises(ConfigError, match="pool_years"):
        RunConfig.from_yaml(_write(tmp_path, _base(
            "index: lst\nanomaly: climatology\nbaseline_years: [2015, 2024]\n"
            "pool_years: [2019, 2024]\n")))


def test_validate_accepts_gap_fill_pool_strategy(tmp_path):
    # gap_fill is the only strategy that keeps the requested year wherever it has
    # data, so it has to be selectable from YAML like the other two.
    cfg = RunConfig.from_yaml(_write(tmp_path, _base(
        "index: ndvi\npool_years: [2019, 2024]\npool_strategy: gap_fill\n")))
    assert cfg.pool_strategy == "gap_fill"


def test_validate_rejects_unknown_pool_strategy(tmp_path):
    with pytest.raises(ConfigError, match="pool_strategy"):
        RunConfig.from_yaml(_write(tmp_path, _base(
            "index: ndvi\npool_years: [2019, 2024]\npool_strategy: sharpest\n")))


def test_validate_rejects_malformed_pool_years(tmp_path):
    with pytest.raises(ConfigError, match="pool_years"):
        RunConfig.from_yaml(_write(tmp_path, _base(
            "index: ndvi\npool_years: [2019]\n")))
    with pytest.raises(ConfigError, match="precede"):
        RunConfig.from_yaml(_write(tmp_path, _base(
            "index: ndvi\npool_years: [2024, 2019]\n")))


def test_pool_years_with_landsat_and_no_missions_warns(tmp_path, caplog):
    # L7/L8/L9 differ radiometrically (and L7 is SLC-off), so pooled Landsat frames
    # can step between missions — must warn, not silently proceed.
    with caplog.at_level("WARNING"):
        cfg = RunConfig.from_yaml(_write(tmp_path, _base(
            "index: ndvi\npool_years: [2019, 2024]\n")))       # _base is sensor: landsat
    assert cfg.pool_years == [2019, 2024]
    assert "missions" in caplog.text
    caplog.clear()
    with caplog.at_level("WARNING"):
        RunConfig.from_yaml(_write(tmp_path, _base(
            "index: ndvi\npool_years: [2019, 2024]\nmissions: [L8, L9]\n")))
    assert "missions" not in caplog.text


def test_render_output_flags_default_to_true(tmp_path):
    # Both outputs stay on unless a config explicitly opts out, so an existing
    # config's deliverables (MP4 + GIF + per-frame PNGs) are unchanged. `gif` records
    # that it was never configured (None) rather than defaulting here, because the
    # answer depends on the run: render() turns it on for a normal one and off for an
    # interpolated one (several hundred quantized frames). Only an explicit value
    # overrides that — see test_render.py's gif-default tests for the resolution.
    cfg = RunConfig.from_yaml(_write(tmp_path, _base("index: lst\n")))
    assert cfg.gif is None and cfg.frames is True
    cfg.validate()                      # "unconfigured" is valid, unlike a non-boolean


def test_render_gif_explicitly_true_is_kept_distinct_from_unset(tmp_path):
    body = _base("index: lst\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, gif: true}")
    assert RunConfig.from_yaml(_write(tmp_path, body)).gif is True


def test_render_output_flags_can_be_switched_off(tmp_path):
    body = _base("index: lst\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, gif: false, frames: false}")
    cfg = RunConfig.from_yaml(_write(tmp_path, body))
    assert cfg.gif is False and cfg.frames is False


@pytest.mark.parametrize("value", ['"false"', "0", "no-thanks"])
def test_render_gif_rejects_a_non_boolean(tmp_path, value):
    # YAML turns a quoted "false" into a non-empty *string*; bool()-coercing it would
    # silently keep writing the output the user asked to skip.
    body = _base("index: lst\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        f"render: {{fps: 4, scale: 30, dimensions: 768, gif: {value}}}")
    with pytest.raises(ConfigError, match="render.gif must be true or false"):
        RunConfig.from_yaml(_write(tmp_path, body))


def test_validate_rejects_a_non_boolean_render_flag_built_directly(tmp_path):
    # The GUI and api.animate build RunConfig directly, so validate() is their only gate.
    cfg = RunConfig.from_yaml(_write(tmp_path, _base("index: lst\n")))
    cfg.frames = "yes"
    with pytest.raises(ConfigError, match="render.frames must be true or false"):
        cfg.validate()


def test_interpolate_defaults_to_off(tmp_path):
    cfg = RunConfig.from_yaml(_write(tmp_path, _base("index: ndvi\n")))
    assert cfg.interpolate == 0 and cfg.interpolate_mode == "auto"


def test_interpolate_is_read_from_the_render_block(tmp_path):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, interpolate: 10, "
        "interpolate_mode: crossfade}"))
    cfg = RunConfig.from_yaml(p)
    assert cfg.interpolate == 10 and cfg.interpolate_mode == "crossfade"


def test_validate_rejects_negative_interpolate(tmp_path):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, interpolate: -1}"))
    with pytest.raises(ConfigError, match="interpolate"):
        RunConfig.from_yaml(p)


def test_validate_rejects_unknown_interpolate_mode(tmp_path):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, interpolate_mode: wobble}"))
    with pytest.raises(ConfigError, match="interpolate_mode"):
        RunConfig.from_yaml(p)


def test_validate_rejects_data_mode_for_a_composite_index(tmp_path):
    # rgb/cir arrive from EE already coloured, so there is no index array to
    # interpolate; the error must name the alternative.
    p = _write(tmp_path, _base("index: rgb\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, interpolate: 5, "
        "interpolate_mode: data}"))
    with pytest.raises(ConfigError, match="crossfade"):
        RunConfig.from_yaml(p)


def test_title_and_subtitle_round_trip_from_top_level_yaml(tmp_path):
    # The header's first two lines are the only place a frame says WHAT and WHERE it
    # is, so both have to survive the YAML -> RunConfig trip verbatim (including
    # non-ASCII, which the bundled DejaVu font can draw).
    cfg = RunConfig.from_yaml(_write(tmp_path, _base(
        "index: ndvi\n"
        'title: "Grumsiner Forst — UNESCO World Heritage beech forest"\n'
        'subtitle: "Brandenburg, Germany"\n')))
    assert cfg.title == "Grumsiner Forst — UNESCO World Heritage beech forest"
    assert cfg.subtitle == "Brandenburg, Germany"


def test_title_and_subtitle_default_to_none(tmp_path):
    # Absent keys must stay None (not ""), so render() can fall back to the index's
    # display_name for the title and omit the second line entirely.
    cfg = RunConfig.from_yaml(_write(tmp_path, _base("index: ndvi\n")))
    assert cfg.title is None and cfg.subtitle is None


def test_credit_defaults_to_none_for_zero_config_auto_attribution(tmp_path):
    # An absent `credit:` key must stay None (not ""), so render._default_credit
    # falls back to the sensor's auto attribution — zero-config compliance is the
    # whole point of this field.
    cfg = RunConfig.from_yaml(_write(tmp_path, _base("index: ndvi\n")))
    assert cfg.credit is None


def test_credit_round_trips_verbatim_from_top_level_yaml(tmp_path):
    cfg = RunConfig.from_yaml(_write(tmp_path, _base(
        'index: ndvi\ncredit: "Imagery courtesy of ACME Corp"\n')))
    assert cfg.credit == "Imagery courtesy of ACME Corp"


def test_credit_explicit_empty_string_survives_as_empty_not_none(tmp_path, caplog):
    # Unlike title/subtitle, an explicit "" is a distinct, deliberate choice (omit
    # the attribution line) that must not collapse to None (which would mean
    # "unset, pick the sensor default" and silently restore the Copernicus notice).
    body = _base('index: ndvi\ncredit: ""\n').replace("sensor: landsat", "sensor: sentinel2")
    with caplog.at_level("WARNING"):
        cfg = RunConfig.from_yaml(_write(tmp_path, body))
    assert cfg.credit == ""


def test_empty_credit_on_sentinel2_warns_about_the_copernicus_licence(tmp_path, caplog):
    body = _base('index: ndvi\ncredit: ""\n').replace("sensor: landsat", "sensor: sentinel2")
    with caplog.at_level("WARNING"):
        cfg = RunConfig.from_yaml(_write(tmp_path, body))
    assert cfg.credit == ""
    assert "Copernicus" in caplog.text
    caplog.clear()
    # Landsat has no such licence requirement: no warning for the same empty credit.
    with caplog.at_level("WARNING"):
        RunConfig.from_yaml(_write(tmp_path, _base('index: ndvi\ncredit: ""\n')))
    assert "Copernicus" not in caplog.text
    caplog.clear()
    # And a non-empty credit on sentinel2 is a deliberate override, not an
    # omission — no warning either.
    with caplog.at_level("WARNING"):
        RunConfig.from_yaml(_write(tmp_path, _base(
            'index: ndvi\ncredit: "Custom credit"\n').replace(
            "sensor: landsat", "sensor: sentinel2")))
    assert "Copernicus" not in caplog.text


# --- render.quality (MP4 encode quality knob, Task 8) ------------------------------

def test_quality_defaults_to_none(tmp_path):
    cfg = RunConfig.from_yaml(_write(tmp_path, _base("index: ndvi\n")))
    assert cfg.quality is None


def test_quality_read_from_render_block(tmp_path):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, quality: 7}"))
    assert RunConfig.from_yaml(p).quality == 7


@pytest.mark.parametrize("q", [1, 5, 10])
def test_quality_accepts_the_full_1_to_10_range(tmp_path, q):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        f"render: {{fps: 4, scale: 30, dimensions: 768, quality: {q}}}"))
    assert RunConfig.from_yaml(p).quality == q


@pytest.mark.parametrize("q", [0, 11])
def test_quality_rejects_out_of_range_values(tmp_path, q):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        f"render: {{fps: 4, scale: 30, dimensions: 768, quality: {q}}}"))
    with pytest.raises(ConfigError, match="quality"):
        RunConfig.from_yaml(p)


@pytest.mark.parametrize("q", ["abc", "[1, 2]", "high", "{}"])
def test_quality_rejects_non_numeric_values_as_a_config_error(tmp_path, q):
    # Review finding: the int() coercion sat outside from_yaml's guarded block, so a
    # non-numeric quality raised a bare ValueError and `cli.main` -- which catches
    # ConfigError/RuntimeError -- let it out as a traceback. Every other malformed key
    # in this file produces a one-line message; this one must too, and it must name
    # both the key and the value so you can find it in the YAML.
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        f"render: {{fps: 4, scale: 30, dimensions: 768, quality: {q}}}"))
    with pytest.raises(ConfigError, match="render.quality must be a whole number"):
        RunConfig.from_yaml(p)


def test_quality_error_names_the_offending_value(tmp_path):
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, quality: abc}"))
    with pytest.raises(ConfigError, match="'abc'"):
        RunConfig.from_yaml(p)


def test_quality_null_is_the_unset_default(tmp_path):
    # `quality:` with no value is YAML null, i.e. "not configured" -- not an error.
    p = _write(tmp_path, _base("index: ndvi\n").replace(
        "render: {fps: 4, scale: 30, dimensions: 768}",
        "render: {fps: 4, scale: 30, dimensions: 768, quality: null}"))
    assert RunConfig.from_yaml(p).quality is None


def test_wne_summer_pooled_example_loads_as_16_9(tmp_path):
    # Task 8 / review H6: every shipped example used to render aspect: match, which
    # for this AOI is a ~0.91 portrait frame — never letterboxed, never the widescreen
    # 16:9 an audience actually expects on a slide or in a video player.
    cfg = RunConfig.from_yaml(_REPO_ROOT / "config" / "wne_summer_pooled.example.yaml")
    assert cfg.aspect == "16:9"


def test_mask_clouds_defaults_on_and_loads_from_yaml(tmp_path):
    assert RunConfig.from_yaml(_write(tmp_path, _base("index: rgb\n"))).mask_clouds is True
    cfg = RunConfig.from_yaml(_write(tmp_path, _base(
        "index: rgb\nmask_clouds: false\n").replace("sensor: landsat", "sensor: sentinel2")))
    assert cfg.mask_clouds is False


def test_mask_clouds_false_is_rejected_for_palette_indices(tmp_path):
    # A palette index colorizes every unmasked pixel through the ramp, so a cloud
    # left in the data would render as a plausible real value (the CLAUDE.md
    # invariant: clouds must never read as low NDVI / bare soil). Only composites
    # (rgb/cir), where a cloud looks like a cloud, may opt out.
    with pytest.raises(ConfigError, match="composite"):
        RunConfig.from_yaml(_write(tmp_path, _base(
            "index: ndvi\nmask_clouds: false\n").replace(
                "sensor: landsat", "sensor: sentinel2")))
    with pytest.raises(ConfigError, match="composite"):
        RunConfig.from_yaml(_write(tmp_path, _base("index: lst\nmask_clouds: false\n")))
    # composites pass
    for idx in ("rgb", "cir"):
        cfg = RunConfig.from_yaml(_write(tmp_path, _base(
            f"index: {idx}\nmask_clouds: false\n").replace(
                "sensor: landsat", "sensor: sentinel2")))
        assert cfg.mask_clouds is False


def test_quarterly_cadence_is_supported_and_quiet_for_landsat(tmp_path, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        cfg = RunConfig.from_yaml(_write(tmp_path, _base(
            "index: lst\n").replace("cadence: monthly", "cadence: quarterly")))
    assert cfg.cadence == "quarterly"
    # quarterly bins are WIDER than monthly, so the sparse-bins warning that fires
    # for sub-monthly landsat runs must stay silent here
    assert "16-day repeat" not in caplog.text
    with caplog.at_level(logging.WARNING):
        RunConfig.from_yaml(_write(tmp_path, _base(
            "index: lst\n").replace("cadence: monthly", "cadence: 10day")))
    assert "16-day repeat" in caplog.text


def test_anomaly_rejects_quarterly_cadence(tmp_path):
    body = _base(
        "index: lst\nanomaly: climatology\nbaseline_years: [2015, 2024]\n"
    ).replace("cadence: monthly", "cadence: quarterly")
    with pytest.raises(ConfigError, match="monthly"):
        RunConfig.from_yaml(_write(tmp_path, body))
