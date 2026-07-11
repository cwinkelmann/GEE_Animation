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


# "lst_sharp": an NDVI-sharpened Landsat LST (NOT the real ECOSTRESS mission — see
# NASA/ECOSTRESS/L2T_LSTE/V2, which is LA-only in EE and barely reaches this AOI's
# latitude). It injects high-frequency NDVI detail (native 30 m minus a ~100 m focal
# mean — the thermal band's effective resolution) into the temperature field, cooler
# where local vegetation detail is higher. Empirical, "somewhat" sharpened — not a
# rigorous TsHARP/DisTrad regression (which fits the slope per scene and adds the
# coarse residual back so the result aggregates to the observed LST).
_LST_SHARP_NDVI_SLOPE = 16.0   # °C per unit of NDVI detail


def _lst_sharp(sensor, image, ee_module=ee):
    lst = (image.select("thermal")
           .multiply(0.00341802).add(149.0).subtract(273.15))
    ndvi = sensor.reflectance(image, ee_module).normalizedDifference(["nir", "red"])
    ndvi_detail = ndvi.subtract(
        ndvi.focal_mean(radius=100, kernelType="circle", units="meters"))
    sharp = lst.subtract(ndvi_detail.multiply(_LST_SHARP_NDVI_SLOPE))
    return (sharp.rename(INDEX_BAND)
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
    # Overlay metadata (drawn on every frame): the bands used and the formula.
    bands: str = ""
    formula: str = None
    # composite=True => a 3-band (R,G,B) visualization, not a 1-band palette index.
    composite: bool = False


SENSORS = {
    "sentinel2": Sensor("sentinel2", _merged("COPERNICUS/S2_SR_HARMONIZED"),
                        "CLOUDY_PIXEL_PERCENTAGE",
                        _s2_mask_clouds, _s2_cloud_band, _s2_reflectance),
    "landsat": Sensor("landsat", _landsat_collection, "CLOUD_COVER",
                      _landsat_mask_clouds, _landsat_cloud_band, _landsat_reflectance),
    "modis": Sensor("modis", _merged("MODIS/061/MOD09A1"), None,
                    _modis_mask_clouds, _modis_cloud_band, _modis_reflectance),
}

# Reflectance-based indices work on any sensor that exposes the band aliases.
_REFL = frozenset({"sentinel2", "landsat", "modis"})

INDICES = {
    "ndvi": Index("ndvi", _REFL,
                  (-0.2, 0.9, ["#a1622f", "#e8d9a0", "#3b7a2a"]), _ndvi,
                  bands="NIR, Red", formula="(NIR - Red) / (NIR + Red)"),
    "lst": Index("lst", frozenset({"landsat"}),
                 (0.0, 40.0, ["#000080", "#0000ff", "#00ffff", "#ffff00", "#ff0000", "#800000"]),
                 _lst, bands="Thermal (ST_B6/ST_B10)",
                 formula="ST_B * 0.00341802 + 149.0 - 273.15 [C]"),
    "evi": Index("evi", _REFL,
                 (0.0, 1.0, ["#a1622f", "#e8d9a0", "#3b7a2a"]), _evi,
                 bands="NIR, Red, Blue",
                 formula="2.5*(NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1)"),
    "ndwi": Index("ndwi", _REFL,
                  (-0.3, 0.6, ["#a1622f", "#f6e8c3", "#2166ac"]), _ndwi,
                  bands="Green, NIR", formula="(Green - NIR) / (Green + NIR)"),
    "ndmi": Index("ndmi", _REFL,
                  (-0.5, 0.8, ["#8c510a", "#f6e8c3", "#01665e"]), _ndmi,
                  bands="NIR, SWIR1", formula="(NIR - SWIR1) / (NIR + SWIR1)"),
    "rgb": Index("rgb", _REFL, (0.0, 0.3, None), _rgb,
                 bands="Red, Green, Blue", composite=True),
    "cir": Index("cir", _REFL, (0.0, 0.3, None), _cir,
                 bands="R<-NIR, G<-Red, B<-Green", composite=True),
    "lst_sharp": Index("lst_sharp", frozenset({"landsat"}),
                       (0.0, 40.0, ["#000080", "#0000ff", "#00ffff", "#ffff00", "#ff0000", "#800000"]),
                       _lst_sharp, bands="Thermal, NIR, Red",
                       formula="LST - 16*(NDVI - NDVI_100m)"),
}


_LST_PALETTE = ["#000080", "#0000ff", "#00ffff", "#ffff00", "#ff0000", "#800000"]

# Ermida et al. (2020) Statistical Mono-Window LST (Landsat-only). Registered
# here (after the dataclasses/constants it needs) so smw_lst can import from us.
from . import smw_lst  # noqa: E402  (deferred to break the import cycle)

INDICES["lst_smw"] = Index("lst_smw", frozenset({"landsat"}),
                           (0.0, 40.0, _LST_PALETTE), smw_lst.compute,
                           build_collection=smw_lst.landsat_collection,
                           bands="TOA Tb, NIR, Red, Green, QA",
                           formula="A*Tb/e + B/e + C  (Ermida 2020 SMW)")


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
