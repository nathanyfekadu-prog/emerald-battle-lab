"""Build a party in RAM from chosen box Pokemon (the memory-copy path to "make that team").

The line planner decides which boxed Pokemon to field; this assembles them directly in memory
instead of navigating the PC UI. A boxed mon is stored as the same 80-byte encrypted Gen-3
substructure a party slot uses for its first 80 bytes — so we copy those bytes verbatim and
fill in only the 20 party-only bytes (level, current/max HP, and the five battle stats), which
the game would otherwise compute on withdraw. Stats use the calculator's own Gen-3 formula so
they match what the cartridge produces.

This is deterministic (no fragile menu navigation) and, because it writes raw party structs,
it also lets a planned team be injected straight into the battle state the brute-forcer runs.
"""

from __future__ import annotations

from typing import Any

import config
from battle.damage_calc import DamageCalculator
from emulator.mgba_instance import MGBAInstance
from optimizer.gen3_save import DecodedPokemon, STORAGE_BOXES_OFFSET, BOX_SLOTS, BOX_MON_SIZE

PARTY_MON_SIZE = 100
PARTY_SIZE = 6

# Party-only field offsets inside the 100-byte struct (after the 80-byte box portion).
_OFF_STATUS = 0x50   # u32
_OFF_LEVEL = 0x54    # u8
_OFF_HP = 0x56       # u16 current
_OFF_MAX_HP = 0x58   # u16
_OFF_ATK = 0x5A      # u16, then def/spe/spa/spd every 2 bytes (Gen-3 stat order)

# The party struct begins 2 bytes after the configured base (see config / state_reader).
_STRUCT_FROM_BASE = 2


def _le16(value: int) -> bytes:
    return int(max(0, min(0xFFFF, value))).to_bytes(2, "little")


def build_party_struct(box_bytes: bytes, mon: DecodedPokemon, calculator: DamageCalculator) -> bytes:
    """Make a 100-byte party struct from a mon's 80-byte box struct + its decoded data."""
    species_name = calculator.species_by_num.get(mon.species_id, mon.species)
    species = calculator._species_data(species_name)
    if species is None:
        raise ValueError(f"no base stats for species {mon.species_id} ({species_name})")
    level = max(1, min(100, mon.level or 1))

    def stat(name: str) -> int:
        return calculator._stat(species, name, level, mon.nature, mon.evs, mon.ivs)

    buf = bytearray(box_bytes[:BOX_MON_SIZE].ljust(PARTY_MON_SIZE, b"\x00"))
    buf[_OFF_STATUS : _OFF_STATUS + 4] = b"\x00\x00\x00\x00"
    buf[_OFF_LEVEL] = level
    max_hp = stat("hp")
    buf[_OFF_HP : _OFF_HP + 2] = _le16(max_hp)        # enter the fight at full HP
    buf[_OFF_MAX_HP : _OFF_MAX_HP + 2] = _le16(max_hp)
    for index, name in enumerate(("atk", "def", "spe", "spa", "spd")):
        off = _OFF_ATK + index * 2
        buf[off : off + 2] = _le16(stat(name))
    return bytes(buf)


def read_box_struct(instance: MGBAInstance, storage_ptr: int, box: int, slot: int) -> bytes:
    """Read the raw 80-byte box struct at (box, slot), both 1-based."""
    index = (box - 1) * BOX_SLOTS + (slot - 1)
    return instance.read_block(storage_ptr + STORAGE_BOXES_OFFSET + index * BOX_MON_SIZE, BOX_MON_SIZE)


def inject_party(
    instance: MGBAInstance,
    party_structs: list[bytes],
    *,
    party_base: int | None = None,
    count_addr: int | None = None,
) -> None:
    """Write up to 6 party structs into gPlayerParty, zeroing any remaining slots.

    `party_base` is the configured base (struct start minus 2); defaults to the player party
    base in config. If `count_addr` is given, the party count byte there is set too."""
    base = (party_base if party_base is not None else config.MEMORY_OVERRIDES["PLAYER_PARTY_BASE"]) + _STRUCT_FROM_BASE
    if len(party_structs) > PARTY_SIZE:
        raise ValueError("a party holds at most 6 Pokemon")
    for slot in range(PARTY_SIZE):
        addr = base + slot * PARTY_MON_SIZE
        data = party_structs[slot] if slot < len(party_structs) else bytes(PARTY_MON_SIZE)
        instance.write_block(addr, data)
    if count_addr is not None:
        instance.write_u8(count_addr, len(party_structs))


def assemble_team_in_memory(
    instance: MGBAInstance,
    storage_ptr: int,
    pulls: list[DecodedPokemon],
    calculator: DamageCalculator,
    *,
    party_base: int | None = None,
    count_addr: int | None = None,
) -> None:
    """Build a full party from chosen boxed Pokemon and write it into memory."""
    structs = [
        build_party_struct(read_box_struct(instance, storage_ptr, mon.box, mon.slot), mon, calculator)
        for mon in pulls
    ]
    inject_party(instance, structs, party_base=party_base, count_addr=count_addr)


__all__ = ["build_party_struct", "read_box_struct", "inject_party", "assemble_team_in_memory"]
