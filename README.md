# geotiff-to-wps

Convert GeoTIFF files to WPS geogrid binary format for use as custom static data in WRF. Supports topography, land use, soil type, green vegetation fraction, albedo, and other fields via built-in presets or custom configuration.

[![build](https://github.com/geotiff-to-wps/workflows/Build/badge.svg)](https://github.com/geotiff-to-wps/actions)
[![codecov](https://codecov.io/gh/mullenkamp/geotiff-to-wps/branch/main/graph/badge.svg)](https://codecov.io/gh/mullenkamp/geotiff-to-wps)
[![PyPI version](https://badge.fury.io/py/geotiff-to-wps.svg)](https://badge.fury.io/py/geotiff-to-wps)

---

**Source Code**: <a href="https://github.com/geotiff-to-wps" target="_blank">https://github.com/geotiff-to-wps</a>

---

## Installation

```bash
pip install geotiff-to-wps
```

Or with UV:

```bash
uv add geotiff-to-wps
```

## Usage

### Command line

```bash
geotiff-to-wps input.tif output_dir/
```

The input GeoTIFF must be in EPSG:4326 (WGS84 geographic coordinates). The output directory will contain the WPS binary tiles and an `index` file ready for use with WPS geogrid.

### Presets

Use `--preset` (or `-p`) to select defaults for common data types:

```bash
geotiff-to-wps -p dem dem_4326.tif /path/to/WPS_GEOG/topo_custom/
geotiff-to-wps -p landuse landuse_4326.tif /path/to/WPS_GEOG/lu_custom/
geotiff-to-wps -p greenfrac gvf_4326.tif /path/to/WPS_GEOG/gvf_custom/
```

Available presets:

| Preset | Type | Wordsize | Signed | Scale factor | Tile border | Description |
|--------|------|----------|--------|-------------|-------------|-------------|
| `dem` (default) | continuous | 2 | yes | — | 3 | Topography height (meters) |
| `landuse` | categorical | 1 | no | — | 0 | Land use category |
| `soiltype` | categorical | 1 | no | — | 0 | Soil type category |
| `greenfrac` | continuous | 2 | yes | 0.001 | 3 | Green vegetation fraction |
| `albedo` | continuous | 2 | yes | 0.001 | 3 | Albedo |

### Overriding preset defaults

Any preset setting can be overridden individually:

```bash
geotiff-to-wps -p dem --units feet --description "Elevation in feet" input.tif output/
geotiff-to-wps -p landuse --category-min 0 --category-max 40 input.tif output/
```

Available override options: `--data-type`, `--signed`/`--unsigned`, `--wordsize`, `--tile-bdr`, `--missing-value`, `--scale-factor`, `--units`, `--description`, `--category-min`, `--category-max`.

### Python

```python
from geotiff_to_wps import convert

# DEM (default preset)
convert("dem_4326.tif", "/path/to/WPS_GEOG/topo_custom/")

# Land use
convert("landuse_4326.tif", "/path/to/WPS_GEOG/lu_custom/", preset="landuse")

# Green fraction with custom description
convert("gvf.tif", "output/", preset="greenfrac", description="Monthly GVF")
```

## Full pipeline: custom static data in WRF

This guide walks through the complete process of replacing WRF's default static data with custom GeoTIFF sources, using topography as an example.

### Step 1: Prepare the GeoTIFF

If your data comes as multiple tiles, mosaic them first:

```bash
gdal_merge.py -o merged.tif tile1.tif tile2.tif ...
```

Reproject to EPSG:4326 with bilinear interpolation and zstd compression:

```bash
gdalwarp -t_srs EPSG:4326 -r bilinear -co COMPRESS=ZSTD merged.tif dem_4326.tif
```

Bilinear interpolation avoids stair-stepping artefacts in the reprojected elevation. For categorical data (land use, soil type), use nearest-neighbour instead:

```bash
gdalwarp -t_srs EPSG:4326 -r near -co COMPRESS=ZSTD landuse.tif landuse_4326.tif
```

The zstd compression reduces file size without affecting the conversion.

### Step 2: Convert to WPS format

```bash
geotiff-to-wps dem_4326.tif /path/to/WPS_GEOG/topo_custom/
```

This creates a directory containing:
- Binary tile files with border overlap for interpolation (continuous data) or exact tiles (categorical data)
- An `index` file describing the grid geometry, projection, and tile layout

### Step 3: Update GEOGRID.TBL

In your WPS installation, edit `geogrid/GEOGRID.TBL` (or `GEOGRID.TBL.ARW`). Find the entry for the field you are replacing (e.g., `HGT_M` for topography) and add a `rel_path` line pointing to your new dataset:

```
name = HGT_M
    ...
    interp_option = default:average_gcell+four_pt
    rel_path = default:topo_gmted2010_30s/
    rel_path = 3s:topo_custom/
```

The label before the colon (e.g., `3s`) is a resolution tag. Geogrid selects the dataset whose resolution tag best matches each domain's grid spacing:

| Label | Approximate resolution |
|-------|----------------------|
| `30s` | ~1 km (30 arc-seconds) |
| `10s` | ~300 m |
| `3s`  | ~90 m |
| `1s`  | ~30 m |

The `default` entry is used when no better match is found. Higher-resolution data is automatically used for finer nest domains.

The `interp_option` controls how source data is interpolated to the WRF grid. See the [WPS interpolation options documentation](https://www2.mmm.ucar.edu/wrf/users/wrf_users_guide/build/html/wps.html#geogrid-metgrid-interpolation-options) for all available methods.

### Step 4: Run geogrid

Run geogrid as usual:

```bash
./geogrid.exe
```

Verify the result by inspecting the relevant field in the output `geo_em.d0X.nc` files:

```python
import xarray as xr

ds = xr.open_dataset("geo_em.d03.nc")
ds["HGT_M"].isel(Time=0).plot()
```

Compare grid-cell values against known reference data to confirm the custom dataset is being used.

## Development

### Setup environment

We use [UV](https://docs.astral.sh/uv/) to manage the development environment and production build.

```bash
uv sync
```

### Run unit tests

You can run all the tests with:

```bash
uv run pytest
```

### Format the code

Execute the following commands to apply linting and check typing:

```bash
uv run ruff check .
uv run black --check --diff .
uv run mypy --install-types --non-interactive geotiff_to_wps
```

To auto-format:

```bash
uv run black .
uv run ruff check --fix .
```

## License

This project is licensed under the terms of the Apache Software License 2.0.
