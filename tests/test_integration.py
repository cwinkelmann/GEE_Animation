import os
from pathlib import Path
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.environ.get("GEE_INTEGRATION"),
    reason="set GEE_INTEGRATION=1 and authenticate to run live EE test",
)
def test_tiny_three_frame_render(tmp_path):
    from gee_animation.config import RunConfig
    from gee_animation import auth, aoi, collection, compositing, render

    cfg = RunConfig(
        name="itest", project=os.environ.get("GEE_PROJECT", "hnee-331218"),
        frame_aoi={"bbox": [13.80, 52.85, 13.83, 52.87]},
        region_aoi={"bbox": [13.805, 52.855, 13.825, 52.865]},
        region_max_cloud_percent=80,     # relaxed so the tiny test reliably finds scenes
        start="2022-06-01", end="2022-09-01",
        sensor="landsat", index="lst", cadence="monthly", max_cloud_percent=80,
        viz_min=0.0, viz_max=40.0, palette=["#000080", "#ff0000"],
        fps=2, scale=30, dimensions=256, out_dir=str(tmp_path),
    )
    auth.init(cfg.project)
    frame = aoi.parse(cfg.frame_aoi)
    region = aoi.parse(cfg.region_aoi)
    coll = collection.build(cfg, frame, region)
    frames = compositing.monthly_median(coll, cfg)
    assert frames, "expected at least one monthly LST frame"
    paths = render.render(frames, cfg, geometry=frame)
    assert any(Path(p).exists() for p in paths)


@pytest.mark.skipif(
    not os.environ.get("GEE_INTEGRATION"),
    reason="set GEE_INTEGRATION=1 and authenticate to run live EE test",
)
def test_build_preserves_per_scene_metadata_through_index_compute():
    # Regression for a live-EE bug: index.compute (products.py) derives a brand-new
    # image and only copies forward system:time_start, so every other per-scene
    # property set earlier in build() -- region_cloud_fraction, the sensor's scene
    # cloud property -- was silently dropped. aggregate_array on a dropped property
    # returns [] rather than erroring, which crashed inventory.scene_inventory with
    # an IndexError and made compositing.pooled_composite's least_cloudy mode raise
    # "pooled scene metadata is misaligned" on every live run. This asserts the
    # collection collection.build() returns still carries that metadata: every
    # aggregate_array the downstream consumers rely on must be the same length as
    # aggregate_array("system:time_start").
    import os as _os
    from gee_animation.config import RunConfig
    from gee_animation import auth, aoi, collection

    cfg = RunConfig(
        name="itest-props", project=_os.environ.get("GEE_PROJECT", "hnee-331218"),
        frame_aoi={"bbox": [13.80, 52.85, 13.83, 52.87]},
        region_aoi={"bbox": [13.805, 52.855, 13.825, 52.865]},
        region_max_cloud_percent=80,     # relaxed so the tiny test reliably finds scenes
        start="2022-06-01", end="2022-09-01",
        sensor="landsat", index="lst", cadence="monthly", max_cloud_percent=80,
        viz_min=0.0, viz_max=40.0, palette=["#000080", "#ff0000"],
        fps=2, scale=30, dimensions=256, out_dir="/tmp",
    )
    auth.init(cfg.project)
    frame = aoi.parse(cfg.frame_aoi)
    region = aoi.parse(cfg.region_aoi)
    coll = collection.build(cfg, frame, region)

    times = coll.aggregate_array("system:time_start").getInfo()
    region_clouds = coll.aggregate_array("region_cloud_fraction").getInfo()
    scene_clouds = coll.aggregate_array("CLOUD_COVER").getInfo()   # landsat's scene_cloud_property

    assert times, "expected at least one candidate scene in the tiny test window"
    assert len(region_clouds) == len(times), (
        "region_cloud_fraction dropped by index.compute's derived image")
    assert len(scene_clouds) == len(times), (
        "CLOUD_COVER dropped by index.compute's derived image")

    # Rendering must be unaffected: the built collection still has exactly the
    # one-band INDEX output, not e.g. leftover source bands from copyProperties.
    band_names = coll.first().bandNames().getInfo()
    assert band_names == ["INDEX"]
