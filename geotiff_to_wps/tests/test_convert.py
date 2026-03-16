"""Tests for geotiff_to_wps.convert."""
import subprocess
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from geotiff_to_wps import convert
from geotiff_to_wps.presets import PRESETS

# DEM preset defaults for existing tests
DEM = PRESETS['dem']
MISSING_VALUE = DEM['missing_value']
TILE_BDR = DEM['tile_bdr']


def create_test_geotiff(path, width, height, west, south, east, north,
                         data=None, nodata=None, dtype='int16'):
    """Create a small GeoTIFF in EPSG:4326."""
    transform = from_bounds(west, south, east, north, width, height)
    if data is None:
        data = np.arange(height, 0, -1, dtype=np.int16).reshape(-1, 1)
        data = np.broadcast_to(data, (height, width)).copy()

    with rasterio.open(
        path, 'w', driver='GTiff', width=width, height=height,
        count=1, dtype=dtype, crs='EPSG:4326', transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)


def read_wps_tile(path, tile_h, tile_w, wordsize=2, signed=True):
    """Read a WPS binary tile into a numpy array."""
    raw = path.read_bytes()
    expected_size = tile_h * tile_w * wordsize
    assert len(raw) == expected_size, f'{path.name}: {len(raw)} != {expected_size}'

    key = (wordsize, signed)
    dtypes = {
        (1, False): '>u1', (1, True): '>i1',
        (2, False): '>u2', (2, True): '>i2',
        (4, False): '>u4', (4, True): '>i4',
    }
    return np.frombuffer(raw, dtype=dtypes[key]).reshape(tile_h, tile_w)


def parse_index(path):
    """Parse a WPS index file into a dict."""
    result = {}
    for line in path.read_text().splitlines():
        if '=' in line:
            key, val = line.split('=', 1)
            val = val.strip().strip('"')
            result[key.strip()] = val
    return result


class TestSingleTile:
    """Test conversion of a small GeoTIFF that fits in one tile."""

    @pytest.fixture()
    def converted(self, tmp_path):
        width, height = 20, 30
        west, south, east, north = 170.0, -46.0, 172.0, -44.0

        data = np.arange(height, 0, -1, dtype=np.int16).reshape(-1, 1)
        data = np.broadcast_to(data, (height, width)).copy()

        tif_path = tmp_path / 'test.tif'
        out_dir = tmp_path / 'wps_out'
        create_test_geotiff(tif_path, width, height, west, south, east, north,
                            data=data)
        convert(tif_path, out_dir)

        return out_dir, width, height, west, south, east, north, data

    def test_index_file_exists(self, converted):
        out_dir = converted[0]
        assert (out_dir / 'index').exists()

    def test_index_fields(self, converted):
        out_dir, width, height, west, south, east, north, _ = converted
        idx = parse_index(out_dir / 'index')

        dx = (east - west) / width
        dy = (north - south) / height

        assert idx['type'] == 'continuous'
        assert idx['signed'] == 'yes'
        assert idx['projection'] == 'regular_ll'
        assert idx['wordsize'] == '2'
        assert idx['endian'] == 'big'
        assert idx['row_order'] == 'bottom_top'
        assert idx['tile_x'] == str(width)
        assert idx['tile_y'] == str(height)
        assert idx['tile_z'] == '1'
        assert idx['tile_bdr'] == str(TILE_BDR)
        assert idx['missing_value'] == str(MISSING_VALUE)
        assert float(idx['dx']) == pytest.approx(dx)
        assert float(idx['dy']) == pytest.approx(dy)

        expected_lat = south + dy / 2
        expected_lon = west + dx / 2
        assert float(idx['known_lat']) == pytest.approx(expected_lat)
        assert float(idx['known_lon']) == pytest.approx(expected_lon)

    def test_single_tile_file(self, converted):
        out_dir, width, height = converted[0], converted[1], converted[2]
        tile_name = f'00001-{width:05d}.00001-{height:05d}'
        assert (out_dir / tile_name).exists()

    def test_tile_size(self, converted):
        out_dir, width, height = converted[0], converted[1], converted[2]
        tile_name = f'00001-{width:05d}.00001-{height:05d}'
        tile_h = height + 2 * TILE_BDR
        tile_w = width + 2 * TILE_BDR
        expected_bytes = tile_h * tile_w * 2
        actual_bytes = (out_dir / tile_name).stat().st_size
        assert actual_bytes == expected_bytes

    def test_row_order_is_bottom_top(self, converted):
        """Verify that WPS row 1 (bottom of tile) is the southern data."""
        out_dir, width, height = converted[0], converted[1], converted[2]
        tile_name = f'00001-{width:05d}.00001-{height:05d}'
        tile_h = height + 2 * TILE_BDR
        tile_w = width + 2 * TILE_BDR
        tile = read_wps_tile(out_dir / tile_name, tile_h, tile_w)

        core = tile[TILE_BDR:-TILE_BDR, TILE_BDR:-TILE_BDR]

        assert core[0, 0] == 1, f'Bottom row should be 1, got {core[0, 0]}'
        assert core[-1, 0] == height, f'Top row should be {height}, got {core[-1, 0]}'

    def test_border_padding(self, converted):
        """Border pixels beyond data edges should be MISSING_VALUE."""
        out_dir, width, height = converted[0], converted[1], converted[2]
        tile_name = f'00001-{width:05d}.00001-{height:05d}'
        tile_h = height + 2 * TILE_BDR
        tile_w = width + 2 * TILE_BDR
        tile = read_wps_tile(out_dir / tile_name, tile_h, tile_w)

        assert np.all(tile[-TILE_BDR:, :] == MISSING_VALUE)
        assert np.all(tile[:TILE_BDR, :] == MISSING_VALUE)
        assert np.all(tile[:, :TILE_BDR] == MISSING_VALUE)
        assert np.all(tile[:, -TILE_BDR:] == MISSING_VALUE)


