from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import config
from battle.battle_state import BattleState
from emulator.mgba_instance import MGBAInstance


_TEXT_CHARS: dict[int, str] = {
    0x00: " ",
    0xAB: "!",
    0xAC: "?",
    0xAD: ".",
    0xAE: "-",
    0xB7: "'",
    0xBA: "/",
}
for _index, _char in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    _TEXT_CHARS[0xBB + _index] = _char
for _index, _char in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _TEXT_CHARS[0xD5 + _index] = _char
for _index, _char in enumerate("0123456789"):
    _TEXT_CHARS[0xA1 + _index] = _char

_TEXT_BYTES: dict[str, int] = {value: key for key, value in _TEXT_CHARS.items()}
_ROM_MOVE_NAME_LENGTH = 13
# Verified against the supplied Run & Bun .ss2 Corgi state, then again after a
# live switch from Nidoqueen to Palpitoad. Run & Bun keeps four 0x5c-byte battle
# mon structs here in battler order (player, enemy, player partner, enemy
# partner). The four move ids begin at +0x0c. The old 0x02000624 address is an
# enemy command-menu buffer, which is why the simulator labelled every player
# action with Arcanine's moves.
_BATTLE_MONS_ADDRESS = 0x020233FC
_BATTLE_MON_SIZE = 0x5C
_BATTLE_MON_MOVES_OFFSET = 0x0C
_BATTLE_MON_HP_OFFSET = 0x2A
_BATTLE_MON_MAX_HP_OFFSET = 0x2E
_BATTLER_PARTY_INDEXES_ADDRESS = 0x020233E6


@dataclass(frozen=True)
class MemoryMap:
    player_party_base: int = config.MEMORY_OVERRIDES.get("PLAYER_PARTY_BASE", config.PLAYER_PARTY_BASE)
    enemy_party_base: int = config.MEMORY_OVERRIDES.get("ENEMY_PARTY_BASE", config.ENEMY_PARTY_BASE)
    party_struct_size: int = config.PARTY_STRUCT_SIZE
    player_hp_offset: int = config.MEMORY_OVERRIDES.get("PLAYER_HP_OFFSET", config.HP_OFFSET)
    player_max_hp_offset: int = config.MEMORY_OVERRIDES.get("PLAYER_MAX_HP_OFFSET", config.MAX_HP_OFFSET)
    enemy_hp_offset: int = config.MEMORY_OVERRIDES.get("ENEMY_HP_OFFSET", config.HP_OFFSET)
    enemy_max_hp_offset: int = config.MEMORY_OVERRIDES.get("ENEMY_MAX_HP_OFFSET", config.MAX_HP_OFFSET)
    battle_outcome: int = config.BATTLE_OUTCOME
    menu_ready_flag: int = config.MENU_READY_FLAG
    battle_type: int = config.BATTLE_TYPE
    player_active_hp: int | None = config.MEMORY_OVERRIDES.get("PLAYER_ACTIVE_HP")
    player_active_max_hp: int | None = config.MEMORY_OVERRIDES.get("PLAYER_ACTIVE_MAX_HP")
    read_player_party_structs: bool = bool(
        config.MEMORY_OVERRIDES.get("READ_PLAYER_PARTY_STRUCTS", True)
    )
    read_enemy_party_structs: bool = bool(
        config.MEMORY_OVERRIDES.get("READ_ENEMY_PARTY_STRUCTS", True)
    )


