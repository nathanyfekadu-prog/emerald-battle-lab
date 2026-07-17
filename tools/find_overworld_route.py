"""Find a verified controller route between two live cartridge positions.

The search branches from savestates, so blocked tiles, doors, map warps, and trainer
engagements are observed from the game rather than guessed from a static map image.
"""

from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emulator.game_state import GameMode, WholeGameStateReader
from emulator.mgba_instance import MGBAInstance
from emulator.overworld import OverworldMover


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--map-group", type=int)
    parser.add_argument("--map-number", type=int)
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--trainer", help="Stop when this trainer name is recognized")
    parser.add_argument("--nodes", type=int, default=1200)
    args = parser.parse_args()
    if not args.trainer and None in (args.map_group, args.map_number, args.x, args.y):
        parser.error("provide either --trainer or the complete map-group/map-number/x/y target")

    output = Path(args.output).expanduser().resolve()
    state_dir = output.parent / f".{output.stem}-states"
    state_dir.mkdir(parents=True, exist_ok=True)
    instance = MGBAInstance(args.rom, args.state, 93)
    reader = WholeGameStateReader(instance)
    mover = OverworldMover(instance)
    try:
        root = reader.read()
        if root.mode != GameMode.OVERWORLD or root.map_id is None:
            raise ValueError("route search must start from the visible overworld")
        root_key = (*root.map_id, root.x, root.y)
        root_path = str(instance.save_state(state_dir / "root.ss0"))
        queue = deque([root_key])
        states = {root_key: root_path}
        parents: dict[tuple[int, int, int, int], tuple[tuple[int, int, int, int], str]] = {}
        target_key = None
        goal_edge: tuple[tuple[int, int, int, int], str] | None = None
        terminal_state = None
        while queue and len(states) <= args.nodes and target_key is None:
            current = queue.popleft()
            for direction in ("UP", "RIGHT", "DOWN", "LEFT"):
                instance.save_state_path = Path(states[current])
                instance.load_state()
                mover.step(direction)
                instance.advance_frames(30)
                snap = reader.read()
                key = (*snap.map_id, snap.x, snap.y) if snap.map_id is not None else None
                trainer_match = bool(
                    args.trainer
                    and args.trainer.casefold() in (snap.trainer_name or "").casefold()
                )
                coordinate_match = bool(
                    key == (args.map_group, args.map_number, args.x, args.y)
                )
                if trainer_match or coordinate_match:
                    target_key = key
                    goal_edge = (current, direction)
                    terminal_state = str(instance.save_state(state_dir / "goal.ss0"))
                    break
                if snap.mode != GameMode.OVERWORLD or key is None or key == current:
                    continue
                if key not in states:
                    path = str(instance.save_state(state_dir / f"node-{len(states):04d}.ss0"))
                    states[key] = path
                    parents[key] = (current, direction)
                    queue.append(key)
        if target_key is None:
            raise RuntimeError(f"route target not found after {len(states)} positions")
        if goal_edge is None:
            raise RuntimeError("route search found a goal without its controller edge")
        directions: list[str] = [goal_edge[1]]
        cursor = goal_edge[0]
        while cursor != root_key:
            parent, direction = parents[cursor]
            directions.append(direction)
            cursor = parent
        directions.reverse()
        payload = {
            "source": str(Path(args.state).expanduser().resolve()),
            "start": list(root_key),
            "goal": list(target_key),
            "trainer": args.trainer,
            "directions": directions,
            "positions_explored": len(states),
            "goal_state": terminal_state,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
    finally:
        instance.shutdown()


if __name__ == "__main__":
    main()
