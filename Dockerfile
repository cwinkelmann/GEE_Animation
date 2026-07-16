# GEE Index Timelapse — Gradio GUI in a container.
#
# Earth Engine auth is NOT interactive here: provide a Google Cloud service-account
# JSON key at runtime and point EE_SERVICE_ACCOUNT_KEY at it (see README "Docker").
FROM python:3.11-slim

WORKDIR /app

# geopandas (GDAL/GEOS/PROJ) and ffmpeg arrive as self-contained Python wheels
# (pyogrio, shapely, pyproj, imageio-ffmpeg) — no apt GDAL/ffmpeg packages needed.
COPY pyproject.toml README.md ./
COPY gee_animation ./gee_animation
RUN pip install --no-cache-dir ".[gui,shapefile]"

# Bind Gradio to all interfaces so the app is reachable from the host.
ENV GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860 \
    EE_PROJECT=hnee-331218
EXPOSE 7860

CMD ["gee-animation-gui"]
