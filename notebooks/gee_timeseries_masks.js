// gee_timeseries_masks.js — second version of gee_timeseries.js.
//
// Same plot, same collection, same NDVI chart — but every mask is put on the
// map as its own layer, so you can SEE what is being thrown away and what is
// not, instead of trusting a chart.
//
// Why this version exists. The first version has a logic error in its SCL
// filter:
//
//     var mask = (veg.neq(1)).or(soil.neq(1));     // first version
//
// "not vegetation" OR "not soil" is true for every pixel (no pixel is both),
// so that mask keeps everything. Villages, water, clouds, shadows all survive
// the "SCL filter" and only the coarse QA60 bits ever remove anything. The
// fix is one line:
//
//     var mask = veg.or(soil);                      // this version
//
// Two more things the layers below make visible:
//  * SCL has NO built-up class. Houses and roads are class 5 "not vegetated",
//    the same class as bare soil. If you keep class 5 for the fields you keep
//    the villages too. Masking them needs another source (here: ESA WorldCover
//    class 50, built-up).
//  * SCL flags clouds that are not there: bright dry fields, roofs, and the
//    edges of real clouds. The "cloud frequency" layer counts how often each
//    pixel is flagged across all scenes; pixels flagged in most scenes of a
//    clear plot are false positives, not weather.

// ---------------------------------------------------------------- 1. Plot

var geometry = ee.Geometry.Polygon(
        [[[13.848353, 52.065431],
        [13.884300,  52.067489],
        [13.892349,  52.042273],
        [13.882110,  52.029619],
        [13.853082, 52.030024]]]);
var geometryBuff = geometry.buffer(-20);

Map.centerObject(geometry, 14);

// ---------------------------------------------------------------- 2. Collection

var START = '2024-10-15';
var END   = '2026-10-15';
var CLOUD_PROB_MAX = 40;   // s2cloudless probability (%) above which a pixel is cloud

var S2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterMetadata('CLOUDY_PIXEL_PERCENTAGE', 'less_than', 15)
  .filterDate(START, END)
  .filterBounds(geometryBuff);

// Join the s2cloudless probability image onto every granule as band 'cloud_prob'.
// SCL alone misses thin cloud, haze and cloud edges; s2cloudless catches those.
// The join is inner: a granule without a probability image is dropped (one of
// 56 scenes on this plot), which is what the gee_animation package does too.
var prob = ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY')
  .filterDate(START, END)
  .filterBounds(geometryBuff);
S2 = ee.ImageCollection(ee.Join.saveFirst('cloud_prob_img').apply({
  primary: S2,
  secondary: prob,
  condition: ee.Filter.equals({leftField: 'system:index', rightField: 'system:index'})
})).map(function (img) {
  return img.addBands(
    ee.Image(img.get('cloud_prob_img')).select('probability').rename('cloud_prob'));
});

print('Scenes in collection:', S2.size());

// ---------------------------------------------------------------- 3. Masks, as functions that RETURN the mask

// SCL classes (Sen2Cor scene classification):
//  1 saturated/defective  2 dark area  3 cloud shadow  4 vegetation
//  5 not vegetated (bare soil AND built-up)  6 water  7 unclassified
//  8 cloud medium prob.  9 cloud high prob.  10 thin cirrus  11 snow/ice

function sclIn(image, classes) {
  var scl = image.select('SCL');
  return scl.remap(classes, classes.map(function () { return 1; }), 0).rename('mask');
}

// (a) the first version's filter, reproduced: always 1
function maskBuggy(image) {
  var scl = image.select('SCL');
  return scl.eq(4).neq(1).or(scl.eq(5).neq(1)).rename('mask');
}
// (b) the intended filter: keep vegetation and bare soil
function maskField(image)  { return sclIn(image, [4, 5]); }
// (c) SCL cloud, shadow, cirrus, snow — what the gee_animation package removes
function maskSclCloud(image) { return sclIn(image, [3, 8, 9, 10, 11]); }
// (d) QA60 bits 10 (cloud) and 11 (cirrus)
function maskQA60(image) {
  var qa = image.select('QA60');
  return qa.bitwiseAnd(1 << 10).neq(0).or(qa.bitwiseAnd(1 << 11).neq(0)).rename('mask');
}
// (e) s2cloudless above threshold
function maskProb(image) { return image.select('cloud_prob').gt(CLOUD_PROB_MAX).rename('mask'); }

// Built-up from ESA WorldCover 2021 (10 m, static). Class 50 = built-up.
var worldcover = ee.ImageCollection('ESA/WorldCover/v200').first();
var builtUp = worldcover.eq(50).rename('mask');
var wcWater = worldcover.eq(80).rename('mask');

// Final keep-mask used for the clean chart: field pixel, not SCL cloud,
// not s2cloudless cloud, not built-up.
function keepClean(image) {
  var keep = maskField(image)
    .and(maskSclCloud(image).not())
    .and(maskProb(image).not())
    .and(builtUp.not());
  return image.updateMask(keep);
}

// ---------------------------------------------------------------- 4. One example scene, every mask as a layer

// The scene with the most SCL cloud INSIDE the plot: that is where false flags
// live. (CLOUDY_PIXEL_PERCENTAGE is for the whole 100 km tile and says nothing
// about your field.) To look at a specific date instead:
//   var example = ee.Image(S2.filterDate('2025-06-01', '2025-06-30').first());
var withPlotCloud = S2.map(function (img) {
  var frac = maskSclCloud(img).reduceRegion({
    reducer: ee.Reducer.mean(), geometry: geometry, scale: 20}).get('mask');
  return img.set('plot_cloud_frac', frac);
});
var example = ee.Image(withPlotCloud.sort('plot_cloud_frac', false).first());
print('Example scene:', example.get('system:index'),
      'SCL cloud fraction inside plot', example.get('plot_cloud_frac'),
      'tile CLOUDY_PIXEL_PERCENTAGE', example.get('CLOUDY_PIXEL_PERCENTAGE'));

