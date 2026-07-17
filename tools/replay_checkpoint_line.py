"""Replay a checkpoint-beam result from its source state and report empirical safety."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from battle.action import Action
from emulator.mgba_pool import MGBAPool
from outcome import TrialSpec
from search.mcts import _simulator_trial_data


def _action(data: dict) -> Action:
    return Action(**{key: data.get(key) for key in (
        "kind", "actor_slot", "move_slot", "target_slot", "switch_target"
    )})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--replays", type=int, default=30)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output")
    parser.add_argument(
        "--fixed-rng",
        action="store_true",
        help="Replay the exact checkpoint timing instead of sampling shifted RNG starts.",
    )
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    source = manifest["source"]
    line = [tuple(_action(item) for item in turn) for turn in manifest["result"]["line"]]
    trials = [TrialSpec(
        trial_id=index,
        actions=line,
        rng_advance_frames=0 if args.fixed_rng else (index * 17) % 97,
        # Mark fixed replays as checkpoint edges so the pool does not add its
        # normal per-worker RNG desynchronization behind the caller's back.
        start_state_path=source if args.fixed_rng else None,
        max_turns=len(line),
        capture_screens=index < 4,
    ) for index in range(args.replays)]
    pool = MGBAPool(args.rom, source, min(args.workers, args.replays))
    try:
        outcomes = pool.run_trials(trials)
    finally:
        pool.shutdown()
    payload = {
        "source": source,
        "replays": len(outcomes),
        "wins": sum(outcome.battle_won for outcome in outcomes),
        "deathless_wins": sum(outcome.battle_won and outcome.player_fainted_count == 0 for outcome in outcomes),
        "blackouts": sum(outcome.final_state.battle_over and not outcome.final_state.player_won for outcome in outcomes),
        "errors": sum(outcome.error is not None for outcome in outcomes),
        "runs": [_simulator_trial_data(outcome) for outcome in outcomes],
    }
    output = Path(args.output) if args.output else Path(args.manifest).with_name("replays.json")
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print({key: payload[key] for key in ("replays", "wins", "deathless_wins", "blackouts", "errors")})
    if payload["runs"]:
        print(payload["runs"][0]["actions"])


if __name__ == "__main__":
    main()
