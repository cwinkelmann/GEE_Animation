"""Random-forest thermal sharpening of Landsat LST with Sentinel-2 predictors.

`lst_rf`: per period, a regression forest learns LST(100 m) ~ {NDVI, NIRv, NDBI,
MNDWI, DEM} with the predictors aggregated to the thermal grid, is applied on the
fine Sentinel-2 grid, and the COARSE RESIDUAL (observed − predicted at 100 m) is
added back so the sharpened field still aggregates to the observed temperature —
the same discipline as `products._lst_sharp` (TsHARP), whose single-predictor
linear fit is kept here as `linear_sharpen`, the baseline the forest must beat.

Honest resolution is 20 m, not 10 m: NDBI and MNDWI need Sentinel-2's SWIR1 (B11),
a 20 m band. Everything runs server-side; `ee_module` is injected for tests.

Validation (pre-registered, scripts/lst_rf_validate.py): degrade real 100 m LST to
300 m, recover 100 m with nearest / linear / forest, score against the real field.
"""
from __future__ import annotations

import math

import ee

from .products import INDEX_BAND, _s2_attach_cloud_prob, _s2_mask_clouds

S2_ID = "COPERNICUS/S2_SR_HARMONIZED"
DEM_ID = "COPERNICUS/DEM/GLO30_2024_1"   # GLO30 without the suffix is deprecated
PREDICTORS = ("ndvi", "nirv", "ndbi", "mndwi", "dem")
#: Sentinel-2 scene-level cloud cap for the predictor composite.
S2_MAX_CLOUD = 70
#: Predictor window = [first scene − pad, last scene + pad]: a winter month may hold
#: one clear Sentinel-2 pass or none, and a masked predictor is a hole in the output.
S2_PAD_DAYS = 30
#: Forest trained on the frame buffered by this: a zoomed frame alone (4.7 km) holds
#: too little urban/water/forest variety to learn from.
TRAIN_BUFFER_M = 3000
N_TREES = 100
N_SAMPLES = 5000
COARSE_M = 100      # TIRS native
FINE_M = 20         # Sentinel-2 SWIR grid
LST_NATIVE_M = 30   # the C2 L2 ST band is delivered on the 30 m grid
S2_NATIVE_M = 10


