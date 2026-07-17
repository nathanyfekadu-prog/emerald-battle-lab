"""Create a deterministic pre-battle checkpoint after advancing the battle RNG."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emulator.mgba_instance import MGBAInstance


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with MGBAInstance(args.rom, args.state, 77) as instance:
        if args.frames:
            instance.send_input("B", args.frames)
        instance.save_state(output)
    print(output)


if __name__ == "__main__":
    main()
