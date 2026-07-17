from __future__ import annotations

from pathlib import Path

from battle.action import Action
from battle.battle_state import BattleState
from battle.damage_calc import DamageCalculator, DamageContext, FieldState, PokemonCalcSet
from search.action_enumerator import ActionEnumerator
from search.mcts import _move_name


def test_damage_calc_prefers_super_effective_stab() -> None:
    calculator = DamageCalculator()
    attacker = PokemonCalcSet("Charizard", level=50)
    defender = PokemonCalcSet("Venusaur", level=50)

    flamethrower = calculator.estimate_move(attacker, defender, "Flamethrower")
    slash = calculator.estimate_move(attacker, defender, "Slash")

    assert flamethrower is not None
    assert slash is not None
    assert flamethrower.type_multiplier == 2.0
    assert flamethrower.expected_damage > slash.expected_damage


def test_action_prioritizer_sorts_known_damage_moves_first() -> None:
    state = BattleState(
        player_hp=[150, 0, 0, 0, 0, 0],
        player_max_hp=[150, 0, 0, 0, 0, 0],
        player_fainted=[False, False, False, False, False, False],
        enemy_hp=[140, 0, 0, 0, 0, 0],
        enemy_max_hp=[140, 0, 0, 0, 0, 0],
        enemy_fainted=[False, False, False, False, False, False],
        battle_over=False,
        player_won=False,
        is_doubles=False,
        menu_ready=True,
        player_names=["Charizard"],
        enemy_names=["Venusaur"],
        player_move_names=["Growl", "Flamethrower", "Tackle", "Slash"],
        player_move_names_by_slot=[["Growl", "Flamethrower", "Tackle", "Slash"]],
    )
    actions = [(Action.move(0),), (Action.move(1),), (Action.move(2),), (Action.move(3),)]

    ordered = ActionEnumerator().prioritize(actions, state)

    assert ordered[0] == (Action.move(1),)


def test_weather_screens_boosts_and_crit_modifiers() -> None:
    calculator = DamageCalculator()
    attacker = PokemonCalcSet("Charizard", level=50, boosts={"spa": 2})
    defender = PokemonCalcSet("Venusaur", level=50, boosts={"spd": 2})

    neutral = calculator.estimate_move(attacker, defender, "Flamethrower")
    rain = calculator.estimate_move(
        attacker,
        defender,
        "Flamethrower",
        DamageContext(field=FieldState(weather="rain")),
    )
    screen = calculator.estimate_move(
        attacker,
        defender,
        "Flamethrower",
        DamageContext(field=FieldState(is_light_screen=True)),
    )
    crit = calculator.estimate_move(
        attacker,
        defender,
        "Flamethrower",
        DamageContext(field=FieldState(is_light_screen=True), critical=True),
    )

    assert neutral is not None and rain is not None and screen is not None and crit is not None
    assert rain.expected_damage < neutral.expected_damage
    assert screen.expected_damage < neutral.expected_damage
    assert crit.expected_damage > screen.expected_damage


def test_calculator_default_field_applies_to_planner_estimates() -> None:
    attacker = PokemonCalcSet("Charizard", level=50)
    defender = PokemonCalcSet("Venusaur", level=50)
    clear = DamageCalculator().estimate_move(attacker, defender, "Flamethrower")
    rain = DamageCalculator(default_field=FieldState(weather="rain")).estimate_move(
        attacker, defender, "Flamethrower"
    )
    assert clear is not None and rain is not None
    assert rain.max_damage < clear.max_damage


def test_explicit_critical_context_keeps_calculator_default_field() -> None:
    attacker = PokemonCalcSet("Charizard", level=50)
    defender = PokemonCalcSet("Venusaur", level=50)
    clear = DamageCalculator().estimate_move(attacker, defender, "Flamethrower", DamageContext(critical=True))
    rain = DamageCalculator(default_field=FieldState(weather="rain")).estimate_move(
        attacker, defender, "Flamethrower", DamageContext(critical=True)
    )
    assert clear is not None and rain is not None
    assert rain.max_damage < clear.max_damage


