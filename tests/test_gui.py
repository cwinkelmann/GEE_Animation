import json
import types
import zipfile
from pathlib import Path

import pytest

from gee_animation import gui


def _write_geojson(tmp_path):
    p = tmp_path / "region.geojson"
    p.write_text(json.dumps({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}))
    return p


def _fake_deps(tmp_path, captured, frames=None):
    if frames is None:
        frames = [types.SimpleNamespace(label="2022-05"), types.SimpleNamespace(label="2022-06")]

    def frame_bbox(region, m):
        captured["buffer"] = m
        return [0.0, 0.0, 2.0, 2.0]

    def render(frames_, cfg, geometry=None):
        # one real PNG per frame (so run_animation can zip them) + the mp4/gif
        pngs = []
        for f in frames_:
            p = tmp_path / f"o_{f.label}.png"
            p.write_bytes(b"x")
            pngs.append(p)
        return [tmp_path / "o.mp4", tmp_path / "o.gif", *pngs]

    def inventory(cfg, f, r):
        captured.update(inventory_cfg=cfg)
        p = tmp_path / f"{cfg.name}_inventory.csv"
        p.write_text("scene_id,used\n")
        return p

    return types.SimpleNamespace(
        init=lambda project: captured.__setitem__("project", project),
        parse=lambda a: ("geom", tuple(sorted(a))),
        frame_bbox=frame_bbox,
        build=lambda cfg, f, r: (captured.update(cfg=cfg, frame=f, region=r) or "COLL"),
        monthly_median=lambda coll, cfg: frames,
        anomaly=lambda frames_, cfg, f, r, build: frames_,
        render=render,
        timeseries=lambda frames_, region, frame, scale: [(f.label, 0.8, 0.5) for f in frames_],
        inventory=inventory,
    )


def test_default_aoi_is_the_shipped_wne_geojson():
    from pathlib import Path
    assert gui.DEFAULT_AOI is not None
    assert gui.DEFAULT_AOI.replace("\\", "/").endswith("docs/aoi/wne/wne.geojson")
    assert Path(gui.DEFAULT_AOI).exists()


def test_indices_for_filters_by_sensor():
    assert "lst" in gui.indices_for("landsat") and "lst_sharp" in gui.indices_for("landsat")
    assert "lst" not in gui.indices_for("sentinel2")
    assert set(gui.indices_for("modis")) == {"ndvi", "evi", "ndwi", "ndmi", "rgb", "cir"}


def test_region_aoi_from_upload_geojson(tmp_path):
    p = _write_geojson(tmp_path)
    assert gui._region_aoi_from_upload(str(p)) == {"geojson": str(p)}


def test_region_aoi_from_upload_zipped_shapefile(tmp_path):
    (tmp_path / "x.shp").write_text("")
    (tmp_path / "x.dbf").write_text("")
    z = tmp_path / "aoi.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(tmp_path / "x.shp", "x.shp")
        zf.write(tmp_path / "x.dbf", "x.dbf")
    out = gui._region_aoi_from_upload(str(z))
    assert "shapefile" in out and out["shapefile"].endswith("x.shp")


