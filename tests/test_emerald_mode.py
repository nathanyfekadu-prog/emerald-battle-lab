from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from tools.import_emerald_trainers import build_dataset
from battle.damage_calc import DamageCalculator, DamageContext, FieldState, PokemonCalcSet
from trainer_data.loader import load_trainer_battles_for_mode
from web.server import (
    _emerald_failure_diagnosis,
    _emerald_level_guidance,
    _emerald_progressive_hints,
    _parse_imported_sets,
    _planned_enemies_for_trainer,
)
from optimizer.turn_planner import _best_player_action


ROOT = Path(__file__).resolve().parents[1]


def test_committed_emerald_catalog_is_complete_and_source_grounded() -> None:
    data = json.loads((ROOT / "data" / "emerald_trainers.json").read_text(encoding="utf-8"))
    assert data["game"] == "pokemon-emerald"
    assert data["source"]["sheet_gid"] == "1064630895"
    assert {key: data["stats"][key] for key in ("trainers", "pokemon", "required_trainers", "locations")} == {
        "trainers": 512, "pokemon": 932, "required_trainers": 85, "locations": 53,
    }
    assert data["stats"]["exact_map_events"] >= 480
    assert data["trainers"][0]["trainer_name"] == "Littleroot Rival"
    assert data["trainers"][0]["required"] is True
    assert data["trainers"][0]["party"][0]["moves"] == ["Pound", "Leer"]
    assert all(trainer["map_location"] for trainer in data["trainers"])
    assert data["trainers"][1]["map_event"]["map_id"] == "MAP_ROUTE102"
    petalburg_grunt = next(
        trainer for trainer in data["trainers"]
        if trainer["trainer_name"] == "Team Aqua Grunt" and trainer["map_location"] == "Petalburg Woods"
    )
    assert petalburg_grunt["map_event"] == {
        "map_id": "MAP_PETALBURG_WOODS",
        "map_name": "PetalburgWoods",
        "x": 26,
        "y": 17,
        "elevation": 3,
        "sight": 0,
        "map_width": 48,
        "map_height": 44,
        "script": "story battle: TRAINER_GRUNT_PETALBURG_WOODS",
        "graphics_id": "OBJ_EVENT_GFX_AQUA_MEMBER_M",
        "trainer_constant": "TRAINER_GRUNT_PETALBURG_WOODS",
        "trainer_name": "GRUNT",
    }


