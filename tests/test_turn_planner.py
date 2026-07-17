from __future__ import annotations

from battle.damage_calc import DamageCalculator, FieldState, PokemonCalcSet
from optimizer.turn_planner import (
    MoveChoice,
    PlannedEnemy,
    PlannedMember,
    PlayerAction,
    _action_is_unreliable,
    _ai_branch_confidence,
    _ai_move_choices,
    _ai_recovery_score,
    _apply_enemy_action,
    _apply_player_action,
    _apply_status_action,
    _best_player_action,
    _end_of_turn,
    _enemy_moves_before_player,
    _move_accuracy,
    _recovery_amount,
    _strength_sap_amount,
    _switch_target_if_needed,
)


def test_recovery_is_a_real_plannable_action() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Wall", species="Blissey", level=50, max_hp=300, hp=70,
        moves=("Recover", "Pound"),
    )
    enemy = PlannedEnemy(
        name="Weak attacker",
        pokemon=PokemonCalcSet("Caterpie", level=20, hp=80, max_hp=80),
        moves=("Tackle",), max_hp=80, hp=80,
    )

    action = _best_player_action(active, enemy, [active], calculator)
    assert action.move_name == "Recover"

    _apply_status_action(active, enemy, "Recover", calculator, target_is_enemy=True)
    assert active.hp == 220


def test_weather_recovery_amounts_are_modeled() -> None:
    rain = DamageCalculator(default_field=FieldState(weather="rain"))
    sun = DamageCalculator(default_field=FieldState(weather="sun"))
    active = PlannedMember("Sun healer", "Venusaur", 50, 200, 50, ("Synthesis",))

    assert _recovery_amount(active, "synthesis", rain) == 50
    assert _recovery_amount(active, "synthesis", sun) == 133


def test_ai_recovery_uses_documented_hp_gates() -> None:
    calculator = DamageCalculator()
    player = PlannedMember("Chip", "Caterpie", 20, 80, 80, ("Tackle",))
    enemy = PlannedEnemy(
        "Healer", PokemonCalcSet("Blissey", level=50, hp=300, max_hp=300),
        ("Recover", "Pound"), 300, 300,
    )

    assert _ai_recovery_score("recover", enemy, player, calculator)[0] == -20.0
    enemy.hp = 120
    score, reason = _ai_recovery_score("recover", enemy, player, calculator)
    assert score >= 6.0
    assert "should-recover" in reason
    enemy.status = "toxic"
    assert _ai_recovery_score("recover", enemy, player, calculator)[0] == 5.0


def test_strength_sap_heals_from_live_attack_and_drops_it() -> None:
    calculator = DamageCalculator()
    user = PlannedMember("Healer", "Vileplume", 50, 150, 30, ("Strength Sap",))
    target = PlannedEnemy(
        "Physical target", PokemonCalcSet("Machamp", level=50, hp=170, max_hp=170),
        ("Karate Chop",), 170, 170,
    )
    expected = _strength_sap_amount(target, calculator)

    _apply_status_action(user, target, "Strength Sap", calculator, target_is_enemy=True)

    assert user.hp == min(user.max_hp, 30 + expected)
    assert target.boosts["atk"] == -1


def test_unknown_active_switches_to_decoded_progress_member() -> None:
    calculator = DamageCalculator()
    team = [
        PlannedMember(
            name="Rah",
            species="Nidoqueen",
            level=32,
            max_hp=109,
            hp=109,
            moves=("Unknown move 1", "Unknown move 2"),
            slot=0,
        ),
        PlannedMember(
            name="Fire answer",
            species="Charizard",
            level=34,
            max_hp=110,
            hp=110,
            moves=("Flamethrower", "Slash"),
            slot=1,
        ),
    ]
    enemy = PlannedEnemy(
        name="Venusaur",
        pokemon=PokemonCalcSet("Venusaur", level=30, hp=100, max_hp=100),
        moves=("Tackle", "Growl"),
        max_hp=100,
        hp=100,
    )

    assert _switch_target_if_needed(team, 0, enemy, calculator) == 1


def test_unknown_move_action_is_marked_unreliable() -> None:
    calculator = DamageCalculator()
    member = PlannedMember(
        name="Rah",
        species="Nidoqueen",
        level=32,
        max_hp=109,
        hp=109,
        moves=("Unknown move 1",),
    )
    enemy = PlannedEnemy(
        name="Manectric",
        pokemon=PokemonCalcSet("Manectric", level=27, hp=83, max_hp=83),
        moves=("Thunderbolt",),
        max_hp=83,
        hp=83,
    )

    action = _best_player_action(member, enemy, [member], calculator)

    assert _action_is_unreliable(action)


