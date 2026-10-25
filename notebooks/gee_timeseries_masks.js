// gee_timeseries_masks.js — gee_timeseries.js with the masks made visible.
//
// The first script's SCL filter keeps every pixel:
//     var mask = (veg.neq(1)).or(soil.neq(1));   // "not veg OR not soil" is always true
// so villages, water, clouds and shadows all end up in the NDVI chart.
// The fix is `veg.or(soil)`. This script uses the fixed filter, shows each
// mask as a map layer, and charts the first version against the fixed one.
//
// Every layer name starts with the data it comes from:
//   S2         = the Sentinel-2 scene itself (bands B2/B3/B4/B8, band SCL)
//   s2cloudless = COPERNICUS/S2_CLOUD_PROBABILITY, one image per scene, 0–100 %
//   WorldCover = ESA/WorldCover/v200, one static 10 m land-cover map (2021)

// ---------------------------------------------------------------- 1. Choose a plot

var PLOTS = {
  forest: ee.Geometry.Polygon([[[13.848353, 52.065431], [13.884300, 52.067489],
                                [13.892349, 52.042273], [13.882110, 52.029619],
                                [13.853082, 52.030024]]]),   // course plot, 77 % tree cover
  water:  ee.Geometry.Rectangle([13.871193, 52.010102, 13.889732, 52.024259]),   // 28 % water
  city:   ee.Geometry.Rectangle([13.386093, 52.465775, 13.437419, 52.488881])    // Berlin, 42 % built-up
};
var PLOT = 'water';                 // <-- 'forest', 'water' or 'city'
var EXAMPLE_DATE = '2025-09-07';    // scene shown in the mask layers (first scene on/after this date)

var geometry = PLOTS[PLOT];
var geometryBuff = geometry.buffer(-20);
Map.centerObject(geometry, 14);

// ---------------------------------------------------------------- 2. Load the scenes

var S2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterMetadata('CLOUDY_PIXEL_PERCENTAGE', 'less_than', 15)
  .filterDate('2024-10-15', '2026-10-15')
  .filterBounds(geometryBuff);

// Attach the s2cloudless probability of the same scene as band 'cloud_prob'.
var s2cloudless = ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY');
S2 = ee.ImageCollection(ee.Join.saveFirst('probImage').apply(
    S2, s2cloudless,
    ee.Filter.equals({leftField: 'system:index', rightField: 'system:index'})))
  .map(function (img) {
    return img.addBands(ee.Image(img.get('probImage')).select('probability').rename('cloud_prob'));
  });

// One static land-cover map; class 50 = built-up.
var worldcover = ee.ImageCollection('ESA/WorldCover/v200').first();

print('Plot:', PLOT, '  scenes:', S2.size());

// ---------------------------------------------------------------- 3. The masks (1 = pixel is in that class)

// SCL classes: 3 cloud shadow · 4 vegetation · 5 not vegetated (bare soil AND
// buildings — SCL has no built-up class) · 6 water · 8/9 cloud · 10 cirrus · 11 snow
function sclIs(img, classes) {
  return img.select('SCL').remap(classes, classes.map(function () { return 1; }), 0);
}
function mFieldBuggy(img) { return sclIs(img, [4]).neq(1).or(sclIs(img, [5]).neq(1)); } // first version
function mField(img)      { return sclIs(img, [4, 5]); }                                  // fixed
function mCloudSCL(img)   { return sclIs(img, [3, 8, 9, 10, 11]); }
function mCloudProb(img)  { return img.select('cloud_prob').gt(40); }
var mBuilt = worldcover.eq(50);

// The mask the chart's third line uses: vegetation or soil, no cloud by either
// source, no building.
function mClean(img) {
  return mField(img).and(mCloudSCL(img).not()).and(mCloudProb(img).not()).and(mBuilt.not());
}

// ---------------------------------------------------------------- 4. Look at the masks on one scene

// ONE scene. The RGB layer is this scene; every mask layer below it is cut
// from the same scene (its SCL band, its s2cloudless image), so you can
// judge each mask against what the scene actually shows.
var example = ee.Image(S2.filterDate(EXAMPLE_DATE, '2026-12-31').first());
print('Example scene:', example.get('system:index'));

Map.addLayer(example, {bands: ['B4', 'B3', 'B2'], min: 0, max: 3000},
             'S2 · true colour of the example scene, unclipped');

function show(mask, color, name, visible) {   // masks are drawn at 70 % so the RGB shows through
  Map.addLayer(mask.selfMask().clip(geometry), {palette: [color]}, name, visible, 0.7);
}
show(sclIs(example, [5]),   '#e3c16f', 'S2 SCL · 5 not vegetated (soil + buildings)', true);
show(sclIs(example, [6]),   '#0000ff', 'S2 SCL · 6 water',                            true);
show(mCloudSCL(example),    '#ff7f00', 'S2 SCL · cloud, shadow, cirrus, snow',        true);
show(mCloudProb(example),   '#b2182b', 's2cloudless · probability > 40 %',            true);
show(mBuilt,                '#fa0000', 'WorldCover · built-up',                       true);
show(mClean(example),       '#1b9e77', 'kept by the fixed mask',                      false);

// ---------------------------------------------------------------- 5. NDVI series: first version vs fixed

// One image per scene with the plot-mean NDVI under each mask as a band;
// ui.Chart.image.series draws one line per band.
var ndviPerMask = S2.map(function (img) {
  var ndvi = img.normalizedDifference(['B8', 'B4']);
  return ee.Image.cat(
      ndvi.updateMask(mFieldBuggy(img)).rename('buggy'),
      ndvi.updateMask(mField(img)).rename('fixed'),
      ndvi.updateMask(mClean(img)).rename('clean'))
    .copyProperties(img, ['system:time_start']);
});

print(ui.Chart.image.series(ndviPerMask, geometryBuff, ee.Reducer.mean(), 10)
  .setChartType('LineChart')
  .setSeriesNames(['first version (nothing masked)', 'SCL veg|soil, fixed', 'fixed + no cloud + no buildings'])
  .setOptions({
    title: 'Mean NDVI in plot "' + PLOT + '"',
    interpolateNulls: true, lineWidth: 1, pointSize: 3,
    hAxis: {title: 'Date'}, vAxis: {title: 'NDVI', minValue: 0, maxValue: 1},
    series: {0: {color: '#777777'}, 1: {color: '#4daf4a'}, 2: {color: '#1b9e77'}}
  }));

// ---------------------------------------------------------------- 6. Median NDVI of the clean series

var pal = ['#d73027', '#f46d43', '#fdae61', '#fee08b', '#d9ef8b', '#a6d96a'];
Map.addLayer(ndviPerMask.select('clean').median().clip(geometryBuff),
             {min: 0.1, max: 0.9, palette: pal}, 'median NDVI, clean', false);
Map.addLayer(geometry, {color: 'green'}, 'plot border');
