from __future__ import annotations

from dataclasses import replace
import json
import threading
from types import SimpleNamespace

from battle.damage_calc import DamageCalculator, PokemonCalcSet
from optimizer.turn_planner import (
    MoveChoice,
    PlannedEnemy,
    PlannedMember,
    PlayerAction,
    _apply_enemy_action,
    _apply_player_action,
    _apply_status_action,
    _best_player_action,
    _end_of_turn,
    _refresh_player_action,
)
from trainer_data.loader import load_trainer_battles
from trainer_data.models import TrainerBattle, TrainerPokemon
from optimizer.gen3_save import DecodedPokemon
import web.server as server
from web.server import (
    _apply_switch_out_effects,
    _best_calc_answer,
    _calc_switch_target,
    _clone_calc_team,
    _contingency_flowchart,
    _run_text_calc_sim_once,
    _doubles_lead_candidates,
    _doubles_lead_indices,
    _line_quality_key,
    _parse_imported_sets,
    _planned_enemies_for_trainer,
    _run_text_calc_sim,
    _run_text_calc_sim_once_doubles,
    _search_best_line_doubles,
)


def test_crit_aware_mode_is_the_default_for_new_lines_and_coach_steps() -> None:
    assert server.CalcSimRequest(trainer_id=0).crit_safe is True
    assert server.CompleteFlowchartRequest(trainer_id=0).crit_safe is True
    assert server.CoachStepRequest(trainer_id=0).crit_safe is True
    assert server.config.NUZLOCKE_GRAVEYARD_BOX == 14


def test_corgi_to_chelle_has_reusable_cartridge_playbook() -> None:
    playbook = server._matching_gauntlet_playbook(load_trainer_battles()[47:55])
    assert playbook is not None
    payload = json.loads(playbook.read_text(encoding="utf-8"))
    assert len(payload["trainers"]) == 8
    assert len(payload["transitions"]) == 7
    assert payload["transitions"][5]["preparation"]["item_donor_boxes"] == [1]
    assert all(
        member.get("box") != 14
        for transition in payload["transitions"]
        for member in (transition.get("preparation") or {}).get("party", [])
    )


def test_cartridge_gauntlet_never_reuses_an_old_video() -> None:
    request = server.GauntletSimRequest(
        trainer_ids=[47, 48], rom="run-and-bun.gba", pc_state="before-corgi.ss0"
    )
    assert server._cached_gauntlet_run(request) is None


def test_gauntlet_between_battle_rules_preserve_damage_and_consumed_items() -> None:
    member = PlannedMember(
        "Route lead", "Pikachu", 30, 100, 37, ("Thunderbolt",),
        item="Oran Berry", status="brn", boosts={"atk": 2}, confused_turns=3,
        consumed_item=True,
    )

    back_to_back = server._clear_between_battle_effects(member, heal=False)
    assert (back_to_back.hp, back_to_back.status, back_to_back.consumed_item) == (37, "brn", True)
    assert back_to_back.boosts == {}
    assert back_to_back.confused_turns == 0

    healed = server._clear_between_battle_effects(member, heal=True)
    assert (healed.hp, healed.status, healed.consumed_item) == (100, None, True)


def test_gauntlet_hands_final_hp_to_the_next_trainer(monkeypatch) -> None:
    team = [PlannedMember("Route lead", "Pikachu", 30, 100, 100, ("Thunderbolt",), slot=4)]
    trainers = [
        TrainerBattle("test", "Route 116", "First", False, ()),
        TrainerBattle("test", "Route 116", "Second", False, ()),
    ]

    def result_for(roster, hp):
        payload = server._member_payload(roster[0])
        payload.update(hp=hp, status="brn", consumed_item=True)
        return {"result": "win-line", "confidence": 0.9, "team": [payload], "turns": []}

    monkeypatch.setattr(server, "_run_text_calc_sim_with_team_select", lambda roster, *_args, **_kwargs: result_for(roster, 41))

    def second_fight(roster, *_args, **_kwargs):
        assert roster[0].hp == 41
        assert roster[0].status == "brn"
        assert roster[0].consumed_item is True
        return result_for(roster, 25)

    monkeypatch.setattr(server, "_run_text_calc_sim", second_fight)
    result = server._run_calc_gauntlet(
        team, trainers, DamageCalculator(), max_turns=10,
        force_enemy_crits=True, heal_between=False,
    )

    assert result["result"] == "route-complete"
    assert result["fights"][1]["starting_team"][0]["hp"] == 41
    assert result["final_team"][0]["hp"] == 25
    # Calculator planning is only 55% of a finished gauntlet. The API layer
    # owns the remaining cartridge replays + approved video gate.
    assert server._GAUNTLET_PROGRESS["pct"] == 55.0
    assert server._GAUNTLET_PROGRESS["completed"] == 2
    assert server._GAUNTLET_PROGRESS["running"] is True
    assert server._GAUNTLET_PROGRESS["stage"] == "cartridge-proof"


def test_healed_gauntlet_reselects_from_full_box_before_each_fight(monkeypatch) -> None:
    box = [
        PlannedMember("First answer", "Pikachu", 30, 100, 100, ("Thunderbolt",), slot=1),
        PlannedMember("Second answer", "Raichu", 30, 110, 110, ("Thunderbolt",), slot=2),
    ]
    trainers = [
        TrainerBattle("test", "Route 116", "First", False, ()),
        TrainerBattle("test", "Route 116", "Second", False, ()),
    ]
    calls = 0

    def select(roster, *_args, **_kwargs):
        nonlocal calls
        assert len(roster) == 2
        index = calls
        calls += 1
        payload = server._member_payload(roster[index])
        payload["hp"] = 50
        return {
            "result": "win-line", "confidence": 1.0, "team": [payload], "turns": [],
            "team_selection": {"indices": [index], "chosen": [roster[index].name]},
        }

    monkeypatch.setattr(server, "_run_text_calc_sim_with_team_select", select)
    result = server._run_calc_gauntlet(
        box, trainers, DamageCalculator(), max_turns=10,
        force_enemy_crits=True, heal_between=True,
    )

    assert calls == 2
    assert [fight["starting_team"][0]["name"] for fight in result["fights"]] == [
        "First answer", "Second answer",
    ]
    assert result["fights"][1]["preparation"]["box_visit"] is True


def test_gauntlet_item_transfer_swaps_owned_copy_without_duplication() -> None:
    donor = PlannedMember("Lavos", "Monferno", 32, 90, 90, ("Mach Punch",), item="Sitrus Berry", slot=1)
    target = PlannedMember("Disdain", "Mightyena", 31, 88, 88, ("Bite",), slot=2)
    roster, party, applied, error = server._apply_owned_item_changes(
        [donor, target], [target],
        [{"pokemon": "Disdain", "new_item": "Sitrus Berry", "reason": "survive Slowbro"}],
    )

    assert error is None
    assert applied[0]["from"] == "Lavos"
    assert party[0].item == "Sitrus Berry"
    assert [member.item for member in roster].count("Sitrus Berry") == 1


