from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from emulator.mgba_instance import MGBAInstance


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe live mGBA RAM addresses.")
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--address", type=lambda value: int(value, 0), default=0x02023AEE)
    parser.add_argument("--count", type=int, default=16)
    parser.add_argument("--width", choices=("u8", "u16", "u32"), default="u16")
    args = parser.parse_args()

    step = {"u8": 1, "u16": 2, "u32": 4}[args.width]
    reader_name = f"read_{args.width}"
    with MGBAInstance(args.rom, args.state, 0) as instance:
        read = getattr(instance, reader_name)
        for index in range(args.count):
            address = args.address + index * step
            print(f"{address:#010x}: {read(address)}")


if __name__ == "__main__":
    main()
