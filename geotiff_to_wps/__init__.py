"""geotiff-to-wps: Convert GeoTIFF files to WPS geogrid binary format."""

__version__ = '0.1.0'

from geotiff_to_wps.convert import convert
from geotiff_to_wps.presets import PRESETS

__all__ = ['convert', 'PRESETS']
