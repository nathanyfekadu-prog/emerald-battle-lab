#!/usr/bin/env python3
"""Render vanilla Emerald map layouts into click-ready PNG map floors.

The renderer consumes the public pret/pokeemerald decompilation assets. It keeps
the app independent of an external image host and preserves the exact 16 px map
tile geometry used by trainer object events.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path

from PIL import Image


def _words(path: Path) -> tuple[int, ...]:
    data = path.read_bytes()
    return struct.unpack(f"<{len(data) // 2}H", data)


def _palette(path: Path) -> list[tuple[int, int, int, int]]:
    lines = path.read_text(encoding="utf-8").splitlines()[3:19]
    colors = [tuple(map(int, line.split())) + (255,) for line in lines]
    return colors + [(0, 0, 0, 255)] * (16 - len(colors))


def _tiles(image_path: Path) -> list[Image.Image]:
    sheet = Image.open(image_path)
    return [
        sheet.crop((x, y, x + 8, y + 8))
        for y in range(0, sheet.height, 8)
        for x in range(0, sheet.width, 8)
    ]


def _tileset_paths(root: Path) -> dict[str, Path]:
    graphics = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src/data/tilesets/graphics.h", root / "src/graphics.c")
    )
    matches = re.findall(
        r"const u32 gTilesetTiles_(\w+)\[\].*?\(\"(data/tilesets/[^\"]+/tiles\.png)\"",
        graphics,
    )
    tile_symbols = {f"gTilesetTiles_{name}": root / relative for name, relative in matches}
    headers = (root / "src/data/tilesets/headers.h").read_text(encoding="utf-8")
    paths: dict[str, Path] = {}
    for name, body in re.findall(
        r"const struct Tileset (gTileset_\w+)\s*=\s*\{(.*?)\n\};",
        headers,
        flags=re.S,
    ):
        tile_match = re.search(r"\.tiles\s*=\s*(gTilesetTiles_\w+)", body)
        if tile_match and tile_match.group(1) in tile_symbols:
            paths[name] = tile_symbols[tile_match.group(1)]
    return paths


def _render_tile(
    tile: Image.Image,
    palette: list[tuple[int, int, int, int]],
    *,
    hflip: bool,
    vflip: bool,
    transparent_zero: bool,
) -> Image.Image:
    if hflip:
        tile = tile.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if vflip:
        tile = tile.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    out = Image.new("RGBA", (8, 8))
    pixels = []
    source = tile.load()
    for y in range(8):
        for x in range(8):
            value = source[x, y]
            red, green, blue, alpha = palette[int(value) & 15]
            pixels.append((red, green, blue, 0 if transparent_zero and int(value) == 0 else alpha))
    out.putdata(pixels)
    return out


def render_layout(root: Path, layout: dict, tileset_paths: dict[str, Path]) -> Image.Image:
    primary_tiles_path = tileset_paths[layout["primary_tileset"]]
    secondary_tiles_path = tileset_paths[layout["secondary_tileset"]]
    primary_dir, secondary_dir = primary_tiles_path.parent, secondary_tiles_path.parent
    primary_tiles, secondary_tiles = _tiles(primary_tiles_path), _tiles(secondary_tiles_path)
    primary_metatiles = _words(primary_dir / "metatiles.bin")
    secondary_metatiles = _words(secondary_dir / "metatiles.bin")
    # Secondary palette files represent the final 16-bank palette after the
    # primary and map-specific tilesets are combined by the game.
    palettes = [_palette(secondary_dir / f"palettes/{index:02}.pal") for index in range(16)]
    blocks = _words(root / layout["blockdata_filepath"])
    width, height = int(layout["width"]), int(layout["height"])
    if len(blocks) < width * height:
        raise ValueError(f"{layout['name']} has too little block data")

    canvas = Image.new("RGBA", (width * 16, height * 16), (0, 0, 0, 255))
    for map_y in range(height):
        for map_x in range(width):
            metatile_id = blocks[map_y * width + map_x] & 0x03FF
            if metatile_id < 512:
                entries = primary_metatiles[metatile_id * 8 : metatile_id * 8 + 8]
            else:
                local_id = metatile_id - 512
                entries = secondary_metatiles[local_id * 8 : local_id * 8 + 8]
            if len(entries) != 8:
                continue
            for layer in range(2):
                for subtile in range(4):
                    value = entries[layer * 4 + subtile]
                    tile_id = value & 0x03FF
                    tile = primary_tiles[tile_id] if tile_id < 512 else secondary_tiles[tile_id - 512]
                    rendered = _render_tile(
                        tile,
                        palettes[(value >> 12) & 15],
                        hflip=bool(value & 0x0400),
                        vflip=bool(value & 0x0800),
                        transparent_zero=layer == 1,
                    )
                    px = map_x * 16 + (subtile % 2) * 8
                    py = map_y * 16 + (subtile // 2) * 8
                    canvas.alpha_composite(rendered, (px, py))
    return canvas.convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("decomp", type=Path, help="Path to a pret/pokeemerald checkout")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path("data/emerald_trainers.json"))
    parser.add_argument("--map", dest="only_map", help="Render only one map folder, e.g. Route102")
    args = parser.parse_args()

    root = args.decomp.resolve()
    layouts = json.loads((root / "data/layouts/layouts.json").read_text(encoding="utf-8"))["layouts"]
    by_id = {layout["id"]: layout for layout in layouts}
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    map_names = sorted({
        event["map_name"]
        for trainer in catalog["trainers"]
        if (event := trainer.get("map_event")) and event.get("map_name")
    })
    if args.only_map:
        map_names = [args.only_map]
    tileset_paths = _tileset_paths(root)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, object]] = {}
    for map_name in map_names:
        map_json_path = root / "data/maps" / map_name / "map.json"
        if not map_json_path.exists():
            continue
        map_data = json.loads(map_json_path.read_text(encoding="utf-8"))
        layout = by_id.get(map_data["layout"])
        if not layout:
            continue
        image = render_layout(root, layout, tileset_paths)
        filename = f"{map_name}.png"
        image.save(args.output / filename, optimize=True)
        manifest[map_name] = {
            "image": f"/static/emerald-maps/{filename}",
            "width": int(layout["width"]),
            "height": int(layout["height"]),
            "pixel_width": image.width,
            "pixel_height": image.height,
        }
        print(f"rendered {map_name}: {image.width}x{image.height}")
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
