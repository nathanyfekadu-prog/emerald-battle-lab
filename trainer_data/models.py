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
    exact_stats: dict[str, int] | None = None


@dataclass(frozen=True)
class TrainerBattle:
    section: str
    location: str | None
    trainer_name: str
    is_double: bool
    party: tuple[TrainerPokemon, ...]
    required: bool = False
    map_location: str | None = None
    sublocation: str | None = None
    source_row: int | None = None
