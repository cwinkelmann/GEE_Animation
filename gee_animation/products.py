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


# --- Landsat Collection 2 Level 2 ------------------------------------------
# QA_PIXEL bits: 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow, 5 snow.
_L_QA_BITS = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)
_L_ALIASES = (("SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"),
              ("blue", "green", "red", "nir", "swir1", "swir2"))


def _landsat_mask_clouds(image, ee_module=ee):
    clear = image.select("QA_PIXEL").bitwiseAnd(_L_QA_BITS).eq(0)
    return image.updateMask(clear)


def _landsat_cloud_band(image, ee_module=ee):
    return image.select("QA_PIXEL").bitwiseAnd(_L_QA_BITS).neq(0).rename("cloud")


def _landsat_reflectance(image, ee_module=ee):
    return image.select(list(_L_ALIASES[0]), list(_L_ALIASES[1])).multiply(0.0000275).add(-0.2)


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
    return (image.select("ST_B10")
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


# "ecostress": an approximation of high-resolution LST. Real ECOSTRESS data is
# NOT in the Earth Engine catalog, so this NDVI-guided thermal-sharpens Landsat's
# own ST_B10 LST: it injects the high-frequency NDVI detail (native 30 m minus a
# ~100 m focal mean — the thermal band's effective resolution) into the
# temperature field, cooler where local vegetation detail is higher. Empirical,
# "somewhat" sharpened — not a rigorous TsHARP regression.
_ECOSTRESS_NDVI_SLOPE = 16.0   # °C per unit of NDVI detail


def _ecostress(sensor, image, ee_module=ee):
    lst = (image.select("ST_B10")
           .multiply(0.00341802).add(149.0).subtract(273.15))
    ndvi = sensor.reflectance(image, ee_module).normalizedDifference(["nir", "red"])
    ndvi_detail = ndvi.subtract(
        ndvi.focal_mean(radius=100, kernelType="circle", units="meters"))
    sharp = lst.subtract(ndvi_detail.multiply(_ECOSTRESS_NDVI_SLOPE))
    return (sharp.rename(INDEX_BAND)
            .set("system:time_start", image.get("system:time_start")))


@dataclass(frozen=True)
class Sensor:
    name: str
    collection_ids: tuple
    scene_cloud_property: str
    mask_clouds: Callable
    cloud_band: Callable
    reflectance: Callable

    def collection(self, ee_module=ee):
        colls = [ee_module.ImageCollection(cid) for cid in self.collection_ids]
        merged = colls[0]
        for extra in colls[1:]:
            merged = merged.merge(extra)
        return merged


@dataclass(frozen=True)
class Index:
    name: str
    sensors: frozenset
    default_viz: tuple
    compute: Callable


SENSORS = {
    "sentinel2": Sensor("sentinel2", ("COPERNICUS/S2_SR_HARMONIZED",),
                        "CLOUDY_PIXEL_PERCENTAGE",
                        _s2_mask_clouds, _s2_cloud_band, _s2_reflectance),
    "landsat": Sensor("landsat",
                      ("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"),
                      "CLOUD_COVER",
                      _landsat_mask_clouds, _landsat_cloud_band, _landsat_reflectance),
    "modis": Sensor("modis", ("MODIS/061/MOD09A1",), None,
                    _modis_mask_clouds, _modis_cloud_band, _modis_reflectance),
}

# Reflectance-based indices work on any sensor that exposes the band aliases.
_REFL = frozenset({"sentinel2", "landsat", "modis"})

INDICES = {
    "ndvi": Index("ndvi", _REFL,
                  (-0.2, 0.9, ["#a1622f", "#e8d9a0", "#3b7a2a"]), _ndvi),
    "lst": Index("lst", frozenset({"landsat"}),
                 (0.0, 40.0, ["#000080", "#0000ff", "#00ffff", "#ffff00", "#ff0000", "#800000"]),
                 _lst),
    "evi": Index("evi", _REFL,
                 (0.0, 1.0, ["#a1622f", "#e8d9a0", "#3b7a2a"]), _evi),
    "ndwi": Index("ndwi", _REFL,
                  (-0.3, 0.6, ["#a1622f", "#f6e8c3", "#2166ac"]), _ndwi),
    "ndmi": Index("ndmi", _REFL,
                  (-0.5, 0.8, ["#8c510a", "#f6e8c3", "#01665e"]), _ndmi),
    "ecostress": Index("ecostress", frozenset({"landsat"}),
                       (0.0, 40.0, ["#000080", "#0000ff", "#00ffff", "#ffff00", "#ff0000", "#800000"]),
                       _ecostress),
}


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
