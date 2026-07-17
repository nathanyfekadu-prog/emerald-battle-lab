"""Turn a checkpoint-beam win into a genuinely continuous, timed replay.

Older searches could pick a different RNG outcome for each saved edge. This tool starts every
turn from the previous *recovered* child, tries visible menu waits, and keeps an edge only when
it reaches the winning branch's next battle state. A failure is explicit; no checkpoint jump is
inserted into the resulting line.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from battle.action import Action
from emulator.mgba_instance import MGBAInstance
from emulator.mgba_pool import MGBAPool
from emulator.state_reader import StateReader
from outcome import TrialSpec
from search.checkpoint_beam import CheckpointBeamSearch


def _read_state(rom: str, path: Path, instance_id: int):
    with MGBAInstance(rom, str(path), instance_id) as instance:
        return StateReader(instance).read()


def _segment_targets(payload: dict, target: Path) -> list[Path]:
    reverse: list[Path] = []
    current = target.resolve()
    while True:
        match = re.search(r"d(\d+)-n(\d+)-a\d+-t\d+\.ss0$", current.name)
        if not match:
            raise RuntimeError(f"cannot trace checkpoint parent from {current}")
        depth, parent_index = map(int, match.groups())
        reverse.append(current)
        if depth == 1:
            break
        current = Path(payload["depths"][depth - 2]["frontier"][parent_index]["state_path"])
    targets = list(reversed(reverse))
    return targets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--manifest", required=True, action="append")
    parser.add_argument(
        "--segment-target",
        action="append",
        help="Explicit winning target for the corresponding manifest segment.",
    )
    parser.add_argument("--line-json", help="Optional JSON containing the complete line")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--min-delay", type=int, default=0)
    parser.add_argument("--max-delay", type=int, default=255)
    parser.add_argument("--delay-batch", type=int, default=32)
    parser.add_argument("--start-turn", type=int, default=1)
    parser.add_argument("--end-turn", type=int)
    parser.add_argument(
        "--prefix-delays",
        default="",
        help="Comma-separated recovered delays before --start-turn.",
    )
    args = parser.parse_args()

    root = Path(args.state).expanduser().resolve()
    manifest_paths = [Path(value).expanduser().resolve() for value in args.manifest]
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in manifest_paths]
    if args.line_json:
        line_payload = json.loads(Path(args.line_json).read_text(encoding="utf-8"))
        raw_line = line_payload.get("line") or line_payload.get("result", {}).get("line")
    else:
        raw_line = [
            turn
            for item in payloads
            for turn in item.get("result", {}).get("line", [])
        ]
    if not raw_line:
        raise ValueError("no replay line found in the selected JSON")
    line = [tuple(Action(**action) for action in turn) for turn in raw_line]
    targets: list[Path] = []
    if args.segment_target and len(args.segment_target) != len(payloads):
        raise ValueError("provide one segment-target for every manifest")
    for index, item in enumerate(payloads):
        if args.segment_target:
            target = Path(args.segment_target[index])
        else:
            target = (
                Path(payloads[index + 1]["source"])
                if index + 1 < len(payloads)
                else Path(item["result"]["state_path"])
            )
        targets.extend(_segment_targets(item, target))
    if len(targets) != len(line):
        raise RuntimeError(f"traced {len(targets)} targets for {len(line)} turns")
    if not 1 <= args.start_turn <= len(line):
        raise ValueError("start-turn is outside the checkpoint line")
    end_turn = args.end_turn or len(line)
    if not args.start_turn <= end_turn <= len(line):
        raise ValueError("end-turn is outside the selected checkpoint suffix")
    if not 0 <= args.min_delay <= args.max_delay:
        raise ValueError("delay bounds must satisfy 0 <= min-delay <= max-delay")
    output = Path(args.output).expanduser().resolve()
    state_dir = output.parent / f".{output.stem}-states"
    state_dir.mkdir(parents=True, exist_ok=True)
    pool = MGBAPool(args.rom, str(root), max(1, args.workers))
    current = root
    delays: list[int] = [
        int(value) for value in args.prefix_delays.split(",") if value.strip()
    ]
    if len(delays) != args.start_turn - 1:
        raise ValueError("prefix-delays must contain exactly start-turn - 1 values")
    recovered_states: list[str] = []
    try:
        pairs = zip(
            line[args.start_turn - 1 : end_turn],
            targets[args.start_turn - 1 : end_turn],
        )
        for turn_number, (turn, target_path) in enumerate(pairs, args.start_turn):
            wanted = _read_state(args.rom, target_path, 900 + turn_number)
            wanted_signature = CheckpointBeamSearch._signature(wanted)
            candidate_paths: dict[int, Path] = {}
            outcomes = []
            matches = []
            batch_size = max(1, args.delay_batch)
            for batch_start in range(args.min_delay, args.max_delay + 1, batch_size):
                batch_end = min(args.max_delay, batch_start + batch_size - 1)
                trials = []
                for delay in range(batch_start, batch_end + 1):
                    candidate = state_dir / f"turn-{turn_number:02d}-delay-{delay:03d}.ss0"
                    candidate_paths[delay] = candidate
                    trials.append(TrialSpec(
                        trial_id=delay,
                        actions=[turn],
                        rng_advance_frames=delay,
                        start_state_path=str(current),
                        output_state_path=str(candidate),
                        max_turns=1,
                        capture_screens=False,
                        stop_on_player_faint=True,
                    ))
                batch_outcomes = pool.run_trials(trials)
                outcomes.extend(batch_outcomes)
                matches = [
                    outcome for outcome in batch_outcomes
                    if outcome.error is None
                    and outcome.player_fainted_count == 0
                    and (
                        CheckpointBeamSearch._signature(outcome.final_state) == wanted_signature
                        or (turn_number == len(line) and outcome.battle_won)
                    )
                ]
                if matches:
                    break
            if not matches:
                nearest = min(
                    (outcome for outcome in outcomes if outcome.error is None),
                    key=lambda outcome: sum(abs(a - b) for a, b in zip(
                        outcome.final_state.player_hp + outcome.final_state.enemy_hp,
                        wanted.player_hp + wanted.enemy_hp,
                    )),
                    default=None,
                )
                detail = None if nearest is None else {
                    "delay": nearest.trial_id,
                    "player_hp": list(nearest.final_state.player_hp),
                    "enemy_hp": list(nearest.final_state.enemy_hp),
                }
                raise RuntimeError(
                    f"turn {turn_number} has no continuous timing in "
                    f"{args.min_delay}..{args.max_delay}; "
                    f"nearest={detail}"
                )
            chosen = min(matches, key=lambda outcome: outcome.trial_id)
            delay = chosen.trial_id
            current = candidate_paths[delay]
            delays.append(delay)
            recovered_states.append(str(current))
            for candidate in candidate_paths.values():
                if candidate != current:
                    candidate.unlink(missing_ok=True)
            print(json.dumps({
                "turn": turn_number, "delay": delay,
                "player_hp": list(chosen.final_state.player_hp),
                "enemy_hp": list(chosen.final_state.enemy_hp),
                "battle_won": chosen.battle_won,
            }), flush=True)
            output.write_text(json.dumps({
                "source": str(root),
                "checkpoint_manifest": [str(path) for path in manifest_paths],
                "line": raw_line,
                "delays": delays,
                "states": recovered_states,
                "state_path": str(current),
                "status": "recovering",
                "next_turn": turn_number + 1,
            }, indent=2), encoding="utf-8")
    finally:
        pool.shutdown()
    result = {
        "source": str(root),
        "checkpoint_manifest": [str(path) for path in manifest_paths],
        "line": raw_line,
        "delays": delays,
        "states": recovered_states,
        "state_path": str(current),
        "status": "continuous-timed" if end_turn == len(line) else "recovering",
    }
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "state": str(current), "delays": delays}))


if __name__ == "__main__":
    main()
