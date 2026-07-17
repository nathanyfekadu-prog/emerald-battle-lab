"""Run the resumable, per-turn savestate battle beam search."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from search.checkpoint_beam import CheckpointBeamSearch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--beam", type=int, default=8)
    parser.add_argument("--actions", type=int, default=6)
    parser.add_argument("--turns", type=int, default=30)
    parser.add_argument(
        "--forbid-answer", action="append", default=[], metavar="ENEMY_SLOT:PARTY_SLOT",
        help="Forbid a party slot while the specified enemy roster slot is alive.",
    )
    args = parser.parse_args()

    forbidden: dict[int, set[int]] = {}
    for value in args.forbid_answer:
        enemy_slot, party_slot = (int(part) for part in value.split(":", 1))
        forbidden.setdefault(enemy_slot, set()).add(party_slot)
    search = CheckpointBeamSearch(
        args.rom, args.state, args.output, workers=args.workers,
        beam_width=args.beam, actions_per_node=args.actions, max_turns=args.turns,
        forbidden_answers=forbidden,
    )

    def progress(depth: int, trials: int, frontier: list) -> None:
        best = frontier[0] if frontier else None
        print({
            "depth": depth, "trials": trials, "survivors": len(frontier),
            "best_score": best.score if best else None,
            "player_hp": list(best.state.player_hp) if best else None,
            "enemy_hp": list(best.state.enemy_hp) if best else None,
        }, flush=True)

    result = search.search(progress)
    print({
        "won": bool(result),
        "state": result.state_path if result else None,
        "turns": len(result.line) if result else None,
    }, flush=True)


if __name__ == "__main__":
    main()