def test_live_gauntlet_roster_excludes_every_box_14_record(monkeypatch) -> None:
    alive = DecodedPokemon(
        "Alive", 25, "pikachu", 30, box=1, slot=1,
        moves=("Thunderbolt",), max_hp=80,
    )
    dead = DecodedPokemon(
        "Dead", 25, "pikachu", 30, box=14, slot=1,
        moves=("Thunderbolt",), max_hp=80, held_item="Sitrus Berry",
    )
    monkeypatch.setattr(server, "_validate_paths", lambda *_args: None)
    monkeypatch.setattr(
        server, "scan_pc_boxes",
        lambda *_args, **_kwargs: SimpleNamespace(party=[], roster=[alive, dead]),
    )

    roster = server._planned_box_from_pc_state("rom", "state", DamageCalculator())

    assert [member.name for member in roster] == ["Alive (pikachu)"]
    assert all(member.item != "Sitrus Berry" for member in roster)


def test_live_gauntlet_roster_uses_save_item_not_text_import(monkeypatch) -> None:
    alive = DecodedPokemon(
        "Alive", 25, "pikachu", 30, box=1, slot=1,
        moves=("Thunderbolt",), max_hp=80, held_item="Sitrus Berry",
    )
    monkeypatch.setattr(server, "_validate_paths", lambda *_args: None)
    monkeypatch.setattr(
        server, "scan_pc_boxes",
        lambda *_args, **_kwargs: SimpleNamespace(party=[], roster=[alive]),
    )

    roster = server._planned_box_from_pc_state(
        "rom", "state", DamageCalculator(), {"pikachu": "Static"}
    )

    assert roster[0].item == "Sitrus Berry"


def _fixed_damage(move: str, amount: int) -> server.DamageRange:
    return server.DamageRange(
        move_name=move, min_damage=amount, max_damage=amount, rolls=(amount,),
        min_percent=0.0, max_percent=0.0, average_percent=0.0,
        ko_chance=0.0, type_multiplier=1.0, accuracy=1.0,
        expected_damage=float(amount), effective_power=40,
        attack_stat=100, defense_stat=100,
    )


def test_oran_berry_heals_immediately_after_the_hit() -> None:
    calculator = DamageCalculator()
    player = PlannedMember(
        "Scizor", "Scizor", 27, 100, 55, ("Bullet Punch",), item="Oran Berry",
    )
    enemy = PlannedEnemy(
        "Arcanine", PokemonCalcSet("Arcanine", level=27, hp=100, max_hp=100),
        ("Wild Charge",), 100, 100,
    )
    choice = MoveChoice("Wild Charge", 1.0, 1.0, _fixed_damage("Wild Charge", 10), "test")

    assert _apply_enemy_action(enemy, player, choice, calculator) == 10
    assert player.hp == 55  # 55 - 10 + the Oran Berry's 10 HP
    assert player.consumed_item is True


def test_enemy_recoil_moves_the_foe_into_the_players_ko_range() -> None:
    calculator = DamageCalculator()
    player = PlannedMember(
        "Growlithe-Hisui", "Growlithe-Hisui", 27, 90, 90,
        ("Rock Slide",), item="Oran Berry", ability="Rock Head",
    )
    enemy = PlannedEnemy(
        "Arcanine", PokemonCalcSet("Arcanine", level=27, hp=93, max_hp=93),
        ("Wild Charge",), 93, 93,
    )
    wild_charge = MoveChoice("Wild Charge", 1.0, 1.0, _fixed_damage("Wild Charge", 40), "test")
    rock_slide = PlayerAction("move", "Rock Slide", score=100.0, damage=_fixed_damage("Rock Slide", 85))

    _apply_enemy_action(enemy, player, wild_charge, calculator)
    assert enemy.hp == 83  # Wild Charge dealt 40, so Arcanine took 10 recoil.
    assert _apply_player_action(player, enemy, rock_slide, calculator) == 83
    assert not enemy.alive


def test_line_fork_uses_post_recoil_hp_for_same_turn_ko(monkeypatch) -> None:
    calculator = DamageCalculator()
    trainer = TrainerBattle(
        section="test", location="Route 116", trainer_name="Recoil threshold", is_double=False,
        party=(TrainerPokemon("Arcanine", 27, None, "Intimidate", None, ("Wild Charge",)),),
    )
    team = [PlannedMember(
        "Growlithe-Hisui", "Growlithe-Hisui", 27, 90, 90,
        ("Rock Slide",), item="Oran Berry", ability="Rock Head",
    )]
    action = PlayerAction("move", "Rock Slide", score=100.0, damage=_fixed_damage("Rock Slide", 85), reason="test")
    choice = MoveChoice("Wild Charge", 1.0, 1.0, _fixed_damage("Wild Charge", 40), "test")
    monkeypatch.setattr(server, "_best_player_action", lambda *args, **kwargs: action)
    monkeypatch.setattr(server, "_refresh_player_action", lambda *args, **kwargs: action)
    monkeypatch.setattr(server, "_calc_enemy_choices", lambda *args, **kwargs: [choice])
    monkeypatch.setattr(server, "_calc_switch_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_ai_hard_switch_target", lambda *args, **kwargs: None)

    result = _run_text_calc_sim_once(team, trainer, calculator, max_turns=1, forced_lead=0, compute_item_recs=False)
    turn = result["turns"][0]

    assert "takes 10 recoil" in turn["calc"]
    assert "Arcanine faints" in turn["calc"]
    assert turn["fork"]["player_ko_chance"] == 1.0
    assert turn["fork"]["player_damage_outcomes"] == [
        {"damage": 83, "remaining_hp": 0, "probability": 1.0, "miss": False}
    ]


def test_set_mode_keeps_the_player_active_after_an_enemy_ko(monkeypatch) -> None:
    calculator = DamageCalculator()
    trainer = TrainerBattle(
        section="test", location="Route 116", trainer_name="Set mode test", is_double=False,
        party=(
            TrainerPokemon("Pikachu", 5, None, None, None, ("Growl",)),
            TrainerPokemon("Lucario", 5, None, None, None, ("Growl",)),
        ),
    )
    team = [
        PlannedMember("Scizor", "Scizor", 27, 100, 45, ("Bullet Punch",)),
        PlannedMember("Palpitoad", "Palpitoad", 27, 100, 100, ("Mud Shot",)),
    ]
    monkeypatch.setattr(server, "_calc_switch_target", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_best_player_action", lambda active, enemy, *_args, **_kwargs: PlayerAction(
        "move", active.known_moves[0], score=100.0,
        damage=_fixed_damage(active.known_moves[0], 999), reason="test KO",
    ))
    monkeypatch.setattr(server, "_calc_enemy_choices", lambda *args, **kwargs: [])

    result = _run_text_calc_sim_once(team, trainer, calculator, max_turns=2, forced_lead=0, compute_item_recs=False)

    assert len(result["turns"]) == 2
    assert all(turn["answer"] == "Scizor" for turn in result["turns"])
    assert "Trainer sends Lucario" in result["turns"][0]["calc"]
    assert "Forced send" not in result["turns"][1]["action"]


def test_corgi_uses_route_116_and_never_recommends_unavailable_sitrus() -> None:
    calculator = DamageCalculator()
    trainer = next(b for b in load_trainer_battles() if b.trainer_name == "Breeder Corgi")
    team = [PlannedMember("Scizor", "Scizor", 27, 100, 100, ("Bullet Punch",), item="Oran Berry")]
    recs = server._recommend_items_for_trainer(
        team, server._planned_enemies_for_trainer(trainer, calculator), calculator, trainer,
    )

    assert trainer.location == "Route 116"
    assert all(rec["suggested_item"] != "Sitrus Berry" for rec in recs)