def test_burn_and_items_affect_damage() -> None:
    calculator = DamageCalculator()
    defender = PokemonCalcSet("Blissey", level=50)
    normal = calculator.estimate_move(PokemonCalcSet("Garchomp", level=50), defender, "Earthquake")
    burned = calculator.estimate_move(PokemonCalcSet("Garchomp", level=50, status="burn"), defender, "Earthquake")
    banded = calculator.estimate_move(PokemonCalcSet("Garchomp", level=50, held_item="Choice Band"), defender, "Earthquake")
    life_orb = calculator.estimate_move(PokemonCalcSet("Garchomp", level=50, held_item="Life Orb"), defender, "Earthquake")

    assert normal is not None and burned is not None and banded is not None and life_orb is not None
    assert burned.expected_damage < normal.expected_damage
    assert banded.expected_damage > normal.expected_damage
    assert life_orb.expected_damage > normal.expected_damage


def test_defensive_items_and_survival_items_affect_damage() -> None:
    calculator = DamageCalculator()
    attacker = PokemonCalcSet("Charizard", level=50)
    normal = calculator.estimate_move(attacker, PokemonCalcSet("Chansey", level=50), "Flamethrower")
    eviolite = calculator.estimate_move(attacker, PokemonCalcSet("Chansey", level=50, held_item="Eviolite"), "Flamethrower")
    sash = calculator.estimate_move(
        PokemonCalcSet("Mewtwo", level=100, held_item="Life Orb"),
        PokemonCalcSet("Abra", level=1, hp=12, max_hp=12, held_item="Focus Sash"),
        "Psystrike",
    )

    assert normal is not None and eviolite is not None and sash is not None
    assert eviolite.expected_damage < normal.expected_damage
    assert sash.ko_chance == 0
    assert sash.max_damage == 11


def test_pursuit_doubles_power_into_switching_target() -> None:
    calculator = DamageCalculator()
    attacker = PokemonCalcSet("Hitmontop", level=50)
    defender = PokemonCalcSet("Gengar", level=50, hp=60, max_hp=60)
    normal = calculator.estimate_move(attacker, defender, "Pursuit")
    switching = calculator.estimate_move(attacker, defender, "Pursuit", DamageContext(defender_is_switching=True))

    assert normal is not None and switching is not None
    assert switching.max_damage > normal.max_damage
    assert "Pursuit switch" in switching.modifiers


def test_retaliate_doubles_after_an_ally_fainted() -> None:
    calculator = DamageCalculator()
    defender = PokemonCalcSet("Pikachu", level=50, hp=100, max_hp=100)
    normal = calculator.estimate_move(PokemonCalcSet("Lopunny", level=50), defender, "Retaliate")
    revenge = calculator.estimate_move(PokemonCalcSet("Lopunny", level=50, allies_fainted=1), defender, "Retaliate")

    assert normal is not None and revenge is not None
    assert revenge.max_damage > normal.max_damage
    assert "Retaliate ally fainted" in revenge.modifiers


def test_resist_berry_and_type_boost_item_affect_damage() -> None:
    calculator = DamageCalculator()
    attacker = PokemonCalcSet("Charizard", level=50)
    defender = PokemonCalcSet("Venusaur", level=50)
    normal = calculator.estimate_move(attacker, defender, "Flamethrower")
    charcoal = calculator.estimate_move(PokemonCalcSet("Charizard", level=50, held_item="Charcoal"), defender, "Flamethrower")
    occa = calculator.estimate_move(attacker, PokemonCalcSet("Venusaur", level=50, held_item="Occa Berry"), "Flamethrower")

    assert normal is not None and charcoal is not None and occa is not None
    assert charcoal.expected_damage > normal.expected_damage
    assert occa.expected_damage < normal.expected_damage


