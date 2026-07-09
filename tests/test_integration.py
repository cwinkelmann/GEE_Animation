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
