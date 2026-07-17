from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BattleState:
    player_hp: list[int]
    player_max_hp: list[int]
    player_fainted: list[bool]
    enemy_hp: list[int]
    enemy_max_hp: list[int]
    enemy_fainted: list[bool]
    battle_over: bool
    player_won: bool
    is_doubles: bool
    menu_ready: bool
    player_names: list[str] = field(default_factory=list)
    enemy_names: list[str] = field(default_factory=list)
    player_move_names: list[str] = field(default_factory=list)
    player_move_names_by_slot: list[list[str]] = field(default_factory=list)
    player_move_ids: list[int] = field(default_factory=list)
    enemy_move_names: list[str] = field(default_factory=list)
    enemy_move_ids: list[int] = field(default_factory=list)
    enemy_move_names_by_slot: list[list[str]] = field(default_factory=list)
    player_species: list[int] = field(default_factory=list)
    enemy_species: list[int] = field(default_factory=list)
    # Party indices currently occupying the battlefield positions.  Older state
    # dumps omit these fields; the solver then falls back to (0,) or (0, 1).
    # Keeping field position separate from party position is essential in doubles:
    # after a switch, party slot 4 may be the Pokemon acting from battlefield slot 1.
    player_active_slots: tuple[int | None, ...] = field(default_factory=tuple)
    enemy_active_slots: tuple[int | None, ...] = field(default_factory=tuple)