def test_accuracy_items_and_focus_band_affect_prediction() -> None:
    calculator = DamageCalculator()
    attacker = PokemonCalcSet("Garchomp", level=50)
    defender = PokemonCalcSet("Froslass", level=50)
    normal = calculator.estimate_move(attacker, defender, "Stone Edge")
    wide_lens = calculator.estimate_move(PokemonCalcSet("Garchomp", level=50, held_item="Wide Lens"), defender, "Stone Edge")
    bright_powder = calculator.estimate_move(attacker, PokemonCalcSet("Froslass", level=50, held_item="Bright Powder"), "Stone Edge")
    focus_band = calculator.estimate_move(
        PokemonCalcSet("Mewtwo", level=100, held_item="Life Orb"),
        PokemonCalcSet("Abra", level=1, hp=12, max_hp=12, held_item="Focus Band"),
        "Psychic",
    )

    assert normal is not None and wide_lens is not None and bright_powder is not None and focus_band is not None
    assert wide_lens.accuracy > normal.accuracy
    assert bright_powder.accuracy < normal.accuracy
    assert 0 < focus_band.ko_chance < 1


def test_showdown_style_ability_power_modifiers() -> None:
    calculator = DamageCalculator()
    defender = PokemonCalcSet("Blissey", level=50)
    normal_slash = calculator.estimate_move(PokemonCalcSet("Gallade", level=50), defender, "Slash")
    sharp_slash = calculator.estimate_move(PokemonCalcSet("Gallade", level=50, ability="Sharpness"), defender, "Slash")
    normal_facade = calculator.estimate_move(PokemonCalcSet("Zangoose", level=50, status="toxic"), defender, "Facade")
    toxic_boost = calculator.estimate_move(PokemonCalcSet("Zangoose", level=50, status="toxic", ability="Toxic Boost"), defender, "Facade")
    normal_shadow_ball = calculator.estimate_move(PokemonCalcSet("Gengar", level=50), PokemonCalcSet("Mandibuzz", level=50), "Shadow Ball")
    tinted_shadow_ball = calculator.estimate_move(PokemonCalcSet("Gengar", level=50, ability="Tinted Lens"), PokemonCalcSet("Mandibuzz", level=50), "Shadow Ball")

    assert normal_slash is not None and sharp_slash is not None
    assert normal_facade is not None and toxic_boost is not None
    assert normal_shadow_ball is not None and tinted_shadow_ball is not None
    assert sharp_slash.expected_damage > normal_slash.expected_damage
    assert toxic_boost.expected_damage > normal_facade.expected_damage
    assert tinted_shadow_ball.expected_damage > normal_shadow_ball.expected_damage


def test_sheer_force_boosts_moves_with_additional_effects() -> None:
    calculator = DamageCalculator()
    defender = PokemonCalcSet("Blissey", level=50)
    normal = calculator.estimate_move(PokemonCalcSet("Nidoking", level=50), defender, "Sludge Bomb")
    sheer_force = calculator.estimate_move(PokemonCalcSet("Nidoking", level=50, ability="Sheer Force"), defender, "Sludge Bomb")

    assert normal is not None and sheer_force is not None
    assert sheer_force.expected_damage > normal.expected_damage
    assert "Sheer Force" in sheer_force.modifiers


def test_ability_bypass_and_no_guard_are_modeled() -> None:
    calculator = DamageCalculator()
    blocked = calculator.estimate_move(PokemonCalcSet("Charizard", level=50), PokemonCalcSet("Shedinja", level=50, ability="Wonder Guard"), "Sludge Bomb")
    bypassed = calculator.estimate_move(PokemonCalcSet("Charizard", level=50, ability="Mold Breaker"), PokemonCalcSet("Shedinja", level=50, ability="Wonder Guard"), "Sludge Bomb")
    no_guard = calculator.estimate_move(PokemonCalcSet("Machamp", level=50, ability="No Guard"), PokemonCalcSet("Gengar", level=50), "Dynamic Punch")

    assert blocked is not None and bypassed is not None and no_guard is not None
    assert blocked.max_damage == 0
    assert bypassed.max_damage > 0
    assert no_guard.accuracy == 1.0