def test_toxic_and_protect_create_real_residual_progress() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Seed",
        species="Venusaur",
        level=50,
        max_hp=155,
        hp=155,
        moves=("Toxic", "Protect"),
    )
    enemy = PlannedEnemy(
        name="Wall",
        pokemon=PokemonCalcSet("Blissey", level=50, hp=330, max_hp=330),
        moves=("Tackle",),
        max_hp=330,
        hp=330,
    )

    _apply_player_action(active, enemy, PlayerAction("move", "Toxic"), calculator)
    _end_of_turn(active, enemy, calculator)
    after_first_toxic = enemy.hp

    _apply_player_action(active, enemy, PlayerAction("move", "Protect"), calculator)
    damage = calculator.estimate_move(enemy.calc_set(), active.calc_set(), "Tackle")
    assert damage is not None
    taken = _apply_enemy_action(enemy, active, MoveChoice("Tackle", 1.0, 1.0, damage), calculator)
    _end_of_turn(active, enemy, calculator)

    assert enemy.status == "toxic"
    assert enemy.toxic_counter == 2
    assert taken == 0
    assert enemy.hp < after_first_toxic


def test_leech_seed_drains_and_heals() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Seed",
        species="Venusaur",
        level=50,
        max_hp=155,
        hp=80,
        moves=("Leech Seed",),
    )
    enemy = PlannedEnemy(
        name="Target",
        pokemon=PokemonCalcSet("Wailord", level=50, hp=250, max_hp=250),
        moves=("Splash",),
        max_hp=250,
        hp=250,
    )

    _apply_player_action(active, enemy, PlayerAction("move", "Leech Seed"), calculator)
    _end_of_turn(active, enemy, calculator)

    assert enemy.leech_seeded is True
    assert enemy.hp == 219
    assert active.hp == 111


def test_ai_branch_confidence_accounts_for_status_branches() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Fast",
        species="Starmie",
        level=50,
        max_hp=135,
        hp=135,
        moves=("Tackle",),
    )
    enemy = PlannedEnemy(
        name="Status",
        pokemon=PokemonCalcSet("Pikachu", level=50, hp=100, max_hp=100),
        moves=("Thunder Wave", "Quick Attack"),
        max_hp=100,
        hp=100,
    )

    choices = _ai_move_choices(enemy, active, [active], calculator)
    confidence = _ai_branch_confidence(choices, active, enemy, calculator)

    assert any(choice.move_name == "Thunder Wave" for choice in choices)
    assert 0 < confidence < 1


def test_ai_never_softmaxes_a_strictly_weaker_attack() -> None:
    calculator = DamageCalculator()
    active = PlannedMember("Target", "Venusaur", 50, 155, 155, ("Tackle",))
    enemy = PlannedEnemy(
        "Attacker", PokemonCalcSet("Charizard", level=50, hp=150, max_hp=150),
        ("Flamethrower", "Scratch"), 150, 150,
    )

    choices = {choice.move_name: choice for choice in _ai_move_choices(enemy, active, [active], calculator)}

    assert choices["Flamethrower"].probability == 1.0
    assert choices["Scratch"].probability == 0.0


def test_ai_setup_status_has_diminishing_returns() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Punisher",
        species="Palpitoad",
        level=32,
        max_hp=99,
        hp=99,
        moves=("Mud Shot",),
    )
    enemy = PlannedEnemy(
        name="Boltund",
        pokemon=PokemonCalcSet("Boltund", level=32, hp=82, max_hp=82),
        moves=("Howl", "Psychic Fangs", "Thunder Fang"),
        max_hp=82,
        hp=82,
        boosts={"atk": 1},
    )

    choices = _ai_move_choices(enemy, active, [active], calculator)
    howl = next(choice for choice in choices if choice.move_name == "Howl")

    assert choices[0].move_name == "Psychic Fangs"
    assert howl.probability < 0.05
    assert howl.reason == "setup already boosted"


def test_ai_setup_status_can_still_be_used_when_safe() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Passive",
        species="Palpitoad",
        level=32,
        max_hp=99,
        hp=99,
        moves=("Growl",),
    )
    enemy = PlannedEnemy(
        name="Boltund",
        pokemon=PokemonCalcSet("Boltund", level=32, hp=82, max_hp=82),
        moves=("Howl", "Crunch", "Thunder Fang"),
        max_hp=82,
        hp=82,
    )

    choices = _ai_move_choices(enemy, active, [active], calculator)

    assert choices[0].move_name == "Howl"
    assert choices[0].reason == "setup AI"


