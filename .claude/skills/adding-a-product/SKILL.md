---
name: adding-a-product
description: Use when adding a new index (NDRE, SAVI, a new LST variant) or a new sensor to gee_animation — registering it in products.py, choosing its viz defaults and legend wording, and knowing which tests and registries must be updated.
---

# Adding an index or sensor

Everything a product needs lives in `gee_animation/products.py`. The registry is
generic — `get_product`, `gui.indices_for`, the header and the legend all derive
from it — so registering correctly is usually the whole job. What costs time is
the handful of things that are **not** next to the registry entry.

## The four registries (three are easy to miss)

| Registry | Location | Why it matters |
|---|---|---|
| `INDICES` / `SENSORS` | the obvious dict | the product itself |
| `Index.sensors` frozenset | inside the entry | the **only** gate on sensor availability — no other file needs a case |
| `_S2_20M_INDICES` | ~250 lines away | drives `native_scale_m`; miss it and a 20 m band renders as if it were 10 m |
| `THERMAL_INDICES` | near the LST block | drives the L8/L9 mission default and the 100 m native scale |

## Band access

`sensor.reflectance()` returns exactly six harmonised aliases —
`blue green red nir swir1 swir2`. Any band outside that set (Sentinel-2's red
edge B5, the Landsat `thermal`/`st_qa` pair) must be selected directly from the
image and scaled by hand, because the alias table cannot carry a band that only
one sensor has. Follow the LST family — it is the only precedent.

Scaling is per-sensor and easy to get wrong: Sentinel-2 reflectance is `×0.0001`;
Landsat C2 L2 is `×0.0000275 − 0.2`.

## Required Index fields beyond the maths

A product that renders correctly but reads badly is not done. Fill in:

- `display_name` — plain language, acronym in parentheses
  ("Vegetation greenness (NDVI)"). It titles the frame and heads the legend.
- `low_label` / `high_label` — what the ramp *ends mean* ("water" → "dense
  vegetation"). Blank means no word anchors on the colorbar.
- `units` — for anything with a physical unit (`"°C"`); shown on the max tick.
- `bands` / `formula` — method-doc metadata; deliberately not drawn on frames.
- `default_viz` — see below.

## Palettes and viz ranges are empirical, not aesthetic

A default range is a claim about the data. `-1..1` on a normalized-difference
index wastes half the ramp; NDVI's current palette came from a numeric sweep
with pinned criteria (water separable from no-data grey, deuteranopia-safe
endpoints, brown/green crossover near 0). If you invent a range, say so in the
handoff and check it against real imagery over the AOI before publishing —
don't let a guessed ramp ship as if it were measured.

## Tests that break for reasons unrelated to your product

`tests/test_products.py::test_registry_contents` asserts a **hardcoded set** of
index names. Update it in the same commit or you get a failure that looks like
your maths is wrong. `tests/test_gui.py` likewise pins default-viz values.

Add at minimum: a maths test (bands selected, scaling, `system:time_start`
preserved) and a sensor-restriction test (`get_product` rejects the pairs your
frozenset excludes, and `native_scale_m` reports the right GSD).

## Checklist

- [ ] compute fn + `INDICES` entry with `sensors` frozenset
- [ ] `_S2_20M_INDICES` / `THERMAL_INDICES` if the band is 20 m or thermal
- [ ] `display_name`, `low_label`, `high_label`, `units`, `bands`, `formula`
- [ ] `default_viz` — measured, or flagged as a guess
- [ ] `test_registry_contents` updated; maths + restriction tests added
- [ ] `config/<name>.example.yaml` and the README index table
- [ ] `pytest -m "not integration" -q` green