def test_scarce_sitrus_requires_a_line_changing_reliability_gain() -> None:
    assert not server._scarce_sitrus_is_worth_it(
        {"result": "win-line", "confidence": 0.80},
        {"result": "win-line", "confidence": 0.90},
    )
    assert server._scarce_sitrus_is_worth_it(
        {"result": "partial-line", "confidence": 0.40},
        {"result": "win-line", "confidence": 0.99},
    )


def test_calc_sim_can_run_in_crit_safe_mode() -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[0]
    team = [
        PlannedMember(
            name="Test answer",
            species="Charizard",
            level=50,
            max_hp=150,
            hp=150,
            moves=("Flamethrower", "Slash", "Protect", "Will-O-Wisp"),
            item="Charcoal",
            ability="Blaze",
        )
    ]

    result = _run_text_calc_sim(team, trainer, calculator, max_turns=2, force_enemy_crits=True)

    assert result["crit_safe"] is True
    assert "crit-aware" in result["risk_policy"].casefold()
    assert result["turns"]
    assert any("Crit-aware mode is ON" in risk for turn in result["turns"] for risk in turn["risks"])


def test_player_move_override_is_kept_in_line_and_flowchart() -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[0]
    team = [PlannedMember(
        name="Test answer", species="Charizard", level=50, max_hp=150, hp=150,
        moves=("Flamethrower", "Slash", "Protect", "Recover"),
    )]

    result = _run_text_calc_sim_once(
        team, trainer, calculator, max_turns=1, forced_lead=0,
        player_move_overrides={1: "Slash"}, compute_item_recs=False,
    )
    assert any("Slash" in (turn.get("action") or "") for turn in result["turns"])

    tree = _contingency_flowchart(
        team, trainer, calculator, max_turns=2, forced_lead=0,
        player_move_overrides={1: "Slash"}, exhaustive=True, node_budget=20,
    )
    actions = [
        step.get("action") or ""
        for node in _walk_nodes(tree)
        for step in node.get("steps") or []
        if step.get("turn") == 1
    ]
    assert actions and all("Slash" in action for action in actions)


def test_damage_outcomes_keep_real_roll_states_and_collapse_guaranteed_kos() -> None:
    damage = server.DamageRange(
        move_name="Test Hit", min_damage=10, max_damage=20,
        rolls=(10, 10, 20, 20), min_percent=10, max_percent=20,
        average_percent=15, ko_chance=0, type_multiplier=1,
        accuracy=0.5, expected_damage=7.5, effective_power=40,
        attack_stat=100, defense_stat=100,
    )

    outcomes = server._damage_outcomes(damage, 15)
    assert outcomes == [
        {"damage": 0, "remaining_hp": 15, "probability": 0.5, "miss": True},
        {"damage": 10, "remaining_hp": 5, "probability": 0.25, "miss": False},
        {"damage": 15, "remaining_hp": 0, "probability": 0.25, "miss": False},
    ]

    guaranteed = replace(damage, rolls=(20,) * 16, accuracy=1.0)
    assert server._damage_outcomes(guaranteed, 15) == [
        {"damage": 15, "remaining_hp": 0, "probability": 1.0, "miss": False},
    ]


def test_exact_damage_override_preserves_the_observed_hp_state() -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[0]
    team = [PlannedMember(
        name="Range tester", species="Charizard", level=50, max_hp=150, hp=150,
        moves=("Slash",),
    )]

    low = _run_text_calc_sim_once(
        team, trainer, calculator, max_turns=1, forced_lead=0,
        player_move_overrides={1: "Slash"}, player_damage_overrides={1: 1},
        compute_item_recs=False,
    )
    high = _run_text_calc_sim_once(
        team, trainer, calculator, max_turns=1, forced_lead=0,
        player_move_overrides={1: "Slash"}, player_damage_overrides={1: 5},
        compute_item_recs=False,
    )

    assert "for 1" in low["turns"][0]["calc"]
    assert "for 5" in high["turns"][0]["calc"]
    assert low["turns"][0]["enemy_hp"] != high["turns"][0]["enemy_hp"]


def test_exhaustive_flowchart_branches_real_damage_ranges_and_both_sides_crits() -> None:
    tree = _contingency_flowchart(
        [PlannedMember("Range tester", "Charmander", 5, 20, 20, ("Scratch",))],
        load_trainer_battles()[0], DamageCalculator(),
        max_turns=1, forced_lead=0, exhaustive=True, node_budget=-1,
    )
    forks = [node["fork"] for node in _walk_nodes(tree) if node.get("fork")]
    fork_types = {fork["type"] for fork in forks}

    assert {"player_crit", "crit", "player_damage", "enemy_damage"} <= fork_types
    damage_labels = [
        branch["label"]
        for fork in forks if fork["type"] in {"player_damage", "enemy_damage"}
        for branch in fork["branches"]
    ]
    assert any("deals" in label and "HP" in label for label in damage_labels)
    assert tree["_meta"]["complete"] is True


def test_crit_aware_plan_still_flowcharts_real_crit_and_noncrit_outcomes() -> None:
    tree = _contingency_flowchart(
        [PlannedMember("Range tester", "Charmander", 5, 20, 20, ("Scratch",))],
        load_trainer_battles()[0], DamageCalculator(),
        max_turns=1, forced_lead=0, force_enemy_crits=True,
        exhaustive=True, node_budget=-1,
    )
    forks = [node["fork"] for node in _walk_nodes(tree) if node.get("fork")]
    enemy_crit_forks = [fork for fork in forks if fork["type"] == "crit"]

    assert enemy_crit_forks, "crit-aware planning must not erase real non-crit battle paths"
    labels = [branch["label"] for fork in enemy_crit_forks for branch in fork["branches"]]
    assert any("crits with" in label for label in labels)
    assert any("No crit" in label for label in labels)


def test_flowchart_branches_the_ai_hard_switch_coin_flip(monkeypatch) -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[47]
    team = [PlannedMember(
        "Test answer", "Charizard", 50, 150, 150,
        ("Flamethrower", "Slash"),
    )]
    monkeypatch.setattr(
        server, "_ai_hard_switch_target",
        lambda enemies, enemy_index, active, calculator: 1 if enemy_index == 0 else None,
    )

    tree = _contingency_flowchart(
        team, trainer, calculator, max_turns=1, forced_lead=0,
        exhaustive=True, node_budget=20,
    )

    assert tree["fork"]["type"] == "ai_switch"
    labels = [branch["label"] for branch in tree["fork"]["branches"]]
    assert any("hard-switches" in label for label in labels)
    assert any("stays in" in label for label in labels)
    assert {branch["probability"] for branch in tree["fork"]["branches"]} == {0.5}


def _collect_branches(node: dict) -> list[dict]:
    """Flatten every branch edge in a contingency tree."""
    out: list[dict] = []
    fork = node.get("fork")
    if fork:
        for branch in fork["branches"]:
            out.append(branch)
            out.extend(_collect_branches(branch["node"]))
    return out