def test_merciless_and_type_changing_abilities_are_modeled() -> None:
    calculator = DamageCalculator()
    normal = calculator.estimate_move(PokemonCalcSet("Toxapex", level=50), PokemonCalcSet("Blissey", level=50, status="poison"), "Poison Jab")
    merciless = calculator.estimate_move(PokemonCalcSet("Toxapex", level=50, ability="Merciless"), PokemonCalcSet("Blissey", level=50, status="poison"), "Poison Jab")
    liquid_voice = calculator.estimate_move(PokemonCalcSet("Primarina", level=50, ability="Liquid Voice"), PokemonCalcSet("Charizard", level=50), "Hyper Voice")
    dragonize = calculator.estimate_move(PokemonCalcSet("Charizard", level=50, ability="Dragonize"), PokemonCalcSet("Garchomp", level=50), "Slash")

    assert normal is not None and merciless is not None and liquid_voice is not None and dragonize is not None
    assert merciless.expected_damage > normal.expected_damage
    assert liquid_voice.type_multiplier == 2.0
    assert dragonize.type_multiplier == 2.0


def test_good_as_gold_blocks_status_moves_in_calc() -> None:
    calculator = DamageCalculator()
    blocked = calculator.estimate_move(PokemonCalcSet("Pikachu", level=50), PokemonCalcSet("Gholdengo", level=50, ability="Good as Gold"), "Thunder Wave")

    assert blocked is not None
    assert blocked.reason == "status_blocked_by_ability"


def test_rnb_docs_are_preserved_next_to_ai_doc() -> None:
    assert Path("data/rnb_ai_document.txt").exists()
    assert Path("data/rnb_mechanic_changes.txt").exists()
    assert Path("data/rnb_move_changes.pdf").exists()
    assert Path("data/rnb_move_changes.txt").exists()


def test_rnb_move_table_changes_are_loaded() -> None:
    calculator = DamageCalculator()
    assert calculator.moves["absorb"]["basePower"] == 40
    assert calculator.moves["return"]["basePower"] == 102
    assert calculator.moves["mistyexplosion"]["basePower"] == 200
    assert calculator.moves["covet"]["type"] == "Fairy"
    assert calculator.moves["superfang"]["type"] == "Dark"
    assert calculator.moves["chargebeam"]["accuracy"] == 100
    assert calculator.moves["chargebeam"]["secondary"]["chance"] == 100
    assert calculator.moves["smog"]["secondary"]["chance"] == 100


def test_rnb_self_ko_moves_halve_defense_and_terrain_boosts_are_50_percent() -> None:
    calculator = DamageCalculator()
    attacker = PokemonCalcSet("Golem", level=50)
    defender = PokemonCalcSet("Blissey", level=50)
    tackle = calculator.estimate_move(attacker, defender, "Tackle")
    explosion = calculator.estimate_move(attacker, defender, "Explosion")
    neutral = calculator.estimate_move(PokemonCalcSet("Raichu", level=50), defender, "Thunderbolt")
    terrain = calculator.estimate_move(
        PokemonCalcSet("Raichu", level=50),
        defender,
        "Thunderbolt",
        DamageContext(field=FieldState(terrain="electric")),
    )

    assert tackle is not None and explosion is not None and neutral is not None and terrain is not None
    assert "RnB self-KO defense halved" in explosion.modifiers
    assert explosion.max_damage > tackle.max_damage * 2
    assert terrain.expected_damage > neutral.expected_damage * 1.45


