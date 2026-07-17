from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    kind: str
    actor_slot: int | None = None
    move_slot: int | None = None
    target_slot: int | None = None
    switch_target: int | None = None

    @classmethod
    def move(
        cls,
        move_slot: int,
        target_slot: int | None = None,
        *,
        actor_slot: int | None = None,
    ) -> "Action":
        return cls(kind="move", actor_slot=actor_slot, move_slot=move_slot, target_slot=target_slot)

    @classmethod
    def switch(cls, party_slot: int, *, actor_slot: int | None = None) -> "Action":
        return cls(kind="switch", actor_slot=actor_slot, switch_target=party_slot)

    @property
    def is_move(self) -> bool:
        return self.kind == "move"

    @property
    def is_switch(self) -> bool:
        return self.kind == "switch"
