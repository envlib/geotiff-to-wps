"""
Convert a GeoTIFF (EPSG:4326) to WPS geogrid binary format.
"""
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from geotiff_to_wps.presets import PRESETS


def _get_numpy_dtype(wordsize, signed):
    """Return (internal_dtype, big_endian_dtype) for the given wordsize and signedness."""
    key = (wordsize, signed)
    dtypes = {
        (1, False): (np.uint8, '>u1'),
        (1, True): (np.int8, '>i1'),
        (2, False): (np.uint16, '>u2'),
        (2, True): (np.int16, '>i2'),
        (4, False): (np.uint32, '>u4'),
        (4, True): (np.int32, '>i4'),
    }
    if key not in dtypes:
        msg = f'Unsupported wordsize={wordsize}, signed={signed}'
        raise ValueError(msg)
    return dtypes[key]


def find_tile_size(axis_size):
    """Find a tile size that evenly divides the axis, preferring 1000-3000."""
    if axis_size <= 3000:
        return axis_size
    for tile_size in range(3000, 999, -100):
        if axis_size % tile_size == 0:
            return tile_size
    for tile_size in range(4000, 100, -1):
        if axis_size % tile_size == 0:
            return tile_size
    return axis_size


def _read_tile(src, tif_row_start, tif_col_start, tile_h, tile_w, nodata_value,
               *, tile_bdr, missing_value, scale_factor, np_dtype):
    """Read a tile region plus border via windowed read, returning bottom_top data.

    Parameters are in GeoTIFF coordinates (row 0 = north, col 0 = west).
    Returns array of shape (tile_h + 2*bdr, tile_w + 2*bdr) in
    bottom_top row order, with missing_value padding at edges.
    """
    height, width = src.height, src.width
    bdr = tile_bdr

    # Full region including border
    req_row_start = tif_row_start - bdr
    req_row_end = tif_row_start + tile_h + bdr
    req_col_start = tif_col_start - bdr
    req_col_end = tif_col_start + tile_w + bdr

    # Clamp to dataset bounds
    read_row_start = max(0, req_row_start)
    read_row_end = min(height, req_row_end)
    read_col_start = max(0, req_col_start)
    read_col_end = min(width, req_col_end)

    # Read from GeoTIFF
    window = Window(
        col_off=read_col_start,
        row_off=read_row_start,
        width=read_col_end - read_col_start,
        height=read_row_end - read_row_start,
    )
    data = src.read(1, window=window).astype(np.float64)

    # Replace nodata
    if nodata_value is not None:
        mask = data == nodata_value
        if mask.any():
            data[mask] = missing_value

    # Apply inverse scale factor (float → scaled int)
    if scale_factor is not None:
        nodata_mask = data == missing_value
        data /= scale_factor
        data[nodata_mask] = missing_value

    # Cast to target dtype
    data = np.rint(data).astype(np_dtype)

    # Place into full tile+border array (still in top_bottom GeoTIFF order)
    out_h = tile_h + 2 * bdr
    out_w = tile_w + 2 * bdr
    out = np.full((out_h, out_w), missing_value, dtype=np_dtype)

    dst_row = read_row_start - req_row_start
    dst_col = read_col_start - req_col_start
    out[dst_row:dst_row + data.shape[0],
        dst_col:dst_col + data.shape[1]] = data

    # Flip to bottom_top order (south-first)
    return out[::-1].copy()


