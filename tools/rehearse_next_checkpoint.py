"""Drive a won checkpoint through the Center and up to the next battle command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emulator.game_state import GameMode, WholeGameStateReader
from emulator.mgba_instance import MGBAInstance
from emulator.overworld import OverworldMover


def _directions(path: str) -> list[str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [str(value).upper() for value in payload["directions"]]


def _walk(mover: OverworldMover, instance: MGBAInstance, directions: list[str]) -> None:
    for direction in directions:
        mover.step(direction)
        instance.advance_frames(30)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--to-center", required=True)
    parser.add_argument("--to-trainer", required=True)
    parser.add_argument("--trainer", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--final-direction", choices=("UP", "DOWN", "LEFT", "RIGHT"))
    args = parser.parse_args()

    with MGBAInstance(args.rom, args.state, 94) as instance:
        reader = WholeGameStateReader(instance)
        mover = OverworldMover(instance)
        _walk(mover, instance, _directions(args.to_center))
        arrived = reader.read()
        if arrived.map_id != (6, 4) or (arrived.x, arrived.y) != (14, 15):
            raise RuntimeError(f"Center route ended at {arrived.map_id} {(arrived.x, arrived.y)}")

        mover.walk("UP", 4)
        for _ in range(6):
            mover.tap("A", settle_frames=70)
        healed = reader.read()
        if tuple(healed.player_hp) != tuple(healed.player_max_hp):
            raise RuntimeError(f"Nurse did not heal the party: {healed.player_hp}")

        _walk(mover, instance, _directions(args.to_trainer))
        if args.final_direction:
            mover.step(args.final_direction)
            instance.advance_frames(30)
        snapshot = reader.read()
        for index in range(180):
            if snapshot.mode == GameMode.BATTLE_COMMAND:
                break
            instance.send_input("B" if index % 3 else "A", 1)
            instance.advance_frames(18)
            snapshot = reader.read()
        else:
            raise RuntimeError("trainer dialogue did not reach a battle command")
        if args.trainer.casefold() not in (snapshot.trainer_name or "").casefold():
            raise RuntimeError(f"expected {args.trainer!r}, got {snapshot.trainer_name!r}")
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        instance.save_state(output)
        print(json.dumps({
            "output": str(output), "trainer": snapshot.trainer_name,
            "player_hp": list(snapshot.player_hp), "map": list(snapshot.map_id or ()),
            "position": [snapshot.x, snapshot.y],
        }, indent=2))


if __name__ == "__main__":
    main()
