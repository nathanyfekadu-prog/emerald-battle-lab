#!/usr/bin/env python3
"""Run one fast, deterministic Emerald simulator battle against Sidney."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from battle.damage_calc import DamageCalculator  # noqa: E402
from tools.render_run_video import render_run_video  # noqa: E402
from trainer_data.loader import load_trainer_battles_for_mode  # noqa: E402
from web.server import (  # noqa: E402
    _clone_calc_team,
    _emerald_rare_candy_raise,
    _parse_imported_sets,
    _search_best_line,
)


SIDNEY_ID = 469
# Deterministic six-member party from the captured pre-League box. This is a
# standalone battle, so there is no carried Gauntlet state or cumulative faint cap.
PARTY_INDICES = (0, 1, 2, 7, 9, 11)


def main() -> None:
    library_path = ROOT / "data" / "emerald_checkpoint_library.json"
    output_path = ROOT / "submission" / "emerald-sidney-simulator.json"
    video_path = ROOT / "submission" / "emerald-sidney-simulator.mp4"

    library = json.loads(library_path.read_text(encoding="utf-8"))
    checkpoint = next(
        entry for entry in library["entries"]
        if entry["trainer_name"] == "Elite Four Sidney"
    )
    calculator = DamageCalculator(game_mode="pokemon-emerald")
    roster = _parse_imported_sets(checkpoint["roster_import"], calculator)
    trainer = load_trainer_battles_for_mode("pokemon-emerald")[SIDNEY_ID]
    level_cap = max(mon.level or 1 for mon in trainer.party)
    party = [
        _emerald_rare_candy_raise(roster[index], level_cap, calculator)
        for index in PARTY_INDICES
    ]
    result = _search_best_line(
        _clone_calc_team(party),
        trainer,
        calculator,
        max_turns=40,
        budget=70,
    )
    action_fingerprint = hashlib.sha256(
        json.dumps(
            [turn.get("action") for turn in result.get("turns") or []],
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "standalone-simulator",
        "trainer": trainer.trainer_name,
        "rules": {
            "items_in_battle": False,
            "revives": False,
            "faint_limit": None,
            "leveling_policy": "boss-cap",
            "level_cap": level_cap,
        },
        "reproducibility": {
            "deterministic": True,
            "fingerprint": action_fingerprint,
        },
        **result,
    }
    video = render_run_video(report, video_path, kind="simulator")
    video["video_url"] = "/submission/emerald-sidney-simulator.mp4"
    report["videos"] = [video]
    report["video_ready"] = True
    report["evidence_policy"] = "video-and-text-required"
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "result": report["result"],
        "trainer": report["trainer"],
        "turns": len(report.get("turns") or []),
        "faints": [
            member["name"] for member in report.get("team") or []
            if int(member.get("hp") or 0) <= 0
        ],
        "report": str(output_path),
        "video": str(video_path),
        "fingerprint": action_fingerprint,
    }, indent=2))
    raise SystemExit(0 if report["result"] == "win-line" else 1)


if __name__ == "__main__":
    main()
