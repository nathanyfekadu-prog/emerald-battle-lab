from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trainer_data.models import TrainerBattle, TrainerPokemon


DEFAULT_TRAINER_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "trainer_battles.json"
DEFAULT_EMERALD_TRAINER_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "emerald_trainers.json"


def normalize_game_mode(game_mode: str | None) -> str:
    value = (game_mode or "run-and-bun").strip().casefold().replace("_", "-")
    return "pokemon-emerald" if value in {"emerald", "pokemon-emerald", "vanilla-emerald"} else "run-and-bun"


def load_trainer_battles_for_mode(game_mode: str | None) -> list[TrainerBattle]:
    mode = normalize_game_mode(game_mode)
    if mode == "pokemon-emerald":
        return load_emerald_trainer_battles()
    return load_trainer_battles()


def load_emerald_trainer_battles(
    path: str | Path = DEFAULT_EMERALD_TRAINER_DATA_PATH,
) -> list[TrainerBattle]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [_emerald_battle_from_dict(item) for item in raw.get("trainers", [])]


def load_trainer_battles(path: str | Path = DEFAULT_TRAINER_DATA_PATH) -> list[TrainerBattle]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    battles = raw.get("battles", raw if isinstance(raw, list) else [])
    return [_battle_from_dict(item) for item in battles]


def find_trainer_battles(
    trainer_name: str,
    path: str | Path = DEFAULT_TRAINER_DATA_PATH,
) -> list[TrainerBattle]:
    needle = trainer_name.casefold()
    return [battle for battle in load_trainer_battles(path) if battle.trainer_name.casefold() == needle]


def _battle_from_dict(data: dict[str, Any]) -> TrainerBattle:
    return TrainerBattle(
        section=str(data["section"]),
        location=data.get("location"),
        trainer_name=str(data["trainer_name"]),
        is_double=bool(data.get("is_double", False)),
        party=tuple(_pokemon_from_dict(item) for item in data.get("party", [])),
    )


def _pokemon_from_dict(data: dict[str, Any]) -> TrainerPokemon:
    return TrainerPokemon(
        species=str(data["species"]),
        level=data.get("level"),
        held_item=data.get("held_item"),
        ability=data.get("ability"),
        nature=data.get("nature"),
        moves=tuple(move for move in data.get("moves", []) if move),
        dex_key=data.get("dex_key"),
        exact_stats=data.get("exact_stats"),
    )


def _emerald_battle_from_dict(data: dict[str, Any]) -> TrainerBattle:
    route = str(data.get("route") or data.get("map_location") or "Unknown")
    return TrainerBattle(
        section="Pokémon Emerald",
        location=route,
        trainer_name=str(data["trainer_name"]),
        is_double=bool(data.get("is_double", False)),
        party=tuple(
            TrainerPokemon(
                species=str(mon["species"]),
                level=mon.get("level"),
                held_item=mon.get("held_item") or mon.get("item"),
                ability=mon.get("ability"),
                nature=mon.get("nature"),
                moves=tuple(move for move in mon.get("moves", []) if move),
                dex_key=mon.get("dex_key"),
                exact_stats={
                    {
                        "attack": "atk", "defense": "def", "sp_attack": "spa",
                        "sp_defense": "spd", "speed": "spe",
                    }.get(str(k), str(k)): int(v)
                    for k, v in (mon.get("stats") or {}).items()
                },
            )
            for mon in data.get("party", [])
        ),
        required=bool(data.get("required", False)),
        map_location=data.get("map_location") or route,
        sublocation=data.get("sublocation"),
        source_row=data.get("source_row"),
    )
