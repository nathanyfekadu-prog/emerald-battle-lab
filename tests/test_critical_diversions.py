from __future__ import annotations

from battle.battle_state import BattleState
from outcome import Outcome, TurnSnapshot
from search.mcts import _critical_diversion_analysis


def _state() -> BattleState:
    return BattleState(
        player_hp=[100, 100], player_max_hp=[100, 100], player_fainted=[False, False],
        enemy_hp=[100], enemy_max_hp=[100], enemy_fainted=[False],
        battle_over=False, player_won=False, is_doubles=False, menu_ready=True,
        player_active_slots=(0,), enemy_active_slots=(0,),
    )


def _outcome(trial: int, damage: int, *, active: int = 0, won: bool = True, fainted: bool = False) -> Outcome:
    player_hp = [max(0, 100 - damage), 100]
    player_fainted = [fainted, False]
    snapshot = TurnSnapshot(
        turn=1, actions=[], player_hp=player_hp, player_max_hp=[100, 100],
        enemy_hp=[70], enemy_max_hp=[100], player_fainted=player_fainted,
        enemy_fainted=[False], battle_over=won, player_won=won,
        action_labels=["Pikachu uses Ice Beam"], player_active_slots=(active,),
        enemy_active_slots=(0,),
    )
    final = BattleState(
        player_hp=player_hp, player_max_hp=[100, 100], player_fainted=player_fainted,
        enemy_hp=[0 if won else 70], enemy_max_hp=[100], enemy_fainted=[won],
        battle_over=won, player_won=won, is_doubles=False, menu_ready=not won,
        player_active_slots=(active,), enemy_active_slots=(0,),
    )
    return Outcome(
        final_state=final, actions_taken=[], instance_id=0, trial_id=trial, frames_run=0,
        battle_won=won, player_fainted_count=int(fainted), enemy_fainted_count=int(won),
        final_player_hp=player_hp, final_enemy_hp=final.enemy_hp, is_sack_line=False,
        turn_snapshots=[snapshot],
    )


def test_safe_damage_spike_records_material_pivot() -> None:
    outcomes = [_outcome(1, 10), _outcome(2, 10), _outcome(3, 10), _outcome(4, 20, active=1)]
    result = _critical_diversion_analysis(outcomes, _state(), {1: 1, 2: 2, 3: 3, 4: 44})

    assert result["critical_safe"] is True
    assert result["diversion_rng_frames"] == 44
    assert result["material_diversions"][0]["state_changed"] is True


def test_damage_spike_that_faints_a_mon_banks_the_line() -> None:
    outcomes = [_outcome(1, 10), _outcome(2, 10), _outcome(3, 10), _outcome(4, 100, active=1, won=False, fainted=True)]
    result = _critical_diversion_analysis(outcomes, _state(), {1: 1, 2: 2, 3: 3, 4: 44})

    assert result["critical_safe"] is False
    assert result["critical_failure_rate"] > 0
    assert result["rescue_diversion"]["rng_frames"] == 44
    assert result["diversion_rng_frames"] == 44
