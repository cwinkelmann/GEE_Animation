"""Land Surface Temperature via the Statistical Mono-Window (SMW) algorithm.

Faithful port of Ermida et al. (2020) — the open-source Google Earth Engine code
for LST from the Landsat series (``sofiaermida/Landsat_SMW_LST``):

    Ermida, S.L., Soares, P., Mantas, V., Göttsche, F.-M., Trigo, I.F., 2020.
    Google Earth Engine open-source code for Land Surface Temperature estimation
    from the Landsat series. Remote Sensing, 12 (9), 1471.
    https://doi.org/10.3390/rs12091471

Unlike the USGS Collection-2 Level-2 Surface Temperature product used by the
plain ``lst`` index (a pre-computed ``ST_B*`` band), SMW derives LST from:

  * at-sensor **brightness temperature** (TOA thermal band, Kelvin),
  * per-pixel **emissivity** (ASTER GED band 13/14 convolved to the Landsat TIR
    band, blended by fractional vegetation cover from NDVI), and
  * atmospheric **total precipitable water** (NCEP reanalysis), which selects the
    A/B/C regression coefficients: ``LST = A·Tb/ε + B/ε + C``.

The two products differ (typically ~1–3 K) — both are offered so results can be
compared. Output here is **°C** (Ermida yields Kelvin; we subtract 273.15 to
share the ``lst`` viz range and colorbar).
"""
from __future__ import annotations

import ee

from .products import INDEX_BAND, _L_CANON, _L_OLI_SRC, _L_TM_SRC

# --- Statistical Mono-Window coefficients (Ermida 2020, SMW_coefficients.js) --
# Per satellite, indexed by TPW bin position 0..9 (6 mm bins; bin 9 = TPW > 54).
_SMW_A = {
    "L4": [0.9755, 1.0155, 1.0672, 1.1499, 1.2277, 1.3649, 1.5085, 1.7045, 1.5886, 2.0215],
    "L5": [0.9765, 1.0229, 1.0817, 1.1738, 1.2605, 1.4166, 1.5727, 1.7879, 1.6347, 2.1168],
    "L7": [0.9764, 1.0201, 1.0750, 1.1612, 1.2425, 1.3864, 1.5336, 1.7345, 1.6066, 2.0533],
    "L8": [0.9751, 1.0090, 1.0541, 1.1282, 1.1987, 1.3205, 1.4540, 1.6350, 1.5468, 1.9403],
    "L9": [0.9751, 1.0093, 1.0539, 1.1267, 1.1961, 1.3155, 1.4463, 1.6229, 1.5396, 1.9223],
}
_SMW_B = {
    "L4": [-205.2767, -233.8902, -257.1884, -286.2166, -316.7643, -361.8276, -410.1157, -472.4909, -442.9489, -571.8563],
    "L5": [-204.6584, -235.5384, -261.3886, -293.6128, -327.1417, -377.7741, -430.0388, -498.1947, -457.8183, -600.7079],
    "L7": [-205.3511, -235.2416, -259.6560, -289.8190, -321.4658, -368.4078, -417.7796, -481.5714, -448.5071, -581.2619],
    "L8": [-205.8929, -232.2750, -253.1943, -279.4212, -307.4497, -348.0228, -393.1718, -451.0790, -429.5095, -547.2681],
    "L9": [-206.2187, -232.7408, -253.4430, -279.1685, -306.7961, -346.5312, -390.7794, -447.2745, -427.0904, -541.7084],
}
_SMW_C = {
    "L4": [212.0051, 230.4049, 239.3072, 244.8497, 253.0033, 258.5471, 265.1131, 270.7000, 277.1511, 279.9854],
    "L5": [211.1321, 230.0619, 239.5256, 245.6042, 254.2301, 259.9711, 266.9520, 272.8413, 279.6160, 282.4583],
    "L7": [211.8507, 230.5468, 239.6619, 245.3286, 253.6144, 259.1390, 265.7486, 271.3659, 277.9058, 280.6800],
    "L8": [212.7173, 230.5698, 238.9548, 244.0772, 251.8341, 257.2740, 263.5599, 268.9405, 275.0895, 277.9953],
    "L9": [213.0526, 230.9401, 239.2572, 244.2379, 251.8873, 257.2174, 263.3479, 268.5970, 274.6380, 277.4964],
}
_TPW_POS = list(range(10))

# ASTER GED band 13/14 -> Landsat TIR emissivity spectral convolution
# (Ermida compute_emissivity.js): c13, c14, offset. L8 and L9 share L8's values.
_EM_COEF = {
    "L4": (0.3222, 0.6498, 0.0272),
    "L5": (-0.0723, 1.0521, 0.0195),
    "L7": (0.2147, 0.7789, 0.0059),
    "L8": (0.6820, 0.2578, 0.0584),
    "L9": (0.6820, 0.2578, 0.0584),
}

