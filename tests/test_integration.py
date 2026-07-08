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
        aoi={"bbox": [13.80, 52.85, 13.83, 52.87]},
        start="2022-06-01", end="2022-09-01",
        sensor="sentinel2", cadence="monthly", max_cloud_percent=80,
        ndvi_min=-0.2, ndvi_max=0.9, palette=["#a1622f", "#3b7a2a"],
        fps=2, scale=20, dimensions=256, out_dir=str(tmp_path),
    )
    auth.init(cfg.project)
    geom = aoi.parse(cfg.aoi)
    coll = collection.build(cfg, geom)
    frames = compositing.monthly_median(coll, cfg)
    assert frames, "expected at least one monthly frame"
    paths = render.render(frames, cfg, geometry=geom)
    assert any(Path(p).exists() for p in paths)
