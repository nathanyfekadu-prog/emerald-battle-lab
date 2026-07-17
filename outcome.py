from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from battle.action import Action
from battle.battle_state import BattleState


TurnAction = tuple[Action, ...]


@dataclass(frozen=True)
class TrialSpec:
    trial_id: int
    actions: list[Any]
    rng_advance_frames: int = 0
    max_turns: int = 100
    capture_screens: bool = True
    start_state_path: str | None = None
    output_state_path: str | None = None
    stop_on_player_faint: bool = False


@dataclass(frozen=True)
class PolicyAction:
    """A battle instruction that is resolved against the live GBA state each turn.

    Planner output names moves and Pokemon.  Keeping those names until execution prevents
    a branched battle from turning e.g. "Wild Charge" into whatever happens to occupy the
    same menu slot on a replacement Pokemon.
    """
    kind: str
    move_name: str | None = None
    switch_to: str | None = None
    actor_slot: int | None = None
    target_slot: int | None = None
    switch_party_slot: int | None = None


@dataclass(frozen=True)
class TurnSnapshot:
    turn: int
    actions: list[Action]
    player_hp: list[int]
    player_max_hp: list[int]
    enemy_hp: list[int]
    enemy_max_hp: list[int]
    player_fainted: list[bool]
    enemy_fainted: list[bool]
    battle_over: bool
    player_won: bool
    screen_width: int | None = None
    screen_height: int | None = None
    screen_rgba_base64: str | None = None
    action_labels: list[str] = field(default_factory=list)
    player_active_slots: tuple[int | None, ...] = field(default_factory=tuple)
    enemy_active_slots: tuple[int | None, ...] = field(default_factory=tuple)
    player_move_names: list[str] = field(default_factory=list)
    enemy_move_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Outcome:
    final_state: BattleState
    actions_taken: list[Action]
    instance_id: int
    trial_id: int
    frames_run: int
    battle_won: bool
    player_fainted_count: int
    enemy_fainted_count: int
    final_player_hp: list[int]
    final_enemy_hp: list[int]
    is_sack_line: bool
    turn_snapshots: list[TurnSnapshot] = field(default_factory=list)
    error: str | None = None
    screen_width: int | None = None
    screen_height: int | None = None
    screen_rgba_base64: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["actions_taken"] = [asdict(action) for action in self.actions_taken]
        data["turn_snapshots"] = [
            {**asdict(snapshot), "actions": [asdict(action) for action in snapshot.actions]}
            for snapshot in self.turn_snapshots
        ]
        return data


def flatten_turn_actions(actions: list[Any]) -> list[Action]:
    flattened: list[Action] = []
    for action in actions:
        if isinstance(action, Action):
            flattened.append(action)
        else:
            flattened.extend(action)
    return flattened
