from pathlib import Path
from unittest.mock import patch

from tools.capture_battle_checkpoints import (
    _trainer_from_map_event,
    checkpoint_suffix,
    destination_for,
    recognize_trainer,
    safe_stem,
)


def test_checkpoint_suffix_accepts_mgba_state_extensions() -> None:
    assert checkpoint_suffix(Path("battle.ss0")) == ".ss0"
    assert checkpoint_suffix(Path("battle.SS12")) == ".ss12"
    assert checkpoint_suffix(Path("battery.sav")) is None
    assert checkpoint_suffix(Path("battle.ssg")) is None


def test_safe_stem_preserves_readability_and_removes_unsafe_characters() -> None:
    assert safe_stem("Leader Roxanne") == "leader-roxanne"
    assert safe_stem("  Twins / May & June! ") == "twins-may-june"
    assert safe_stem("///") == "unrecognized-trainer"


def test_destination_adds_a_copy_number_without_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "Pokemon Run and Bun.ss0"
    source.write_bytes(b"state")
    first = tmp_path / "leader-roxanne.ss0"
    first.write_bytes(b"existing")

    assert destination_for(source, "Leader Roxanne") == tmp_path / "leader-roxanne-2.ss0"


def test_fight_label_is_safe_for_a_checkpoint_filename() -> None:
    assert safe_stem("fight-Tibo") == "fight-tibo"


def test_recognition_uses_emerald_trainer_data() -> None:
    class FakeInstance:
        def read_u8(self, _address: int) -> int:
            return 2

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            pass

    class FakeSnapshot:
        trainer_name = "Youngster Calvin"
        trainer_location = "Petalburg Woods"
        trainer_confidence = "exact"
        mode = "battle-command"
        is_known_trainer = True
        is_wild_battle = False

    class FakeBattle:
        enemy_species = [0] * 6
        enemy_max_hp = [0] * 6

    with patch("emulator.mgba_instance.MGBAInstance", return_value=FakeInstance()), patch(
        "emulator.game_state.WholeGameStateReader.read", return_value=FakeSnapshot()
    ), patch(
        "emulator.state_reader.StateReader.read", return_value=FakeBattle()
    ), patch("battle.damage_calc.DamageCalculator") as calculator:
        result = recognize_trainer(Path("rom.gba"), Path("state.ss0"), "pokemon-emerald")

    calculator.assert_called_once_with(game_mode="pokemon-emerald")
    assert result["trainer_name"] == "Youngster Calvin"


def test_map_event_identifies_youngster_calvin_from_the_first_battle_tile() -> None:
    assert _trainer_from_map_event((0, 17), 33, 15, ("POOCHYENA",), (18,)) == "Youngster Calvin"
    assert _trainer_from_map_event((0, 17), 33, 18, ("NINCADA",), (18,)) is None


def test_map_event_identifies_bug_catcher_rick_before_party_stats_stabilize() -> None:
    assert _trainer_from_map_event((0, 17), 25, 14, ("WURMPLE", "WURMPLE"), (16, 17)) == "Bug Catcher Rick"


def test_map_event_identifies_petalburg_woods_story_trainer_by_party() -> None:
    assert _trainer_from_map_event((24, 11), 27, 23, ("POOCHYENA",), (20,)) == "Team Aqua Grunt"


def test_map_event_identifies_rustboro_gym_trainers() -> None:
    assert _trainer_from_map_event((11, 3), 5, 15, ("GEODUDE",), (29,)) == "Youngster Josh"
    assert _trainer_from_map_event((11, 3), 1, 8, ("GEODUDE", "GEODUDE"), (25, 25)) == "Hiker Marc"