def test_ai_move_choices_can_force_enemy_crit_damage() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Answer",
        species="Blissey",
        level=50,
        max_hp=330,
        hp=330,
        moves=("Seismic Toss",),
    )
    enemy = PlannedEnemy(
        name="Attacker",
        pokemon=PokemonCalcSet("Garchomp", level=50, hp=180, max_hp=180),
        moves=("Earthquake",),
        max_hp=180,
        hp=180,
    )

    normal = _ai_move_choices(enemy, active, [active], calculator)[0]
    crit = _ai_move_choices(enemy, active, [active], calculator, force_crit=True)[0]

    assert normal.damage is not None and crit.damage is not None
    assert crit.damage.max_damage > normal.damage.max_damage


def test_toxic_can_rank_above_weak_chip_against_bulk() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Wallbreaker",
        species="Venusaur",
        level=50,
        max_hp=155,
        hp=155,
        moves=("Toxic", "Tackle"),
    )
    enemy = PlannedEnemy(
        name="Wall",
        pokemon=PokemonCalcSet("Blissey", level=50, hp=330, max_hp=330),
        moves=("Pound",),
        max_hp=330,
        hp=330,
    )

    action = _best_player_action(active, enemy, [active], calculator)

    assert action.move_name == "Toxic"


def test_guaranteed_secondary_stat_drop_applies_after_damage() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Control",
        species="Starmie",
        level=50,
        max_hp=135,
        hp=135,
        moves=("Icy Wind",),
    )
    enemy = PlannedEnemy(
        name="Target",
        pokemon=PokemonCalcSet("Pikachu", level=50, hp=110, max_hp=110),
        moves=("Quick Attack",),
        max_hp=110,
        hp=110,
    )
    damage = calculator.estimate_move(active.calc_set(), enemy.calc_set(), "Icy Wind")
    assert damage is not None

    _apply_player_action(active, enemy, PlayerAction("move", "Icy Wind", damage=damage), calculator)

    assert enemy.boosts["spe"] == -1


def test_shield_dust_and_covert_cloak_block_additional_effects() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Control",
        species="Starmie",
        level=50,
        max_hp=135,
        hp=135,
        moves=("Icy Wind",),
    )
    shield_dust = PlannedEnemy(
        name="Dust",
        pokemon=PokemonCalcSet("Butterfree", level=50, ability="Shield Dust", hp=120, max_hp=120),
        moves=("Tackle",),
        max_hp=120,
        hp=120,
    )
    cloak = PlannedEnemy(
        name="Cloak",
        pokemon=PokemonCalcSet("Pikachu", level=50, held_item="Covert Cloak", hp=110, max_hp=110),
        moves=("Quick Attack",),
        max_hp=110,
        hp=110,
    )

    for enemy in (shield_dust, cloak):
        damage = calculator.estimate_move(active.calc_set(), enemy.calc_set(), "Icy Wind")
        assert damage is not None
        _apply_player_action(active, enemy, PlayerAction("move", "Icy Wind", damage=damage), calculator)
        assert enemy.boosts.get("spe", 0) == 0


def test_salt_cure_and_trapping_secondaries_are_stateful() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Garg",
        species="Garganacl",
        level=50,
        max_hp=160,
        hp=160,
        moves=("Salt Cure", "Anchor Shot"),
    )
    water_enemy = PlannedEnemy(
        name="Water",
        pokemon=PokemonCalcSet("Blastoise", level=50, hp=200, max_hp=200),
        moves=("Tackle",),
        max_hp=200,
        hp=200,
    )
    trap_enemy = PlannedEnemy(
        name="Trapped",
        pokemon=PokemonCalcSet("Blissey", level=50, hp=330, max_hp=330),
        moves=("Pound",),
        max_hp=330,
        hp=330,
    )

    salt_damage = calculator.estimate_move(active.calc_set(), water_enemy.calc_set(), "Salt Cure")
    assert salt_damage is not None
    _apply_player_action(active, water_enemy, PlayerAction("move", "Salt Cure", damage=salt_damage), calculator)
    after_hit = water_enemy.hp
    _end_of_turn(active, water_enemy, calculator)
    assert water_enemy.salt_cured is True
    assert water_enemy.hp == after_hit - 50

    trap_damage = calculator.estimate_move(active.calc_set(), trap_enemy.calc_set(), "Anchor Shot")
    assert trap_damage is not None
    _apply_player_action(active, trap_enemy, PlayerAction("move", "Anchor Shot", damage=trap_damage), calculator)
    assert trap_enemy.trapped is True