def test_rnb_hidden_power_thunder_wave_and_magma_armor_mechanics() -> None:
    calculator = DamageCalculator()
    hidden_power = calculator.estimate_move(
        PokemonCalcSet("Starmie", level=50, ivs={"hp": 30, "atk": 31, "def": 30, "spa": 31, "spd": 31, "spe": 31}),
        PokemonCalcSet("Garchomp", level=50),
        "Hidden Power",
    )
    crit_blocked = calculator.estimate_move(
        PokemonCalcSet("Charizard", level=50),
        PokemonCalcSet("Magcargo", level=50, ability="Magma Armor"),
        "Slash",
        DamageContext(critical=True),
    )
    twave = calculator.estimate_move(
        PokemonCalcSet("Pikachu", level=50),
        PokemonCalcSet("Blissey", level=50),
        "Thunder Wave",
    )

    assert hidden_power is not None and crit_blocked is not None and twave is not None
    assert hidden_power.type_multiplier == 4.0
    assert "crit blocked" in crit_blocked.modifiers
    assert twave.reason == "status_or_zero_power"


def test_turn_weather_and_super_effective_ability_modifiers() -> None:
    calculator = DamageCalculator()
    neutral = calculator.estimate_move(PokemonCalcSet("Starmie", level=50), PokemonCalcSet("Garchomp", level=50), "Ice Beam")
    neuroforce = calculator.estimate_move(PokemonCalcSet("Starmie", level=50, ability="Neuroforce"), PokemonCalcSet("Garchomp", level=50), "Ice Beam")
    normal_earth_power = calculator.estimate_move(PokemonCalcSet("Nidoking", level=50), PokemonCalcSet("Blissey", level=50), "Earth Power")
    sand_force = calculator.estimate_move(
        PokemonCalcSet("Nidoking", level=50, ability="Sand Force"),
        PokemonCalcSet("Blissey", level=50),
        "Earth Power",
        DamageContext(field=FieldState(weather="sand")),
    )
    normal_thunderbolt = calculator.estimate_move(PokemonCalcSet("Magnezone", level=50), PokemonCalcSet("Milotic", level=50), "Thunderbolt")
    analytic = calculator.estimate_move(
        PokemonCalcSet("Magnezone", level=50, ability="Analytic"),
        PokemonCalcSet("Milotic", level=50),
        "Thunderbolt",
        DamageContext(turn_order="last"),
    )

    assert neutral is not None and neuroforce is not None
    assert normal_earth_power is not None and sand_force is not None
    assert normal_thunderbolt is not None and analytic is not None
    assert neuroforce.expected_damage > neutral.expected_damage
    assert sand_force.expected_damage > normal_earth_power.expected_damage
    assert analytic.expected_damage > normal_thunderbolt.expected_damage


def test_matches_run_bun_trainer_from_enemy_hp_fingerprint() -> None:
    calculator = DamageCalculator()
    state = BattleState(
        player_hp=[100, 0, 0, 0, 0, 0],
        player_max_hp=[100, 0, 0, 0, 0, 0],
        player_fainted=[False, False, False, False, False, False],
        enemy_hp=[93, 88, 83, 83, 82, 0],
        enemy_max_hp=[93, 88, 83, 83, 82, 0],
        enemy_fainted=[False, False, False, False, False, False],
        battle_over=False,
        player_won=False,
        is_doubles=False,
        menu_ready=True,
    )

    match = calculator.matched_trainer(state)

    assert match is not None
    assert match.battle.trainer_name == "Breeder Corgi"
    assert match.sets[0].moves == ("Flare Blitz", "Wild Charge", "Extreme Speed", "Will-O-Wisp")


