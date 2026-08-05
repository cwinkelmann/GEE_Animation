### AOI selection
Use two different AOIs. There is one rectangle. That should be the frame for animation. Make sure the aspect ratio is not changed when saving. Then there is polygon for the important region. This needs to ave a cloud cover less then 10%

### Landsat Surface Temperature (LST) Data

The Landsat Surface Temperature (LST) data is derived from the thermal infrared bands of the Landsat satellites. It provides information about the land surface temperature, which is crucial for various applications such as climate studies, urban heat island analysis, and agricultural monitoring.

A rough description I was given is:
"Land Surface Temperture
LST in Barnim from data of collection 2 from Landsat 4,5,7 and 8 missions
Resolution resampled to 30m from (100m to 120m). Temporal resulution of Landsat missions is 8 to 16 daysdepending from how many satellites data is available. Cloud cover reduces data availabilty for certain periods.
Please select a year, the first month and the amount of months that should be used to generate the LST layer.For each point clicked on the map a plotline will be added to the chart.The chart plots the yearly LST as displayed within a bufferzone around each point.The data is reduced according to the mode of the selected band. The radius of the buffer can be selected below."

### Different Indices based on Landsat, MODIS, and Sentinel-2 Data

some humidity indices, LST etc. The project should be able to compute and visualize different indices based on the data from Landsat, MODIS, and Sentinel-2 satellites. These indices can include NDVI (Normalized Difference Vegetation Index), EVI (Enhanced Vegetation Index), LST (Land Surface Temperature), and other relevant indices that provide insights into vegetation health, water stress, and land surface conditions.


### A simple GUI for selecting AOIs, indices, and time ranges
The gui should allow users to upload their own AOIs, generate a frame by say how many meters it should extend around the AOI. The user should be able to select the index they want to compute, the time range (start and end date), and any other relevant parameters. The GUI should provide an intuitive interface for users to interact with the data and visualize the results.
The gui should be as simple as possible i.e. with GRADIO.

THe gui should contain a map, then the produced animation video and a chart of the selected index. Cross it over east to west and north to south. The x axis is the coordinates then the y axis is the value of the index. The chart should be interactive and allow users to zoom in and out, pan, and hover over data points to see specific values. The GUI should also provide options for exporting the generated animations and charts for further analysis or reporting.
### AOI selection
Use two different AOIs. There is one rectangle. That should be the frame for animation. Make sure the aspect ratio is not changed when saving. Then there is polygon for the important region. This needs to ave a cloud cover less then 10%

### Landsat Surface Temperature (LST) Data

The Landsat Surface Temperature (LST) data is derived from the thermal infrared bands of the Landsat satellites. It provides information about the land surface temperature, which is crucial for various applications such as climate studies, urban heat island analysis, and agricultural monitoring.

### Different Indices based on Landsat, MODIS, and Sentinel-2 Data

some humidity indices, LST etc. The project should be able to compute and visualize different indices based on the data from Landsat, MODIS, and Sentinel-2 satellites. These indices can include NDVI (Normalized Difference Vegetation Index), EVI (Enhanced Vegetation Index), LST (Land Surface Temperature), and other relevant indices that provide insights into vegetation health, water stress, and land surface conditions.


### GEnerate intermediate frames
Could it make sense to generate intermediate frames between the Landsat images? This could be done using interpolation techniques or machine learning models to create a smoother animation of the changes in the selected index over time. The generated intermediate frames would help visualize gradual changes and trends more effectively, providing a clearer understanding of the temporal dynamics of the selected index.


### Docker container which can be run remotely
The project should be packaged into a Docker container that can be run remotely. This would allow users to deploy the application on cloud platforms or remote servers without worrying about environment setup and dependencies. The Docker container should include all necessary libraries, tools, and configurations required to run the application seamlessly. Users should be able to pull the Docker image and run it with minimal configuration, ensuring reproducibility and ease of use across different environments. Only the google cloud service account key should be passed from the outside and there should be a simple auth so noone anonymous could bleed my account dry.

