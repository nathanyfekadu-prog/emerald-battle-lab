"""Translate calculator line rows into state-aware emulator instructions."""

from __future__ import annotations

import re
from typing import Any

from outcome import PolicyAction


def compile_planner_policy(result: dict[str, Any]) -> tuple[list[Any], list[str]]:
    policy: list[Any] = []
    warnings: list[str] = []
    team_slots: dict[str, int] = {}
    for index, member in enumerate(result.get("team") or []):
        for label in (member.get("name"), member.get("species")):
            if label:
                team_slots[_normalized(str(label))] = index
    for row in result.get("turns") or []:
        turn = row.get("turn", len(policy) + 1)
        slot_actions = [
            action for action in (row.get("slot_actions") or [])
            if action.get("side") == "player"
        ]
        if slot_actions:
            compiled = tuple(_from_slot_action(action, team_slots) for action in slot_actions)
            if all(compiled):
                policy.append(compiled)
            else:
                warnings.append(f"Turn {turn} contains an unsupported doubles action.")
            continue

        text = str(row.get("action") or "")
        switch = re.search(r"(?:Forced send|[Ss]witch(?:\s+[^-]+)?\s*->)\s*([^.;]+)", text)
        if switch:
            target = _clean_label(switch.group(1))
            policy.append(PolicyAction(
                "switch", switch_to=target, switch_party_slot=team_slots.get(_normalized(target))
            ))
            continue
        move = re.search(r"(?:click(?:ed)?|uses?)\s+([^.;|]+)", text, flags=re.IGNORECASE)
        if move:
            policy.append(PolicyAction("move", move_name=move.group(1).strip()))
            continue
        warnings.append(f"Turn {turn} is advisory only and cannot be replayed safely: {text or 'no action'}")
    return policy, warnings


def _from_slot_action(action: dict[str, Any], team_slots: dict[str, int]) -> PolicyAction | None:
    actor_slot = int(action.get("field_slot", 0))
    if action.get("kind") == "move" and action.get("move"):
        target = action.get("target_slot")
        return PolicyAction(
            "move", move_name=str(action["move"]), actor_slot=actor_slot,
            target_slot=int(target) if target is not None else None,
        )
    if action.get("kind") == "switch" and action.get("switch_to"):
        target = str(action["switch_to"])
        return PolicyAction(
            "switch", switch_to=target, actor_slot=actor_slot,
            switch_party_slot=team_slots.get(_normalized(target)),
        )
    return None


def _clean_label(value: str) -> str:
    # Planner labels may append species in parentheses; the live reader exposes nicknames.
    return re.sub(r"\s*\([^)]*\)\s*$", "", value).strip()


def _normalized(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


__all__ = ["compile_planner_policy"]
