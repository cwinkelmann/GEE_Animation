import textwrap
import pytest
from gee_animation.config import RunConfig, ConfigError


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
    assert cfg.viz_min == -0.2 and cfg.viz_max == 0.9
    assert cfg.palette == ["#a1622f", "#e8d9a0", "#3b7a2a"]
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
    assert cfg.viz_min == -0.2 and cfg.viz_max == 0.9
    assert cfg.palette == ["#a1622f", "#e8d9a0", "#3b7a2a"]


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