def test_contingency_flowchart_branches_on_every_realistic_enemy_move() -> None:
    # Every move with a nontrivial probability under the score-roll AI model gets
    # an explicit branch instead of a lossy "Other moves" catch-all.
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[0]
    team = [
        PlannedMember(
            name="Test answer",
            species="Charizard",
            level=50,
            max_hp=150,
            hp=150,
            moves=("Flamethrower", "Slash", "Protect", "Will-O-Wisp"),
            item="Charcoal",
            ability="Blaze",
        )
    ]

    tree = _contingency_flowchart(team, trainer, calculator, max_turns=12)
    branches = _collect_branches(tree)

    move_branches = [b for b in branches if " uses " in b["label"]]
    assert move_branches, "expected explicit per-move branches"
    assert all(b["probability"] >= 0.02 for b in move_branches)
    assert all("Other moves" not in b["label"] for b in move_branches)


def test_coach_recommends_lead_and_predicts_enemy() -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[47]
    enemies = server._planned_enemies_for_trainer(trainer, calculator)
    team = [
        PlannedMember("Drednaw", "Drednaw", 50, 160, 160, ("Waterfall", "Rock Slide"), item="Oran Berry"),
        PlannedMember("Nidoqueen", "Nidoqueen", 50, 180, 180, ("Mud Shot", "Sludge Bomb"), item="Oran Berry"),
    ]
    req = server.CoachStepRequest(trainer_id=47, player_active=None, enemy_active=0)

    res = server._coach_step(team, enemies, trainer, req, calculator)

    # It picks a concrete lead and a concrete action, and exposes rosters for the dropdowns.
    assert 0 <= res["player_active"] < len(team)
    assert res["recommendation"]["label"]
    assert len(res["team"]) == len(team) and len(res["enemies"]) == len(enemies)
    # It predicts the foe's moves, each with a projected resulting position.
    assert res["enemy_prediction"]
    for pred in res["enemy_prediction"]:
        assert "projection" in pred and "player_hp" in pred["projection"]


