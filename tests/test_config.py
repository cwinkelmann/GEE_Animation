import textwrap
import pytest
from gee_animation.config import RunConfig, ConfigError


def _write(tmp_path, body: str):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return p


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
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
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
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
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
    assert cfg.frame_aoi == {"bbox": [13.7, 52.8, 13.9, 52.95]}
    assert cfg.region_aoi == {"bbox": [13.7, 52.8, 13.9, 52.95]}
    assert cfg.ndvi_min == -0.2 and cfg.ndvi_max == 0.9
    assert cfg.fps == 4 and cfg.dimensions == 768
    assert cfg.out_dir == "out"


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
        aoi:
          frame: {bbox: [0, 0, 1, 1]}
          region: {bbox: [0, 0, 1, 1]}
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
        cadence: weekly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="cadence"):
        RunConfig.from_yaml(p)


def test_rejects_ndvi_max_not_greater_than_min(tmp_path):
    p = _write(tmp_path, """
        name: t
        project: p
        aoi:
          frame: {bbox: [0, 0, 1, 1]}
          region: {bbox: [0, 0, 1, 1]}
        start: "2022-01-01"
        end: "2023-01-01"
        sensor: sentinel2
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: 0.9, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="ndvi.max"):
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
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: []}
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
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000", "#ffffff"]}
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
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
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
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
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
        cadence: monthly
        max_cloud_percent: 60
        ndvi: {min: -0.2, max: 0.9, palette: ["#000000"]}
        render: {fps: 4, scale: 20, dimensions: 768}
    """)
    with pytest.raises(ConfigError, match="region_max_cloud_percent"):
        RunConfig.from_yaml(p)
