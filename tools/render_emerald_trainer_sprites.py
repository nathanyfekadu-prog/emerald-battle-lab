#!/usr/bin/env python3
"""Extract the real Emerald overworld sprite used by each mapped trainer."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("decomp", type=Path)
    parser.add_argument("--catalog", type=Path, default=Path("data/emerald_trainers.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.decomp.resolve()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    wanted = {
        event["graphics_id"]
        for trainer in catalog["trainers"]
        if (event := trainer.get("map_event")) and event.get("graphics_id")
    }

    pointers_text = (root / "src/data/object_events/object_event_graphics_info_pointers.h").read_text(encoding="utf-8")
    info_text = (root / "src/data/object_events/object_event_graphics_info.h").read_text(encoding="utf-8")
    tables_text = (root / "src/data/object_events/object_event_pic_tables.h").read_text(encoding="utf-8")
    graphics_text = (root / "src/data/object_events/object_event_graphics.h").read_text(encoding="utf-8")
    pointer_map = dict(re.findall(r"\[(OBJ_EVENT_GFX_[A-Z0-9_]+)\]\s*=\s*&?(gObjectEventGraphicsInfo_\w+)", pointers_text))
    graphic_paths = {
        symbol: root / relative
        for symbol, relative in re.findall(
            r"const u32 (gObjectEventPic_\w+)\[\]\s*=\s*INCGFX_U32\(\"([^\"]+\.png)\"",
            graphics_text,
        )
    }
    info_blocks = {
        name: body
        for name, body in re.findall(
            r"const struct ObjectEventGraphicsInfo (gObjectEventGraphicsInfo_\w+)\s*=\s*\{(.*?)\n\};",
            info_text,
            flags=re.S,
        )
    }
    table_symbols = {
        table: symbol
        for table, symbol in re.findall(
            r"static const struct SpriteFrameImage (sPicTable_\w+)\[\]\s*=\s*\{\s*\n\s*overworld_frame\((gObjectEventPic_\w+)",
            tables_text,
        )
    }

    args.output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, object]] = {}
    for graphics_id in sorted(wanted):
        info_name = pointer_map.get(graphics_id)
        body = info_blocks.get(info_name or "", "")
        table_match = re.search(r"\.images\s*=\s*(sPicTable_\w+)", body)
        width_match = re.search(r"\.width\s*=\s*(\d+)", body)
        height_match = re.search(r"\.height\s*=\s*(\d+)", body)
        if not (table_match and width_match and height_match):
            continue
        source_path = graphic_paths.get(table_symbols.get(table_match.group(1), ""))
        if not source_path or not source_path.exists():
            continue
        width, height = int(width_match.group(1)), int(height_match.group(1))
        source = Image.open(source_path)
        frame = source.crop((0, 0, width, height)).convert("RGBA")
        indexed = source.crop((0, 0, width, height))
        rgba_pixels, indexed_pixels = frame.load(), indexed.load()
        transparent_pixels = []
        for y in range(height):
            for x in range(width):
                red, green, blue, alpha = rgba_pixels[x, y]
                transparent_pixels.append(
                    (red, green, blue, 0 if indexed_pixels[x, y] == 0 else alpha)
                )
        frame.putdata(transparent_pixels)
        filename = f"{graphics_id}.png"
        frame.save(args.output / filename, optimize=True)
        manifest[graphics_id] = {
            "image": f"/static/emerald-trainers/{filename}",
            "width": width,
            "height": height,
        }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Rendered {len(manifest)}/{len(wanted)} trainer overworld sprites")


if __name__ == "__main__":
    main()