def test_switches_are_prioritized_when_known_enemy_fast_ohkos_active() -> None:
    calculator = DamageCalculator()
    pelipper_hp = calculator._stat(calculator._species_data("Pelipper"), "hp", 27, None, None, None)
    golem_hp = calculator._stat(calculator._species_data("Golem"), "hp", 27, None, None, None)
    state = BattleState(
        player_hp=[pelipper_hp, golem_hp, 0, 0, 0, 0],
        player_max_hp=[pelipper_hp, golem_hp, 0, 0, 0, 0],
        player_fainted=[False, False, False, False, False, False],
        enemy_hp=[0, 0, 0, 83, 82, 0],
        enemy_max_hp=[93, 88, 83, 83, 82, 0],
        enemy_fainted=[True, True, True, False, False, False],
        battle_over=False,
        player_won=False,
        is_doubles=False,
        menu_ready=True,
        player_names=["Pelipper", "Golem"],
        enemy_names=["Arcanine", "Furfrou", "Lucario", "Manectric", "Boltund"],
        player_move_names=["Tackle", "Growl", "Vine Whip", "Sleep Powder"],
        player_move_names_by_slot=[["Tackle", "Growl", "Vine Whip", "Sleep Powder"], []],
    )
    actions = [(Action.move(0),), (Action.switch(1),)]

    ordered = ActionEnumerator().prioritize(actions, state)

    assert ordered[0] == (Action.switch(1),)


def test_switches_are_filtered_without_ohko_or_preservation_reason() -> None:
    calculator = DamageCalculator()
    venusaur_hp = calculator._stat(calculator._species_data("Venusaur"), "hp", 27, None, None, None)
    golem_hp = calculator._stat(calculator._species_data("Golem"), "hp", 27, None, None, None)
    state = BattleState(
        player_hp=[golem_hp, venusaur_hp, 0, 0, 0, 0],
        player_max_hp=[golem_hp, venusaur_hp, 0, 0, 0, 0],
        player_fainted=[False, False, False, False, False, False],
        enemy_hp=[93, 88, 83, 83, 82, 0],
        enemy_max_hp=[93, 88, 83, 83, 82, 0],
        enemy_fainted=[False, False, False, False, False, False],
        battle_over=False,
        player_won=False,
        is_doubles=False,
        menu_ready=True,
        player_names=["Golem", "Venusaur"],
        enemy_names=["Arcanine", "Furfrou", "Lucario", "Manectric", "Boltund"],
        player_move_names=["Tackle", "Growl", "Vine Whip", "Sleep Powder"],
        player_move_names_by_slot=[["Tackle", "Growl", "Vine Whip", "Sleep Powder"], []],
    )

    ordered = ActionEnumerator().prioritize([(Action.move(0),), (Action.switch(1),)], state)

    assert ordered == [(Action.move(0),)]


def test_status_move_can_rank_above_weak_chip_when_it_reduces_safe_physical_damage() -> None:
    state = BattleState(
        player_hp=[120, 0, 0, 0, 0, 0],
        player_max_hp=[120, 0, 0, 0, 0, 0],
        player_fainted=[False, False, False, False, False, False],
        enemy_hp=[93, 88, 83, 83, 82, 0],
        enemy_max_hp=[93, 88, 83, 83, 82, 0],
        enemy_fainted=[False, False, False, False, False, False],
        battle_over=False,
        player_won=False,
        is_doubles=False,
        menu_ready=True,
        player_names=["Fletchinder"],
        enemy_names=["Arcanine", "Furfrou", "Lucario", "Manectric", "Boltund"],
        player_move_names=["Growl", "Peck", "Tackle", "Quick Attack"],
        player_move_names_by_slot=[["Growl", "Peck", "Tackle", "Quick Attack"]],
    )

    ordered = ActionEnumerator().prioritize(
        [(Action.move(0),), (Action.move(1),), (Action.move(2),), (Action.move(3),)],
        state,
    )

    assert ordered[0] == (Action.move(0),)