def test_serene_grace_changes_secondary_confidence() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Answer",
        species="Blissey",
        level=50,
        max_hp=330,
        hp=330,
        moves=("Seismic Toss",),
    )
    plain_enemy = PlannedEnemy(
        name="Plain",
        pokemon=PokemonCalcSet("Togekiss", level=50, hp=160, max_hp=160),
        moves=("Air Slash",),
        max_hp=160,
        hp=160,
    )
    serene_enemy = PlannedEnemy(
        name="Grace",
        pokemon=PokemonCalcSet("Togekiss", level=50, ability="Serene Grace", hp=160, max_hp=160),
        moves=("Air Slash",),
        max_hp=160,
        hp=160,
    )
    plain_damage = calculator.estimate_move(plain_enemy.calc_set(), active.calc_set(), "Air Slash")
    serene_damage = calculator.estimate_move(serene_enemy.calc_set(), active.calc_set(), "Air Slash")
    assert plain_damage is not None
    assert serene_damage is not None

    plain_confidence = _ai_branch_confidence(
        [MoveChoice("Air Slash", 1.0, 1.0, plain_damage)],
        active,
        plain_enemy,
        calculator,
    )
    serene_confidence = _ai_branch_confidence(
        [MoveChoice("Air Slash", 1.0, 1.0, serene_damage)],
        active,
        serene_enemy,
        calculator,
    )

    assert serene_confidence < plain_confidence


def test_contrary_simple_and_clear_body_affect_stat_changes() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Control",
        species="Starmie",
        level=50,
        max_hp=135,
        hp=135,
        moves=("Icy Wind", "Swords Dance"),
        ability="Simple",
    )
    contrary_enemy = PlannedEnemy(
        name="Contrary",
        pokemon=PokemonCalcSet("Shuckle", level=50, ability="Contrary", hp=120, max_hp=120),
        moves=("Tackle",),
        max_hp=120,
        hp=120,
    )
    clear_body_enemy = PlannedEnemy(
        name="Clear",
        pokemon=PokemonCalcSet("Metagross", level=50, ability="Clear Body", hp=160, max_hp=160),
        moves=("Tackle",),
        max_hp=160,
        hp=160,
    )

    contrary_damage = calculator.estimate_move(active.calc_set(), contrary_enemy.calc_set(), "Icy Wind")
    clear_damage = calculator.estimate_move(active.calc_set(), clear_body_enemy.calc_set(), "Icy Wind")
    assert contrary_damage is not None and clear_damage is not None

    _apply_player_action(active, contrary_enemy, PlayerAction("move", "Icy Wind", damage=contrary_damage), calculator)
    _apply_player_action(active, clear_body_enemy, PlayerAction("move", "Icy Wind", damage=clear_damage), calculator)
    _apply_player_action(active, clear_body_enemy, PlayerAction("move", "Swords Dance"), calculator)

    assert contrary_enemy.boosts["spe"] == 1
    assert clear_body_enemy.boosts.get("spe", 0) == 0
    assert active.boosts["atk"] == 4


def test_magic_bounce_and_good_as_gold_block_status_actions() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Status",
        species="Blissey",
        level=50,
        max_hp=330,
        hp=330,
        moves=("Toxic",),
    )
    bouncer = PlannedEnemy(
        name="Bounce",
        pokemon=PokemonCalcSet("Espeon", level=50, ability="Magic Bounce", hp=140, max_hp=140),
        moves=("Tackle",),
        max_hp=140,
        hp=140,
    )
    gold = PlannedEnemy(
        name="Gold",
        pokemon=PokemonCalcSet("Gholdengo", level=50, ability="Good as Gold", hp=160, max_hp=160),
        moves=("Tackle",),
        max_hp=160,
        hp=160,
    )

    _apply_player_action(active, bouncer, PlayerAction("move", "Toxic"), calculator)
    assert bouncer.status is None
    assert active.status == "toxic"

    active.status = None
    _apply_player_action(active, gold, PlayerAction("move", "Toxic"), calculator)
    assert gold.status is None
    assert active.status is None


