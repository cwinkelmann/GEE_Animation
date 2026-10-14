# GEE Index Timelapse — Gradio GUI in a container.
#
# Earth Engine auth is NOT interactive here: provide a Google Cloud service-account
# JSON key at runtime and point EE_SERVICE_ACCOUNT_KEY at it (see README "Docker").
FROM python:3.11-slim

WORKDIR /app

# geopandas (GDAL/GEOS/PROJ) and ffmpeg arrive as self-contained Python wheels
# (pyogrio, shapely, pyproj, imageio-ffmpeg) — no apt GDAL/ffmpeg packages needed,
# except libexpat, which the rasterio wheel's GDAL links at runtime and the slim
# image lacks ("libexpat.so.1: cannot open shared object file" on first lst_rf run).
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY gee_animation ./gee_animation
# `ml` brings scikit-learn/rasterio/joblib for the GUI's "train the sharpening
# forest locally" option (lst_rf with sharpen: local).
RUN pip install --no-cache-dir ".[gui,shapefile,ml,server]"

# Bind Gradio to all interfaces so the app is reachable from the host.
ENV GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860 \
    EE_PROJECT=hnee-331218
EXPOSE 7860

# Non-root; /data holds renders (GEE_OUTPUT_DIR) and the frame cache. The multi-user
# server (compose, `gee-animation-server`) overrides CMD; see deploy/.
# GRADIO_TEMP_DIR: Gradio copies every file a handler returns (mp4, gif, zip, each frame
# PNG) into this directory. Left at its default that is /tmp/gradio on the container's
# writable layer, outside both volumes and outside retention. Inside the cache volume the
# copies are on mounted storage, and the server's sweeper ages them out with the runs.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data/out /data/cache/gradio-tmp && chown -R app /data
USER app
ENV GEE_OUTPUT_DIR=/data/out \
    GEE_ANIMATION_CACHE_DIR=/data/cache \
    GRADIO_TEMP_DIR=/data/cache/gradio-tmp

CMD ["gee-animation-gui"]
