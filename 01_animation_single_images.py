"""Example: build an NDVI timelapse from a config file.

Usage:
    python 01_animation_single_images.py            # uses config.example.yaml
    gee-animation --config config.example.yaml      # equivalent via CLI
"""
from gee_animation.cli import run

if __name__ == "__main__":
    paths = run("config.example.yaml")
    for p in paths:
        print(f"wrote {p}")
