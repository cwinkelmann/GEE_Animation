import textwrap
import pytest
from gee_animation.config import RunConfig, ConfigError


def _write(tmp_path, body: str):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_from_yaml_loads_valid_config(tmp_path):
    p = _write(tmp_path, """
        name: test
        project: hnee-331218
        aoi:
          bbox: [13.7, 52.8, 13.9, 52.95]
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        cadence: monthly
        max_cloud_percent: 60
        ndvi:
          min: -0.2
          max: 0.9
          palette: ["#a1622f", "#3b7a2a"]
        render:
          fps: 4
          scale: 20
          dimensions: 768
    """)
    cfg = RunConfig.from_yaml(p)
    assert cfg.name == "test"
    assert cfg.project == "hnee-331218"
    assert cfg.aoi == {"bbox": [13.7, 52.8, 13.9, 52.95]}
    assert cfg.ndvi_min == -0.2 and cfg.ndvi_max == 0.9
    assert cfg.fps == 4 and cfg.dimensions == 768
    assert cfg.out_dir == "out"


def test_rejects_end_before_start(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {bbox: [0, 0, 1, 1]}
        start: "2023-01-01"
        end: "2022-01-01"
        sensor: sentinel2
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="end.*after.*start"):
        RunConfig.from_yaml(p)


def test_rejects_unsupported_sensor(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {bbox: [0, 0, 1, 1]}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: modis
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="sensor"):
        RunConfig.from_yaml(p)


def test_requires_aoi_bbox_or_geojson(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi: {}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="aoi"):
        RunConfig.from_yaml(p)
