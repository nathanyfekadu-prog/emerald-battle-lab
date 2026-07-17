from __future__ import annotations

from battle.action import Action
from battle.battle_state import BattleState
from battle.damage_calc import default_calculator
from outcome import TurnAction


class ActionEnumerator:
    def legal_actions(self, state: BattleState) -> list[TurnAction]:
        if state.is_doubles:
            return self._doubles_actions(state)
        return self._singles_actions(state)

    def _singles_actions(self, state: BattleState) -> list[TurnAction]:
        active_slot = next((slot for slot in state.player_active_slots if slot is not None), 0)
        move_count = self._move_count(state, active_slot)
        actions: list[TurnAction] = [(Action.move(slot, actor_slot=0),) for slot in range(move_count)]
        for slot, fainted in enumerate(state.player_fainted):
            if slot == active_slot or fainted:
                continue
            if slot < len(state.player_max_hp) and state.player_max_hp[slot] <= 0:
                continue
            actions.append((Action.switch(slot, actor_slot=0),))
        return actions

    def _doubles_actions(self, state: BattleState) -> list[TurnAction]:
        active_slots = self._active_slots(state.player_active_slots, state.player_fainted)
        enemy_slots = self._active_slots(state.enemy_active_slots, state.enemy_fainted)
        live_enemy_positions = [
            position
            for position, party_slot in enumerate(enemy_slots)
            if party_slot is not None
            and party_slot < len(state.enemy_fainted)
            and not state.enemy_fainted[party_slot]
        ]
        if not live_enemy_positions:
            return []
        per_active: list[list[Action]] = []
        calculator = default_calculator()
        for field_slot, active_slot in enumerate(active_slots):
            active_is_live = (
                active_slot is not None
                and active_slot < len(state.player_fainted)
                and not state.player_fainted[active_slot]
            )
            choices: list[Action] = []
            if active_is_live:
                for move_slot in range(self._move_count(state, active_slot)):
                    move_name = self._move_name_for_slot(state, active_slot, move_slot)
                    move_data = (
                        calculator.moves.get(self._normalized(move_name), {})
                        if calculator is not None and move_name
                        else {}
                    )
                    target_kind = move_data.get("target")
                    # Spread, self, side, and field moves skip Emerald's red
                    # doubles target picker. Single-target attacks must branch
                    # once per live opposing field position.
                    targets: list[int | None] = (
                        (list(live_enemy_positions) if len(live_enemy_positions) > 1 else [None])
                        if target_kind in {None, "normal", "adjacentFoe", "any"}
                        else [None]
                    )
                    choices.extend(
                        Action.move(move_slot, target_slot, actor_slot=field_slot)
                        for target_slot in targets
                    )
            for slot, fainted in enumerate(state.player_fainted):
                if slot in active_slots or fainted:
                    continue
                if slot < len(state.player_max_hp) and state.player_max_hp[slot] <= 0:
                    continue
                choices.append(Action.switch(slot, actor_slot=field_slot))
            per_active.append(choices)

        if any(not choices for choices in per_active):
            return []

        combined: list[TurnAction] = []
        for left in per_active[0]:
            for right in per_active[1]:
                if (
                    left.is_switch
                    and right.is_switch
                    and left.switch_target == right.switch_target
                ):
                    continue
                combined.append((left, right))
        return combined

    @staticmethod
    def _move_name_for_slot(state: BattleState, party_slot: int, move_slot: int) -> str:
        if party_slot < len(state.player_move_names_by_slot):
            names = state.player_move_names_by_slot[party_slot]
            if move_slot < len(names):
                return names[move_slot]
        return ""

    @staticmethod
    def _normalized(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())

    @staticmethod
    def _active_slots(explicit: tuple[int | None, ...], fainted: list[bool]) -> tuple[int | None, int | None]:
        if explicit:
            slots = list(explicit[:2])
            while len(slots) < 2:
                slots.append(None)
            return slots[0], slots[1]
        defaults: list[int | None] = [index if index < len(fainted) else None for index in range(2)]
        return defaults[0], defaults[1]

    @staticmethod
    def _move_count(state: BattleState, party_slot: int) -> int:
        if party_slot < len(state.player_move_names_by_slot):
            known = [move for move in state.player_move_names_by_slot[party_slot] if move]
            if known:
                return min(4, len(known))
        if party_slot == 0 and state.player_move_names:
            return min(4, len([move for move in state.player_move_names if move])) or 4
        return 4

    def prioritize(self, actions: list[TurnAction], state: BattleState) -> list[TurnAction]:
        calculator = default_calculator()
        if calculator is None:
            return actions
        filtered = self._filter_switches(actions, state, calculator)
        indexed = list(enumerate(filtered))
        return [
            action
            for _index, action in sorted(
                indexed,
                key=lambda item: (calculator.priority_key(state, item[1]), -item[0]),
                reverse=True,
            )
        ]

    def _filter_switches(self, actions: list[TurnAction], state: BattleState, calculator: object) -> list[TurnAction]:
        move_actions = [action for action in actions if all(item.is_move for item in action)]
        switch_actions = [action for action in actions if any(item.is_switch for item in action)]
        if not move_actions or state.is_doubles:
            return actions

        allowed_switches: list[TurnAction] = []
        for turn_action in switch_actions:
            allowed = True
            for action in turn_action:
                if action.is_switch and action.switch_target is not None:
                    allowed = bool(calculator.switch_decision(state, action.switch_target).allowed)
            if allowed:
                allowed_switches.append(turn_action)
        return move_actions + allowed_switches