def test_coach_step_uses_reported_state() -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[47]
    enemies = server._planned_enemies_for_trainer(trainer, calculator)
    team = [PlannedMember("Drednaw", "Drednaw", 50, 160, 160, ("Waterfall", "Rock Slide"), item="Oran Berry")]
    # Report a specific live position: our mon chipped, the foe near death.
    req = server.CoachStepRequest(
        trainer_id=47, player_active=0, player_hp=70,
        enemy_active=0, enemy_hp=max(1, enemies[0].max_hp // 6),
    )

    res = server._coach_step(team, enemies, trainer, req, calculator)

    assert res["player_hp"] == 70
    assert res["enemy_hp"] == max(1, enemies[0].max_hp // 6)
    # Against a nearly-dead foe the recommended damaging move should read as a KO.
    assert res["recommendation"]["kind"] in {"move", "switch"}


def _walk_nodes(node: dict) -> list[dict]:
    out = [node]
    fork = node.get("fork")
    if fork:
        for branch in fork["branches"]:
            out.extend(_walk_nodes(branch["node"]))
    return out


def test_contingency_flowchart_branches_deep_and_stays_bounded() -> None:
    # Breeder Corgi (the long, branchy fight that motivated this): the chart must keep
    # forking on enemy moves several turns deep — not collapse to a single line after a
    # couple of turns — while state memoization keeps it finite. Converged lines link
    # back through compact merge-leaves instead of re-expanding (which used to explode).
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[47]
    assert "Corgi" in trainer.trainer_name
    team = [
        PlannedMember("Nidoqueen", "Nidoqueen", 50, 180, 180, ("Mud Shot", "Sludge Bomb"), item="Oran Berry"),
        PlannedMember("Drednaw", "Drednaw", 50, 160, 160, ("Rock Slide", "Waterfall"), item="Oran Berry"),
    ]

    tree = _contingency_flowchart(team, trainer, calculator, max_turns=30)
    nodes = _walk_nodes(tree)

    # Forks reach several turns deep, not just turn 1.
    fork_turns = {n["fork"]["turn"] for n in nodes if n.get("fork")}
    assert max(fork_turns) >= 4
    # Memoization is doing real work: reconverged lines become merge-leaves.
    assert any(n.get("outcome") == "merge" for n in nodes)
    state_ids = {n.get("state_id") for n in nodes if n.get("state_id")}
    assert all(
        n.get("merge_state_id") in state_ids
        for n in nodes if n.get("outcome") == "merge"
    ), "every merge leaf must link to its shared continuation"
    # Bounded: no exponential blow-up in the serialized tree.
    assert len(nodes) < 2000


def test_contingency_flowchart_always_terminates_and_marks_cut_lines() -> None:
    # The user-facing chart runs in exhaustive mode (every nonzero-probability enemy move
    # branches, no catch-all). Bounded best-first expansion must still return promptly,
    # and any line the budget could not reach must be closed with an explicit truncation
    # leaf pointing at the Co-pilot — never a silent dead end or a missing node.
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[47]
    team = [
        PlannedMember("Nidoqueen", "Nidoqueen", 50, 180, 180, ("Mud Shot", "Sludge Bomb"), item="Oran Berry"),
        PlannedMember("Drednaw", "Drednaw", 50, 160, 160, ("Rock Slide", "Waterfall"), item="Oran Berry"),
    ]

    tree = _contingency_flowchart(
        team, trainer, calculator, max_turns=30, exhaustive=True, node_budget=40,
    )
    nodes = _walk_nodes(tree)

    assert all(n is not None for n in nodes), "every branch slot must be filled"
    leaves = [n for n in nodes if not n.get("fork")]
    assert leaves
    for leaf in leaves:
        assert leaf.get("outcome") in {"win-line", "partial-line", "merge", "truncated"}
    truncated = [n for n in nodes if n.get("outcome") == "truncated"]
    assert truncated, "a 40-node budget on Corgi must truncate some rare tails"
    assert all("Co-pilot" in (n.get("note") or "") for n in truncated)
    # No lossy catch-all in exhaustive mode: every branch is a concrete outcome.
    branch_labels = [b["label"] for b in _collect_branches(tree)]
    assert all("Other moves" not in label for label in branch_labels)


def test_uncapped_contingency_flowchart_expands_every_nonzero_branch() -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[0]
    team = _parse_imported_sets(server.SAMPLE_CALC_IMPORT, calculator)[:6]
    progress: list[tuple[int, int, bool]] = []
    tree = _contingency_flowchart(
        team, trainer, calculator, max_turns=8, exhaustive=True,
        node_budget=-1, time_budget_s=None,
        progress_callback=lambda expanded, queued, complete=False: progress.append((expanded, queued, complete)),
    )
    assert tree["_meta"]["complete"] is True
    assert tree["_meta"]["truncated_branches"] == 0
    assert progress
    assert progress[-1] == (tree["_meta"]["expanded_nodes"], 0, True)


def test_exhaustive_flowchart_keeps_sub_one_percent_ai_moves(monkeypatch) -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[0]
    team = [PlannedMember("Lead", "Charizard", 50, 150, 150, ("Slash",))]

    def fake_line(*args, **kwargs):
        pinned = (kwargs.get("enemy_move_overrides") or {}).get(1)
        row = {
            "turn": 1, "answer": "Lead", "enemy": "Foe", "action": "Use Slash",
            "calc": "", "your_hp": "150/150", "enemy_hp": "10/10", "state_sig": "start",
        }
        if pinned is None:
            row["fork"] = {"enemy_alternatives": [
                {"move": "Common", "probability": 0.9996},
                {"move": "Tiny", "probability": 0.0004},
            ]}
        return {"turns": [row], "result": "partial-line", "confidence": 1.0}

    monkeypatch.setattr(server, "_run_text_calc_sim_once", fake_line)
    tree = _contingency_flowchart(
        team, trainer, calculator, max_turns=1, forced_lead=0,
        exhaustive=True, node_budget=-1,
    )

    tiny = next(branch for branch in tree["fork"]["branches"] if "Tiny" in branch["label"])
    assert tiny["probability"] == 0.0004
    assert "<0.1%" in tiny["label"]


def test_contingency_flowchart_can_stop_and_preserve_partial_tree() -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[47]
    team = [
        PlannedMember("Nidoqueen", "Nidoqueen", 50, 180, 180, ("Mud Shot", "Sludge Bomb")),
        PlannedMember("Drednaw", "Drednaw", 50, 160, 160, ("Rock Slide", "Waterfall")),
    ]
    cancel = threading.Event()
    cancel.set()

    tree = _contingency_flowchart(
        team, trainer, calculator, max_turns=30, exhaustive=True,
        node_budget=-1, cancel_event=cancel,
    )

    assert tree["_meta"]["cancelled"] is True
    assert tree["_meta"]["complete"] is False
    assert all(node is not None for node in _walk_nodes(tree))


def test_contingency_steps_keep_names_for_current_state_search() -> None:
    step = server._contingency_step({
        "turn": 3, "answer": "Arcanine", "enemy": "Skarmory",
        "action": "Use Wild Charge", "calc": "85-101", "your_hp": "80/100",
    })

    assert step["answer"] == "Arcanine"
    assert step["enemy"] == "Skarmory"


def test_flowchart_progress_is_monotonic_and_reports_completion() -> None:
    server._flowchart_progress_reset()
    server._flowchart_progress_update(10, 30)
    first = dict(server._FLOWCHART_PROGRESS)
    server._flowchart_progress_update(20, 50)
    second = dict(server._FLOWCHART_PROGRESS)
    server._flowchart_progress_update(70, 0, complete=True)
    final = dict(server._FLOWCHART_PROGRESS)
    assert second["pct"] >= first["pct"]
    assert final["pct"] == 100.0
    assert final["complete"] is True
    assert final["running"] is False


def test_strategy_report_detects_extended_tactical_playbook() -> None:
    result = {
        "turns": [
            {"turn": 1, "answer": "A", "enemy": "X", "action": "A vs X: click Protect.", "calc": "", "risks": [], "consistency": "stateful-calc"},
            {"turn": 2, "answer": "A", "enemy": "X", "action": "A vs X: click Reflect.", "calc": "", "risks": [], "consistency": "stateful-calc"},
            {"turn": 3, "answer": "A", "enemy": "X", "action": "A vs X: click Wild Charge.", "calc": "A takes 20 recoil", "risks": [], "consistency": "stateful-calc"},
            {"turn": 4, "answer": "B", "enemy": "Y", "action": "B vs Y: click Encore.", "calc": "", "risks": ["enemy survives on a low roll"], "consistency": "stateful-calc"},
            {"turn": 5, "answer": "B", "enemy": "Y", "action": "B vs Y: click U-turn.", "calc": "", "risks": [], "consistency": "stateful-calc"},
        ],
        "threat_answers": [],
    }
    report = server._build_strategy_report(result)
    used = {row["id"] for row in report if row["used"]}
    assert {"protect-scout", "screen-support", "recoil-budget", "move-lock", "roll-proof", "momentum-move"} <= used
    modeled = {row["id"]: row["modeled"] for row in report}
    assert modeled["protect-scout"] is True
    assert modeled["screen-support"] is False
    assert modeled["move-lock"] is False


def test_singles_line_search_explores_alternate_leads(monkeypatch) -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[0]
    team = _parse_imported_sets(server.SAMPLE_CALC_IMPORT, calculator)[:3]
    seen_leads: list[int | None] = []
    real = server._run_text_calc_sim_once

    def spy(*args, **kwargs):
        seen_leads.append(kwargs.get("forced_lead"))
        return real(*args, **kwargs)

    monkeypatch.setattr(server, "_run_text_calc_sim_once", spy)
    server._search_best_line(team, trainer, calculator, max_turns=4, budget=8)
    assert {0, 1, 2} <= {lead for lead in seen_leads if lead is not None}


def test_kill_fork_no_ko_branch_is_a_miss_when_min_roll_kills() -> None:
    # ko_chance is damage rolls x accuracy, so when even the MIN roll would KO, the only
    # "no KO" outcome left is a miss. The pinned no-KO timeline used to leave the foe at
    # 1 HP with the damage applied — a state that cannot occur in the real game. It must
    # instead undo the attack entirely and say the move missed.
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[0]  # Youngster Calvin's low-level mons
    # Hydro Pump is 85% accurate in Run & Bun, so ko_chance is 0.85 with every roll a KO.
    team = [
        PlannedMember("Sweeper", "Drednaw", 50, 160, 160, ("Hydro Pump",), item="Oran Berry"),
    ]

    base = _run_text_calc_sim_once(
        _clone_calc_team(team), trainer, calculator, max_turns=6, compute_item_recs=False,
    )
    miss_turns = [
        row["turn"] for row in base["turns"]
        if (row.get("fork") or {}).get("player_no_ko_means_miss")
        and 0.0 < float((row.get("fork") or {}).get("player_ko_chance") or 0.0) < 1.0
    ]
    assert miss_turns, "a min-roll OHKO with a <100% accuracy move must flag the miss fork"

    turn = miss_turns[0]
    pinned = _run_text_calc_sim_once(
        _clone_calc_team(team), trainer, calculator, max_turns=6,
        kill_overrides={turn: False}, compute_item_recs=False,
    )
    pinned_row = next(row for row in pinned["turns"] if row["turn"] == turn)
    assert "misses" in (pinned_row.get("calc") or ""), "the pinned no-KO turn must be a miss"
    assert "1 HP" not in (pinned_row.get("calc") or "")
    # The foe is untouched, so the same enemy is still up next turn at full-ish HP —
    # there must be no phantom '1 HP survivor'.
    assert "survives" not in (pinned_row.get("calc") or "")


def test_calc_switch_searches_past_doomed_best_attacker() -> None:
    calculator = DamageCalculator()
    enemy = PlannedEnemy(
        name="Garchomp",
        pokemon=PokemonCalcSet("Garchomp", level=50, hp=180, max_hp=180),
        moves=("Earthquake",),
        max_hp=180,
        hp=180,
    )
    team = [
        PlannedMember("Doomed lead", "Pikachu", 50, 100, 1, ("Thunderbolt",)),
        PlannedMember("Safe pivot", "Charizard", 50, 150, 150, ("Flamethrower",)),
        PlannedMember("Tempting attacker", "Abomasnow", 50, 140, 40, ("Ice Beam",)),
    ]

    assert _calc_switch_target(team, 0, enemy, calculator) == 1


def test_calc_reports_tactical_sac_when_no_pivot_survives() -> None:
    calculator = DamageCalculator()
    trainer = TrainerBattle(
        section="test",
        location="test",
        trainer_name="Test Trainer",
        is_double=False,
        party=(TrainerPokemon("Garchomp", 50, None, None, None, ("Earthquake",)),),
    )
    team = [PlannedMember("Doomed lead", "Pikachu", 50, 100, 1, ("Thunderbolt",))]

    result = _run_text_calc_sim(team, trainer, calculator, max_turns=1)

    assert result["turns"][0]["consistency"] == "tactical-sac"
    assert "Tactical sac" in result["turns"][0]["action"]
    assert any("No safe pivot" in risk for risk in result["turns"][0]["risks"])


def test_calc_sim_recommends_split_available_held_items() -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[47]
    team = [
        PlannedMember("Nidoqueen", "Nidoqueen", 32, 109, 109, ("Mud Shot", "Sludge Bomb"), item="Oran Berry"),
        PlannedMember("Drednaw", "Drednaw", 32, 109, 109, ("Rock Tomb", "Water Gun")),
    ]

    result = _run_text_calc_sim(team, trainer, calculator, max_turns=1)
    recommendations = {item["pokemon"]: item for item in result["item_recommendations"]}

    assert recommendations["Nidoqueen"]["suggested_item"] == "Soft Sand"
    assert "Route 109" in recommendations["Nidoqueen"]["source"]
    assert recommendations["Drednaw"]["suggested_item"] == "Hard Stone"
    assert result["result"] == "partial-line"
    assert result["optimized_item_line"]["item_changes"][0]["pokemon"] == "Nidoqueen"
    assert result["optimized_item_line"]["item_changes"][0]["new_item"] == "Soft Sand"


def test_calc_sim_shows_recommended_item_line_even_when_current_items_win() -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[47]
    team = [
        PlannedMember("Nidoqueen", "Nidoqueen", 50, 180, 180, ("Mud Shot", "Sludge Bomb"), item="Oran Berry"),
        PlannedMember("Drednaw", "Drednaw", 50, 160, 160, ("Rock Slide", "Waterfall"), item="Oran Berry"),
    ]

    result = _run_text_calc_sim(team, trainer, calculator, max_turns=30)

    assert result["result"] == "win-line"
    assert result["optimized_item_line"]["item_retry_policy"].startswith("Recommended-item line")
    assert result["optimized_item_line"]["item_changes"]
    assert result["optimized_item_line"]["turns"]


def test_free_send_prefers_faster_guaranteed_ko_even_if_hit_would_ko() -> None:
    calculator = DamageCalculator()
    enemy = PlannedEnemy(
        name="Lucario",
        pokemon=PokemonCalcSet("Lucario", level=50, hp=83, max_hp=83),
        moves=("Aura Sphere", "Shadow Ball", "Flash Cannon"),
        max_hp=83,
        hp=83,
    )
    team = [
        PlannedMember("Slower sack", "Nidoqueen", 50, 109, 5, ("Earth Power",)),
        PlannedMember("Fast answer", "Salazzle", 50, 95, 9, ("Flame Burst",)),
    ]

    assert _best_calc_answer(team, enemy, calculator, allow_sac=True) == 1


def test_switch_uses_baited_move_to_enable_safe_answer() -> None:
    calculator = DamageCalculator()
    enemy = PlannedEnemy(
        name="Lucario",
        pokemon=PokemonCalcSet("Lucario", level=50, hp=83, max_hp=83),
        moves=("Aura Sphere", "Shadow Ball", "Flash Cannon"),
        max_hp=83,
        hp=83,
    )
    team = [
        PlannedMember("Bait", "Snorlax", 50, 200, 200, ("Body Slam",)),
        PlannedMember("Answer", "Gengar", 50, 60, 60, ("Focus Blast",)),
    ]

    assert _calc_switch_target(team, 0, enemy, calculator) == 1


def test_switch_does_not_immediately_reverse_without_pressure() -> None:
    calculator = DamageCalculator()
    enemy = PlannedEnemy(
        name="Boltund",
        pokemon=PokemonCalcSet("Boltund", level=50, hp=26, max_hp=82),
        moves=("Thunder Fang", "Psychic Fangs", "Howl"),
        max_hp=82,
        hp=26,
    )
    team = [
        PlannedMember("Nidoqueen", "Nidoqueen", 50, 109, 53, ("Sludge Bomb", "Mud Shot")),
        PlannedMember("Drednaw", "Drednaw", 50, 109, 51, ("Rock Tomb", "Water Gun")),
    ]

    assert _calc_switch_target(team, 1, enemy, calculator, previous_switch=(0, 1, 0), enemy_index=0) is None


def test_switch_does_not_feed_setup_turns_without_payoff() -> None:
    calculator = DamageCalculator()
    enemy = PlannedEnemy(
        name="Boltund",
        pokemon=PokemonCalcSet("Boltund", level=50, hp=82, max_hp=82),
        moves=("Howl",),
        max_hp=82,
        hp=82,
    )
    team = [
        PlannedMember("Current", "Nidoqueen", 50, 109, 109, ("Sludge Bomb",)),
        PlannedMember("Pivot", "Palpitoad", 50, 99, 99, ("Mud Shot",)),
    ]

    assert _calc_switch_target(team, 0, enemy, calculator) is None


def test_consecutive_switch_requires_real_payoff() -> None:
    calculator = DamageCalculator()
    enemy = PlannedEnemy(
        name="Boltund",
        pokemon=PokemonCalcSet("Boltund", level=50, hp=26, max_hp=82),
        moves=("Thunder Fang", "Psychic Fangs", "Howl"),
        max_hp=82,
        hp=26,
    )
    team = [
        PlannedMember("Nidoqueen", "Nidoqueen", 50, 109, 53, ("Sludge Bomb", "Mud Shot")),
        PlannedMember("Drednaw", "Drednaw", 50, 109, 51, ("Rock Tomb", "Water Gun")),
        PlannedMember("Shellder", "Shellder", 50, 71, 71, ("Icicle Spear", "Water Gun"), item="Oran Berry"),
    ]

    assert _calc_switch_target(team, 1, enemy, calculator, previous_switch=(0, 1, 0), enemy_index=0, switch_streak=1) is None
    assert _calc_switch_target(team, 0, enemy, calculator, previous_switch=(1, 0, 0), enemy_index=0, switch_streak=1) is None


def test_boltund_answer_attacks_instead_of_optional_pivoting() -> None:
    calculator = DamageCalculator()
    enemy = PlannedEnemy(
        name="Boltund",
        pokemon=PokemonCalcSet("Boltund", level=27, hp=82, max_hp=82, ability="Strong Jaw"),
        moves=("Thunder Fang", "Psychic Fangs", "Crunch", "Howl"),
        max_hp=82,
        hp=82,
        ability="Strong Jaw",
    )
    team = [
        PlannedMember("Drednaw", "Drednaw", 32, 109, 109, ("Rock Slide", "Water Gun"), item="Hard Stone"),
        PlannedMember("Palpitoad", "Palpitoad", 32, 99, 99, ("Mud Shot", "Water Pulse"), item="Wise Glasses"),
        PlannedMember("Nidoqueen", "Nidoqueen", 32, 109, 109, ("Mud Shot", "Sludge Bomb"), item="Soft Sand"),
        PlannedMember("Shellder", "Shellder", 32, 71, 71, ("Icicle Spear", "Water Gun"), item="Sitrus Berry"),
    ]

    assert _calc_switch_target(team, 1, enemy, calculator) is None


def test_overleveled_team_attacks_instead_of_switch_looping() -> None:
    calculator = DamageCalculator()
    trainer = load_trainer_battles()[0]
    team = [
        PlannedMember("Monferno", "Monferno", 30, 90, 90, ("Flame Wheel", "Mach Punch"), item="Oran Berry"),
        PlannedMember("Nidoqueen", "Nidoqueen", 30, 109, 109, ("Mud Shot", "Sludge Bomb"), item="Oran Berry"),
        PlannedMember("Drednaw", "Drednaw", 30, 109, 109, ("Rock Tomb", "Water Gun")),
    ]

    result = _run_text_calc_sim(team, trainer, calculator, max_turns=10)

    assert result["result"] == "win-line"
    assert result["turns"]
    assert all(not turn["action"].startswith("Switch ") for turn in result["turns"])


def test_boltund_line_uses_a_safe_finisher_after_one_pivot() -> None:
    calculator = DamageCalculator()
    corgi = load_trainer_battles()[47]
    boltund_only = TrainerBattle(
        section=corgi.section,
        location=corgi.location,
        trainer_name=corgi.trainer_name,
        is_double=corgi.is_double,
        party=tuple(mon for mon in corgi.party if mon.species == "Boltund"),
    )
    team = [
        PlannedMember("Nidoqueen", "Nidoqueen", 32, 109, 109, ("Mud Shot", "Sludge Bomb"), item="Oran Berry"),
        PlannedMember("Drednaw", "Drednaw", 32, 109, 109, ("Rock Slide", "Water Gun"), item="Hard Stone"),
        PlannedMember("Palpitoad", "Palpitoad", 32, 99, 99, ("Mud Shot", "Water Pulse"), item="Wise Glasses"),
        PlannedMember("Shellder", "Shellder", 32, 71, 71, ("Icicle Spear", "Water Gun"), item="Sitrus Berry"),
    ]

    result = _run_text_calc_sim(team, boltund_only, calculator, max_turns=12)
    boltund_turns = [turn for turn in result["turns"] if turn["enemy"] == "Boltund"]

    assert sum(turn["action"].startswith("Switch ") for turn in boltund_turns) <= 1
    # After Rock Slide leaves Boltund at 14 HP, move-line search may improve on the
    # stronger-but-95%-accurate Mud Shot by using guaranteed-hit Sludge Bomb instead.
    assert any(
        move in turn["action"]
        for turn in boltund_turns
        for move in ("Mud Shot", "Sludge Bomb")
    )


def test_consecutive_switch_allowed_when_it_creates_payoff() -> None:
    calculator = DamageCalculator()
    enemy = PlannedEnemy(
        name="Lucario",
        pokemon=PokemonCalcSet("Lucario", level=50, hp=83, max_hp=83),
        moves=("Aura Sphere", "Shadow Ball", "Flash Cannon"),
        max_hp=83,
        hp=83,
    )
    team = [
        PlannedMember("Bait", "Snorlax", 50, 200, 200, ("Body Slam",)),
        PlannedMember("Answer", "Gengar", 50, 60, 60, ("Focus Blast",)),
    ]

    assert _calc_switch_target(team, 0, enemy, calculator, previous_switch=(2, 0, 0), enemy_index=0, switch_streak=1) == 1


def test_switch_avoids_pursuit_trap_on_outgoing_member() -> None:
    calculator = DamageCalculator()
    enemy_without_pursuit = PlannedEnemy(
        name="Hitmontop",
        pokemon=PokemonCalcSet("Hitmontop", level=50, hp=100, max_hp=100),
        moves=("Mach Punch",),
        max_hp=100,
        hp=100,
    )
    enemy_with_pursuit = PlannedEnemy(
        name="Hitmontop",
        pokemon=PokemonCalcSet("Hitmontop", level=50, hp=100, max_hp=100),
        moves=("Mach Punch", "Pursuit"),
        max_hp=100,
        hp=100,
    )
    team = [
        PlannedMember("Fleeing", "Gengar", 50, 60, 60, ("Lick",)),
        PlannedMember("Answer", "Alakazam", 50, 120, 120, ("Psychic",)),
    ]

    assert _calc_switch_target(team, 0, enemy_without_pursuit, calculator) == 1
    assert _calc_switch_target(team, 0, enemy_with_pursuit, calculator) is None


def test_regenerator_heals_when_switching_out() -> None:
    member = PlannedMember("Pivot", "Mienshao", 50, 150, 75, ("Fake Out",), ability="Regenerator")

    _apply_switch_out_effects(member)

    assert member.hp == 125


def test_enemy_status_move_refreshes_player_damage_before_same_turn_attack() -> None:
    calculator = DamageCalculator()
    enemy = PlannedEnemy(
        name="Arcanine",
        pokemon=PokemonCalcSet("Arcanine", level=27, hp=93, max_hp=93),
        moves=("Will-O-Wisp",),
        max_hp=93,
        hp=93,
    )
    active = PlannedMember("Drednaw", "Drednaw", 32, 109, 109, ("Rock Slide",), item="Hard Stone")
    action = _best_player_action(active, enemy, [active], calculator)
    burn = calculator.estimate_move(enemy.calc_set(), active.calc_set(), "Will-O-Wisp")

    _apply_enemy_action(enemy, active, MoveChoice("Will-O-Wisp", 7.0, 1.0, burn), calculator)
    refreshed = _refresh_player_action(active, enemy, action, calculator)

    assert active.status == "burn"
    assert action.damage is not None and refreshed.damage is not None
    assert refreshed.damage.max_damage < action.damage.max_damage


def test_imported_hp_enables_pre_damage_pinch_ability_lines() -> None:
    calculator = DamageCalculator()
    imported = _parse_imported_sets(
        "Prepped (Grotle)\nLevel: 50\nAbility: Overgrow\nHP: 30/100\n- Razor Leaf",
        calculator,
    )
    full_hp = PlannedMember("Full", "Grotle", 50, 100, 100, ("Razor Leaf",), ability="Overgrow")
    enemy = PlannedEnemy(
        name="Numel",
        pokemon=PokemonCalcSet("Numel", level=50, hp=100, max_hp=100),
        moves=("Lava Plume",),
        max_hp=100,
        hp=100,
    )

    assert imported[0].hp == 30
    assert imported[0].max_hp == 100
    prepped_action = _best_calc_answer(imported, enemy, calculator, allow_sac=True)
    prepped_damage = calculator.estimate_move(imported[prepped_action].calc_set(), enemy.calc_set(), "Razor Leaf")
    full_damage = calculator.estimate_move(full_hp.calc_set(), enemy.calc_set(), "Razor Leaf")
    assert prepped_damage is not None and full_damage is not None
    assert prepped_damage.expected_damage > full_damage.expected_damage


def test_self_ko_moves_faint_the_user_in_stateful_calc() -> None:
    calculator = DamageCalculator()
    enemy = PlannedEnemy(
        name="Target",
        pokemon=PokemonCalcSet("Blissey", level=50, hp=300, max_hp=300),
        moves=("Tackle",),
        max_hp=300,
        hp=300,
    )
    active = PlannedMember("Boomer", "Golem", 50, 120, 120, ("Explosion",))
    damage = calculator.estimate_move(active.calc_set(), enemy.calc_set(), "Explosion")

    dealt = _apply_player_action(active, enemy, PlayerAction("move", "Explosion", damage=damage), calculator)

    assert dealt > 0
    assert active.hp == 0


def test_belly_drum_can_trigger_salac_berry_speed_boost() -> None:
    calculator = DamageCalculator()
    user = PlannedEnemy(
        name="Drummer",
        pokemon=PokemonCalcSet("Munchlax", level=50, hp=100, max_hp=100, held_item="Salac Berry"),
        moves=("Belly Drum",),
        max_hp=100,
        hp=60,
    )
    target = PlannedMember("Target", "Pikachu", 50, 100, 100, ("Thunderbolt",))

    _apply_status_action(user, target, "Belly Drum", calculator, target_is_enemy=False)
    _end_of_turn(target, user, calculator)

    assert user.hp == 10
    assert user.boosts["atk"] == 6
    assert user.boosts["spe"] == 1
    assert user.consumed_item is True


# --------------------------------------------------------------------------------------
# Doubles line-search coverage. The doubles "line finder" used to run a single greedy
# pass; these exercise the alternate-lead exploration and forced/suppressed voluntary
# pivots that `_search_best_line_doubles` and `_run_text_calc_sim_once_doubles` now do.
# --------------------------------------------------------------------------------------

def _eq_twins_trainer() -> TrainerBattle:
    """Two lvl-100 Earthquake Garchomp: a spread move that OHKOs grounded leads, so the
    EQ-immune bench (Levitate / Flying) is what survives — a clean pivot test bed."""
    return TrainerBattle(
        section="test",
        location="test",
        trainer_name="EQ Twins [Double]",
        is_double=True,
        party=(
            TrainerPokemon("Garchomp", 100, None, None, None, ("Earthquake",)),
            TrainerPokemon("Garchomp", 100, None, None, None, ("Earthquake",)),
        ),
    )


def _eq_test_team() -> list[PlannedMember]:
    # Slots 0/1 are grounded and die to Earthquake; slots 2/3 are immune (Levitate / Flying).
    return [
        PlannedMember("Raichu", "Raichu", 50, 90, 90, ("Thunderbolt",), ability="Static"),
        PlannedMember("Pikachu", "Pikachu", 50, 80, 80, ("Thunderbolt",), ability="Static"),
        PlannedMember("Gengar", "Gengar", 50, 120, 120, ("Shadow Ball", "Sludge Bomb"), ability="Levitate"),
        PlannedMember("Skarmory", "Skarmory", 50, 140, 140, ("Steel Wing", "Air Slash"), ability="Keen Eye"),
    ]


def test_doubles_sim_honors_forced_leads() -> None:
    calculator = DamageCalculator()
    trainer = _eq_twins_trainer()
    # Force the grounded pair to lead and suppress the auto-pivot so they actually stay in.
    result = _run_text_calc_sim_once_doubles(
        _eq_test_team(), trainer, calculator, max_turns=1,
        forced_leads=(0, 1), switch_overrides={1: {0: None, 1: None}},
    )
    answer = result["turns"][0]["answer"]
    assert "Raichu" in answer and "Pikachu" in answer
    # The forced opening is genuinely different from the heuristic default (the EQ-immune pair).
    assert _doubles_lead_indices(_eq_test_team(), _planned_enemies_for_trainer(trainer, calculator), calculator) == (2, 3)


def test_doubles_sim_ignores_invalid_forced_leads() -> None:
    calculator = DamageCalculator()
    trainer = _eq_twins_trainer()
    default = _run_text_calc_sim_once_doubles(_eq_test_team(), trainer, calculator, max_turns=1)
    # Out-of-range / duplicate lead indices must fall back to the auto-selected pair, not crash.
    bad = _run_text_calc_sim_once_doubles(
        _eq_test_team(), trainer, calculator, max_turns=1, forced_leads=(9, 9),
    )
    assert bad["turns"][0]["answer"] == default["turns"][0]["answer"]


def test_doubles_switch_override_forces_pivot() -> None:
    calculator = DamageCalculator()
    trainer = _eq_twins_trainer()
    # Force slot A to pivot specifically to Skarmory (index 3), overriding the greedy choice.
    result = _run_text_calc_sim_once_doubles(
        _eq_test_team(), trainer, calculator, max_turns=1,
        forced_leads=(0, 1), switch_overrides={1: {0: 3}},
    )
    calc = result["turns"][0]["calc"]
    assert "Switch Raichu -> Skarmory" in calc
    assert "Skarmory" in result["turns"][0]["answer"]


def test_doubles_switch_override_suppresses_pivot() -> None:
    calculator = DamageCalculator()
    trainer = _eq_twins_trainer()
    # By default slot A pivots Raichu out to dodge the lethal Earthquake...
    default = _run_text_calc_sim_once_doubles(
        _eq_test_team(), trainer, calculator, max_turns=1, forced_leads=(0, 1),
    )
    assert "Switch Raichu" in default["turns"][0]["calc"]
    # ...but a None override pins it in place.
    pinned = _run_text_calc_sim_once_doubles(
        _eq_test_team(), trainer, calculator, max_turns=1,
        forced_leads=(0, 1), switch_overrides={1: {0: None}},
    )
    assert "Switch Raichu" not in pinned["turns"][0]["calc"]
    assert "Raichu" in pinned["turns"][0]["answer"]


def test_doubles_lead_candidates_exclude_default_and_are_distinct() -> None:
    calculator = DamageCalculator()
    trainer = _eq_twins_trainer()
    team = _eq_test_team()
    enemies = _planned_enemies_for_trainer(trainer, calculator)
    default = tuple(sorted(_doubles_lead_indices(team, enemies, calculator)))
    candidates = _doubles_lead_candidates(team, enemies, calculator)

    assert candidates, "expected alternate openings to explore"
    for a, b in candidates:
        assert a != b
        assert 0 <= a < len(team) and 0 <= b < len(team)
        assert tuple(sorted((a, b))) != default  # the default is run separately, not re-tried
    assert len({tuple(sorted(p)) for p in candidates}) == len(candidates)  # no duplicates


def test_doubles_line_search_is_never_worse_than_greedy() -> None:
    calculator = DamageCalculator()
    trainer = _eq_twins_trainer()
    greedy = _run_text_calc_sim_once_doubles(_eq_test_team(), trainer, calculator, max_turns=10)
    searched = _search_best_line_doubles(_eq_test_team(), trainer, calculator, max_turns=10)
    # The search is a hill-climb that always keeps the best line found, so it can only
    # match or beat the single greedy pass.
    assert _line_quality_key(searched) >= _line_quality_key(greedy)


def test_doubles_line_search_explores_alternate_leads(monkeypatch) -> None:
    calculator = DamageCalculator()
    trainer = _eq_twins_trainer()
    seen_leads: list[tuple[int, int] | None] = []
    real = server._run_text_calc_sim_once_doubles

    def spy(*args, **kwargs):
        seen_leads.append(kwargs.get("forced_leads"))
        return real(*args, **kwargs)

    monkeypatch.setattr(server, "_run_text_calc_sim_once_doubles", spy)
    _search_best_line_doubles(_eq_test_team(), trainer, calculator, max_turns=4)
    # The search must actually try non-default openings, not just the greedy base line.
    assert any(leads is not None for leads in seen_leads)


def test_run_text_calc_sim_routes_doubles_through_search() -> None:
    calculator = DamageCalculator()
    trainer = _eq_twins_trainer()
    result = _run_text_calc_sim(_eq_test_team(), trainer, calculator, max_turns=6)
    assert result["is_doubles"] is True
    assert result["turns"]
    assert result["result"] in {"win-line", "partial-line"}
