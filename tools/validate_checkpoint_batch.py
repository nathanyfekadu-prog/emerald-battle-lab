"""Replay several saved lines once from their exact starting checkpoints."""

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--case", action="append", nargs=3, metavar=("LABEL", "STATE", "LINE"))
    args = parser.parse_args()
    if not args.case:
        parser.error("provide at least one --case LABEL STATE LINE.json")
    specs = []
    for index, (_label, state, line_path) in enumerate(args.case):
        payload = json.loads(Path(line_path).read_text(encoding="utf-8"))
        raw_line = payload.get("line") or payload.get("result", {}).get("line")
        if not raw_line:
            raise ValueError(f"no line in {line_path}")
        line = [tuple(Action(**action) for action in turn) for turn in raw_line]
        specs.append(TrialSpec(
            trial_id=index,
            actions=line,
            start_state_path=str(Path(state).expanduser().resolve()),
            max_turns=len(line) + 3,
            stop_on_player_faint=True,
        ))
    pool = MGBAPool(args.rom, specs[0].start_state_path, min(8, len(specs)))
    try:
        outcomes = pool.run_trials(specs)
    finally:
        pool.shutdown()
    by_id = {outcome.trial_id: outcome for outcome in outcomes}
    result = []
    for index, (label, _state, _line) in enumerate(args.case):
        outcome = by_id[index]
        result.append({
            "trainer": label,
            "won": outcome.battle_won,
            "faints": outcome.player_fainted_count,
            "error": str(outcome.error) if outcome.error else None,
            "battle_over": outcome.final_state.battle_over,
            "player_hp": list(outcome.final_state.player_hp),
            "enemy_hp": list(outcome.final_state.enemy_hp),
        })
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