class StateReader:
    def __init__(self, instance: MGBAInstance, memory_map: MemoryMap | None = None):
        self.instance = instance
        self.memory = memory_map or MemoryMap()
        self._explicit_memory_map = memory_map is not None
        self._rom_bytes: bytes | None = None
        self._move_names_base: int | None = None

    def read(self) -> BattleState:
        if self.memory.read_player_party_structs:
            player_hp = self._read_party_hp(self.memory.player_party_base, self.memory.player_hp_offset)
            player_max_hp = self._read_party_hp(self.memory.player_party_base, self.memory.player_max_hp_offset)
        else:
            player_hp = [0] * 6
            player_max_hp = [0] * 6

        if self.memory.read_enemy_party_structs:
            enemy_hp = self._read_party_hp(self.memory.enemy_party_base, self.memory.enemy_hp_offset)
            enemy_max_hp = self._read_party_hp(self.memory.enemy_party_base, self.memory.enemy_max_hp_offset)
        else:
            enemy_hp = [0] * 6
            enemy_max_hp = [0] * 6

        player_hp, player_max_hp = self._sanitize_party(player_hp, player_max_hp)
        enemy_hp, enemy_max_hp = self._sanitize_party(enemy_hp, enemy_max_hp)
        read_u32 = getattr(self.instance, "read_u32", None)
        battle_type = (
            read_u32(self.memory.battle_type)
            if callable(read_u32)
            else self.instance.read_u16(self.memory.battle_type)
        )
        menu_flag = self.instance.read_u16(self.memory.menu_ready_flag)

        is_doubles = bool(battle_type & 1)
        player_battlers = (0, 2) if is_doubles else (0,)
        enemy_battlers = (1, 3) if is_doubles else (1,)
        player_active_slots = self._read_active_party_slots(player_battlers)
        enemy_active_slots = self._read_active_party_slots(enemy_battlers)
        command_phase = (
            self.instance.read_u8(config.RUN_BUN_BATTLE_COMMAND_PHASE)
            if hasattr(self.instance, "read_u8") else 0
        )
        if self._explicit_memory_map:
            # Unit/custom maps intentionally supply a dedicated active-HP
            # address and expect it to be authoritative. The production Run &
            # Bun map uses live battler structs below and phase-gates them so
            # stale post-battle RAM cannot corrupt overworld HP.
            self._apply_active_player_override(player_hp, player_max_hp)
        if command_phase:
            self._apply_battler_hp_overrides(
                player_hp, player_max_hp, player_battlers, player_active_slots
            )
            self._apply_battler_hp_overrides(
                enemy_hp, enemy_max_hp, enemy_battlers, enemy_active_slots
            )

        outcome = self.instance.read_u16(self.memory.battle_outcome)
        player_all_fainted = self._all_present_fainted(player_hp, player_max_hp)
        enemy_all_fainted = self._all_present_fainted(enemy_hp, enemy_max_hp)
        # Run & Bun does not consistently write Emerald's vanilla outcome flag
        # before leaving the battle screen. Party HP plus the live battler HP
        # overrides are authoritative for the terminal states we care about.
        battle_over = outcome in (1, 2) or player_all_fainted or enemy_all_fainted
        player_won = (outcome == 1 or enemy_all_fainted) and not player_all_fainted
        # Do not infer menu readiness from HP. Party/summary screens still have
        # live HP, and treating that as battle-menu readiness lets later inputs
        # get sent to the wrong UI.
        menu_ready = not battle_over and menu_flag != 0
        player_move_ids = self._read_battler_move_ids(player_battlers[0])
        enemy_move_ids = self._read_battler_move_ids(enemy_battlers[0])
        player_move_names = self._move_names(player_move_ids)
        enemy_move_names = self._move_names(enemy_move_ids)

        return BattleState(
            player_hp=player_hp,
            player_max_hp=player_max_hp,
            player_fainted=[max_hp > 0 and hp <= 0 for hp, max_hp in zip(player_hp, player_max_hp)],
            enemy_hp=enemy_hp,
            enemy_max_hp=enemy_max_hp,
            enemy_fainted=[max_hp > 0 and hp <= 0 for hp, max_hp in zip(enemy_hp, enemy_max_hp)],
            battle_over=battle_over,
            player_won=player_won,
            is_doubles=is_doubles,
            menu_ready=menu_ready,
            player_names=self._read_party_names(self.memory.player_party_base),
            enemy_names=self._read_enemy_names(),
            player_move_names=player_move_names,
            player_move_names_by_slot=self._move_names_by_party_slot(
                player_battlers, player_active_slots
            ),
            player_move_ids=player_move_ids,
            enemy_move_names=enemy_move_names,
            enemy_move_ids=enemy_move_ids,
            enemy_move_names_by_slot=self._move_names_by_party_slot(
                enemy_battlers, enemy_active_slots
            ),
            player_species=self._read_party_species(self.memory.player_party_base),
            enemy_species=self._read_party_species(self.memory.enemy_party_base),
            player_active_slots=player_active_slots,
            enemy_active_slots=enemy_active_slots,
        )

    def wait_for_menu(self, timeout_frames: int = 500) -> bool:
        for _ in range(timeout_frames):
            state = self.read()
            if state.menu_ready or state.battle_over:
                return state.menu_ready
            self.instance.advance_frames(1)
        return False

    def active_player_fainted(self) -> bool:
        """Fast live-struct check used while the forced replacement UI is open.

        Doubles has two player battlers (0 and 2).  Checking only battler zero
        allowed a partner faint to be checkpointed as another healthy turn.
        """
        read_u32 = getattr(self.instance, "read_u32", None)
        battle_type = (
            read_u32(self.memory.battle_type)
            if callable(read_u32)
            else self.instance.read_u16(self.memory.battle_type)
        )
        battlers = (0, 2) if battle_type & 1 else (0,)
        for battler in battlers:
            address = _BATTLE_MONS_ADDRESS + battler * _BATTLE_MON_SIZE
            hp = self.instance.read_u16(address + _BATTLE_MON_HP_OFFSET)
            max_hp = self.instance.read_u16(address + _BATTLE_MON_MAX_HP_OFFSET)
            if 0 < max_hp <= 999 and hp == 0:
                return True
        return False

    def read_live_battle(self) -> BattleState:
        """Read a battle screen with live battler HP even when command phase is zero.

        Faint dialogue and the forced-party screen temporarily clear the command
        phase.  Ordinary ``read`` deliberately avoids live overlays at phase zero
        because battle structs remain stale in the overworld; callers use this
        method only after independently detecting an in-battle forced faint.
        """
        state = self.read()
        player_hp = list(state.player_hp)
        player_max_hp = list(state.player_max_hp)
        enemy_hp = list(state.enemy_hp)
        enemy_max_hp = list(state.enemy_max_hp)
        player_battlers = (0, 2) if state.is_doubles else (0,)
        enemy_battlers = (1, 3) if state.is_doubles else (1,)
        self._apply_battler_hp_overrides(
            player_hp, player_max_hp, player_battlers, state.player_active_slots
        )
        self._apply_battler_hp_overrides(
            enemy_hp, enemy_max_hp, enemy_battlers, state.enemy_active_slots
        )
        player_fainted = [
            max_hp > 0 and hp <= 0 for hp, max_hp in zip(player_hp, player_max_hp)
        ]
        enemy_fainted = [
            max_hp > 0 and hp <= 0 for hp, max_hp in zip(enemy_hp, enemy_max_hp)
        ]
        player_all_fainted = self._all_present_fainted(player_hp, player_max_hp)
        enemy_all_fainted = self._all_present_fainted(enemy_hp, enemy_max_hp)
        return replace(
            state,
            player_hp=player_hp,
            player_max_hp=player_max_hp,
            player_fainted=player_fainted,
            enemy_hp=enemy_hp,
            enemy_max_hp=enemy_max_hp,
            enemy_fainted=enemy_fainted,
            battle_over=state.battle_over or player_all_fainted or enemy_all_fainted,
            player_won=(state.player_won or enemy_all_fainted) and not player_all_fainted,
        )

    def _read_party_hp(self, base: int, offset: int) -> list[int]:
        values: list[int] = []
        for slot in range(6):
            address = base + slot * self.memory.party_struct_size + offset
            values.append(self.instance.read_u16(address))
        return values

    def _read_party_names(self, base: int) -> list[str]:
        if not hasattr(self.instance, "read_u8"):
            return [f"Pokemon {slot + 1}" for slot in range(6)]
        names: list[str] = []
        for slot in range(6):
            slot_base = base + slot * self.memory.party_struct_size
            names.append(self._best_name(slot_base, [10, 8, 0]) or f"Pokemon {slot + 1}")
        return names

    def _read_enemy_names(self) -> list[str]:
        # Trainer Pokemon have no nickname, so the nickname field holds the species name in
        # caps (e.g. "COMBUSKEN") — same field and offset the player party uses.
        if not hasattr(self.instance, "read_u8"):
            return [f"Enemy {slot + 1}" for slot in range(6)]
        names: list[str] = []
        for slot in range(6):
            slot_base = self.memory.enemy_party_base + slot * self.memory.party_struct_size
            names.append(self._best_name(slot_base, [10, 8, 0]) or f"Enemy {slot + 1}")
        return names

    def _read_party_species(self, base: int) -> list[int]:
        if not hasattr(self.instance, "read_block"):
            return [0] * 6
        # The bases are struct-start-minus-2 (see config), so the real 100-byte struct begins
        # at base + slot*size + 2.
        return [
            self._decode_species(base + slot * self.memory.party_struct_size + 2)
            for slot in range(6)
        ]

    def _decode_species(self, struct_addr: int) -> int:
        # Gen-3 stores species in the *encrypted* Growth substructure, not at a fixed offset,
        # so decrypt the 48-byte secure block (key = personality ^ OT id), verify the
        # checksum, and read species from the Growth chunk. Reading offset 0 (the old code)
        # returned the personality, not the species. Reuses the box decoder's primitives
        # (imported lazily to avoid a circular import: gen3_save imports this module).
        try:
            from optimizer.gen3_save import (
                SUBSTRUCT_ORDERS,
                _checksum,
                _decrypt_secure,
                _u16,
                _u32,
            )

            raw = self.instance.read_block(struct_addr, 80)
        except Exception:
            return 0
        if len(raw) < 80:
            return 0
        personality = _u32(raw, 0)
        ot_id = _u32(raw, 4)
        checksum = _u16(raw, 28)
        if personality in (0, 0xFFFFFFFF):
            return 0
        secure = _decrypt_secure(raw[32:80], personality ^ ot_id)
        if _checksum(secure) != checksum:
            return 0
        order = SUBSTRUCT_ORDERS[personality % 24]
        chunks = [secure[index * 12 : (index + 1) * 12] for index in range(4)]
        growth = {kind: chunks[index] for index, kind in enumerate(order)}[0]
        species_id = _u16(growth, 0)
        # Run & Bun has an expanded species table; cap defensively so a bad decode can't
        # inject an absurd id.
        return species_id if 0 < species_id <= 4096 else 0

    def _best_name(self, base: int, offsets: list[int]) -> str:
        for offset in offsets:
            name = self._clean_name(self._read_game_string(base + offset, 10))
            if name:
                return name
        return ""

    def _read_game_string(self, address: int, max_length: int) -> str:
        chars: list[str] = []
        for index in range(max_length):
            value = self.instance.read_u8(address + index)
            if value == 0xFF:
                break
            if value not in _TEXT_CHARS:
                break
            chars.append(_TEXT_CHARS[value])
        return "".join(chars).strip()

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = "".join(char for char in name if char.isalnum() or char in " '-.").strip()
        if len(cleaned) < 2:
            return ""
        return cleaned

    def _read_battler_move_ids(self, battler: int) -> list[int]:
        if not hasattr(self.instance, "rom_path"):
            return [0] * 4
        address = (
            _BATTLE_MONS_ADDRESS
            + battler * _BATTLE_MON_SIZE
            + _BATTLE_MON_MOVES_OFFSET
        )
        ids = [self.instance.read_u16(address + index * 2) for index in range(4)]
        # Validate against the ROM name table. A blank inactive doubles battler is
        # represented by four zeroes; corrupt pointers become unknowns, never a
        # plausible-looking move label.
        return [move_id if self._move_name(move_id) else 0 for move_id in ids]

    def _move_names(self, move_ids: list[int]) -> list[str]:
        return [
            self._move_name(move_id) or f"Unknown move {index + 1}"
            for index, move_id in enumerate(move_ids)
        ]

    def _read_active_party_slots(self, battlers: tuple[int, ...]) -> tuple[int | None, ...]:
        slots: list[int | None] = []
        for battler in battlers:
            value = self.instance.read_u16(_BATTLER_PARTY_INDEXES_ADDRESS + battler * 2)
            slots.append(value if 0 <= value < 6 else None)
        return tuple(slots)

    def _move_names_by_party_slot(
        self,
        battlers: tuple[int, ...],
        party_slots: tuple[int | None, ...],
    ) -> list[list[str]]:
        names_by_slot = [[] for _ in range(6)]
        for battler, party_slot in zip(battlers, party_slots):
            if party_slot is not None:
                names_by_slot[party_slot] = self._move_names(
                    self._read_battler_move_ids(battler)
                )
        return names_by_slot

    def _move_name(self, move_id: int) -> str:
        if move_id <= 0:
            return ""
        base = self._get_move_names_base()
        if base is None:
            return ""
        offset = base + (move_id - 1) * _ROM_MOVE_NAME_LENGTH
        rom = self._get_rom_bytes()
        if offset < 0 or offset + _ROM_MOVE_NAME_LENGTH > len(rom):
            return ""
        return self._decode_game_bytes(rom[offset : offset + _ROM_MOVE_NAME_LENGTH])

    def _get_move_names_base(self) -> int | None:
        if self._move_names_base is not None:
            return self._move_names_base
        rom = self._get_rom_bytes()
        encoded_pound = self._encode_game_string("Pound")
        index = rom.find(encoded_pound)
        self._move_names_base = index if index >= 0 else None
        return self._move_names_base

    def _get_rom_bytes(self) -> bytes:
        if self._rom_bytes is None:
            self._rom_bytes = Path(self.instance.rom_path).read_bytes()
        return self._rom_bytes

    @staticmethod
    def _encode_game_string(value: str) -> bytes:
        encoded = bytes(_TEXT_BYTES[char] for char in value if char in _TEXT_BYTES)
        return encoded + b"\xff"

    @staticmethod
    def _decode_game_bytes(values: bytes) -> str:
        chars: list[str] = []
        for value in values:
            if value == 0xFF:
                break
            if value not in _TEXT_CHARS:
                break
            chars.append(_TEXT_CHARS[value])
        return "".join(chars).strip()

    def _apply_active_player_override(self, player_hp: list[int], player_max_hp: list[int]) -> None:
        if self.memory.player_active_hp is None or self.memory.player_active_max_hp is None:
            return
        active_hp = self.instance.read_u16(self.memory.player_active_hp)
        active_max_hp = self.instance.read_u16(self.memory.player_active_max_hp)
        if active_max_hp <= 0:
            return
        player_hp[0] = active_hp
        player_max_hp[0] = active_max_hp

    def _apply_battler_hp_overrides(
        self,
        hp: list[int],
        max_hp: list[int],
        battlers: tuple[int, ...],
        party_slots: tuple[int | None, ...],
    ) -> None:
        """Overlay live battle-mon HP onto the matching party members.

        The trainer-party buffers lag during doubles replacements, and the old
        player override always wrote battler zero into party slot zero. Both
        errors made damaged/fainted active Pokemon look healthy after a switch.
        """
        for battler, party_slot in zip(battlers, party_slots):
            if party_slot is None or party_slot < 0 or party_slot >= len(hp):
                continue
            address = _BATTLE_MONS_ADDRESS + battler * _BATTLE_MON_SIZE
            live_hp = self.instance.read_u16(address + _BATTLE_MON_HP_OFFSET)
            live_max = self.instance.read_u16(address + _BATTLE_MON_MAX_HP_OFFSET)
            if 0 < live_max <= 999 and 0 <= live_hp <= live_max:
                hp[party_slot] = live_hp
                max_hp[party_slot] = live_max

    @staticmethod
    def _sanitize_party(hp_values: list[int], max_hp_values: list[int]) -> tuple[list[int], list[int]]:
        hp_clean: list[int] = []
        max_clean: list[int] = []
        for hp, max_hp in zip(hp_values, max_hp_values):
            if max_hp <= 0 or max_hp > 999 or hp > max_hp:
                hp_clean.append(0)
                max_clean.append(0)
            else:
                hp_clean.append(hp)
                max_clean.append(max_hp)
        return hp_clean, max_clean

    @staticmethod
    def _all_present_fainted(hp_values: list[int], max_hp_values: list[int]) -> bool:
        present = [hp for hp, max_hp in zip(hp_values, max_hp_values) if max_hp > 0]
        return bool(present) and all(hp <= 0 for hp in present)


__all__ = ["BattleState", "MemoryMap", "StateReader"]
