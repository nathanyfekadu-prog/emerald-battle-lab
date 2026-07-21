#!/usr/bin/env python3
"""Validate the captured Sidney-to-Wallace judge Gauntlet and save its report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from battle.damage_calc import DamageCalculator  # noqa: E402
from trainer_data.loader import load_trainer_battles_for_mode  # noqa: E402
from web.server import _parse_imported_sets, _run_calc_gauntlet  # noqa: E402
from tools.render_run_video import render_run_video  # noqa: E402


LEAGUE_IDS = [469, 470, 471, 472, 473]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--library", type=Path, default=ROOT / "data" / "emerald_checkpoint_library.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "submission" / "emerald-league-gauntlet.json"
    )
    parser.add_argument("--leveling-policy", choices=("none", "party-max", "boss-cap"), default="boss-cap")
    parser.add_argument(
        "--force-enemy-crits", action="store_true",
        help="Optional stress test; Hardcore Nuzlocke rules do not force every enemy hit to crit.",
    )
    parser.add_argument("--max-total-faints", type=int, choices=range(0, 7), default=3)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()

    library = json.loads(args.library.read_text(encoding="utf-8"))
    sidney = next(entry for entry in library["entries"] if entry["trainer_name"] == "Elite Four Sidney")
    calculator = DamageCalculator(game_mode="pokemon-emerald")
    roster = _parse_imported_sets(sidney["roster_import"], calculator)
    battles = load_trainer_battles_for_mode("pokemon-emerald")
    results = []
    fingerprints = []
    for _repetition in range(max(1, args.repetitions)):
        result = _run_calc_gauntlet(
            roster,
            [battles[index] for index in LEAGUE_IDS],
            calculator,
            max_turns=40,
            force_enemy_crits=args.force_enemy_crits,
            heal_between=True,
            optimize_between_fights=False,
            leveling_policy=args.leveling_policy,
            deathless_required=True,
            max_total_faints=args.max_total_faints,
            allow_revives=False,
        )
        fingerprint_payload = {
            "result": result.get("result"),
            "chosen": (result.get("route_team_selection") or {}).get("chosen"),
            "reproducibility_key": (result.get("route_team_selection") or {}).get("reproducibility_key"),
            "fights": [
                {
                    "trainer": fight.get("trainer"),
                    "result": fight.get("result"),
                    "faints": [
                        member.get("name") for member in fight.get("ending_team") or []
                        if int(member.get("hp") or 0) <= 0
                    ],
                    "actions": [turn.get("action") for turn in fight.get("turns") or []],
                }
                for fight in result.get("fights") or []
            ],
        }
        fingerprints.append(json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")))
        results.append(result)
    result = results[0]
    reproducible = len(set(fingerprints)) == 1
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "route": [battles[index].trainer_name for index in LEAGUE_IDS],
        "rules": {
            "ruleset": "hardcore-nuzlocke",
            "items_in_battle": False,
            "revives": False,
            "between_fight_healing": "bag",
            "hint_mode": False,
            "leveling_policy": args.leveling_policy,
            "excluded_boxes": [13, 14],
            "enemy_crits_forced": args.force_enemy_crits,
            "max_total_faints": args.max_total_faints,
        },
        "reproducibility": {
            "runs": len(results),
            "identical": reproducible,
            "fingerprint": hashlib.sha256(fingerprints[0].encode("utf-8")).hexdigest()[:16],
        },
        **result,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    video_path = args.output.with_name("emerald-league-gauntlet-demo.mp4")
    video = render_run_video(report, video_path, kind="gauntlet")
    video["video_url"] = "/submission/emerald-league-gauntlet-demo.mp4"
    report["videos"] = [video]
    report["video_ready"] = True
    report["evidence_policy"] = "video-and-text-required"
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "result": result["result"],
        "completed": result["completed"],
        "queued": result["queued"],
        "stopped_reason": result.get("stopped_reason"),
        "output": str(args.output),
        "video": str(video_path),
        "reproducible": reproducible,
    }, indent=2))
    raise SystemExit(0 if result["result"] == "route-complete" and reproducible else 1)


if __name__ == "__main__":
    main()