def test_region_aoi_from_upload_rejects_unknown(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("nope")
    with pytest.raises(ValueError, match="unsupported"):
        gui._region_aoi_from_upload(str(p))


def test_run_animation_builds_config_and_threads_geometry(tmp_path):
    aoi = _write_geojson(tmp_path)
    captured = {}
    mp4, gif, frame_pngs, zip_path, status, series = gui.run_animation(
        aoi_path=str(aoi), buffer_m=1500, sensor="sentinel2", index="ndvi",
        start="2022-05-01", end="2022-07-01", region_max_cloud_percent=15,
        fps=5, dimensions=512, out_dir=str(tmp_path), deps=_fake_deps(tmp_path, captured))
    cfg = captured["cfg"]
    assert cfg.sensor == "sentinel2" and cfg.index == "ndvi"
    assert cfg.frame_aoi == {"bbox": [0.0, 0.0, 2.0, 2.0]}     # from frame_bbox(region, buffer)
    assert cfg.region_aoi == {"geojson": str(aoi)}
    assert captured["buffer"] == 1500.0
    assert cfg.viz_min == -1.0 and cfg.viz_max == 1.0          # NDVI default viz (definitional -1..1)
    assert cfg.scale == 30 and cfg.region_max_cloud_percent == 15
    assert mp4.endswith("o.mp4") and gif.endswith("o.gif")
    # per-frame PNGs are offered for download, bundled into a single zip
    assert [Path(p).name for p in frame_pngs] == ["o_2022-05.png", "o_2022-06.png"]
    assert zip_path.endswith("sentinel2_ndvi_frames.zip")   # {cfg.name}_frames.zip
    with zipfile.ZipFile(zip_path) as zf:
        assert sorted(zf.namelist()) == ["o_2022-05.png", "o_2022-06.png"]
    assert "Rendered 2 of 2 monthly periods" in status        # May + June both rendered
    assert series == [("2022-05", 0.8, 0.5), ("2022-06", 0.8, 0.5)]   # (month, inside, outside)
    # inside/outside summary appended to the status
    assert "inside AOI 0.800" in status and "outside 0.500" in status and "+0.300" in status


def test_run_animation_status_reports_dropped_months(tmp_path):
    # 4-month range but only 2 frames -> status flags the 2 dropped (cloud-filtered) months
    aoi = _write_geojson(tmp_path)
    frames = [types.SimpleNamespace(label="2022-05"), types.SimpleNamespace(label="2022-08")]
    *_, status, _ = gui.run_animation(
        aoi_path=str(aoi), buffer_m=1000, sensor="sentinel2", index="ndvi",
        start="2022-05-01", end="2022-09-01", region_max_cloud_percent=10,
        out_dir=str(tmp_path), deps=_fake_deps(tmp_path, {}, frames=frames))
    assert "Rendered 2 of 4 monthly periods" in status
    assert "2 period(s) had no scene" in status and "10%" in status


def test_run_animation_uses_500m_scale_for_modis(tmp_path):
    aoi = _write_geojson(tmp_path)
    captured = {}
    gui.run_animation(aoi_path=str(aoi), buffer_m=1000, sensor="modis", index="ndmi",
                      start="2022-05-01", end="2022-07-01", out_dir=str(tmp_path),
                      deps=_fake_deps(tmp_path, captured))
    assert captured["cfg"].scale == 500


def test_run_animation_raises_on_no_frames(tmp_path):
    aoi = _write_geojson(tmp_path)
    deps = _fake_deps(tmp_path, {}, frames=[])
    with pytest.raises(RuntimeError, match="No imagery"):
        gui.run_animation(aoi_path=str(aoi), buffer_m=1000, sensor="sentinel2", index="ndvi",
                          start="2022-05-01", end="2022-07-01", out_dir=str(tmp_path), deps=deps)


def test_run_animation_rejects_unsupported_pair(tmp_path):
    aoi = _write_geojson(tmp_path)
    with pytest.raises(ValueError, match="not available"):
        gui.run_animation(aoi_path=str(aoi), buffer_m=1000, sensor="sentinel2", index="lst",
                          start="2022-05-01", end="2022-07-01", out_dir=str(tmp_path),
                          deps=_fake_deps(tmp_path, {}))


def test_run_animation_requires_aoi(tmp_path):
    with pytest.raises(ValueError, match="upload an AOI"):
        gui.run_animation(aoi_path=None, buffer_m=1000, sensor="sentinel2", index="ndvi",
                          start="2022-05-01", end="2022-07-01", deps=_fake_deps(tmp_path, {}))


# --- CLI-parity controls: cadence, preset/aspect, pooling, inventory, validation ---

def _run(tmp_path, captured, **kw):
    """run_animation over a throwaway AOI + fake deps, with sensible GUI defaults."""
    aoi = _write_geojson(tmp_path)
    kw.setdefault("sensor", "sentinel2")
    kw.setdefault("index", "ndvi")
    kw.setdefault("start", "2022-05-01")
    kw.setdefault("end", "2022-07-01")
    return gui.run_animation(aoi_path=str(aoi), buffer_m=1000, out_dir=str(tmp_path),
                             deps=_fake_deps(tmp_path, captured), **kw)


def test_cadence_reaches_the_config(tmp_path):
    captured = {}
    _run(tmp_path, captured, cadence="10day")
    assert captured["cfg"].cadence == "10day"      # not the old hardcoded "monthly"


def test_cadence_defaults_to_monthly(tmp_path):
    captured = {}
    _run(tmp_path, captured)
    assert captured["cfg"].cadence == "monthly"


def test_preset_and_aspect_default_to_1080p_match(tmp_path):
    # The GUI used to omit preset entirely, so coarse products (LST 100 m, MODIS 500 m)
    # were rendered at their tiny native pixel count. Match api.animate's defaults.
    captured = {}
    _run(tmp_path, captured)
    assert captured["cfg"].preset == "1080p" and captured["cfg"].aspect == "match"


def test_preset_and_aspect_are_passed_through(tmp_path):
    captured = {}
    _run(tmp_path, captured, preset="4k", aspect="16:9")
    assert captured["cfg"].preset == "4k" and captured["cfg"].aspect == "16:9"


def test_native_preset_maps_to_none(tmp_path):
    captured = {}
    _run(tmp_path, captured, preset=gui.NATIVE_PRESET)
    assert captured["cfg"].preset is None


def test_pooling_params_reach_the_config(tmp_path):
    captured = {}
    _run(tmp_path, captured, pool_start_year=2019, pool_end_year=2024,
         pool_strategy="median")
    cfg = captured["cfg"]
    assert cfg.pool_years == [2019, 2024] and cfg.pool_strategy == "median"


def test_pooling_off_by_default(tmp_path):
    captured = {}
    _run(tmp_path, captured)
    assert captured["cfg"].pool_years is None


def test_pooling_needs_both_years(tmp_path):
    with pytest.raises(ValueError, match="both a first and a last year"):
        _run(tmp_path, {}, pool_start_year=2019)


# --- browser-submitted values, not Python defaults ---------------------------------
#
# gr.Number(value=None) is rendered by the browser as 0 (verified against the live
# DOM: {value: "0", valueAsNumber: 0}), so an *untouched* pooling-years form submits
# pool_start_year=0, pool_end_year=0 — not None. These tests exercise exactly that,
# rather than the Python-only None default every other pooling test uses.

def test_pooling_both_zero_is_off_like_an_untouched_browser_form(tmp_path):
    # What an untouched form actually POSTs (see module docstring above). Before the
    # fix this silently switched pooling on with pool_years=[0, 0], sending the
    # search to year 0000 and producing the misleading "No imagery found" error.
    captured = {}
    _run(tmp_path, captured, pool_start_year=0, pool_end_year=0)
    assert captured["cfg"].pool_years is None


def test_pooling_zero_in_one_box_with_a_real_year_still_refused(tmp_path):
    # A 0 (untouched) alongside a real year in the other box must not silently
    # become a half-open range — it's the same user mistake as leaving one truly
    # blank, so it must raise the same clear error.
    with pytest.raises(ValueError, match="both a first and a last year"):
        _run(tmp_path, {}, pool_start_year=0, pool_end_year=2024)
    with pytest.raises(ValueError, match="both a first and a last year"):
        _run(tmp_path, {}, pool_start_year=2019, pool_end_year=0)


def test_pooling_real_years_still_work(tmp_path):
    # Guard against over-correcting: a genuine pair must still reach the config.
    captured = {}
    _run(tmp_path, captured, pool_start_year=2019, pool_end_year=2024)
    assert captured["cfg"].pool_years == [2019, 2024]


def test_pooling_status_carries_the_provenance_warning(tmp_path):
    *_, status, _ = _run(tmp_path, {}, pool_start_year=2019, pool_end_year=2024)
    assert "COSMETIC ONLY" in status and "quantitative analysis" in status


def test_inventory_writes_a_csv(tmp_path):
    captured = {}
    aoi = _write_geojson(tmp_path)
    csv_path, status = gui.run_inventory(
        aoi_path=str(aoi), buffer_m=1000, sensor="sentinel2", index="ndvi",
        start="2022-05-01", end="2022-07-01", out_dir=str(tmp_path),
        deps=_fake_deps(tmp_path, captured))
    assert csv_path.endswith("sentinel2_ndvi_inventory.csv")
    assert Path(csv_path).exists() and "inventory" in status.lower()
    assert captured["inventory_cfg"].cadence == "monthly"
    assert "cfg" not in captured                       # inventory short-circuits build


def test_inventory_refuses_pooling_and_runs_nothing(tmp_path):
    captured = {}
    aoi = _write_geojson(tmp_path)
    with pytest.raises(ValueError, match="does not support cross-year pooling"):
        gui.run_inventory(
            aoi_path=str(aoi), buffer_m=1000, sensor="sentinel2", index="ndvi",
            start="2022-05-01", end="2022-07-01", out_dir=str(tmp_path),
            pool_start_year=2019, pool_end_year=2024,
            deps=_fake_deps(tmp_path, captured))
    assert "cfg" not in captured and "inventory_cfg" not in captured


def test_invalid_pool_strategy_is_a_friendly_error_not_a_crash(tmp_path):
    # proves cfg.validate() runs: the GUI builds RunConfig directly, bypassing from_yaml
    with pytest.raises(ValueError, match="Invalid settings: unknown pool_strategy"):
        _run(tmp_path, {}, pool_start_year=2019, pool_end_year=2024,
             pool_strategy="bogus")


def test_validation_rejects_reversed_pool_years(tmp_path):
    with pytest.raises(ValueError, match="Invalid settings"):
        _run(tmp_path, {}, pool_start_year=2024, pool_end_year=2019)


def test_landsat_submonthly_warning_reaches_the_status(tmp_path):
    # config.validate() only log.warning()s this; the GUI must show it to the user
    *_, status, _ = _run(tmp_path, {}, sensor="landsat", index="lst", cadence="10day")
    assert "16-day repeat" in status


def test_status_is_cadence_neutral(tmp_path):
    frames = [types.SimpleNamespace(label="2022-05-01")]
    aoi = _write_geojson(tmp_path)
    *_, status, _ = gui.run_animation(
        aoi_path=str(aoi), buffer_m=1000, sensor="sentinel2", index="ndvi",
        start="2022-05-01", end="2022-06-01", cadence="10day", out_dir=str(tmp_path),
        deps=_fake_deps(tmp_path, {}, frames=frames))
    assert "Rendered 1 of 3 10day periods" in status
    assert "2 period(s) had no scene" in status
    assert "month" not in status                        # no monthly-specific wording


def test_build_app_constructs():
    pytest.importorskip("gradio")
    app = gui.build_app()
    assert app is not None


def test_page_css_restores_document_scrolling():
    """Gradio 6 lays <html>/<body> out as a flex column and clips .gradio-container
    with `overflow-y: hidden`, which leaves this tall form unscrollable in browsers
    that won't scroll a flex <html>. The overrides must reach launch() — Gradio 6
    moved `css` off the Blocks constructor, so passing it there only warns."""
    for fragment in ("display: block", "overflow-y: auto", "height: auto"):
        assert fragment in gui.PAGE_CSS, f"missing scroll fix: {fragment}"
    assert ".gradio-container" in gui.PAGE_CSS
    assert "overflow-y: visible" in gui.PAGE_CSS


def test_main_passes_page_css_to_launch(monkeypatch):
    """The stylesheet is useless if it never reaches launch()."""
    pytest.importorskip("gradio")
    seen = {}

    class FakeApp:
        def launch(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(gui, "build_app", lambda: FakeApp())
    gui.main()
    assert seen.get("css") == gui.PAGE_CSS


def _make_run(d, name, months, mp4=True, gif=True):
    """Create a rendered-run layout: <name>.mp4/.gif + <name>_<month>.png frames."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    if mp4:
        (d / f"{name}.mp4").write_bytes(b"v")
    if gif:
        (d / f"{name}.gif").write_bytes(b"g")
    for m in months:
        (d / f"{name}_{m}.png").write_bytes(b"x")


def test_list_previous_runs_finds_flat_and_nested(tmp_path):
    _make_run(tmp_path, "wne_lst", ["2022-01", "2022-02"])          # flat: out/wne_lst.mp4
    _make_run(tmp_path / "run10yr", "big", ["2015-01"])             # nested subdir
    runs = gui.list_previous_runs(str(tmp_path))
    labels = [l for l, _ in runs]
    values = [v for _, v in runs]
    assert any(l.startswith("wne_lst ") and "2 frames" in l for l in labels)
    assert any(l.startswith("run10yr/big") and "(1 frame)" in l for l in labels)
    assert any(v.endswith("wne_lst.mp4") for v in values)          # value = media path
    assert any(v.endswith("run10yr/big.mp4") for v in values)


def test_list_previous_runs_does_not_grab_sibling_prefix(tmp_path):
    _make_run(tmp_path, "wne_lst", ["2022-01"])                    # 1 frame
    _make_run(tmp_path, "wne_lst_smw", ["2022-01", "2022-02"])     # shares the folder
    labels = [l for l, _ in gui.list_previous_runs(str(tmp_path))]
    assert any(l.startswith("wne_lst ") and "(1 frame)" in l for l in labels)
    assert any(l.startswith("wne_lst_smw ") and "(2 frames)" in l for l in labels)


def test_list_previous_runs_missing_base_returns_empty(tmp_path):
    assert gui.list_previous_runs(str(tmp_path / "nope")) == []


def test_load_previous_run_returns_media_frames_and_zip(tmp_path):
    d = tmp_path / "run"
    _make_run(d, "a", ["2022-01", "2022-02"])
    mp4, gif, frames, zip_path, status = gui.load_previous_run(str(d / "a.mp4"))
    assert mp4.endswith("a.mp4") and gif.endswith("a.gif")
    assert len(frames) == 2 and all(f.endswith(".png") for f in frames)
    assert zip_path and Path(zip_path).exists()
    with zipfile.ZipFile(zip_path) as zf:
        assert len(zf.namelist()) == 2
    assert "Loaded **a**" in status and "2 frame" in status and "2022-01 → 2022-02" in status


def test_load_previous_run_enriches_status_from_metadata(tmp_path):
    from gee_animation import metadata
    d = tmp_path / "run"
    _make_run(d, "a", ["2022-01", "2022-02"])
    metadata.write_db(d / "metadata.db", {"name": "a", "sensor": "landsat", "index": "lst"},
                      [("2022-01", 3, 0.1, 20.0), ("2022-02", 4, 0.0, 24.0)])
    *_, status = gui.load_previous_run(str(d / "a.mp4"))
    assert "2 months 2022-01→2022-02 in metadata" in status
    assert "mean value 22.0" in status and "range 20.0…24.0" in status   # over aoi_mean


def test_load_previous_run_without_metadata_and_gif_only(tmp_path):
    d = tmp_path / "run"
    _make_run(d, "a", ["2022-07"], mp4=False)                      # gif only, no metadata.db
    val = [v for l, v in gui.list_previous_runs(str(tmp_path))][0]
    assert val.endswith("a.gif")
    mp4, gif, frames, _, status = gui.load_previous_run(val)
    assert mp4 is None and gif.endswith("a.gif")
    assert "metadata" not in status and "Loaded **a**" in status


def test_load_previous_run_requires_selection():
    with pytest.raises(ValueError, match="select a previous run"):
        gui.load_previous_run(None)
