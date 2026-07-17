"""Replay a saved beam-search line across many deterministic RNG offsets."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from battle.action import Action
from emulator.mgba_pool import MGBAPool
from outcome import TrialSpec


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--replays", type=int, default=256)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output")
    parser.add_argument("--fixed-rng", action="store_true")
    args = parser.parse_args()

    payload = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    result = payload.get("result") or payload
    line = [tuple(Action(**action) for action in turn) for turn in result["line"]]
    pool = MGBAPool(args.rom, args.state, max(1, args.workers))
    try:
        outcomes = pool.run_trials([
            TrialSpec(
                trial_id=index,
                actions=line,
                rng_advance_frames=0 if args.fixed_rng else (index * 17) % 997,
                start_state_path=str(Path(args.state).expanduser().resolve()) if args.fixed_rng else None,
                max_turns=max(30, len(line) + 4),
                capture_screens=False,
                stop_on_player_faint=True,
            )
            for index in range(max(1, args.replays))
        ])
    finally:
        pool.shutdown()
    summary = {
        "manifest": str(Path(args.manifest).resolve()),
        "state": str(Path(args.state).resolve()),
        "replays": len(outcomes),
        "rng_mode": "fixed exact start" if args.fixed_rng else "997-frame sampled window",
        "wins": sum(outcome.battle_won for outcome in outcomes),
        "deathless_wins": sum(
            outcome.battle_won and outcome.player_fainted_count == 0 for outcome in outcomes
        ),
        "faints": sum(outcome.player_fainted_count > 0 for outcome in outcomes),
        "errors": sum(outcome.error is not None for outcome in outcomes),
        "unfinished": sum(
            not outcome.final_state.battle_over for outcome in outcomes if outcome.error is None
        ),
        "error_breakdown": dict(Counter(
            str(outcome.error) for outcome in outcomes if outcome.error is not None
        ).most_common(12)),
        "sample_failures": [
            {
                "trial_id": outcome.trial_id,
                "rng_advance_frames": 0 if args.fixed_rng else (outcome.trial_id * 17) % 997,
                "error": str(outcome.error) if outcome.error is not None else None,
                "battle_over": outcome.final_state.battle_over,
                "battle_won": outcome.battle_won,
                "player_hp": list(outcome.final_state.player_hp),
                "enemy_hp": list(outcome.final_state.enemy_hp),
            }
            for outcome in outcomes
            if outcome.error is not None or not outcome.final_state.battle_over
        ][:12],
    }
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