def test_contact_abilities_and_ko_boosts_are_stateful() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Attacker",
        species="Gyarados",
        level=50,
        max_hp=170,
        hp=170,
        moves=("Tackle",),
        ability="Moxie",
    )
    rough_skin = PlannedEnemy(
        name="Skin",
        pokemon=PokemonCalcSet("Garchomp", level=50, ability="Rough Skin", hp=1, max_hp=180),
        moves=("Tackle",),
        max_hp=180,
        hp=1,
    )
    damage = calculator.estimate_move(active.calc_set(), rough_skin.calc_set(), "Tackle")
    assert damage is not None

    _apply_player_action(active, rough_skin, PlayerAction("move", "Tackle", damage=damage), calculator)

    assert rough_skin.alive is False
    assert active.hp < active.max_hp
    assert active.boosts["atk"] == 1


def test_unseen_fist_can_chip_through_protect() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Bypass",
        species="Urshifu",
        level=50,
        max_hp=170,
        hp=170,
        moves=("Tackle",),
        ability="Unseen Fist",
    )
    enemy = PlannedEnemy(
        name="Protect",
        pokemon=PokemonCalcSet("Blissey", level=50, hp=330, max_hp=330),
        moves=("Protect",),
        max_hp=330,
        hp=330,
        protected=True,
    )
    damage = calculator.estimate_move(active.calc_set(), enemy.calc_set(), "Tackle")
    assert damage is not None

    dealt = _apply_player_action(active, enemy, PlayerAction("move", "Tackle", damage=damage), calculator)

    assert dealt > 0
    assert dealt < damage.min_damage


def test_rnb_defend_order_functions_like_protect() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Wall",
        species="Vespiquen",
        level=50,
        max_hp=150,
        hp=150,
        moves=("Defend Order",),
    )
    enemy = PlannedEnemy(
        name="Hit",
        pokemon=PokemonCalcSet("Golem", level=50, hp=150, max_hp=150),
        moves=("Tackle",),
        max_hp=150,
        hp=150,
    )

    _apply_player_action(active, enemy, PlayerAction("move", "Defend Order"), calculator)
    damage = calculator.estimate_move(enemy.calc_set(), active.calc_set(), "Tackle")
    assert damage is not None
    taken = _apply_enemy_action(enemy, active, MoveChoice("Tackle", 1.0, 1.0, damage), calculator)

    assert active.protected is True
    assert taken == 0


def test_rnb_disguise_blocks_once_without_chip() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Attacker",
        species="Golem",
        level=50,
        max_hp=150,
        hp=150,
        moves=("Rock Throw",),
    )
    enemy = PlannedEnemy(
        name="Mask",
        pokemon=PokemonCalcSet("Mimikyu", level=50, ability="Disguise", hp=120, max_hp=120),
        moves=("Tackle",),
        max_hp=120,
        hp=120,
    )
    first = calculator.estimate_move(active.calc_set(), enemy.calc_set(), "Rock Throw")
    assert first is not None

    dealt = _apply_player_action(active, enemy, PlayerAction("move", "Rock Throw", damage=first), calculator)
    second = calculator.estimate_move(active.calc_set(), enemy.calc_set(), "Rock Throw")

    assert dealt == 0
    assert enemy.hp == enemy.max_hp
    assert enemy.ability_on is False
    assert second is not None and second.max_damage > 0


def test_rnb_gale_wings_and_electric_thunder_wave_accuracy() -> None:
    calculator = DamageCalculator()
    talonflame = PlannedMember(
        name="Bird",
        species="Talonflame",
        level=50,
        max_hp=150,
        hp=1,
        moves=("Fly",),
        ability="Gale Wings",
    )
    pikachu = PlannedEnemy(
        name="Fast",
        pokemon=PokemonCalcSet("Pikachu", level=50, hp=100, max_hp=100),
        moves=("Quick Attack",),
        max_hp=100,
        hp=100,
    )

    assert _enemy_moves_before_player(pikachu, talonflame, "Quick Attack", "Fly", calculator) is False
    assert _move_accuracy("Thunder Wave", calculator, talonflame) == 0.9
    electric_user = PlannedMember("Zap", "Pikachu", 50, 100, 100, ("Thunder Wave",))
    assert _move_accuracy("Thunder Wave", calculator, electric_user) == 1.0


def test_rnb_confuse_berry_restores_half_at_quarter_hp() -> None:
    calculator = DamageCalculator()
    active = PlannedMember(
        name="Berry",
        species="Blissey",
        level=50,
        max_hp=320,
        hp=80,
        moves=("Protect",),
        item="Figy Berry",
    )
    enemy = PlannedEnemy(
        name="Idle",
        pokemon=PokemonCalcSet("Magikarp", level=50, hp=80, max_hp=80),
        moves=("Splash",),
        max_hp=80,
        hp=80,
    )

    _end_of_turn(active, enemy, calculator)

    assert active.hp == 240
    assert active.consumed_item is True