# Per satellite: (L2 id, TOA id, L2 source bands -> _L_CANON, TOA brightness-temp band).
_SMW_SATS = (
    ("L4", "LANDSAT/LT04/C02/T1_L2", "LANDSAT/LT04/C02/T1_TOA", _L_TM_SRC, "B6"),
    ("L5", "LANDSAT/LT05/C02/T1_L2", "LANDSAT/LT05/C02/T1_TOA", _L_TM_SRC, "B6"),
    ("L7", "LANDSAT/LE07/C02/T1_L2", "LANDSAT/LE07/C02/T1_TOA", _L_TM_SRC, "B6_VCID_1"),
    ("L8", "LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC08/C02/T1_TOA", _L_OLI_SRC, "B10"),
    ("L9", "LANDSAT/LC09/C02/T1_L2", "LANDSAT/LC09/C02/T1_TOA", _L_OLI_SRC, "B10"),
)

_MS_PER_6H = 21600000   # NCEP reanalysis time step (6 hours) in milliseconds


def _join_bt(l2_coll, toa_coll, tir_band, ee_module):
    """Attach the TOA brightness-temperature band as ``bt`` to each L2 image.

    Matches L2 and TOA scenes on ``system:index`` (identical per Landsat scene).
    """
    filt = ee_module.Filter.equals(leftField="system:index", rightField="system:index")
    joined = ee_module.Join.saveFirst("toa").apply(
        l2_coll, toa_coll.select([tir_band], ["bt"]), filt)

    def _add(img):
        img = ee_module.Image(img)
        return img.addBands(ee_module.Image(img.get("toa")).select(["bt"]))

    return ee_module.ImageCollection(joined).map(_add)


def landsat_collection(cfg, frame_geom, ee_module=ee):
    """Satellite-aware Landsat collection for SMW: canonical bands + ``bt`` + SATID.

    Filtered per satellite (so the L2<->TOA join stays cheap), then merged. Each
    image keeps a client-side ``SATID`` property and the L2 ``CLOUD_COVER`` so the
    generic build() coarse-cloud/region-cloud/mask steps work unchanged.
    """
    parts = []
    for satid, l2_id, toa_id, src, tir in _SMW_SATS:
        l2 = (ee_module.ImageCollection(l2_id)
              .filterDate(cfg.start, cfg.end).filterBounds(frame_geom)
              .select(src, _L_CANON))
        toa = (ee_module.ImageCollection(toa_id)
               .filterDate(cfg.start, cfg.end).filterBounds(frame_geom))
        # SATID drives the per-image SMW coefficients; "mission" lets build() apply
        # the same L8/L9-only thermal filter it uses for the plain landsat collection.
        joined = _join_bt(l2, toa, tir, ee_module).map(
            lambda img: img.set({"SATID": satid, "mission": satid}))
        parts.append(joined)
    merged = parts[0]
    for extra in parts[1:]:
        merged = merged.merge(extra)
    return merged


def _fvc(ndvi, ee_module):
    """Fraction of vegetation cover from NDVI (Ermida compute_FVC.js)."""
    fvc = ndvi.expression("((ndvi - 0.2) / (0.86 - 0.2)) ** 2", {"ndvi": ndvi})
    return fvc.where(fvc.lt(0.0), 0.0).where(fvc.gt(1.0), 1.0)


def _emissivity(sensor, image, fvc, satid, ee_module):
    """Per-pixel TIR emissivity: ASTER GED band 13/14 blended by FVC.

    Port of Ermida compute_emissivity.js (dynamic / use_ndvi=True) with the
    ASTER bare-ground vegetation correction from ASTER_bare_emiss.js.
    """
    coef = ee_module.Dictionary({s: list(c) for s, c in _EM_COEF.items()}).get(satid)
    coef = ee_module.List(coef)
    c13 = ee_module.Image(ee_module.Number(coef.get(0)))
    c14 = ee_module.Image(ee_module.Number(coef.get(1)))
    c = ee_module.Image(ee_module.Number(coef.get(2)))

    aster = ee_module.Image("NASA/ASTER_GED/AG100_003")
    aster_fvc = _fvc(aster.select("ndvi").multiply(0.01), ee_module)

    def _bare(band):
        em = aster.select(band).multiply(0.001)
        return em.expression("(EM - 0.99 * fvc) / (1.0 - fvc)",
                             {"EM": em, "fvc": aster_fvc}).clip(image.geometry())

    emiss_bare = image.expression(
        "c13 * EM13 + c14 * EM14 + c",
        {"EM13": _bare("emissivity_band13"), "EM14": _bare("emissivity_band14"),
         "c13": c13, "c14": c14, "c": c})

    em = image.expression("fvc * 0.99 + (1 - fvc) * em_bare",
                          {"fvc": fvc, "em_bare": emiss_bare})
    qa = image.select("QA_PIXEL")
    em = em.where(qa.bitwiseAnd(1 << 7), 0.99)      # water
    em = em.where(qa.bitwiseAnd(1 << 5), 0.989)     # snow/ice
    return em


