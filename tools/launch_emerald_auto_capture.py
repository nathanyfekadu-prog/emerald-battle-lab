#!/usr/bin/env python3
"""Prepare and launch Pokémon Emerald's automatic mGBA battle capture setup."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = Path(__file__).with_name("emerald_auto_capture.lua.template")
WATCHER = Path(__file__).with_name("capture_battle_checkpoints.py")


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch Pokémon Emerald with automatic battle checkpoints.")
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Folder for named checkpoints")
    parser.add_argument("--mgba", default="mgba", help="mGBA executable (default: mgba)")
    args = parser.parse_args()

    rom = args.rom.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not rom.is_file():
        raise SystemExit(f"ROM not found: {rom}")
    if shutil.which(args.mgba) is None and not Path(args.mgba).is_file():
        raise SystemExit(f"mGBA not found: {args.mgba}")
    output.mkdir(parents=True, exist_ok=True)

    generated_script = output / "emerald-auto-capture.lua"
    generated_script.write_text(
        TEMPLATE.read_text(encoding="utf-8").replace("__OUTPUT_DIR__", str(output).replace("\\", "\\\\")),
        encoding="utf-8",
    )
    subprocess.Popen(
        [sys.executable, str(WATCHER), "--rom", str(rom), "--watch", str(output)],
        cwd=PROJECT_ROOT,
    )
    subprocess.Popen([args.mgba, str(rom)])
    print(f"mGBA is open and the checkpoint namer is watching {output}.")
    print("In mGBA, open Tools → Scripting, load this one-time script, then play normally:")
    print(generated_script)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
