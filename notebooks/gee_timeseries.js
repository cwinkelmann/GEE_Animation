// Create your plot
// (you can also do it manually directly on the map)
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


// Add plot and buffer to the map
// and specify fill color and layer name
Map.addLayer(geometry,{color:'green'},'Border');
Map.addLayer(geometryBuff,{color:'red'},'Buffer');

// Center map on the plot
Map.centerObject(geometry);

// Load image collection of Sentinel-2 imagery
// (choose SR for atmospheric corrections to surface reflectance)
var S2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
// Remove cloudy images from the collection
  .filterMetadata('CLOUDY_PIXEL_PERCENTAGE', 'less_than', 15)
  // Filter to study period
  .filterDate('2024-10-15', '2026-10-15')
  // Filter to plot boundaries
  .filterBounds(geometryBuff);

// Function to keep only vegetation and soil pixels
function keepFieldPixel(image) {
  // Select SCL layer
  var scl = image.select('SCL');
  // Select vegetation and soil pixels
  var veg = scl.eq(4); // 4 = Vegetation
  var soil = scl.eq(5); // 5 = Bare soils
  // Mask if not veg or soil
  var mask = (veg.neq(1)).or(soil.neq(1));
  return image.updateMask(mask);
}

// Show that mask on the first scene (1 = pixel kept), over the scene itself.
var first = ee.Image(S2.first());
var firstMask = (first.select('SCL').eq(4).neq(1)).or(first.select('SCL').eq(5).neq(1));
Map.addLayer(first, {bands: ['B4', 'B3', 'B2'], min: 0, max: 3000}, 'First scene, true colour');
Map.addLayer(firstMask.selfMask().clip(geometry), {palette: ['red']}, 'First scene, SCL mask (1 = kept)', true, 0.6);

// Apply custom filter to S2 collection
var S2 = S2.map(keepFieldPixel);

// Filter defined here:
// https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR#description

function maskS2clouds(image) {
  var qa = image.select('QA60');

// Bits 10 and 11 are clouds and cirrus, respectively.
  var cloudBitMask = 1 << 10;
  var cirrusBitMask = 1 << 11;

// Both flags should be set to zero, indicating clear conditions.
  var mask = qa.bitwiseAnd(cloudBitMask).eq(0)
      .and(qa.bitwiseAnd(cirrusBitMask).eq(0));

  return image.updateMask(mask);
}

// Function to compute NDVI and add result as new band
var addNDVI = function(image) {
return image.addBands(image.normalizedDifference(['B8', 'B4']));
};

// Add NDVI band to image collection
var S2 = S2.map(addNDVI);

var evoNDVI = ui.Chart.image.seriesByRegion(
  S2,                // Image collection
  geometryBuff,      // Region
  ee.Reducer.mean(), // Type of reducer to apply
  'nd',              // Band
  10);               // Scale

  var plotNDVI = evoNDVI                    // Data
    .setChartType('LineChart')            // Type of plot
    .setSeriesNames(['SCL filter only'])
    .setOptions({                         // Plot customization
      interpolateNulls: true,
      lineWidth: 1,
      pointSize: 3,
      title: 'NDVI annual evolution',
      hAxis: {title: 'Date'},
      vAxis: {title: 'NDVI'}
});

// Apply second filter
var S2 = S2.map(maskS2clouds);

// Plot results
var plotNDVI = ui.Chart.image.seriesByRegion(
  S2,
  geometryBuff,
  ee.Reducer.mean(),
  'nd',10)
  .setChartType('LineChart')
  .setSeriesNames(['After cloud filter'])
  .setOptions({
    interpolateNulls: true,
    lineWidth: 1,
    pointSize: 3,
    title: 'NDVI annual evolution',
    hAxis: {title: 'Date'},
    vAxis: {title: 'NDVI'},
    series: {0:{color: 'red'}}
  });

print(plotNDVI)

// Extract NDVI band from S2 collection
var NDVI = S2.select(['nd']);
// Extract median NDVI value for each pixel
var NDVImed = NDVI.median();

// Hex values for red to green color palette
var pal = ['#d73027', '#f46d43', '#fdae61', '#fee08b', '#d9ef8b', '#a6d96a'];

// Display NDVI results on map
Map.addLayer(
  NDVImed.clip(geometryBuff),        // Clip map to plot borders
  {min:0.1, max:0.9, palette: pal},  // Specify color palette
  'NDVI'                             // Layer name
  )