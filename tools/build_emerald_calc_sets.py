#!/usr/bin/env python3
"""Build trainer sets for the local Run & Bun-style Emerald calculator."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "emerald_trainers.json"
OUTPUT = ROOT / "web" / "static" / "rnbcalc" / "js" / "data" / "sets" / "emerald.js"


def build_sets() -> dict[str, dict[str, object]]:
    payload = json.loads(SOURCE.read_text())
    sets: dict[str, dict[str, object]] = {}

    for trainer in payload["trainers"]:
        trainer_name = trainer["trainer_name"]
        location = trainer.get("map_location") or trainer.get("route") or "Unknown location"
        battle_kind = "Double" if trainer.get("is_double") else "Single"
        required = "Required" if trainer.get("required") else "Optional"

        for slot, pokemon in enumerate(trainer.get("party", []), start=1):
            species = pokemon["species"]
            label = f"Emerald · {trainer_name} · {location} · Slot {slot}"
            species_sets = sets.setdefault(species, {})
            if label in species_sets:
                label += f" · Battle #{trainer['id']}"
            entry: dict[str, object] = {
                "level": pokemon["level"],
                "moves": pokemon.get("moves", []),
                "index": trainer["id"],
                "trainer": trainer_name,
                "location": location,
                "battleType": battle_kind,
                "story": required,
            }
            if pokemon.get("item"):
                entry["item"] = pokemon["item"]
            if pokemon.get("ability"):
                entry["ability"] = pokemon["ability"]
            species_sets[label] = entry

    return sets


def main() -> None:
    sets = build_sets()
    pokemon_count = sum(len(species_sets) for species_sets in sets.values())
    body = (
        "/* Generated from data/emerald_trainers.json. Run tools/build_emerald_calc_sets.py to refresh. */\n"
        "var SETDEX_ADV = "
        + json.dumps(sets, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
        f"window.EMERALD_CALC_SET_COUNT = {pokemon_count};\n"
    )
    OUTPUT.write_text(body)
    print(f"Wrote {pokemon_count} Emerald trainer Pokémon across {len(sets)} species to {OUTPUT}")


if __name__ == "__main__":
    main()