def _add_tpw(image, ee_module):
    """Interpolate NCEP total precipitable water to the image time; bin it.

    Port of Ermida NCEP_TPW.js: linear interpolation between the two 6-hourly
    ``NCEP_RE/surface_wv`` model times bracketing the acquisition, then a 6 mm
    binning into the TPWpos index used to select the SMW coefficients.
    """
    date = ee_module.Date(image.get("system:time_start"))
    year = ee_module.Number.parse(date.format("yyyy"))
    month = ee_module.Number.parse(date.format("MM"))
    day = ee_module.Number.parse(date.format("dd"))
    date1 = ee_module.Date.fromYMD(year, month, day)
    date2 = date1.advance(1, "days")

    def _datedist(img):
        return img.set("DateDist", ee_module.Number(img.get("system:time_start"))
                       .subtract(date.millis()).abs())

    # NOTE: NCEP_RE/surface_wv is flagged deprecated in the EE catalog but still
    # serves valid data (as in Ermida's upstream code). Revisit if it is removed.
    tpw_coll = (ee_module.ImageCollection("NCEP_RE/surface_wv")
                .filter(ee_module.Filter.date(date1.format("yyyy-MM-dd"),
                                              date2.format("yyyy-MM-dd")))
                .map(_datedist))
    closest = tpw_coll.sort("DateDist").toList(2)

    tpw1 = ee_module.Image(ee_module.Algorithms.If(
        closest.size().eq(0), ee_module.Image.constant(-999.0),
        ee_module.Image(closest.get(0)).select("pr_wtr")))
    tpw2 = ee_module.Image(ee_module.Algorithms.If(
        closest.size().eq(0), ee_module.Image.constant(-999.0),
        ee_module.Algorithms.If(closest.size().eq(1), tpw1,
                                ee_module.Image(closest.get(1)).select("pr_wtr"))))
    time1 = ee_module.Number(ee_module.Algorithms.If(
        closest.size().eq(0), 1.0,
        ee_module.Number(tpw1.get("DateDist")).divide(_MS_PER_6H)))
    time2 = ee_module.Number(ee_module.Algorithms.If(
        closest.size().lt(2), 0.0,
        ee_module.Number(tpw2.get("DateDist")).divide(_MS_PER_6H)))

    tpw = tpw1.expression("tpw1 * time2 + tpw2 * time1",
                          {"tpw1": tpw1, "time1": time1, "tpw2": tpw2, "time2": time2}
                          ).clip(image.geometry())
    pos = tpw.expression(
        "value = (TPW>0 && TPW<=6) ? 0"
        ": (TPW>6 && TPW<=12) ? 1"
        ": (TPW>12 && TPW<=18) ? 2"
        ": (TPW>18 && TPW<=24) ? 3"
        ": (TPW>24 && TPW<=30) ? 4"
        ": (TPW>30 && TPW<=36) ? 5"
        ": (TPW>36 && TPW<=42) ? 6"
        ": (TPW>42 && TPW<=48) ? 7"
        ": (TPW>48 && TPW<=54) ? 8"
        ": (TPW>54) ? 9"
        ": 0", {"TPW": tpw}).clip(image.geometry())
    return image.addBands(tpw.rename("TPW")).addBands(pos.rename("TPWpos"))


def compute(sensor, image, ee_module=ee):
    """SMW LST (°C) for one satellite-aware Landsat image (see module docstring)."""
    satid = ee_module.String(image.get("SATID"))
    refl = sensor.reflectance(image, ee_module)
    ndvi = refl.normalizedDifference(["nir", "red"]).rename("NDVI")
    fvc = _fvc(ndvi, ee_module).rename("FVC")
    em = _emissivity(sensor, image, fvc, satid, ee_module)

    image = _add_tpw(image, ee_module)
    tpwpos = image.select("TPWpos")

    def _coeff(table):
        vals = ee_module.List(ee_module.Dictionary(
            {s: list(v) for s, v in table.items()}).get(satid))
        return tpwpos.remap(_TPW_POS, vals, 0.0).resample("bilinear")

    a_img, b_img, c_img = _coeff(_SMW_A), _coeff(_SMW_B), _coeff(_SMW_C)
    lst_k = image.expression(
        "A * Tb / em + B / em + C",
        {"A": a_img, "B": b_img, "C": c_img, "em": em, "Tb": image.select("bt")}
    ).updateMask(image.select("TPW").lt(0).Not())

    return (lst_k.subtract(273.15).rename(INDEX_BAND)
            .set("system:time_start", image.get("system:time_start")))