def _utm_epsg(lon: float, lat: float) -> str:
    """UTM zone EPSG for a lon/lat (same rule as render._utm_epsg; copied to avoid
    a products -> sharpen -> render import cycle)."""
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def s2_predictors(start, end, geom, ee_module=ee):
    """Sentinel-2 SR median over [start, end) → ndvi, nirv, ndbi, mndwi (+ dem)."""
    coll = (ee_module.ImageCollection(S2_ID)
            .filterDate(start, end).filterBounds(geom)
            .filter(ee_module.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", S2_MAX_CLOUD)))
    # the sensor's mask reads SCL AND the s2cloudless `cloud_prob` band, which
    # collection.build normally joins via Sensor.attach_aux — do the same here
    coll = _s2_attach_cloud_prob(coll, start, end, geom, ee_module=ee_module)
    coll = coll.map(lambda img: _s2_mask_clouds(img, ee_module))
    med = coll.select(["B3", "B4", "B8", "B11"]).median().multiply(0.0001)
    ndvi = med.normalizedDifference(["B8", "B4"]).rename("ndvi")
    nirv = ndvi.multiply(med.select("B8")).rename("nirv")
    ndbi = med.normalizedDifference(["B11", "B8"]).rename("ndbi")
    mndwi = med.normalizedDifference(["B3", "B11"]).rename("mndwi")
    dem = ee_module.ImageCollection(DEM_ID).select("DEM").mosaic().rename("dem")
    return ndvi.addBands(nirv).addBands(ndbi).addBands(mndwi).addBands(dem).toFloat()


def _on_grid(image, proj, native_m: float, target_m: float, ee_module=ee):
    """`image` (a composite with no useful default projection) averaged onto
    `proj` at `target_m` from its `native_m` grid."""
    return (image.setDefaultProjection(proj.atScale(native_m))
            .reduceResolution(ee_module.Reducer.mean(), maxPixels=4096)
            .reproject(proj.atScale(target_m)))


def rf_sharpen(lst_coarse, predictors, *, proj, coarse_m, fine_m, region,
               lst_native_m=LST_NATIVE_M, predictors_native_m=S2_NATIVE_M,
               n_trees=N_TREES, n_samples=N_SAMPLES, seed=1, ee_module=ee):
    """Sharpen `lst_coarse` from `coarse_m` to `fine_m` with a regression forest."""
    lst_c = _on_grid(lst_coarse, proj, lst_native_m, coarse_m, ee_module).rename("lst")
    pred_c = _on_grid(predictors, proj, predictors_native_m, coarse_m, ee_module)
    pred_f = _on_grid(predictors, proj, predictors_native_m, fine_m, ee_module)
    train = pred_c.addBands(lst_c).sample(
        region=region, scale=coarse_m, numPixels=n_samples, seed=seed, tileScale=4)
    model = (ee_module.Classifier.smileRandomForest(
                 numberOfTrees=n_trees, minLeafPopulation=5, bagFraction=0.7, seed=seed)
             .setOutputMode("REGRESSION")
             .train(train, "lst", list(PREDICTORS)))
    fit_f = pred_f.classify(model).rename("lst")
    # Residual against the FINE prediction aggregated to the coarse grid — not a
    # second prediction at coarse predictors. A forest is nonlinear, so
    # mean(f(x_fine)) != f(mean(x)); only this form makes the sharpened field
    # aggregate back to the observed coarse LST exactly (the TsHARP guarantee).
    fit_f_c = (fit_f.reduceResolution(ee_module.Reducer.mean(), maxPixels=4096)
               .reproject(proj.atScale(coarse_m)))
    residual_c = lst_c.subtract(fit_f_c)        # observed − modelled, on the coarse grid
    sharp = fit_f.add(residual_c)               # fine fit + coarse residual (nearest)
    return sharp.rename(INDEX_BAND).toFloat()


def linear_sharpen(lst_coarse, predictors, *, proj, coarse_m, fine_m, region,
                   predictor: str = "nirv", lst_native_m=LST_NATIVE_M,
                   predictors_native_m=S2_NATIVE_M, ee_module=ee):
    """TsHARP baseline on the same inputs: LST = a·x + b fitted at `coarse_m`,
    applied at `fine_m`, coarse residual added back (mirrors products._lst_sharp)."""
    lst_c = _on_grid(lst_coarse, proj, lst_native_m, coarse_m, ee_module).rename("y")
    x_c = _on_grid(predictors.select(predictor), proj, predictors_native_m, coarse_m,
                   ee_module).rename("x")
    x_f = _on_grid(predictors.select(predictor), proj, predictors_native_m, fine_m,
                   ee_module)
    fit = x_c.addBands(lst_c).reduceRegion(
        ee_module.Reducer.linearFit(), geometry=region, scale=coarse_m,
        bestEffort=True, maxPixels=int(1e9))
    a = ee_module.Number(fit.get("scale"))
    b = ee_module.Number(fit.get("offset"))
    residual_c = lst_c.subtract(x_c.multiply(a).add(b))
    sharp = x_f.multiply(a).add(b).add(residual_c)
    return sharp.rename(INDEX_BAND).toFloat()


def lst_rf_period(coll, cfg, ee_module=ee):
    """`Index.reduce_period_cfg` for lst_rf: one period's Landsat LST collection
    (INDEX band, °C) → the sharpened composite.

    The Sentinel-2 window is derived server-side from the scenes actually in
    `coll` (padded), so gap-filled donor periods get predictors from THEIR dates.
    The forest trains on the frame buffered by TRAIN_BUFFER_M.
    """
    from .aoi import parse as parse_aoi
    lst = coll.select(INDEX_BAND).median()
    proj_base = coll.first().select(INDEX_BAND).projection()
    millis = coll.aggregate_array("system:time_start")
    start = ee_module.Date(millis.reduce(ee_module.Reducer.min())).advance(-S2_PAD_DAYS, "day")
    end = ee_module.Date(millis.reduce(ee_module.Reducer.max())).advance(S2_PAD_DAYS, "day")
    frame = parse_aoi(cfg.frame_aoi, ee_module)
    region = frame.buffer(TRAIN_BUFFER_M)
    predictors = s2_predictors(start, end, region, ee_module)
    return rf_sharpen(lst, predictors, proj=proj_base, coarse_m=COARSE_M, fine_m=FINE_M,
                      region=region, ee_module=ee_module)
