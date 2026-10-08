"""SPIKE (throwaway): train the lst_rf forest locally instead of in Earth Engine.

One month (2024-07), buffered R12 frame. EE only serves composites as GeoTIFFs:
the Landsat LST composite and the Sentinel-2 predictor stack, both on one 20 m
UTM grid. Everything else — 100 m aggregation, forest training, prediction,
residual — runs in numpy/scikit-learn. The EE-trained result on the same inputs
is downloaded too, for an apples-to-apples comparison and timing.
"""
import dataclasses, io, time, urllib.request, zipfile
import numpy as np, rasterio, ee
from sklearn.ensemble import RandomForestRegressor
from gee_animation import aoi, auth, collection, sharpen
from gee_animation.config import RunConfig
from gee_animation.products import INDEX_BAND

MONTH = ("2024-07-01", "2024-08-01")
FINE, COARSE = 20, 100
OUT = "/private/tmp/claude-501/-Users-christian-work-hnee-GEE-animation/cb67f53d-9e2d-4a19-b00f-f9ebfe8add3b/scratchpad/spike"
import os; os.makedirs(OUT, exist_ok=True)

def download(image, name, region, crs, scale):
    t = time.time()
    url = image.getDownloadURL({"region": region, "crs": crs, "scale": scale, "format": "GEO_TIFF"})
    data = urllib.request.urlopen(url, timeout=600).read()
    path = f"{OUT}/{name}.tif"
    open(path, "wb").write(data)
    with rasterio.open(path) as ds:
        arr = ds.read().astype("float32"); nod = ds.nodata
        if nod is not None: arr[arr == nod] = np.nan
        prof = ds.profile
    print(f"  {name}: {len(data)/1e6:.1f} MB, {arr.shape}, {time.time()-t:.1f}s (EE side incl. compute)", flush=True)
    return arr, prof

def block_mean(a, k):  # (bands, H, W) -> (bands, H/k, W/k), nan-aware
    b, h, w = a.shape; h2, w2 = h // k * k, w // k * k
    x = a[:, :h2, :w2].reshape(b, h2 // k, k, w2 // k, k)
    return np.nanmean(x, axis=(2, 4))

cfg = RunConfig.from_yaml("config/r12_zoom_lst_rf_10yr.yaml")
cfg = dataclasses.replace(cfg, index="lst", start=MONTH[0], end=MONTH[1], pool_years=None)
auth.init(cfg.project)
frame = aoi.parse(cfg.frame_aoi); region_geom = aoi.parse(cfg.region_aoi)
b = cfg.frame_aoi["bbox"]; crs = sharpen._utm_epsg((b[0]+b[2])/2, (b[1]+b[3])/2)
train_region = frame.buffer(sharpen.TRAIN_BUFFER_M).bounds()
proj = ee.Projection(crs)
coll = collection.build(cfg, frame, region_geom)
lst = coll.filterDate(*MONTH).select(INDEX_BAND).median()
preds = sharpen.s2_predictors(ee.Date(MONTH[0]).advance(-30, "day"), ee.Date(MONTH[1]).advance(30, "day"), train_region)

print("downloads (EE serves composites only):", flush=True)
lst_f, prof = download(lst.setDefaultProjection(proj.atScale(30)).reduceResolution(ee.Reducer.mean(), maxPixels=64).reproject(proj.atScale(FINE)), "lst_20m", train_region, crs, FINE)
pred_f, _ = download(preds.setDefaultProjection(proj.atScale(10)).reduceResolution(ee.Reducer.mean(), maxPixels=64).reproject(proj.atScale(FINE)), "pred_20m", train_region, crs, FINE)

# --- local pipeline -----------------------------------------------------------
t0 = time.time()
k = COARSE // FINE
lst_c = block_mean(lst_f, k)[0]                    # (Hc, Wc)
pred_c = block_mean(pred_f, k)                     # (5, Hc, Wc)
ok = np.isfinite(lst_c) & np.all(np.isfinite(pred_c), axis=0)
X = pred_c[:, ok].T; y = lst_c[ok]
rng = np.random.default_rng(1)
idx = rng.choice(len(y), size=min(5000, len(y)), replace=False)
rf = RandomForestRegressor(n_estimators=100, min_samples_leaf=5, max_samples=0.7, n_jobs=-1, random_state=1)
t1 = time.time(); rf.fit(X[idx], y[idx]); t_fit = time.time() - t1
Hf, Wf = pred_f.shape[1] // k * k, pred_f.shape[2] // k * k
pf = pred_f[:, :Hf, :Wf]
okf = np.all(np.isfinite(pf), axis=0)
fit_f = np.full((Hf, Wf), np.nan, "float32")
t1 = time.time(); fit_f[okf] = rf.predict(pf[:, okf].T); t_pred = time.time() - t1
fit_c = block_mean(fit_f[None], k)[0]
resid_c = lst_c[:Hf//k, :Wf//k] - fit_c
sharp = fit_f + np.repeat(np.repeat(resid_c, k, 0), k, 1)
t_local = time.time() - t0
print(f"local: {len(y)} coarse cells ({len(idx)} sampled), fit {t_fit:.1f}s, predict {t_pred:.1f}s, total {t_local:.1f}s", flush=True)
# conservation check: sharpened aggregated back == observed coarse
agg = block_mean(sharp[None], k)[0]
print(f"conservation |agg(sharp) - lst_c| max: {np.nanmax(np.abs(agg - lst_c[:Hf//k, :Wf//k])):.4f} K", flush=True)

# --- EE reference on identical inputs ----------------------------------------
print("EE reference (trains + predicts server-side):", flush=True)
ee_sharp, _ = download(sharpen.rf_sharpen(lst, preds, proj=proj, coarse_m=COARSE, fine_m=FINE, region=train_region), "ee_sharp_20m", train_region, crs, FINE)
es = ee_sharp[0, :Hf, :Wf]
both = np.isfinite(es) & np.isfinite(sharp)
d = (sharp - es)[both]
print(f"local vs EE: RMSE {np.sqrt(np.mean(d**2)):.3f} K, mean diff {d.mean():+.3f} K, |d|>1K: {np.mean(np.abs(d) > 1)*100:.1f}% of pixels", flush=True)
print(f"local field: min {np.nanmin(sharp):.1f} max {np.nanmax(sharp):.1f} | EE field: min {np.nanmin(es):.1f} max {np.nanmax(es):.1f}")
prof.update(count=1, dtype="float32", nodata=np.nan, height=Hf, width=Wf)
with rasterio.open(f"{OUT}/local_sharp_20m.tif", "w", **prof) as dst: dst.write(sharp[None].astype("float32"))
print("wrote", f"{OUT}/local_sharp_20m.tif")