class TestMultipleTiles:
    """Test conversion with data large enough for multiple tiles."""

    @pytest.fixture()
    def converted(self, tmp_path):
        width, height = 2000, 3000
        west, south, east, north = 170.0, -46.0, 172.0, -43.0

        data = np.random.RandomState(42).randint(
            0, 2000, size=(height, width), dtype=np.int16)

        tif_path = tmp_path / 'test_multi.tif'
        out_dir = tmp_path / 'wps_out'
        create_test_geotiff(tif_path, width, height, west, south, east, north,
                            data=data)
        convert(tif_path, out_dir)

        return out_dir, width, height, data

    def test_multiple_tile_files(self, converted):
        out_dir = converted[0]
        idx = parse_index(out_dir / 'index')
        tile_x = int(idx['tile_x'])
        tile_y = int(idx['tile_y'])
        width, height = converted[1], converted[2]

        n_tiles_x = width // tile_x
        n_tiles_y = height // tile_y
        expected_files = n_tiles_x * n_tiles_y

        tile_files = [f for f in out_dir.iterdir() if f.name != 'index']
        assert len(tile_files) == expected_files

    def test_tile_data_matches_input(self, converted):
        """Reconstruct full grid from tiles and compare to input."""
        out_dir, width, height, input_data = converted
        idx = parse_index(out_dir / 'index')
        tile_x = int(idx['tile_x'])
        tile_y = int(idx['tile_y'])

        n_tiles_x = width // tile_x
        n_tiles_y = height // tile_y

        reconstructed = np.empty((height, width), dtype=np.int16)

        for ty in range(n_tiles_y):
            for tx in range(n_tiles_x):
                x_start = tx * tile_x + 1
                x_end = (tx + 1) * tile_x
                y_start = ty * tile_y + 1
                y_end = (ty + 1) * tile_y

                tile_name = f'{x_start:05d}-{x_end:05d}.{y_start:05d}-{y_end:05d}'
                tile_h = tile_y + 2 * TILE_BDR
                tile_w = tile_x + 2 * TILE_BDR
                tile = read_wps_tile(out_dir / tile_name, tile_h, tile_w)

                core = tile[TILE_BDR:-TILE_BDR, TILE_BDR:-TILE_BDR]

                row_start = height - (ty + 1) * tile_y
                row_end = height - ty * tile_y
                reconstructed[row_start:row_end, :] = core[::-1]

        np.testing.assert_array_equal(reconstructed, input_data)


