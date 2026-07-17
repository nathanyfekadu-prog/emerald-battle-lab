from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrainerPokemon:
    species: str
    level: int | None
    held_item: str | None
    ability: str | None
    nature: str | None
    moves: tuple[str, ...]
    dex_key: str | None = None


@dataclass(frozen=True)
class TrainerBattle:
    section: str
    location: str | None
    trainer_name: str
    is_double: bool
    party: tuple[TrainerPokemon, ...]
