from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from battle.action import Action
from search.checkpoint_beam import CheckpointBeamSearch, CheckpointNode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--beam", type=int, default=12)
    parser.add_argument("--actions", type=int, default=12)
    parser.add_argument("--turns", type=int, default=30)
    parser.add_argument("--frontier-offset", type=int, default=0)
    parser.add_argument("--source-depth", type=int, default=-1, help="1-based saved depth; default is the latest")
    args = parser.parse_args()

    source = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    start = max(0, args.frontier_offset)
    depth_index = args.source_depth - 1 if args.source_depth > 0 else -1
    payloads = source["depths"][depth_index]["frontier"][start:start + max(1, args.beam)]
    if not payloads:
        raise SystemExit("The source manifest has no resumable frontier.")
    search = CheckpointBeamSearch(
        args.rom,
        source["source"],
        args.output,
        workers=args.workers,
        beam_width=args.beam,
        actions_per_node=args.actions,
        max_turns=args.turns,
    )
    frontier: list[CheckpointNode] = []
    for payload in payloads:
        state = search._read_state(payload["state_path"])
        line = tuple(
            tuple(Action(**action) for action in turn)
            for turn in payload["line"]
        )
        frontier.append(CheckpointNode(
            payload["state_path"], state, line,
            search._score(state, len(line)), 0,
        ))

    def progress(depth: int, trials: int, current: list[CheckpointNode]) -> None:
        best = current[0] if current else None
        print({
            "depth": depth, "trials": trials, "survivors": len(current),
            "best_score": best.score if best else None,
            "player_hp": best.state.player_hp if best else None,
            "enemy_hp": best.state.enemy_hp if best else None,
        }, flush=True)

    result = search.search(progress, initial_frontier=frontier)
    print({
        "won": result is not None,
        "state": result.state_path if result else None,
        "turns": len(result.line) if result else None,
    })


if __name__ == "__main__":
    main()
