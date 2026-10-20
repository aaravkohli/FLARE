#!/usr/bin/env python3
"""Build the offline mission backdrop from the committed USGS 3DEP GeoTIFF.

Requires Pillow only at asset-generation time. Runtime terrain decoding uses no
network request or image decoder, so the GCS, relief and shadows share one height
function from the first rendered frame.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import struct
from pathlib import Path

from PIL import Image


SOURCE_URL = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/"
    "3DEPElevation/ImageServer/exportImage"
)
REQUEST_BOUNDS = {"west": -105.232, "south": 40.008, "east": -105.204, "north": 40.032}
QUANTIZATION_M = 0.01
MAX_WORLD_HEIGHT_M = 17.0
EDGE_BLEND_M = 150.0


def metres_per_degree(latitude_deg: float) -> tuple[float, float]:
    latitude = math.radians(latitude_deg)
    north = 111132.92 - 559.82 * math.cos(2 * latitude) + 1.175 * math.cos(4 * latitude)
    east = 111412.84 * math.cos(latitude) - 93.5 * math.cos(3 * latitude)
    return east, north


def fnv1a32(data: bytes) -> str:
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return f"{value:08x}"


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def value_noise(x: int, y: int, period: int) -> float:
    """Deterministic smooth patches without striping or a random seed."""
    fx, fy = x / period, y / period
    ix, iy = math.floor(fx), math.floor(fy)
    tx, ty = fx - ix, fy - iy
    tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)

    def node(px: int, py: int) -> float:
        value = (px * 374761393 + py * 668265263) & 0xFFFFFFFF
        value = ((value ^ (value >> 13)) * 1274126177) & 0xFFFFFFFF
        return (value ^ (value >> 16)) / 0xFFFFFFFF * 2 - 1

    north = node(ix, iy) * (1 - tx) + node(ix + 1, iy) * tx
    south = node(ix, iy + 1) * (1 - tx) + node(ix + 1, iy + 1) * tx
    return north * (1 - ty) + south * ty


def make_surface_maps(
    heights: list[float], columns: int, rows: int, dx_m: float, dz_m: float,
    minimum: float, maximum: float, output: Path,
) -> None:
    """Illustrative cover and fine material detail, never aerial photography.

    Elevation samples are not resampled or rewritten. Only the visual maps are
    enlarged; cover variation is synthetic and must not be treated as land data.
    """
    albedo = Image.new("RGB", (columns, rows))
    normal = Image.new("RGB", (columns, rows))
    roughness = Image.new("L", (columns, rows))
    ap, np, rp = albedo.load(), normal.load(), roughness.load()

    def sample(x: int, y: int) -> float:
        return heights[min(rows - 1, max(0, y)) * columns + min(columns - 1, max(0, x))]

    span = max(maximum - minimum, 1)
    edge_fade_m = 220.0
    for row in range(rows):
        for column in range(columns):
            elevation = sample(column, row)
            east_slope = (sample(column + 1, row) - sample(column - 1, row)) / (2 * dx_m)
            south_slope = (sample(column, row + 1) - sample(column, row - 1)) / (2 * dz_m)
            slope = math.hypot(east_slope, south_slope)
            elevation_ratio = (elevation - minimum) / span
            grain = (
                value_noise(column, row, 23) * 0.55
                + value_noise(column, row, 9) * 0.3
                + value_noise(column, row, 3) * 0.15
            )
            rock = clamp((slope - 0.16) * 2.4 + (elevation_ratio - 0.68) * 0.8)
            vegetation = clamp(0.64 - slope * 1.3 - elevation_ratio * 0.2 + grain * 0.42)
            soil = (166, 153, 125)
            scrub = (132, 145, 111)
            stone = (172, 169, 151)
            rgb = tuple(
                round(clamp((soil[channel] * (1 - vegetation) + scrub[channel] * vegetation)
                      * (1 - rock) + stone[channel] * rock + grain * 14, 0, 255))
                for channel in range(3)
            )
            # This is the complete albedo, not a multiplier for vertex colors.
            # Match the outer ring's linear-light vertex palette at the seam.
            edge_distance = min(
                column * dx_m, (columns - 1 - column) * dx_m,
                row * dz_m, (rows - 1 - row) * dz_m,
            )
            blend = clamp(edge_distance / edge_fade_m)
            blend = blend * blend * (3 - 2 * blend)
            x = (column - (columns - 1) / 2) * dx_m
            z = (row - (rows - 1) / 2) * dz_m
            representative = (13 * math.exp(-((x + 220)**2 / 65000 + (z + 140)**2 / 19000))
                              + 8 * math.exp(-((x - 260)**2 / 24000 + (z - 80)**2 / 43000)) - 3)
            edge_weight = clamp(edge_distance / EDGE_BLEND_M)
            edge_weight = edge_weight * edge_weight * (3 - 2 * edge_weight)
            local_height = representative + (elevation - maximum + MAX_WORLD_HEIGHT_M - representative) * edge_weight
            height_mix = clamp((local_height + 52) / 70)
            stone_mix = clamp((1 - 1 / math.sqrt(1 + slope * slope)) * 2.8, 0, .48)

            def linear(channel: int) -> float:
                c = channel / 255
                return c / 12.92 if c <= .04045 else ((c + .055) / 1.055)**2.4

            def srgb(channel: float) -> int:
                c = channel * 12.92 if channel <= .0031308 else 1.055 * channel**(1 / 2.4) - .055
                return round(clamp(c) * 255)

            low, high, outer_stone = (137, 147, 118), (169, 160, 132), (166, 165, 149)
            edge_rgb = tuple(srgb((linear(low[c]) * (1 - height_mix) + linear(high[c]) * height_mix)
                                 * (1 - stone_mix) + linear(outer_stone[c]) * stone_mix) for c in range(3))
            ap[column, row] = tuple(round(edge_rgb[c] * (1 - blend) + rgb[c] * blend) for c in range(3))
            # Small normal-map influence supplements the actual displaced geometry.
            nx, ny, nz = -east_slope * 0.16, -south_slope * 0.16, 1.0
            magnitude = math.sqrt(nx * nx + ny * ny + nz * nz)
            np[column, row] = (
                round(128 + ((nx / magnitude * 0.5 + 0.5) * 255 - 128) * blend),
                round(128 + ((ny / magnitude * 0.5 + 0.5) * 255 - 128) * blend),
                round(255 + ((nz / magnitude * 0.5 + 0.5) * 255 - 255) * blend),
            )
            target_roughness = 255 * clamp(0.91 + 0.06 * vegetation - 0.04 * rock, 0, 1)
            rp[column, row] = round(255 + (target_roughness - 255) * blend)

    # A second scale of subtle, static soil grain keeps close views readable.
    # It changes material response only, not the source relief or its checksum.
    size = 1024
    albedo = albedo.resize((size, size), Image.Resampling.BICUBIC)
    normal = normal.resize((size, size), Image.Resampling.BICUBIC)
    roughness = roughness.resize((size, size), Image.Resampling.BICUBIC)
    ap, np, rp = albedo.load(), normal.load(), roughness.load()
    for row in range(size):
        for column in range(size):
            distance = min(column * dx_m * (columns - 1) / (size - 1),
                           (size - 1 - column) * dx_m * (columns - 1) / (size - 1),
                           row * dz_m * (rows - 1) / (size - 1),
                           (size - 1 - row) * dz_m * (rows - 1) / (size - 1))
            weight = clamp(distance / edge_fade_m)
            weight = weight * weight * (3 - 2 * weight)
            grain = value_noise(column, row, 4) * 3.5 + value_noise(column, row, 1) * 2.5
            ap[column, row] = tuple(round(clamp(c + grain * weight, 0, 255)) for c in ap[column, row])
            nx, ny, nz = np[column, row]
            np[column, row] = (round(clamp(nx + grain * 2.5 * weight, 0, 255)),
                               round(clamp(ny + value_noise(column + 77, row + 31, 2) * 10 * weight, 0, 255)), nz)
            rp[column, row] = round(clamp(rp[column, row] + grain * weight, 0, 255))
    albedo.save(output / "terrain-albedo.png", optimize=True)
    normal.save(output / "terrain-normal.png", optimize=True)
    roughness.save(output / "terrain-roughness.png", optimize=True)


def build(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        if image.mode != "F":
            raise ValueError(f"Expected 32-bit float DEM, got {image.mode}")
        columns, rows = image.size
        if columns != 257 or rows != 257:
            raise ValueError(f"Expected 257×257 source grid, got {columns}×{rows}")
        pixel_scale = image.tag_v2.get(33550)
        tiepoint = image.tag_v2.get(33922)
        geokeys = image.tag_v2.get(34735)
        if pixel_scale is None or tiepoint is None or geokeys is None:
            raise ValueError("Source lacks GeoTIFF pixel-scale or tiepoint tags")
        keys = {geokeys[index]: geokeys[index + 3] for index in range(4, len(geokeys), 4)}
        if keys.get(1025) != 1 or keys.get(2048) != 4326:
            raise ValueError("Expected PixelIsArea EPSG:4326 source GeoTIFF")
        longitude_step, latitude_step = float(pixel_scale[0]), float(pixel_scale[1])
        west_edge, north_edge = float(tiepoint[3]), float(tiepoint[4])
        if longitude_step <= 0 or latitude_step <= 0 or tuple(tiepoint[:3]) != (0, 0, 0):
            raise ValueError("Unexpected source raster orientation")
        heights = [float(value) for value in image.get_flattened_data()]

    if not all(math.isfinite(value) for value in heights):
        raise ValueError("Source DEM has missing or non-finite heights")
    minimum, maximum = min(heights), max(heights)
    source_base = math.floor(minimum / QUANTIZATION_M) * QUANTIZATION_M
    quantized = [round((value - source_base) / QUANTIZATION_M) for value in heights]
    if any(value < 0 or value > 65535 for value in quantized):
        raise ValueError("DEM elevations exceed 16-bit quantization range")
    encoded = struct.pack(f"<{len(quantized)}H", *quantized)

    # PixelIsArea tiepoints refer to the outer image corner. World coordinates
    # represent pixel centres; +X is east and +Z is south.
    west_center = west_edge + longitude_step / 2
    east_center = west_edge + (columns - 0.5) * longitude_step
    north_center = north_edge - latitude_step / 2
    south_center = north_edge - (rows - 0.5) * latitude_step
    center_latitude = (north_center + south_center) / 2
    center_longitude = (west_center + east_center) / 2
    east_per_degree, north_per_degree = metres_per_degree(center_latitude)
    width_m = (east_center - west_center) * east_per_degree
    depth_m = (north_center - south_center) * north_per_degree

    grid = {
        "columns": columns,
        "rows": rows,
        "worldWidthM": round(width_m, 6),
        "worldDepthM": round(depth_m, 6),
        "elevationBaseM": round(source_base, 2),
        "elevationStepM": QUANTIZATION_M,
        "minimumElevationM": round(minimum, 6),
        "maximumElevationM": round(maximum, 6),
        "maxWorldHeightM": MAX_WORLD_HEIGHT_M,
        "edgeBlendM": EDGE_BLEND_M,
        "encodedFnv1a32": fnv1a32(encoded),
    }
    manifest = {
        "name": "Boulder foothills 3DEP mission backdrop",
        "source": "USGS 3DEP Elevation ImageServer GeoTIFF export",
        "sourceUrl": SOURCE_URL,
        "requestBoundsWgs84": REQUEST_BOUNDS,
        "deliveredRasterBoundsWgs84": {
            "west": west_edge,
            "south": round(north_edge - rows * latitude_step, 12),
            "east": round(west_edge + columns * longitude_step, 12),
            "north": north_edge,
        },
        "sampleCenterBoundsWgs84": {
            "west": round(west_center, 12),
            "south": round(south_center, 12),
            "east": round(east_center, 12),
            "north": round(north_center, 12),
        },
        "localOriginWgs84": {"longitude": round(center_longitude, 12), "latitude": round(center_latitude, 12)},
        "crs": "EPSG:4326",
        "sourceResolutionDegrees": {"longitude": longitude_step, "latitude": latitude_step},
        "sourceResolutionApproxM": {
            "east": round(longitude_step * east_per_degree, 6),
            "south": round(latitude_step * north_per_degree, 6),
        },
        "grid": grid,
        "mapping": {
            "localX": "east, centred on the sample-centre extent",
            "localZ": "south, centred on the sample-centre extent",
            "localY": "source elevation minus maximum source elevation plus 17 m; 1:1 relief, absolute datum removed",
            "edgeTreatment": "outer 150 m blends to representative procedural terrain",
            "simulationEffect": "visual only; no RF, routing, flight, or collision calculation uses this DEM",
        },
        "surfaceMaps": "1024px illustrative dry grass, soil and stone albedo with synthetic fine grain, normals and roughness; edge palette matches the representative outer terrain; not orthoimagery or land-cover evidence",
        "sceneProps": "Deterministic illustrative research test range: eight buildings including hangars and labs, marked flight apron, operations and service yards, 32 solar panels, planted test plots, connecting roads, three launch pads, six wooded areas and a 50 m scale cue; facilities, land use, roads and vegetation are not source-mapped and have no RF, routing, flight or collision effect",
        "verticalDatum": "not asserted from delivered GeoTIFF",
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "heightmapSha256": hashlib.sha256(encoded).hexdigest(),
        "preprocessCommand": "python3 scripts/preprocess_mission_terrain.py --input src/mission-simulation/assets/terrain/source-dem.tif",
    }

    payload = base64.b64encode(encoded).decode("ascii")
    heightmap_ts = (
        "// Generated by scripts/preprocess_mission_terrain.py from source-dem.tif.\n"
        "// 1 cm unsigned little-endian elevation increments in row-major north-to-south order.\n"
        f"export const TERRAIN_GRID = {json.dumps(grid, separators=(',', ':'))} as const;\n"
        f"export const HEIGHTMAP_BASE64 = '{payload}';\n"
    )
    (output / "heightmap.ts").write_text(heightmap_ts)
    (output / "terrain-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    make_surface_maps(
        heights, columns, rows, longitude_step * east_per_degree,
        latitude_step * north_per_degree, minimum, maximum, output,
    )
    print(f"Built {columns}×{rows} terrain; {minimum:.3f}–{maximum:.3f} m; {width_m:.1f}×{depth_m:.1f} local m")
    print(f"Source SHA-256: {manifest['sourceSha256']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("src/mission-simulation/assets/terrain/source-dem.tif"))
    parser.add_argument("--output", type=Path, default=Path("src/mission-simulation/assets/terrain"))
    args = parser.parse_args()
    build(args.input, args.output)


if __name__ == "__main__":
    main()
