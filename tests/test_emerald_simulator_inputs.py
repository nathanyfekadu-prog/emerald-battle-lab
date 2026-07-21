from __future__ import annotations

from battle.battle_state import BattleState
from emulator.input_controller import InputController
from search.action_enumerator import ActionEnumerator
from search.mcts import MCTS
from battle.action import Action


def _state(moves: list[str]) -> BattleState:
    return BattleState(
        player_hp=[23, 0, 0, 0, 0, 0],
        player_max_hp=[23, 0, 0, 0, 0, 0],
        player_fainted=[False, True, True, True, True, True],
        enemy_hp=[18, 0, 0, 0, 0, 0],
        enemy_max_hp=[18, 0, 0, 0, 0, 0],
        enemy_fainted=[False, True, True, True, True, True],
        battle_over=False,
        player_won=False,
        is_doubles=False,
        menu_ready=True,
        player_move_names=moves,
        player_move_names_by_slot=[moves],
        player_active_slots=(0,),
    )


def test_live_move_enumeration_never_invents_empty_or_unknown_slots() -> None:
    actions = ActionEnumerator(game_mode="pokemon-emerald").legal_actions(
        _state(["Tackle", "Growl", "Mud-Slap", ""])
    )
    assert [turn[0].move_slot for turn in actions] == [0, 1, 2]

    actions = ActionEnumerator(game_mode="pokemon-emerald").legal_actions(
        _state(["Tackle", "Unknown move 2", "Mud-Slap", ""])
    )
    assert [turn[0].move_slot for turn in actions] == [0, 2]


def test_simple_emerald_fight_prefers_real_damage_over_setup_spam() -> None:
    enumerator = ActionEnumerator(game_mode="pokemon-emerald")
    state = _state(["Tackle", "Growl", "Mud-Slap", ""])
    actions = enumerator.legal_actions(state)

    finisher = enumerator.finishing_action(actions, state)

    assert finisher is not None
    assert finisher[0].move_slot == 0


def test_move_cursor_is_normalized_before_selecting_requested_slot() -> None:
    controller = object.__new__(InputController)
    taps: list[str] = []
    controller._tap = taps.append  # type: ignore[method-assign]

    controller._move_cursor_2x2(3)

    assert taps == ["UP", "LEFT", "RIGHT", "DOWN"]


def test_validation_line_has_a_finishing_tail_for_lower_damage_rolls() -> None:
    search = object.__new__(MCTS)
    search.max_turns = 8
    search.game_mode = "pokemon-emerald"
    search.initial_state = _state(["Tackle", "Growl", "Mud-Slap", ""])
    search.enumerator = ActionEnumerator(game_mode="pokemon-emerald")
    search.root_actions = [(Action.move(1),), (Action.move(2),), (Action.move(0),)]

    padded = search._pad_validation_line([(Action.move(2),)] * 5)

    assert len(padded) == 8
    assert padded[-3:] == [(Action.move(0),)] * 3