class TestNodata:
    """Test that nodata values are replaced with MISSING_VALUE."""

    def test_nodata_replacement(self, tmp_path):
        width, height = 10, 10
        nodata = -32768
        data = np.full((height, width), 500, dtype=np.int16)
        data[0, 0] = nodata
        data[9, 9] = nodata

        tif_path = tmp_path / 'nodata.tif'
        out_dir = tmp_path / 'wps_out'
        create_test_geotiff(tif_path, width, height, 170, -46, 171, -45,
                            data=data, nodata=nodata)
        convert(tif_path, out_dir)

        tile_name = f'00001-{width:05d}.00001-{height:05d}'
        tile_h = height + 2 * TILE_BDR
        tile_w = width + 2 * TILE_BDR
        tile = read_wps_tile(out_dir / tile_name, tile_h, tile_w)
        core = tile[TILE_BDR:-TILE_BDR, TILE_BDR:-TILE_BDR]

        assert core[height - 1, 0] == MISSING_VALUE
        assert core[0, 9] == MISSING_VALUE
        assert core[5, 5] == 500


class TestValidation:
    """Test error handling."""

    def test_wrong_crs(self, tmp_path):
        tif_path = tmp_path / 'wrong_crs.tif'
        out_dir = tmp_path / 'wps_out'
        data = np.ones((10, 10), dtype=np.int16)
        transform = from_bounds(0, 0, 100000, 100000, 10, 10)

        with rasterio.open(
            tif_path, 'w', driver='GTiff', width=10, height=10,
            count=1, dtype='int16', crs='EPSG:2193', transform=transform,
        ) as dst:
            dst.write(data, 1)

        with pytest.raises(ValueError, match='EPSG:4326'):
            convert(tif_path, out_dir)

    def test_missing_input(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            convert(tmp_path / 'nonexistent.tif', tmp_path / 'out')

    def test_unknown_preset(self, tmp_path):
        with pytest.raises(ValueError, match='Unknown preset'):
            convert(tmp_path / 'dummy.tif', tmp_path / 'out', preset='bogus')


class TestCategorical:
    """Test categorical data conversion (e.g., land use)."""

    def test_landuse_preset(self, tmp_path):
        width, height = 10, 10
        data = np.array([[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]] * 10, dtype=np.uint8)

        tif_path = tmp_path / 'landuse.tif'
        out_dir = tmp_path / 'wps_out'
        create_test_geotiff(tif_path, width, height, 170, -46, 171, -45,
                            data=data, dtype='uint8')
        convert(tif_path, out_dir, preset='landuse')

        idx = parse_index(out_dir / 'index')
        assert idx['type'] == 'categorical'
        assert idx['signed'] == 'no'
        assert idx['wordsize'] == '1'
        assert idx['tile_bdr'] == '0'
        assert idx['category_min'] == '1'
        assert idx['category_max'] == '10'
        assert 'scale_factor' not in idx

    def test_categorical_tile_size(self, tmp_path):
        """Tile file size should reflect wordsize=1 and tile_bdr=0."""
        width, height = 10, 10
        data = np.ones((height, width), dtype=np.uint8) * 5

        tif_path = tmp_path / 'cat.tif'
        out_dir = tmp_path / 'wps_out'
        create_test_geotiff(tif_path, width, height, 170, -46, 171, -45,
                            data=data, dtype='uint8')
        convert(tif_path, out_dir, preset='landuse')

        tile_name = f'00001-{width:05d}.00001-{height:05d}'
        # No border for categorical: tile_bdr=0
        expected_bytes = height * width * 1
        actual_bytes = (out_dir / tile_name).stat().st_size
        assert actual_bytes == expected_bytes

    def test_categorical_data_values(self, tmp_path):
        """Verify category values are preserved."""
        width, height = 10, 10
        data = np.arange(1, 11, dtype=np.uint8).reshape(1, -1)
        data = np.broadcast_to(data, (height, width)).copy()

        tif_path = tmp_path / 'cat.tif'
        out_dir = tmp_path / 'wps_out'
        create_test_geotiff(tif_path, width, height, 170, -46, 171, -45,
                            data=data, dtype='uint8')
        convert(tif_path, out_dir, preset='landuse')

        tile_name = f'00001-{width:05d}.00001-{height:05d}'
        tile = read_wps_tile(out_dir / tile_name, height, width,
                             wordsize=1, signed=False)

        # Bottom row in WPS = south = last GeoTIFF row
        assert tile[0, 0] == 1
        assert tile[0, 9] == 10


class TestScaleFactor:
    """Test continuous data with scale_factor (e.g., green fraction)."""

    def test_greenfrac_scaling(self, tmp_path):
        width, height = 10, 10
        # Float data: 0.0 to 0.9
        data = np.full((height, width), 0.5, dtype=np.float32)
        data[0, 0] = 0.0
        data[9, 9] = 0.9

        tif_path = tmp_path / 'gvf.tif'
        out_dir = tmp_path / 'wps_out'
        create_test_geotiff(tif_path, width, height, 170, -46, 171, -45,
                            data=data, dtype='float32')
        convert(tif_path, out_dir, preset='greenfrac')

        idx = parse_index(out_dir / 'index')
        assert idx['scale_factor'] == '0.001'
        assert idx['type'] == 'continuous'
        assert idx['signed'] == 'yes'
        assert idx['wordsize'] == '2'

        bdr = PRESETS['greenfrac']['tile_bdr']
        tile_name = f'00001-{width:05d}.00001-{height:05d}'
        tile_h = height + 2 * bdr
        tile_w = width + 2 * bdr
        tile = read_wps_tile(out_dir / tile_name, tile_h, tile_w)
        core = tile[bdr:-bdr, bdr:-bdr]

        # 0.5 / 0.001 = 500
        assert core[5, 5] == 500
        # 0.9 / 0.001 = 900 (GeoTIFF row 9 = south = WPS row 0)
        assert core[0, 9] == 900
        # 0.0 / 0.001 = 0 (GeoTIFF row 0 = north = WPS top row)
        assert core[height - 1, 0] == 0


class TestPresetOverride:
    """Test that individual overrides take precedence over preset defaults."""

    def test_override_units(self, tmp_path):
        width, height = 10, 10
        tif_path = tmp_path / 'test.tif'
        out_dir = tmp_path / 'wps_out'
        create_test_geotiff(tif_path, width, height, 170, -46, 171, -45)

        convert(tif_path, out_dir, preset='dem', units='feet')

        idx = parse_index(out_dir / 'index')
        assert idx['units'] == 'feet'
        # Other DEM defaults should be unchanged
        assert idx['type'] == 'continuous'
        assert idx['signed'] == 'yes'
        assert idx['wordsize'] == '2'

    def test_override_missing_value(self, tmp_path):
        width, height = 10, 10
        tif_path = tmp_path / 'test.tif'
        out_dir = tmp_path / 'wps_out'
        create_test_geotiff(tif_path, width, height, 170, -46, 171, -45)

        convert(tif_path, out_dir, preset='dem', missing_value=-32768)

        idx = parse_index(out_dir / 'index')
        assert idx['missing_value'] == '-32768'


class TestCLI:
    """Test the command-line entry point."""

    def test_help(self):
        result = subprocess.run(
            [sys.executable, '-m', 'geotiff_to_wps.cli', '--help'],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert 'GeoTIFF' in result.stdout
        assert '--preset' in result.stdout

    def test_cli_converts(self, tmp_path):
        width, height = 10, 10
        tif_path = tmp_path / 'test.tif'
        out_dir = tmp_path / 'wps_out'
        create_test_geotiff(tif_path, width, height, 170, -46, 171, -45)

        result = subprocess.run(
            [sys.executable, '-m', 'geotiff_to_wps.cli',
             str(tif_path), str(out_dir)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert (out_dir / 'index').exists()

    def test_cli_preset(self, tmp_path):
        width, height = 10, 10
        data = np.ones((height, width), dtype=np.uint8) * 3
        tif_path = tmp_path / 'lu.tif'
        out_dir = tmp_path / 'wps_out'
        create_test_geotiff(tif_path, width, height, 170, -46, 171, -45,
                            data=data, dtype='uint8')

        result = subprocess.run(
            [sys.executable, '-m', 'geotiff_to_wps.cli',
             '--preset', 'landuse', str(tif_path), str(out_dir)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        idx = parse_index(out_dir / 'index')
        assert idx['type'] == 'categorical'

    def test_cli_error_exit_code(self, tmp_path):
        result = subprocess.run(
            [sys.executable, '-m', 'geotiff_to_wps.cli',
             str(tmp_path / 'nope.tif'), str(tmp_path / 'out')],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert 'Error' in result.stderr
