from pathlib import Path

import pytest
import config

from battle.battle_state import BattleState
from battle.action import Action
from emulator.autonomy import CheckpointedGameRunner, RouteAction
from emulator.mgba_pool import PolicyDivergence, _resolve_policy_action
from emulator.planner_policy import compile_planner_policy
from outcome import PolicyAction
from search.checkpoint_beam import CheckpointBeamSearch


def _state() -> BattleState:
    return BattleState(
        player_hp=[50, 40], player_max_hp=[50, 40], player_fainted=[False, False],
        enemy_hp=[60], enemy_max_hp=[60], enemy_fainted=[False],
        battle_over=False, player_won=False, is_doubles=False, menu_ready=True,
        player_names=["Arcanine", "Palpitoad"],
        player_move_names=["Wild Charge", "Flare Blitz"],
        player_move_names_by_slot=[["Wild Charge", "Flare Blitz"]],
        player_active_slots=(0,),
    )


def test_named_move_resolves_from_live_moves() -> None:
    action = _resolve_policy_action(PolicyAction("move", move_name="wild-charge"), _state())
    assert action.move_slot == 0


def test_named_move_uses_active_party_slot_after_switch() -> None:
    state = BattleState(**{
        **_state().__dict__,
        "player_active_slots": (1,),
        "player_move_names": ["Surf", "Growl"],
        "player_move_names_by_slot": [[], ["Surf", "Growl"]],
    })
    action = _resolve_policy_action(PolicyAction("move", move_name="Surf"), state)
    assert action.move_slot == 0


def test_named_policy_fails_closed_after_divergence() -> None:
    with pytest.raises(PolicyDivergence, match="unavailable"):
        _resolve_policy_action(PolicyAction("move", move_name="Surf"), _state())


def test_named_switch_resolves_live_party() -> None:
    action = _resolve_policy_action(PolicyAction("switch", switch_to="palpitoad"), _state())
    assert action.switch_target == 1


class _FakeInstance:
    save_state_path = Path("source.ss0")

    def __init__(self) -> None:
        self.x = 4
        self.y = 7
        self.inputs: list[str] = []

    def read_u16(self, address: int) -> int:
        return self.x if address == config.RUN_BUN_PLAYER_X else self.y

    def send_input(self, button: str, frames: int) -> None:
        self.inputs.append(button)

    def advance_frames(self, frames: int) -> None:
        pass

    def write_u8(self, address: int, value: int) -> None:
        self.inputs.append(f"WRITE:{address:#x}={value}")

    def save_state(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"state")
        return path


def test_checkpointed_menu_script_is_resumable(tmp_path: Path) -> None:
    instance = _FakeInstance()
    runner = CheckpointedGameRunner(instance, tmp_path)
    run = runner.run("bag test", [RouteAction("open_bag"), RouteAction("close_menu")])
    assert run.status == "complete"
    assert len(run.checkpoints) == 3
    assert (tmp_path / run.run_id / "run.json").is_file()
    assert instance.inputs[:3] == ["START", f"WRITE:{config.RUN_BUN_START_MENU_CURSOR:#x}=2", "A"]


def test_compile_single_planner_line_uses_names() -> None:
    policy, warnings = compile_planner_policy({"team": [
        {"species": "Nidoqueen"}, {"species": "Palpitoad"},
    ], "turns": [
        {"turn": 1, "action": "Rah vs Arcanine: click Mud Shot."},
        {"turn": 2, "action": "Switch Rah -> Palpitoad."},
    ]})
    assert [step.kind for step in policy] == ["move", "switch"]
    assert policy[0].move_name == "Mud Shot"
    assert policy[1].switch_to == "Palpitoad"
    assert policy[1].switch_party_slot == 1
    assert warnings == []


def test_compile_doubles_planner_line_keeps_field_positions() -> None:
    policy, warnings = compile_planner_policy({"turns": [{"turn": 1, "slot_actions": [
        {"side": "player", "field_slot": 0, "kind": "move", "move": "Rock Slide"},
        {"side": "player", "field_slot": 1, "kind": "switch", "switch_to": "Lavos"},
        {"side": "enemy", "field_slot": 0, "kind": "move", "move": "Protect"},
    ]}]})
    assert len(policy) == 1 and len(policy[0]) == 2
    assert policy[0][0].actor_slot == 0
    assert policy[0][1].actor_slot == 1
    assert warnings == []


def test_checkpoint_beam_score_prioritizes_enemy_faints_and_player_hp() -> None:
    healthy = _state()
    chipped = BattleState(**{**healthy.__dict__, "player_hp": [20, 20]})
    enemy_down = BattleState(**{
        **healthy.__dict__, "enemy_hp": [0], "enemy_fainted": [True],
        "battle_over": True, "player_won": True,
    })
    assert CheckpointBeamSearch._score(healthy, 1) > CheckpointBeamSearch._score(chipped, 1)
    assert CheckpointBeamSearch._score(enemy_down, 2) > CheckpointBeamSearch._score(healthy, 1)


def test_checkpoint_beam_keeps_both_doubles_pivot_directions() -> None:
    actions = [
        (Action.move(0, actor_slot=0), Action.move(0, actor_slot=1)),
        (Action.move(0, actor_slot=0), Action.switch(2, actor_slot=1)),
        (Action.switch(2, actor_slot=0), Action.move(0, actor_slot=1)),
        (Action.switch(2, actor_slot=0), Action.switch(3, actor_slot=1)),
    ]

    selected = CheckpointBeamSearch._diverse_actions(actions, 4)

    assert selected == actions


def test_checkpoint_beam_line_tie_break_is_stable_with_optional_targets() -> None:
    first = ((Action.move(0, actor_slot=0),),)
    second = ((Action.switch(2, actor_slot=0),),)

    assert CheckpointBeamSearch._line_key(first) == CheckpointBeamSearch._line_key(first)
    assert sorted([second, first], key=CheckpointBeamSearch._line_key) == [first, second]
