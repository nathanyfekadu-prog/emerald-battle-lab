from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emulator.game_state import WholeGameStateReader
from emulator.mgba_instance import MGBAInstance
from emulator.overworld import OverworldMover


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--route", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    directions = json.loads(Path(args.route).read_text(encoding="utf-8"))["directions"]
    with MGBAInstance(args.rom, args.state, 955) as instance:
        mover = OverworldMover(instance)
        reader = WholeGameStateReader(instance)
        for direction in directions:
            mover.step(str(direction).upper())
            instance.advance_frames(30)
        arrived = reader.read()
        if arrived.map_id != (6, 4) or (arrived.x, arrived.y) != (14, 15):
            raise RuntimeError(f"Center route ended at {arrived.map_id} {(arrived.x, arrived.y)}")
        mover.walk("UP", 4)
        for _ in range(10):
            mover.tap("A", settle_frames=100)
        instance.advance_frames(180)
        healed = reader.read()
        if tuple(healed.player_hp) != tuple(healed.player_max_hp):
            raise RuntimeError(f"Nurse did not heal the party: {healed.player_hp}")
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        instance.save_state(output)
        print({"output": str(output), "hp": healed.player_hp, "position": [healed.x, healed.y]})


if __name__ == "__main__":
    main()
