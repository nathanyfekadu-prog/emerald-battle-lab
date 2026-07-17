from __future__ import annotations

from emulator.state_reader import MemoryMap, StateReader
import emulator.state_reader as state_reader_module


class FakeInstance:
    def __init__(self, memory: dict[int, int]):
        self.memory = memory
        self.frames = 0

    def read_u16(self, address: int) -> int:
        return self.memory.get(address, 0)

    def advance_frames(self, n: int) -> None:
        self.frames += n


def test_state_reader_applies_run_bun_active_hp_override() -> None:
    memory_map = MemoryMap(
        player_party_base=0x1000,
        enemy_party_base=0x2000,
        party_struct_size=100,
        player_hp_offset=0x38,
        player_max_hp_offset=0x3A,
        enemy_hp_offset=0x38,
        enemy_max_hp_offset=0x3A,
        battle_outcome=0x3000,
        menu_ready_flag=0x3002,
        battle_type=0x3004,
        player_active_hp=0x4000,
        player_active_max_hp=0x4002,
    )
    fake = FakeInstance({0x4000: 24, 0x4002: 60})
    state = StateReader(fake, memory_map).read()

    assert state.player_hp[0] == 24
    assert state.player_max_hp[0] == 60
    assert state.player_fainted[0] is False
    assert state.menu_ready is False


def test_state_reader_uses_menu_ready_flag() -> None:
    memory_map = MemoryMap(
        player_party_base=0x1000,
        enemy_party_base=0x2000,
        party_struct_size=100,
        player_hp_offset=0x38,
        player_max_hp_offset=0x3A,
        enemy_hp_offset=0x38,
        enemy_max_hp_offset=0x3A,
        battle_outcome=0x3000,
        menu_ready_flag=0x3002,
        battle_type=0x3004,
        player_active_hp=0x4000,
        player_active_max_hp=0x4002,
    )
    fake = FakeInstance({0x3002: 1, 0x4000: 24, 0x4002: 60})
    state = StateReader(fake, memory_map).read()

    assert state.menu_ready is True


def test_state_reader_treats_double_as_a_bitmask_flag() -> None:
    memory_map = MemoryMap(
        player_party_base=0x1000,
        enemy_party_base=0x2000,
        battle_type=0x3004,
        player_active_hp=None,
        player_active_max_hp=None,
    )
    # Run & Bun's real trainer double battle reads 0x800D, not the literal 1.
    state = StateReader(FakeInstance({0x3004: 0x800D}), memory_map).read()

    assert state.is_doubles is True
    assert len(state.player_active_slots) == 2
    assert len(state.enemy_active_slots) == 2


def test_run_bun_battle_struct_reads_both_move_sets_and_active_slots() -> None:
    class MoveInstance(FakeInstance):
        rom_path = "/fake/run-and-bun.gba"

    class NamedMoveReader(StateReader):
        def _move_name(self, move_id: int) -> str:
            return {341: "Mud Shot", 44: "Bite", 394: "Flare Blitz", 528: "Wild Charge"}.get(move_id, "")

    memory: dict[int, int] = {}
    player_moves = state_reader_module._BATTLE_MONS_ADDRESS + state_reader_module._BATTLE_MON_MOVES_OFFSET
    enemy_moves = player_moves + state_reader_module._BATTLE_MON_SIZE
    memory.update({player_moves: 341, player_moves + 2: 44})
    memory.update({enemy_moves: 394, enemy_moves + 2: 528})
    memory[state_reader_module._BATTLER_PARTY_INDEXES_ADDRESS] = 1
    memory[state_reader_module._BATTLER_PARTY_INDEXES_ADDRESS + 2] = 3

    state = NamedMoveReader(MoveInstance(memory)).read()

    assert state.player_move_names[:2] == ["Mud Shot", "Bite"]
    assert state.enemy_move_names[:2] == ["Flare Blitz", "Wild Charge"]
    assert state.player_active_slots == (1,)
    assert state.enemy_active_slots == (3,)
    assert state.player_move_names_by_slot[1][:2] == ["Mud Shot", "Bite"]


def test_party_hp_marks_blackout_when_rom_outcome_flag_is_not_set() -> None:
    memory_map = MemoryMap(
        player_party_base=0x1000,
        enemy_party_base=0x2000,
        party_struct_size=100,
        player_hp_offset=0x38,
        player_max_hp_offset=0x3A,
        enemy_hp_offset=0x38,
        enemy_max_hp_offset=0x3A,
        battle_outcome=0x3000,
        menu_ready_flag=0x3002,
        battle_type=0x3004,
        player_active_hp=None,
        player_active_max_hp=None,
    )
    fake = FakeInstance({
        0x1000 + 0x3A: 50,
        0x1000 + 100 + 0x3A: 60,
        0x2000 + 0x38: 30,
        0x2000 + 0x3A: 70,
    })

    state = StateReader(fake, memory_map).read()

    assert state.battle_over is True
    assert state.player_won is False