var rgbVis = {bands: ['B4', 'B3', 'B2'], min: 0, max: 3000};
var sclVis = {min: 1, max: 11, palette: [
  '#ff0004', '#868686', '#774b0a', '#10d22c', '#ffff52', '#0000ff',
  '#818181', '#c0c0c0', '#f1f1f1', '#bac5eb', '#52fff9']};
// SCL legend:  1 red saturated · 2 grey dark · 3 brown shadow · 4 green veg ·
//              5 yellow not-veg · 6 blue water · 7 grey unclassified ·
//              8 light-grey cloud med · 9 white cloud high · 10 pale-blue cirrus · 11 cyan snow

function show(mask, color, name, shown) {
  Map.addLayer(mask.selfMask(), {palette: [color]}, name, shown);
}

Map.addLayer(example, rgbVis, '0 · example scene, true colour');
Map.addLayer(example.select('SCL'), sclVis, '1 · SCL all classes', false);

show(sclIn(example, [4]),         '#10d22c', '2 · SCL 4 vegetation',           false);
show(sclIn(example, [5]),         '#e3c16f', '2 · SCL 5 not vegetated (soil + built)', true);
show(sclIn(example, [6]),         '#0000ff', '2 · SCL 6 water',                true);
show(sclIn(example, [3]),         '#5e3c99', '2 · SCL 3 cloud shadow',         true);
show(sclIn(example, [8, 9]),      '#ff7f00', '2 · SCL 8+9 cloud',              true);
show(sclIn(example, [10]),        '#fdbf6f', '2 · SCL 10 thin cirrus',         true);
show(sclIn(example, [11]),        '#52fff9', '2 · SCL 11 snow/ice',            false);
show(sclIn(example, [1, 2, 7]),   '#999999', '2 · SCL 1/2/7 saturated, dark, unclassified', false);

show(maskQA60(example),           '#e41a1c', '3 · QA60 cloud|cirrus bits',     false);
Map.addLayer(example.select('cloud_prob'), {min: 0, max: 100, palette: ['#000000', '#ffffff']},
             '3 · s2cloudless probability 0–100', false);
show(maskProb(example),           '#b2182b', '3 · s2cloudless > ' + CLOUD_PROB_MAX + ' %', true);

show(builtUp,                     '#fa0000', '4 · WorldCover built-up',        true);
show(wcWater,                     '#0064c8', '4 · WorldCover water',           false);

show(maskBuggy(example),          '#777777', '5 · first-version mask (keeps everything)', false);
show(maskField(example),          '#4daf4a', '5 · field mask veg|soil (fixed)', false);
show(maskField(example).and(maskSclCloud(example).not()).and(maskProb(example).not()).and(builtUp.not()),
                                  '#1b9e77', '5 · final keep mask',             false);

// ---------------------------------------------------------------- 5. Across the collection: how often is a pixel called cloud?

// Mean of the SCL cloud flag over all scenes = fraction of scenes in which the
// pixel was flagged. In a plot that passed a 15 % scene filter, a pixel flagged
// in > 30 % of scenes is almost certainly a bright surface, not a cloud.
var cloudFreq = S2.map(function (img) { return sclIn(img, [8, 9, 10]); }).mean();
Map.addLayer(cloudFreq.clip(geometry), {min: 0, max: 0.5, palette: ['#ffffff', '#ff7f00', '#7f0000']},
             '6 · SCL cloud frequency 0–50 % of scenes', false);
var probFreq = S2.map(maskProb).mean();
Map.addLayer(probFreq.clip(geometry), {min: 0, max: 0.5, palette: ['#ffffff', '#b2182b', '#67001f']},
             '6 · s2cloudless cloud frequency 0–50 % of scenes', false);

// ---------------------------------------------------------------- 6. NDVI charts: buggy vs fixed vs clean

function addNDVI(image) {
  return image.addBands(image.normalizedDifference(['B8', 'B4']));
}

var S2buggy = S2.map(function (img) { return img.updateMask(maskBuggy(img)); }).map(addNDVI);
var S2field = S2.map(function (img) { return img.updateMask(maskField(img)); }).map(addNDVI);
var S2clean = S2.map(keepClean).map(addNDVI);

function ndviChart(coll, label, color) {
  return ui.Chart.image.seriesByRegion(coll, geometryBuff, ee.Reducer.mean(), 'nd', 10)
    .setChartType('LineChart')
    .setSeriesNames([label])
    .setOptions({
      interpolateNulls: true, lineWidth: 1, pointSize: 3,
      title: 'Mean NDVI in plot — ' + label,
      hAxis: {title: 'Date'}, vAxis: {title: 'NDVI', minValue: 0, maxValue: 1},
      series: {0: {color: color}}
    });
}

print(ndviChart(S2buggy, 'first-version mask (nothing masked)', '#777777'));
print(ndviChart(S2field, 'SCL veg|soil only', '#4daf4a'));
print(ndviChart(S2clean, 'veg|soil, no cloud, no built-up', '#1b9e77'));

// ---------------------------------------------------------------- 7. Median NDVI of the clean collection

var pal = ['#d73027', '#f46d43', '#fdae61', '#fee08b', '#d9ef8b', '#a6d96a'];
Map.addLayer(S2clean.select('nd').median().clip(geometryBuff),
             {min: 0.1, max: 0.9, palette: pal}, '7 · median NDVI, clean', true);
Map.addLayer(geometry, {color: 'green'}, 'Border');
Map.addLayer(geometryBuff, {color: 'red'}, 'Buffer', false);
