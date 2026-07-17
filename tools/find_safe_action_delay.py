from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from battle.action import Action
from emulator.mgba_pool import MGBAPool, TrialSpec


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--kind", choices=("move", "switch"), required=True)
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--max-delay", type=int, default=300)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    action = (
        Action.move(args.slot, actor_slot=0)
        if args.kind == "move"
        else Action.switch(args.slot, actor_slot=0)
    )
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    specs = [
        TrialSpec(
            trial_id=delay, actions=[(action,)], rng_advance_frames=delay,
            max_turns=1, output_state_path=str(output / f"delay-{delay:03d}.ss0"),
        )
        for delay in range(args.max_delay + 1)
    ]
    pool = MGBAPool(args.rom, args.state, min(args.workers, len(specs)))
    try:
        outcomes = pool.run_trials(specs)
    finally:
        pool.shutdown()
    kept = []
    for outcome in sorted(outcomes, key=lambda item: item.trial_id):
        state = outcome.final_state
        path = output / f"delay-{outcome.trial_id:03d}.ss0"
        safe = not outcome.error and not any(state.player_fainted)
        if safe:
            kept.append(outcome.trial_id)
            print({
                "delay": outcome.trial_id, "player_hp": state.player_hp,
                "enemy_hp": state.enemy_hp, "active": state.player_active_slots,
                "won": state.battle_over and state.player_won,
            })
        else:
            path.unlink(missing_ok=True)
    print({"safe": len(kept), "delays": kept})


if __name__ == "__main__":
    main()