def convert(input_path, output_dir, *, preset='dem', **overrides):
    """Convert a GeoTIFF to WPS geogrid binary format.

    Parameters
    ----------
    input_path : str or Path
        Path to the input GeoTIFF file. Must be in EPSG:4326.
    output_dir : str or Path
        Directory to write WPS binary tiles and index file to.
        Created if it does not exist.
    preset : str
        Preset name for default settings. One of: dem, landuse,
        soiltype, greenfrac, albedo.
    **overrides
        Override individual preset settings. Valid keys: data_type,
        signed, wordsize, tile_bdr, missing_value, scale_factor,
        units, description, category_min, category_max.

    Raises
    ------
    FileNotFoundError
        If input_path does not exist.
    ValueError
        If the GeoTIFF is not in EPSG:4326 or preset is unknown.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    if preset not in PRESETS:
        msg = f'Unknown preset {preset!r}, choose from: {", ".join(PRESETS)}'
        raise ValueError(msg)

    if not input_path.exists():
        msg = f'{input_path} not found'
        raise FileNotFoundError(msg)

    # Merge preset defaults with overrides
    cfg = {**PRESETS[preset]}
    for key, val in overrides.items():
        if val is not None:
            cfg[key] = val

    data_type = cfg['data_type']
    signed = cfg['signed']
    wordsize = cfg['wordsize']
    tile_bdr = cfg['tile_bdr']
    missing_value = cfg['missing_value']
    scale_factor = cfg.get('scale_factor')
    units = cfg.get('units', '')
    description = cfg.get('description', '')
    category_min = cfg.get('category_min')
    category_max = cfg.get('category_max')

    np_dtype, be_dtype = _get_numpy_dtype(wordsize, signed)

    output_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(input_path) as src:
        # Validate CRS
        if src.crs is None or src.crs.to_epsg() != 4326:
            msg = f'GeoTIFF must be in EPSG:4326, got {src.crs}'
            raise ValueError(msg)

        height = src.height
        width = src.width
        transform = src.transform
        nodata_value = src.nodata

        # Grid geometry
        dx = abs(transform.a)
        dy = abs(transform.e)

        # Bottom-left pixel centre (WPS known point)
        known_lon = transform.c + dx / 2
        known_lat = transform.f - (height * dy) + dy / 2

        # Tile sizes (always evenly divide — fallback is full axis)
        tile_x = find_tile_size(width)
        tile_y = find_tile_size(height)
        n_tiles_x = width // tile_x
        n_tiles_y = height // tile_y
        total_tiles = n_tiles_x * n_tiles_y

        n_digits = 5 if max(width, height) < 100000 else 6
        fmt = f'{{:0{n_digits}d}}'
        fmt_filename = f'{fmt}-{fmt}.{fmt}-{fmt}'

        print(f'Input: {width}x{height} pixels, dx={dx:.10f} dy={dy:.10f} deg')
        print(f'Preset: {preset}, type={data_type}, wordsize={wordsize}')
        print(f'Tiles: {n_tiles_x}x{n_tiles_y} tiles of {tile_x}x{tile_y} pixels')

        # Auto-detect category_max for categorical data
        if data_type == 'categorical' and category_max is None:
            # Read min block to find max category value
            cat_max = 0
            for ty in range(n_tiles_y):
                for tx in range(n_tiles_x):
                    tif_row_start = height - (ty + 1) * tile_y
                    tif_col_start = tx * tile_x
                    window = Window(
                        col_off=tif_col_start, row_off=tif_row_start,
                        width=tile_x, height=tile_y,
                    )
                    block = src.read(1, window=window)
                    if nodata_value is not None:
                        block = block[block != nodata_value]
                    if block.size > 0:
                        cat_max = max(cat_max, int(np.max(block)))
            category_max = cat_max
            print(f'Auto-detected category_max: {category_max}')

        # Write tiles
        tile_count = 0
        for ty in range(n_tiles_y):
            for tx in range(n_tiles_x):
                tif_row_start = height - (ty + 1) * tile_y
                tif_col_start = tx * tile_x

                tile_data = _read_tile(
                    src, tif_row_start, tif_col_start,
                    tile_y, tile_x, nodata_value,
                    tile_bdr=tile_bdr,
                    missing_value=missing_value,
                    scale_factor=scale_factor,
                    np_dtype=np_dtype,
                )

                # WPS tile naming (1-indexed)
                wps_x_start = tx * tile_x + 1
                wps_x_end = (tx + 1) * tile_x
                wps_y_start = ty * tile_y + 1
                wps_y_end = (ty + 1) * tile_y

                tile_name = fmt_filename.format(
                    wps_x_start, wps_x_end, wps_y_start, wps_y_end)

                with open(output_dir / tile_name, 'wb') as f:
                    f.write(tile_data.astype(be_dtype).tobytes())

                tile_count += 1
                if tile_count % 10 == 0 or tile_count == total_tiles:
                    print(f'  Written {tile_count}/{total_tiles} tiles',
                          end='\r')

        print()

    # Write index file
    lines = [
        f'type = {data_type}',
        f'signed = {"yes" if signed else "no"}',
        f'projection = regular_ll',
        f'dx = {dx:.10f}',
        f'dy = {dy:.10f}',
        f'known_x = 1.0',
        f'known_y = 1.0',
        f'known_lat = {known_lat:.10f}',
        f'known_lon = {known_lon:.10f}',
        f'wordsize = {wordsize}',
        f'endian = big',
        f'row_order = bottom_top',
        f'tile_x = {tile_x}',
        f'tile_y = {tile_y}',
        f'tile_z = 1',
        f'tile_bdr = {tile_bdr}',
        f'missing_value = {missing_value}',
    ]

    if scale_factor is not None:
        lines.append(f'scale_factor = {scale_factor}')

    if data_type == 'categorical':
        if category_min is not None:
            lines.append(f'category_min = {category_min}')
        if category_max is not None:
            lines.append(f'category_max = {category_max}')

    if units:
        lines.append(f'units = "{units}"')
    if description:
        lines.append(f'description = "{description}"')

    index_path = output_dir / 'index'
    with open(index_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')

    print(f'Index file: {index_path}')
    print(f'Done: {total_tiles} tiles written to {output_dir}')
