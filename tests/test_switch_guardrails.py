from __future__ import annotations

from battle.action import Action
from battle.battle_state import BattleState
from outcome import Outcome
from search.mcts import (
    MCTS,
    Node,
    _consecutive_switch_count,
    _previous_active_slot_if_last_action_was_switch,
    score_outcomes,
)


def _state() -> BattleState:
    return BattleState(
        player_hp=[50, 50, 50, 50, 50, 50],
        player_max_hp=[50, 50, 50, 50, 50, 50],
        player_fainted=[False, False, False, False, False, False],
        enemy_hp=[25, 0, 0, 0, 0, 0],
        enemy_max_hp=[50, 0, 0, 0, 0, 0],
        enemy_fainted=[False, True, True, True, True, True],
        battle_over=False,
        player_won=False,
        is_doubles=False,
        menu_ready=True,
    )


def _outcome(actions: list[Action], state: BattleState) -> Outcome:
    return Outcome(
        final_state=state,
        actions_taken=actions,
        instance_id=0,
        trial_id=0,
        frames_run=0,
        battle_won=False,
        player_fainted_count=0,
        enemy_fainted_count=0,
        final_player_hp=state.player_hp,
        final_enemy_hp=state.enemy_hp,
        is_sack_line=False,
    )


def test_score_penalizes_switch_spam() -> None:
    state = _state()
    move_line = score_outcomes([_outcome([Action.move(0), Action.move(1)], state)], state)
    switch_line = score_outcomes(
        [_outcome([Action.switch(1), Action.switch(0), Action.switch(1)], state)],
        state,
    )

    assert switch_line.score < move_line.score


def test_detects_previous_active_after_switch() -> None:
    assert _previous_active_slot_if_last_action_was_switch([(Action.switch(2),)]) == 0
    assert _previous_active_slot_if_last_action_was_switch(
        [(Action.switch(2),), (Action.move(0),)]
    ) is None
    assert _consecutive_switch_count([Action.switch(1), Action.switch(0), Action.move(0)]) == 1


def test_actions_for_node_blocks_immediate_switch_back() -> None:
    mcts = object.__new__(MCTS)
    mcts.initial_state = _state()
    mcts.root_actions = mcts.enumerator.legal_actions(mcts.initial_state) if hasattr(mcts, "enumerator") else []
    from search.action_enumerator import ActionEnumerator

    mcts.enumerator = ActionEnumerator()
    mcts.root_actions = mcts.enumerator.legal_actions(mcts.initial_state)

    root = Node(0, None)
    node = Node(1, (Action.switch(1),), parent=root)
    actions = MCTS._actions_for_node(mcts, node)

    assert (Action.switch(0),) not in actions
    assert (Action.switch(2),) not in actions
    assert all(action[0].is_move for action in actions)
