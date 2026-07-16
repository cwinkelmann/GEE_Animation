"""Google Earth Engine NDVI forest timelapse generator."""

from .api import Animation, animate

__version__ = "0.1.0"
__all__ = ["animate", "Animation"]
