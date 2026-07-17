from __future__ import annotations

from battle.battle_state import BattleState
from battle.damage_calc import DamageCalculator
from optimizer.turn_planner import PlannedMember
from search.action_enumerator import ActionEnumerator
from trainer_data.models import TrainerBattle, TrainerPokemon
from web.server import (
    CalcSimRequest,
    _contingency_flowchart,
    _run_text_calc_sim_once_doubles,
    _trainer_for_calc_request,
)


def _doubles_state(**overrides) -> BattleState:
    values = dict(
        player_hp=[0, 80, 70, 90, 100, 0],
        player_max_hp=[100, 80, 70, 90, 100, 0],
        player_fainted=[True, False, False, False, False, False],
        enemy_hp=[0, 90, 0, 85, 0, 0],
        enemy_max_hp=[100, 90, 0, 85, 0, 0],
        enemy_fainted=[True, False, False, False, False, False],
        battle_over=False,
        player_won=False,
        is_doubles=True,
        menu_ready=True,
        player_move_names_by_slot=[[], ["Protect", "Tackle"], ["Thunderbolt", "Surf"], ["Thunderbolt"], ["Ice Beam"], []],
        player_active_slots=(0, 2),
        enemy_active_slots=(1, 3),
    )
    values.update(overrides)
    return BattleState(**values)


def test_doubles_actions_keep_field_slots_separate_from_party_slots() -> None:
    actions = ActionEnumerator().legal_actions(_doubles_state())

    assert actions
    # Player field slot 0 is fainted, so it must replace; field slot 1 can act.
    assert all(turn[0].is_switch and turn[0].actor_slot == 0 for turn in actions)
    assert any(turn[1].is_move and turn[1].actor_slot == 1 for turn in actions)
    # Enemy field positions 0 and 1 map to party indices 1 and 3, but targets
    # remain battlefield positions so emulator inputs stay stable after switches.
    single_target = [turn[1] for turn in actions if turn[1].is_move and turn[1].move_slot == 0]
    spread = [turn[1] for turn in actions if turn[1].is_move and turn[1].move_slot == 1]
    assert {action.target_slot for action in single_target} == {0, 1}
    assert {action.target_slot for action in spread} == {None}
    assert all(
        not (turn[0].is_switch and turn[1].is_switch and turn[0].switch_target == turn[1].switch_target)
        for turn in actions
    )


def test_custom_doubles_request_builds_and_reorders_opponent_leads() -> None:
    calculator = DamageCalculator()
    request = CalcSimRequest(
        trainer_id=-1,
        custom_trainer_name="Summer Cup Final",
        custom_enemy_imports="""Pikachu @ Light Ball
Ability: Static
Level: 50
- Thunderbolt

Charizard
Ability: Blaze
Level: 50
- Flamethrower

Gengar
Ability: Levitate
Level: 50
- Shadow Ball""",
        custom_is_double=True,
        enemy_leads=[2, 0],
    )

    trainer = _trainer_for_calc_request(request, calculator)

    assert trainer.trainer_name == "Summer Cup Final"
    assert trainer.is_double is True
    assert [member.species for member in trainer.party] == ["Gengar", "Pikachu", "Charizard"]


def _spread_test_battle() -> TrainerBattle:
    return TrainerBattle(
        section="test",
        location="test",
        trainer_name="Spread Test",
        is_double=True,
        party=(
            TrainerPokemon("Pikachu", 50, None, "Static", None, ("Protect",)),
            TrainerPokemon("Skarmory", 50, None, "Keen Eye", None, ("Protect",)),
        ),
    )


def _spread_test_team() -> list[PlannedMember]:
    return [
        PlannedMember("Blastoise", "Blastoise", 50, 180, 180, ("Surf",), ability="Torrent"),
        PlannedMember("Charizard", "Charizard", 50, 170, 170, ("Protect",), ability="Blaze"),
    ]


def test_player_all_adjacent_move_hits_both_foes_and_partner() -> None:
    result = _run_text_calc_sim_once_doubles(
        _spread_test_team(), _spread_test_battle(), DamageCalculator(),
        max_turns=1, forced_leads=(0, 1), compute_item_recs=False,
    )

    events = result["turns"][0]["calc"]
    assert "Blastoise uses Surf (spread) → Pikachu" in events
    assert "Blastoise uses Surf (spread) → Skarmory" in events
    assert "Blastoise uses Surf (spread) → Charizard" in events


def test_doubles_flowchart_branches_on_distinct_damage_roll_states() -> None:
    tree = _contingency_flowchart(
        _spread_test_team(), _spread_test_battle(), DamageCalculator(),
        max_turns=1, forced_doubles_leads=(0, 1), node_budget=12,
    )

    assert tree["_meta"]["battle_mode"] == "doubles"
    assert tree.get("fork", {}).get("type") == "doubles_damage"
    assert len(tree["fork"]["branches"]) >= 2
