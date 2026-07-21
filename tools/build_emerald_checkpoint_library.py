#!/usr/bin/env python3
"""Build the ROM-free Emerald fight library from user-owned mGBA checkpoints.

The generated JSON contains only decoded battle/team metadata.  It never copies
the ROM or embeds copyrighted cartridge bytes, so the web simulator can offer
the captured fights to judges without asking them to provide a game file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from battle.damage_calc import DamageCalculator  # noqa: E402
from emulator.game_state import WholeGameStateReader  # noqa: E402
from emulator.mgba_instance import MGBAInstance  # noqa: E402
from emulator.state_reader import StateReader  # noqa: E402
from optimizer.gen3_save import RomNameResolver, read_save_snapshot  # noqa: E402
from tools.capture_battle_checkpoints import (  # noqa: E402
    EMERALD_MAP_ONLY_TRAINERS,
    EMERALD_OVERWORLD_MAPS,
    EMERALD_SPECIAL_MAPS,
    safe_stem,
)


BOSS_WORDS = (
    "leader ", "elite four ", "champion ", "rival", "admin ",
    "maxie", "archie", "wally",
)


def _map_name(map_id: tuple[int, int] | None) -> str | None:
    if map_id is None:
        return None
    if map_id[0] == 0 and 0 <= map_id[1] < len(EMERALD_OVERWORLD_MAPS):
        return EMERALD_OVERWORLD_MAPS[map_id[1]]
    return EMERALD_SPECIAL_MAPS.get(map_id)


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold().removeprefix("map"))


def _state_label(path: Path) -> str:
    return path.stem.removeprefix("fight-")


def _display_label(path: Path) -> str:
    return re.sub(r"-\d+$", "", _state_label(path)).replace("-", " ").title()


def _species_names(
    names: list[str], species_ids: list[int], max_hp: list[int], calculator: DamageCalculator
) -> tuple[str, ...]:
    resolved: list[str] = []
    for name, species_id, hp in zip(names, species_ids, max_hp):
        if hp <= 0:
            continue
        value = calculator.species_by_num.get(species_id, name) if name.startswith("Enemy ") else name
        resolved.append(str(value).casefold())
    return tuple(resolved)


def _trainer_score(
    trainer: dict[str, Any], label: str, map_name: str | None, x: int, y: int,
    enemy_species: tuple[str, ...], map_only_name: str | None,
) -> int:
    event = trainer.get("map_event") or {}
    trainer_name = str(trainer.get("trainer_name") or "")
    score = 0
    aliases = {
        safe_stem(trainer_name),
        safe_stem(str(event.get("trainer_name") or "")),
    }
    if label in aliases:
        score += 160
    elif any(label.startswith(alias + "-") for alias in aliases if alias):
        score += 110
    if map_only_name and trainer_name == map_only_name:
        score += 800
    if map_name and _norm(event.get("map_name")) == _norm(map_name):
        score += 260
        ex, ey = int(event.get("x", -999)), int(event.get("y", -999))
        sight = max(1, int(event.get("sight", 0)))
        if (ex == x and abs(ey - y) <= sight + 1) or (ey == y and abs(ex - x) <= sight + 1):
            score += 120
    party = tuple(str(mon.get("species") or "").casefold() for mon in trainer.get("party", []))
    if enemy_species and party == enemy_species:
        score += 500
    return score


def _member_payload(mon: Any) -> dict[str, Any]:
    return {
        "name": mon.display_name,
        "species": mon.species,
        "level": mon.level,
        "hp": mon.hp,
        "max_hp": mon.max_hp,
        "moves": list(mon.moves),
        "item": mon.held_item,
        "nature": mon.nature,
        "ivs": mon.ivs or {},
        "evs": mon.evs or {},
        "box": mon.box,
        "slot": mon.slot,
    }


def _showdown_import(party: list[dict[str, Any]]) -> str:
    blocks = []
    for mon in party:
        lines = [str(mon["name"])]
        if mon.get("item"):
            lines[0] += f" @ {mon['item']}"
        lines.append(f"Level: {mon['level']}")
        if mon.get("nature"):
            lines.append(f"{mon['nature']} Nature")
        lines.extend(f"- {move}" for move in mon.get("moves") or [])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_library(rom: Path, checkpoint_dir: Path) -> dict[str, Any]:
    catalog_path = ROOT / "data" / "emerald_trainers.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    trainers = catalog["trainers"]
    calculator = DamageCalculator(game_mode="pokemon-emerald")
    resolver = RomNameResolver(rom)
    entries: list[dict[str, Any]] = []
    unmatched: list[str] = []

    for index, path in enumerate(sorted(checkpoint_dir.glob("fight-*.ss0"))):
        with MGBAInstance(str(rom), str(path), instance_id=120 + index % 20) as instance:
            battle = StateReader(instance).read()
            world = WholeGameStateReader(instance, calculator=calculator).read()
            snapshot = read_save_snapshot(
                instance, resolver, calculator.species_by_num,
                calculator.moves_by_num, calculator.moves,
            )
        label = _state_label(path)
        map_name = _map_name(world.map_id)
        enemy_species = _species_names(
            battle.enemy_names, battle.enemy_species, battle.enemy_max_hp, calculator
        )
        map_only_name = EMERALD_MAP_ONLY_TRAINERS.get(world.map_id)
        ranked = sorted(
            (
                (_trainer_score(t, label, map_name, world.x, world.y, enemy_species, map_only_name), i, t)
                for i, t in enumerate(trainers)
            ),
            reverse=True,
            key=lambda row: row[0],
        )
        if not ranked or ranked[0][0] <= 0:
            unmatched.append(path.name)
            continue
        _, trainer_id, trainer = ranked[0]
        party = [_member_payload(mon) for mon in snapshot.party]
        box = [
            _member_payload(mon) for mon in snapshot.boxes
            if 1 <= int(mon.box) < 13
        ]
        trainer_name = str(trainer["trainer_name"])
        ace_level = max((int(mon.get("level") or 1) for mon in trainer.get("party", [])), default=1)
        boss = bool(trainer.get("required")) and any(word in trainer_name.casefold() for word in BOSS_WORDS)
        story = bool(trainer.get("required")) or boss
        entries.append({
            "checkpoint_id": label,
            "display_name": trainer_name or _display_label(path),
            "trainer_id": trainer_id,
            "trainer_name": trainer_name,
            "location": trainer.get("map_location") or trainer.get("route"),
            "map_name": map_name or (trainer.get("map_event") or {}).get("map_name"),
            "map_id": list(world.map_id) if world.map_id else None,
            "position": [world.x, world.y],
            "enemy_species": list(enemy_species),
            "ace_level": ace_level,
            "is_double": bool(trainer.get("is_double")),
            "required": bool(trainer.get("required")),
            "tier": "boss" if boss else "story" if story else "trainer",
            "recommended_test": boss,
            "player_party": party,
            "eligible_box": box,
            "team_import": _showdown_import(party),
            "roster_import": _showdown_import(party + box),
            "binary_included": False,
        })

    return {
        "game": "pokemon-emerald",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "kind": "decoded user-owned mGBA save states",
            "rom_included": False,
            "checkpoint_binaries_included": False,
            "note": "Only battle/team metadata required by the simulator is distributed.",
        },
        "rules": {
            "graveyard_box": 14,
            "excluded_boxes": [13, 14],
            "default_hint_mode": False,
            "default_ruleset": "hardcore-nuzlocke",
        },
        "stats": {
            "checkpoints": len(entries),
            "unique_trainers": len({entry["trainer_id"] for entry in entries}),
            "boss_checkpoints": sum(entry["tier"] == "boss" for entry in entries),
            "unmatched": len(unmatched),
        },
        "entries": entries,
        "unmatched": unmatched,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--checkpoints", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "data" / "emerald_checkpoint_library.json",
    )
    args = parser.parse_args()
    payload = build_library(args.rom.expanduser().resolve(), args.checkpoints.expanduser().resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload["stats"], indent=2))
    if payload["unmatched"]:
        print("Unmatched:", ", ".join(payload["unmatched"]))


if __name__ == "__main__":
    main()