def test_real_emerald_map_renders_cover_trainer_floors() -> None:
    map_root = ROOT / "web" / "static" / "emerald-maps"
    manifest = json.loads((map_root / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest) >= 80
    assert manifest["Route102"] == {
        "image": "/static/emerald-maps/Route102.png",
        "width": 50,
        "height": 20,
        "pixel_width": 800,
        "pixel_height": 320,
    }
    with Image.open(map_root / "Route102.png") as route:
        assert route.size == (800, 320)
    sprite_root = ROOT / "web" / "static" / "emerald-trainers"
    sprite_manifest = json.loads((sprite_root / "manifest.json").read_text(encoding="utf-8"))
    assert len(sprite_manifest) >= 50
    with Image.open(sprite_root / "OBJ_EVENT_GFX_AQUA_MEMBER_M.png") as sprite:
        assert sprite.size == (16, 32)


def test_emerald_judge_ui_uses_the_rom_free_checkpoint_planner() -> None:
    html = (ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    library = json.loads((ROOT / "data" / "emerald_checkpoint_library.json").read_text(encoding="utf-8"))
    assert library["stats"] == {
        "checkpoints": 173, "unique_trainers": 158,
        "boss_checkpoints": 20, "unmatched": 0,
    }
    assert "Captured battle planner" in html
    assert "Load the Elite Four → Wallace Gauntlet" in html
    assert "Hardcore Nuzlocke" in html
    assert "Pokémon Emerald Generation III damage calculator" not in html
    assert all(entry["binary_included"] is False for entry in library["entries"])


def test_emerald_ui_keeps_repeatable_baselines_and_plain_gauntlet_failures() -> None:
    html = (ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'result_state: ""' in html
    assert '{ ...current, state: msg.data.output_state }' not in html
    assert "Continue from victory" in html
    assert 'pct: 0, stage: "setup-error"' in html
    assert "visibleMatches = matches.slice(0, 24)" in html
    assert 'gameMode !== "emerald" && <div className={`sim-line-record' in html
    assert 'video_ready: Boolean((data.videos || []).some((video) => video.video_ready))' in html
    assert "Saving required video and full log" in html
    assert "Every completed route has a planner video and full text log" in html


def test_importer_preserves_bold_required_and_double_battle_rows() -> None:
    csv_text = """,Money,Route,Location on Route,Pokémon,Level,Attack 1,Attack 2,Attack 3,Attack 4,EXP,HP,Attack,Defense,Sp. Attack,Sp Defense,Speed
Leader Test [2],100,Route 110,North,Plusle,20,Spark,,,,100,50,30,20,35,25,40
[2],,,,Minun,20,Thunderbolt,,,,100,49,29,21,36,26,41
"""
    grid_html = """
    <style>.ritz .waffle .required{font-weight:bold;}</style>
    <table><tr><th><div class="row-header-wrapper">1</div></th><td class="plain"></td></tr>
    <tr><th><div class="row-header-wrapper">2</div></th><td class="required">Leader Test [2]</td></tr>
    <tr><th><div class="row-header-wrapper">3</div></th><td class="required">[2]</td></tr></table>
    """
    data = build_dataset(csv_text, grid_html)
    trainer = data["trainers"][0]
    assert trainer["required"] is True
    assert trainer["is_double"] is True
    assert [member["species"] for member in trainer["party"]] == ["Plusle", "Minun"]
    assert trainer["map_location"] == "Route 110"


def test_emerald_mode_uses_gen_three_data_and_exact_sheet_stats() -> None:
    calculator = DamageCalculator(game_mode="pokemon-emerald")
    assert calculator.data["generation"] == 3
    assert calculator.moves["shadowball"]["category"] == "Physical"
    assert "tinkaton" not in calculator.species
    rival = load_trainer_battles_for_mode("pokemon-emerald")[0]
    known = calculator._known_set_from_trainer_mon(rival.party[0])  # noqa: SLF001
    assert known.pokemon.max_hp == 19
    assert known.pokemon.stat_overrides == {
        "hp": 19, "atk": 8, "def": 8, "spa": 11, "spd": 11, "spe": 12,
    }


def test_emerald_crit_and_doubles_modifiers_are_generation_three() -> None:
    emerald = DamageCalculator(game_mode="pokemon-emerald")
    modern = DamageCalculator()
    attacker = PokemonCalcSet("Swampert", level=50)
    defender = PokemonCalcSet("Camerupt", level=50)
    normal = emerald.estimate_move(attacker, defender, "Surf")
    critical = emerald.estimate_move(attacker, defender, "Surf", DamageContext(critical=True))
    spread = emerald.estimate_move(
        attacker, defender, "Surf", DamageContext(field=FieldState(is_doubles=True))
    )
    modern_spread = modern.estimate_move(
        attacker, defender, "Surf", DamageContext(field=FieldState(is_doubles=True))
    )
    assert normal and critical and spread and modern_spread
    assert critical.max_damage > normal.max_damage * 1.8
    assert spread.max_damage < modern_spread.max_damage


def test_emerald_catalog_includes_standard_gym_level_caps() -> None:
    data = json.loads((ROOT / "data" / "emerald_trainers.json").read_text(encoding="utf-8"))
    assert [(row["trainer_name"], row["ace_level"]) for row in data["level_caps"]] == [
        ("Leader Roxanne", 15), ("Leader Brawly", 19), ("Leader Wattson", 24),
        ("Leader Flannery", 29), ("Leader Norman", 31), ("Leader Winona", 33),
        ("Leader Tate&Liza [2]", 42), ("Leader Juan", 46),
    ]


def test_progressive_hints_warn_over_cap_without_recommending_more_levels() -> None:
    calculator = DamageCalculator(game_mode="pokemon-emerald")
    team = _parse_imported_sets("Swampert\nLevel: 20\n- Water Gun", calculator)
    trainer = load_trainer_battles_for_mode("pokemon-emerald")[13]
    result = {"result": "route-stopped", "team": [], "turns": []}
    guidance = _emerald_level_guidance(team, trainer, result, 15, calculator)
    hints = _emerald_progressive_hints(team, trainer, result, guidance, calculator)
    assert guidance["legal"] is False
    assert guidance["over_cap"] == ["Swampert"]
    assert guidance["target_max"] is None
    assert len(hints) == 4
    assert "cap is 15" in hints[0]["text"]


def test_progressive_hints_name_the_lead_instead_of_showing_its_slot_number() -> None:
    calculator = DamageCalculator(game_mode="pokemon-emerald")
    team = _parse_imported_sets("Ali (Electrike)\nLevel: 10\n- Tackle", calculator)
    trainer = load_trainer_battles_for_mode("pokemon-emerald")[1]
    result = {
        "team": [{"name": "Ali", "species": "Electrike"}],
        "line_search": {"lead": 0},
        "turns": [{"action": "Ali vs Poochyena: click Tackle."}],
    }
    guidance = _emerald_level_guidance(team, trainer, result, 15, calculator)
    hints = _emerald_progressive_hints(team, trainer, result, guidance, calculator)
    assert hints[2]["text"].endswith("Start by considering Ali.")


def test_roxanne_failure_explains_the_legal_water_gun_upgrade() -> None:
    calculator = DamageCalculator(game_mode="pokemon-emerald")
    team = _parse_imported_sets(
        "Mudkip\nLevel: 6\nIVs: 23 HP / 23 Def / 7 SpD\n- Tackle\n- Growl\n- Mud-Slap\n\n"
        "Poochyena\nLevel: 3\nIVs: 5 Def\n- Tackle",
        calculator,
    )
    trainer = load_trainer_battles_for_mode("pokemon-emerald")[13]
    result = {"result": "partial-line", "team": [], "turns": []}
    guidance = _emerald_level_guidance(team, trainer, result, 15, calculator)
    diagnosis = _emerald_failure_diagnosis(team, trainer, result, guidance, calculator)
    joined = " ".join(diagnosis["recommended_steps"] + [row["action"] for row in diagnosis["blockers"]])
    assert diagnosis["status"] == "needs-preparation"
    assert "level 10" in joined
    assert "Water Gun" in joined
    assert "Do not grind IVs" in joined
    assert diagnosis["level_cap"] == 15


def test_reliable_super_effective_progress_beats_generic_growl() -> None:
    calculator = DamageCalculator(game_mode="pokemon-emerald")
    team = _parse_imported_sets(
        "Mudkip\nLevel: 15\nQuiet Nature\n- Tackle\n- Growl\n- Mud-Slap\n- Water Gun",
        calculator,
    )
    nosepass = _planned_enemies_for_trainer(
        load_trainer_battles_for_mode("pokemon-emerald")[13], calculator
    )[-1]
    action = _best_player_action(team[0], nosepass, team, calculator)
    assert action.move_name == "Water Gun"


def test_failure_diagnosis_does_not_claim_owned_water_coverage_is_missing() -> None:
    calculator = DamageCalculator(game_mode="pokemon-emerald")
    team = _parse_imported_sets("Mudkip\nLevel: 10\n- Tackle\n- Water Gun", calculator)
    trainer = load_trainer_battles_for_mode("pokemon-emerald")[13]
    result = {"result": "partial-line", "team": [], "turns": []}
    guidance = _emerald_level_guidance(team, trainer, result, 15, calculator)
    diagnosis = _emerald_failure_diagnosis(team, trainer, result, guidance, calculator)
    coverage = next(row for row in diagnosis["blockers"] if row["kind"] == "coverage")
    assert "Water" not in coverage["evidence"]