def test_switch_allowed_when_active_barely_damages_and_gets_2hkoed() -> None:
    calculator = DamageCalculator()
    arbok_hp = calculator._stat(calculator._species_data("Arbok"), "hp", 27, None, None, None)
    golem_hp = calculator._stat(calculator._species_data("Golem"), "hp", 27, None, None, None)
    state = BattleState(
        player_hp=[arbok_hp, golem_hp, 0, 0, 0, 0],
        player_max_hp=[arbok_hp, golem_hp, 0, 0, 0, 0],
        player_fainted=[False, False, False, False, False, False],
        enemy_hp=[93, 88, 83, 83, 82, 0],
        enemy_max_hp=[93, 88, 83, 83, 82, 0],
        enemy_fainted=[False, False, False, False, False, False],
        battle_over=False,
        player_won=False,
        is_doubles=False,
        menu_ready=True,
        player_names=["Arbok", "Golem"],
        enemy_names=["Arcanine", "Furfrou", "Lucario", "Manectric", "Boltund"],
        player_move_names=["Poison Sting", "Wrap", "Bite", "Glare"],
        player_move_names_by_slot=[
            ["Poison Sting", "Wrap", "Bite", "Glare"],
            ["Magnitude", "Rock Throw", "Tackle", "Defense Curl"],
        ],
    )

    decision = calculator.switch_decision(state, 1)

    assert decision.allowed is True
    assert decision.reason == "bad_damage_under_pressure"


def test_non_active_move_label_does_not_borrow_active_moves() -> None:
    state = BattleState(
        player_hp=[100, 100, 0, 0, 0, 0],
        player_max_hp=[100, 100, 0, 0, 0, 0],
        player_fainted=[False, False, False, False, False, False],
        enemy_hp=[100, 0, 0, 0, 0, 0],
        enemy_max_hp=[100, 0, 0, 0, 0, 0],
        enemy_fainted=[False, False, False, False, False, False],
        battle_over=False,
        player_won=False,
        is_doubles=False,
        menu_ready=True,
        player_names=["Rah", "Pole"],
        player_move_names=["Flare Blitz", "Wild Charge", "ExtremeSpeed", "Will-O-Wisp"],
        player_move_names_by_slot=[["Flare Blitz", "Wild Charge", "ExtremeSpeed", "Will-O-Wisp"], []],
    )

    assert _move_name(state, 0, active_slot=1) == "top move"


def test_defensive_abilities_and_special_defense_override() -> None:
    calculator = DamageCalculator()
    blocked = calculator.estimate_move(PokemonCalcSet("Blastoise", level=50), PokemonCalcSet("Chesnaught", level=50, ability="Bulletproof"), "Aura Sphere")
    soundproof = calculator.estimate_move(PokemonCalcSet("Toxtricity", level=50), PokemonCalcSet("Kommo-o", level=50, ability="Soundproof"), "Boomburst")
    normal_psychic = calculator.estimate_move(PokemonCalcSet("Mewtwo", level=50), PokemonCalcSet("Blissey", level=50), "Psychic")
    psystrike = calculator.estimate_move(PokemonCalcSet("Mewtwo", level=50), PokemonCalcSet("Blissey", level=50), "Psystrike")
    normal_fire = calculator.estimate_move(PokemonCalcSet("Charizard", level=50), PokemonCalcSet("Bronzong", level=50), "Flamethrower")
    heatproof = calculator.estimate_move(PokemonCalcSet("Charizard", level=50), PokemonCalcSet("Bronzong", level=50, ability="Heatproof"), "Flamethrower")

    assert blocked is not None and soundproof is not None
    assert normal_psychic is not None and psystrike is not None
    assert normal_fire is not None and heatproof is not None
    assert blocked.max_damage == 0
    assert soundproof.max_damage == 0
    assert psystrike.defense_stat < normal_psychic.defense_stat
    assert heatproof.expected_damage < normal_fire.expected_damage


def test_stay_in_risk_ranks_known_moves() -> None:
    calculator = DamageCalculator()
    risk = calculator.stay_in_risk(
        PokemonCalcSet("Starmie", level=50),
        PokemonCalcSet("Garchomp", level=50, hp=183, max_hp=183),
        ["Surf", "Ice Beam", "Rapid Spin"],
    )

    assert risk.best_move == "Ice Beam"
    assert risk.best_damage is not None
    assert risk.best_damage.ko_chance > 0
    assert risk.safe_to_stay_in is False
