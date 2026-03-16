"""Command-line interface for geotiff-to-wps."""
import argparse
import sys

from geotiff_to_wps.convert import convert
from geotiff_to_wps.presets import PRESETS


def main():
    parser = argparse.ArgumentParser(
        description='Convert a GeoTIFF (EPSG:4326) to WPS geogrid binary format.',
    )
    parser.add_argument('input', help='Path to input GeoTIFF file (must be EPSG:4326)')
    parser.add_argument('output', help='Output directory for WPS binary tiles and index file')
    parser.add_argument(
        '-p', '--preset', default='dem', choices=PRESETS.keys(),
        help='Preset for common data types (default: dem)',
    )

    # Override options
    parser.add_argument('--data-type', choices=['continuous', 'categorical'], default=None)
    parser.add_argument('--signed', dest='signed', action='store_true', default=None)
    parser.add_argument('--unsigned', dest='signed', action='store_false')
    parser.add_argument('--wordsize', type=int, choices=[1, 2, 4], default=None)
    parser.add_argument('--tile-bdr', type=int, default=None)
    parser.add_argument('--missing-value', type=int, default=None)
    parser.add_argument('--scale-factor', type=float, default=None)
    parser.add_argument('--units', default=None)
    parser.add_argument('--description', default=None)
    parser.add_argument('--category-min', type=int, default=None)
    parser.add_argument('--category-max', type=int, default=None)

    args = parser.parse_args()

    # Build overrides dict from explicitly provided options
    overrides = {}
    for key in ['data_type', 'signed', 'wordsize', 'tile_bdr', 'missing_value',
                'scale_factor', 'units', 'description', 'category_min', 'category_max']:
        val = getattr(args, key)
        if val is not None:
            overrides[key] = val

    try:
        convert(args.input, args.output, preset=args.preset, **overrides)
    except (FileNotFoundError, ValueError) as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
