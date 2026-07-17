from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trainer_data.models import TrainerBattle, TrainerPokemon


DEFAULT_TRAINER_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "trainer_battles.json"


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
    )
