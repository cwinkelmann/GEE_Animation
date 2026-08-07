"""Sensor + index registry: resolve a (sensor, index) product for the pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import ee

INDEX_BAND = "INDEX"

# --- Sentinel-2 -------------------------------------------------------------
_S2_SCL_CLOUD = [3, 8, 9, 10, 11]
_S2_ALIASES = (("B2", "B3", "B4", "B8", "B11", "B12"),
               ("blue", "green", "red", "nir", "swir1", "swir2"))


def _s2_mask_clouds(image, ee_module=ee):
    scl = image.select("SCL")
    mask = ee_module.Image.constant(1)
    for cls in _S2_SCL_CLOUD:
        mask = mask.And(scl.neq(cls))
    return image.updateMask(mask)


def _s2_cloud_band(image, ee_module=ee):
    scl = image.select("SCL")
    return scl.remap(_S2_SCL_CLOUD, [1] * len(_S2_SCL_CLOUD), 0).rename("cloud")


def _s2_reflectance(image, ee_module=ee):
    return image.select(list(_S2_ALIASES[0]), list(_S2_ALIASES[1])).multiply(0.0001)


# --- Landsat Collection 2 Level 2 (harmonized L4-L9) ------------------------
# QA_PIXEL bits: 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow, 5 snow.
_L_QA_BITS = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)

# L4/5/7 (TM/ETM+) and L8/9 (OLI/TIRS) name bands differently; harmonize every
# mission to a single canonical band set at collection build. SR and ST scale
# factors are identical across all C2 L2 missions, and QA_PIXEL is standardized.
_L_CANON = ["blue", "green", "red", "nir", "swir1", "swir2", "thermal", "QA_PIXEL"]
_L_TM_SRC = ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B7", "ST_B6", "QA_PIXEL"]
_L_OLI_SRC = ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7", "ST_B10", "QA_PIXEL"]
# (mission id, Collection-2 L2 id) per mission; each image is tagged with a
# "mission" property so build() can select missions (e.g. L8/L9-only for thermal).
_L_TM_MISSIONS = (("L4", "LANDSAT/LT04/C02/T1_L2"), ("L5", "LANDSAT/LT05/C02/T1_L2"),
                  ("L7", "LANDSAT/LE07/C02/T1_L2"))
_L_OLI_MISSIONS = (("L8", "LANDSAT/LC08/C02/T1_L2"), ("L9", "LANDSAT/LC09/C02/T1_L2"))


def _landsat_collection(ee_module=ee):
    def _part(mission, cid, src):
        return (ee_module.ImageCollection(cid).select(src, _L_CANON)
                .map(lambda img, m=mission: img.set("mission", m)))
    parts = [_part(m, cid, _L_TM_SRC) for m, cid in _L_TM_MISSIONS]
    parts += [_part(m, cid, _L_OLI_SRC) for m, cid in _L_OLI_MISSIONS]
    merged = parts[0]
    for extra in parts[1:]:
        merged = merged.merge(extra)
    return merged


def _landsat_mask_clouds(image, ee_module=ee):
    clear = image.select("QA_PIXEL").bitwiseAnd(_L_QA_BITS).eq(0)
    return image.updateMask(clear)


def _landsat_cloud_band(image, ee_module=ee):
    return image.select("QA_PIXEL").bitwiseAnd(_L_QA_BITS).neq(0).rename("cloud")


def _landsat_reflectance(image, ee_module=ee):
    # bands already renamed to canonical aliases at collection build
    return image.select(["blue", "green", "red", "nir", "swir1", "swir2"]).multiply(0.0000275).add(-0.2)


# --- MODIS (MOD09A1 8-day surface reflectance, 500 m) -----------------------
_MODIS_ALIASES = (("sur_refl_b03", "sur_refl_b04", "sur_refl_b01",
                   "sur_refl_b02", "sur_refl_b06", "sur_refl_b07"),
                  ("blue", "green", "red", "nir", "swir1", "swir2"))


def _modis_clear(image, ee_module=ee):
    # StateQA (MOD09A1 state flags): bits 0-1 cloud state (0=clear),
    # bit 2 cloud shadow, bit 10 internal cloud flag.
    state = image.select("StateQA")
    return (state.bitwiseAnd(3).eq(0)
            .And(state.bitwiseAnd(1 << 2).eq(0))
            .And(state.bitwiseAnd(1 << 10).eq(0)))


def _modis_mask_clouds(image, ee_module=ee):
    return image.updateMask(_modis_clear(image, ee_module))


def _modis_cloud_band(image, ee_module=ee):
    return _modis_clear(image, ee_module).Not().rename("cloud")


def _modis_reflectance(image, ee_module=ee):
    return image.select(list(_MODIS_ALIASES[0]), list(_MODIS_ALIASES[1])).multiply(0.0001)


# --- MODIS LST (MOD11A1 Terra daily, 1 km) ----------------------------------
# A separate data product from MOD09 reflectance (different bands + QA), so it needs
# its own sensor entry. Daily revisit fills the cloud-locked months Landsat's 16-day
# repeat misses — at the cost of a coarse 1 km native resolution.
_MOD11_TERRA = "MODIS/061/MOD11A1"


def _modis_lst_mask_clouds(image, ee_module=ee):
    # QC_Day mandatory-QA bits 0-1: 0 good, 1 produced/other-quality, 2-3 not produced.
    # LST is only retrieved under clear sky, so the band is already masked over cloud;
    # this additionally drops the "not produced" pixels.
    good = image.select("QC_Day").bitwiseAnd(3).lte(1)
    return image.updateMask(good)


def _modis_lst_cloud_band(image, ee_module=ee):
    # No LST retrieval == cloud/no-data: 1 where LST_Day_1km is masked.
    return image.select("LST_Day_1km").mask().Not().rename("cloud")


def _no_reflectance(image, ee_module=ee):
    raise NotImplementedError("modis_lst is a thermal sensor; it has no reflectance bands")


def _lst_modis(sensor, image, ee_module=ee):
    # MOD11 LST_Day_1km: uint16, scale 0.02, in kelvin -> degrees Celsius.
    return (image.select("LST_Day_1km").multiply(0.02).subtract(273.15)
            .rename(INDEX_BAND)
            .set("system:time_start", image.get("system:time_start")))


# --- Index computations -----------------------------------------------------
# NOTE: deriving a new image (select/band-math/rename) drops the source metadata,
# so `system:time_start` must be copied forward or monthly compositing's
# filterDate grouping finds no images.
def _ndvi(sensor, image, ee_module=ee):
    refl = sensor.reflectance(image, ee_module)
    return (refl.normalizedDifference(["nir", "red"]).rename(INDEX_BAND)
            .set("system:time_start", image.get("system:time_start")))


def _lst(sensor, image, ee_module=ee):
    return (image.select("thermal")
            .multiply(0.00341802).add(149.0).subtract(273.15)
            .rename(INDEX_BAND)
            .set("system:time_start", image.get("system:time_start")))


def _evi(sensor, image, ee_module=ee):
    # EVI = G·(nir − red) / (nir + C1·red − C2·blue + L), standard MODIS coefficients
    # (G=2.5, C1=6, C2=7.5, L=1). Computed on scaled reflectance (the offset matters).
    refl = sensor.reflectance(image, ee_module)
    evi = refl.expression(
        "2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1)",
        {"nir": refl.select("nir"), "red": refl.select("red"), "blue": refl.select("blue")},
    ).rename(INDEX_BAND)
    return evi.set("system:time_start", image.get("system:time_start"))


def _ndwi(sensor, image, ee_module=ee):
    # NDWI (McFeeters) = (green − nir)/(green + nir) — open water.
    refl = sensor.reflectance(image, ee_module)
    return (refl.normalizedDifference(["green", "nir"]).rename(INDEX_BAND)
            .set("system:time_start", image.get("system:time_start")))


def _ndmi(sensor, image, ee_module=ee):
    # NDMI = (nir − swir1)/(nir + swir1) — canopy/soil moisture.
    refl = sensor.reflectance(image, ee_module)
    return (refl.normalizedDifference(["nir", "swir1"]).rename(INDEX_BAND)
            .set("system:time_start", image.get("system:time_start")))


def _rgb(sensor, image, ee_module=ee):
    # True-colour composite: R=Red, G=Green, B=Blue (natural colour).
    return (sensor.reflectance(image, ee_module)
            .select(["red", "green", "blue"], ["R", "G", "B"])
            .set("system:time_start", image.get("system:time_start")))


def _cir(sensor, image, ee_module=ee):
    # Colour-infrared (false colour): R←NIR, G←Red, B←Green. Healthy vegetation,
    # highly reflective in NIR, reads bright red.
    return (sensor.reflectance(image, ee_module)
            .select(["nir", "red", "green"], ["R", "G", "B"])
            .set("system:time_start", image.get("system:time_start")))


# "lst_sharp": TsHARP / DisTrad thermal sharpening (Kustas 2003, Agam 2007) — NOT the
# real ECOSTRESS mission. Fit LST = a·NIRv + b per scene at the coarse (100 m TIRS)
# grid, apply it at the fine (30 m) NIRv scale, then add the coarse residual back so
# the sharpened field aggregates to the observed coarse LST (conservative — a
# temperature, not a texture). The predictor is NIRv (NDVI·NIR), which keeps dynamic
# range where NDVI saturates in a closed canopy. The unit-tested math lives in
# imaging.distrad_sharpen; this mirrors it in Earth Engine.
_LST_SHARP_COARSE_M = 100   # TIRS native — the LST's true resolution


def _lst_sharp(sensor, image, ee_module=ee):
    lst = (image.select("thermal")
           .multiply(0.00341802).add(149.0).subtract(273.15))
    refl = sensor.reflectance(image, ee_module)
    nirv = refl.normalizedDifference(["nir", "red"]).multiply(refl.select("nir"))
    coarse = lst.projection().atScale(_LST_SHARP_COARSE_M)
    lst_c = lst.reduceResolution(ee_module.Reducer.mean(), maxPixels=512).reproject(coarse)
    pred_c = nirv.reduceResolution(ee_module.Reducer.mean(), maxPixels=512).reproject(coarse)
    fit = (pred_c.rename("x").addBands(lst_c.rename("y"))
           .reduceRegion(ee_module.Reducer.linearFit(), geometry=image.geometry(),
                         scale=_LST_SHARP_COARSE_M, bestEffort=True, maxPixels=int(1e9)))
    a = ee_module.Number(fit.get("scale"))
    b = ee_module.Number(fit.get("offset"))
    residual_c = lst_c.subtract(pred_c.multiply(a).add(b))     # coarse residual
    sharp = nirv.multiply(a).add(b).add(residual_c)            # fit@fine + residual (nearest)
    # toFloat() -> a homogeneous band type; reduceResolution otherwise gives each
    # image a data-dependent range and the monthly median rejects the collection.
    return (sharp.rename(INDEX_BAND).toFloat()
            .set("system:time_start", image.get("system:time_start")))


def _merged(*collection_ids):
    """A collection builder that loads and merges one or more collections as-is."""
    def build(ee_module=ee):
        merged = ee_module.ImageCollection(collection_ids[0])
        for cid in collection_ids[1:]:
            merged = merged.merge(ee_module.ImageCollection(cid))
        return merged
    return build


@dataclass(frozen=True)
class Sensor:
    name: str
    collection: Callable          # (ee_module=ee) -> ee.ImageCollection
    scene_cloud_property: str      # None if the sensor has no per-scene cloud metadata
    mask_clouds: Callable
    cloud_band: Callable
    reflectance: Callable


@dataclass(frozen=True)
class Index:
    name: str
    sensors: frozenset
    default_viz: tuple
    compute: Callable
    # Optional (cfg, frame_geom, ee_module) -> ImageCollection. When set, build()
    # uses it instead of the sensor's default collection — for indices that need a
    # bespoke, satellite-aware source (e.g. lst_smw joins the TOA thermal band).
    build_collection: Callable = None
    # Method-doc metadata: the bands used and the formula. Deliberately NOT drawn on
    # the frame any more — an analyst-persona review found the scaling coefficients
    # ("ST_B * 0.00341802 + 149.0 - 273.15") read as debug output and cost the frame
    # credibility while saying nothing about what the viewer is looking at. Nothing is
    # lost: both live in the method doc, where a reader who wants the definition can
    # find it. `display_name` is what the frame shows instead.
    bands: str = ""
    formula: str = None
    # Plain-language product name for the frame header (line 1, when no `cfg.title`
    # is set). "NDVI" names a variable; "Vegetation greenness (NDVI)" names a subject
    # and still carries the acronym for anyone who wants it.
    display_name: str = ""
    # composite=True => a 3-band (R,G,B) visualization, not a 1-band palette index.
    composite: bool = False
    # Physical unit of the index values (e.g. "°C" for thermal indices); "" if
    # unitless (normalized-difference indices, composites). Shown on the colorbar.
    units: str = ""
    # Word anchors for the colorbar ends -- e.g. "bare"/"dense vegetation" -- so a lay
    # viewer reads what the ramp *means*, not just its numbers. "" (the default) draws
    # no anchor; composites have no colorbar at all, so both stay "".
    low_label: str = ""
    high_label: str = ""


SENSORS = {
    "sentinel2": Sensor("sentinel2", _merged("COPERNICUS/S2_SR_HARMONIZED"),
                        "CLOUDY_PIXEL_PERCENTAGE",
                        _s2_mask_clouds, _s2_cloud_band, _s2_reflectance),
    "landsat": Sensor("landsat", _landsat_collection, "CLOUD_COVER",
                      _landsat_mask_clouds, _landsat_cloud_band, _landsat_reflectance),
    "modis": Sensor("modis", _merged("MODIS/061/MOD09A1"), None,
                    _modis_mask_clouds, _modis_cloud_band, _modis_reflectance),
    "modis_lst": Sensor("modis_lst", _merged(_MOD11_TERRA), None,
                        _modis_lst_mask_clouds, _modis_lst_cloud_band, _no_reflectance),
}

# Reflectance-based indices work on any sensor that exposes the band aliases.
_REFL = frozenset({"sentinel2", "landsat", "modis"})

# Thermal (LST) indices. The Landsat ones drive the L8/L9 mission default and the
# 100 m native scale; lst_modis is a MODIS product (its own sensor + 1 km native).
THERMAL_INDICES = frozenset({"lst", "lst_smw", "lst_sharp", "lst_modis"})

# Coarsest-relevant native ground sampling (metres) per sensor, with overrides.
_SENSOR_NATIVE_M = {"sentinel2": 10, "landsat": 30, "modis": 500, "modis_lst": 1000}
_S2_20M_INDICES = frozenset({"ndmi"})   # uses the 20 m SWIR band


def native_scale_m(sensor: str, index: str) -> int:
    """Native GSD (metres) a (sensor, index) can honestly resolve.

    Rendering finer than this is Earth Engine interpolating — e.g. Landsat thermal is
    100 m (TIRS; TM/ETM+ coarser), so a 2.75 m/px render is a ~36x upsample.
    """
    if sensor == "landsat" and index == "lst_sharp":
        return 30    # sharpened to the fine NIRv (reflectance) grid
    if sensor == "landsat" and index in THERMAL_INDICES:
        return 100
    if sensor == "sentinel2" and index in _S2_20M_INDICES:
        return 20
    return _SENSOR_NATIVE_M.get(sensor, 30)

INDICES = {
    # Normalized-difference indices span their definitional -1..1 range.
    # Palette: 7 evenly-spaced stops over -0.2..1.0 (imaging.colorize interpolates
    # linearly between stops placed at equal fractions of vmin..vmax), boundaries at
    # -0.2, 0, 0.2, 0.4, 0.6, 0.8, 1.0. The five stops from 0..1 are the unmodified,
    # official 5-class ColorBrewer BrBG (colour-vision-deficiency safe); the old
    # brown/green ramp had near-identical endpoints under deuteranopia (measured
    # separation 18/255 -- ~8% of men could not tell bare soil from dense canopy).
    # These endpoints separate at 90/255 under the same simulation.
    #
    # A naive version of this fix (blue stop at -0.25, BrBG's brown landing exactly
    # at 0) was verified numerically to fail: with only ONE stop spanning the whole
    # water range, water as shallow as NDVI=-0.1 already interpolated to brown
    # (colorize blends linearly between adjacent stops, and -0.1 sits close to the
    # brown endpoint of that wide segment) -- the exact "lake reads as scorched
    # earth" bug (H3) this palette exists to fix. A second, paler water stop keeps
    # the entire negative range in the blue family (R<B at every pixel <= NDVI 0)
    # while the land ramp still turns visibly brown by NDVI~0.032 (measured
    # crossover; see tests/test_imaging.py for the sweep that picked this value).
    #
    # "#e0f3f8" (RdYlBu's pale-blue neighbour of "#4575b4") was the first candidate
    # tried and rejected: at NDVI=0 it is only 18.1/255 from render.NODATA_RGB
    # (240,240,240) -- the SAME magnitude gap H2 condemned in the old land ramp,
    # meaning a viewer could not tell "observed shallow water" from "no observation"
    # over exactly the -0.03..+0.03 band this project paints no-data honestly for.
    # "#aeaec7" (a muted slate-blue, still officially in the blue family, not a
    # ColorBrewer stock colour) was picked by a small numeric sweep over candidate
    # hexes requiring: (1) R<B for every pixel at NDVI<=0; (2) >=60/255 from
    # NODATA_RGB at NDVI=0 (measured 101.9); (3) decisively brown by NDVI=0.10,
    # R-B>=40 (measured 53); (4) brown crossover <=0.08 (measured 0.0323). Endpoint
    # (first/last stop) deuteranopia separation is unaffected: still 89.5/255. This
    # is not a water mask: turbid/vegetated water with slightly positive NDVI still
    # renders brownish.
    "ndvi": Index("ndvi", _REFL,
                  (-0.2, 1.0, ["#4575b4", "#aeaec7", "#8c510a", "#d8b365",
                               "#f6e8c3", "#5ab4ac", "#01665e"]), _ndvi,
                  bands="NIR, Red", formula="(NIR - Red) / (NIR + Red)",
                  display_name="Vegetation greenness (NDVI)",
                  low_label="water", high_label="dense vegetation"),
    "lst": Index("lst", frozenset({"landsat"}),
                 (-10.0, 40.0, ["#000080", "#0000ff", "#00ffff", "#ffff00", "#ff0000", "#800000"]),
                 _lst, bands="Thermal (ST_B6/ST_B10)",
                 formula="ST_B * 0.00341802 + 149.0 - 273.15 [C]", units="°C",
                 display_name="Land surface temperature",
                 low_label="cooler", high_label="warmer"),
    "evi": Index("evi", _REFL,
                 (-1.0, 1.0, ["#a1622f", "#e8d9a0", "#3b7a2a"]), _evi,
                 bands="NIR, Red, Blue",
                 formula="2.5*(NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1)",
                 display_name="Vegetation greenness (EVI)",
                 low_label="bare", high_label="dense vegetation"),
    "ndwi": Index("ndwi", _REFL,
                  (-1.0, 1.0, ["#a1622f", "#f6e8c3", "#2166ac"]), _ndwi,
                  bands="Green, NIR", formula="(Green - NIR) / (Green + NIR)",
                  display_name="Surface water index (NDWI)",
                  low_label="dry", high_label="water"),
    "ndmi": Index("ndmi", _REFL,
                  (-1.0, 1.0, ["#8c510a", "#f6e8c3", "#01665e"]), _ndmi,
                  bands="NIR, SWIR1", formula="(NIR - SWIR1) / (NIR + SWIR1)",
                  display_name="Vegetation moisture (NDMI)",
                  low_label="dry", high_label="moist"),
    "rgb": Index("rgb", _REFL, (0.0, 0.3, None), _rgb,
                 bands="Red, Green, Blue", composite=True,
                 display_name="True colour"),
    "cir": Index("cir", _REFL, (0.0, 0.3, None), _cir,
                 bands="R<-NIR, G<-Red, B<-Green", composite=True,
                 display_name="Colour infrared"),
    "lst_sharp": Index("lst_sharp", frozenset({"landsat"}),
                       (-10.0, 40.0, ["#000080", "#0000ff", "#00ffff", "#ffff00", "#ff0000", "#800000"]),
                       _lst_sharp, bands="Thermal(100m) + NIRv(30m)",
                       formula="TsHARP: fit LST~NIRv @100m, apply @30m, +coarse residual",
                       units="°C",
                       display_name="Land surface temperature (sharpened)",
                       low_label="cooler", high_label="warmer"),
}


_LST_PALETTE = ["#000080", "#0000ff", "#00ffff", "#ffff00", "#ff0000", "#800000"]

# Ermida et al. (2020) Statistical Mono-Window LST (Landsat-only). Registered
# here (after the dataclasses/constants it needs) so smw_lst can import from us.
from . import smw_lst  # noqa: E402  (deferred to break the import cycle)

INDICES["lst_smw"] = Index("lst_smw", frozenset({"landsat"}),
                           (-10.0, 40.0, _LST_PALETTE), smw_lst.compute,
                           build_collection=smw_lst.landsat_collection,
                           bands="TOA Tb, NIR, Red, Green, QA",
                           formula="A*Tb/e + B/e + C  (Ermida 2020 SMW)", units="°C",
                           display_name="Land surface temperature (split-window)",
                           low_label="cooler", high_label="warmer")

# MODIS LST (MOD11A1 Terra daily, 1 km) — coarse but ~daily, so it fills the
# cloud-locked months Landsat's 16-day revisit misses.
INDICES["lst_modis"] = Index("lst_modis", frozenset({"modis_lst"}),
                             (-10.0, 40.0, _LST_PALETTE), _lst_modis,
                             bands="MOD11A1 LST_Day_1km (1 km, daily)",
                             formula="LST_Day_1km * 0.02 - 273.15 [C]", units="°C",
                             display_name="Land surface temperature (MODIS)",
                             low_label="cooler", high_label="warmer")


def get_product(sensor: str, index: str):
    if sensor not in SENSORS:
        raise ValueError(f"unsupported sensor {sensor!r}; supported: {sorted(SENSORS)}")
    if index not in INDICES:
        raise ValueError(f"unsupported index {index!r}; supported: {sorted(INDICES)}")
    idx = INDICES[index]
    if sensor not in idx.sensors:
        raise ValueError(
            f"index {index!r} not available for sensor {sensor!r}; "
            f"supported sensors: {sorted(idx.sensors)}"
        )
    return SENSORS[sensor], idx
