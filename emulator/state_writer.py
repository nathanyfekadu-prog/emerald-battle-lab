"""Write into a live mGBA battle so the bruteforce can test a planned configuration.

The read side (StateReader) treats the ROM as the source of truth; this is the matching
write side, built on the new bridge WRITE8/16/32/WRITEBLOCK commands. It is deliberately
conservative: it only writes fields whose offset has been *confirmed by probing the ROM*,
and refuses to guess. Run & Bun uses a custom in-battle battler struct (HP at 0x58, not
vanilla's 0x28), so vanilla offsets do not apply and each field must be located before use.

Current capability
------------------
* ``write_party_hp`` / ``write_party_max_hp`` — HP offsets are already confirmed in
  config (used by StateReader), so these are safe today. Handy for setting up exact test
  positions (e.g. "what if the foe is at 1 HP going into this turn").
* ``write_held_item`` — needs ``PLAYER_ITEM_OFFSET`` in config.MEMORY_OVERRIDES. Until that
  offset is probed it raises with guidance rather than corrupting the struct.

Injecting a *whole* planned team (species/moves/IVs/EVs) is a larger job: the overworld
party (gPlayerParty) stores encrypted, checksummed Gen-3 substructures, so it needs the
encoder in optimizer/gen3_save.py plus the party base address. That is intentionally not
done here yet — see CHANGELOG / the emulator notes.
"""

from __future__ import annotations

import config
from emulator.mgba_instance import MGBAInstance
from emulator.state_reader import MemoryMap


class StateWriter:
    def __init__(self, instance: MGBAInstance, memory_map: MemoryMap | None = None):
        self.instance = instance
        self.memory = memory_map or MemoryMap()
        self._item_offset: int | None = config.MEMORY_OVERRIDES.get("PLAYER_ITEM_OFFSET")

    # -- HP (offsets already confirmed, safe to use) -------------------------------------
    def write_party_hp(self, slot: int, hp: int, *, enemy: bool = False) -> None:
        base = self.memory.enemy_party_base if enemy else self.memory.player_party_base
        offset = self.memory.enemy_hp_offset if enemy else self.memory.player_hp_offset
        self.instance.write_u16(self._slot_addr(base, slot) + offset, max(0, hp))

    def write_party_max_hp(self, slot: int, max_hp: int, *, enemy: bool = False) -> None:
        base = self.memory.enemy_party_base if enemy else self.memory.player_party_base
        offset = self.memory.enemy_max_hp_offset if enemy else self.memory.player_max_hp_offset
        self.instance.write_u16(self._slot_addr(base, slot) + offset, max(0, max_hp))

    # -- Held item (needs a probed offset) -----------------------------------------------
    def write_held_item(self, slot: int, item_id: int, *, enemy: bool = False) -> None:
        """Set the held item of a battler slot to the given Run & Bun item id.

        Requires config.MEMORY_OVERRIDES['PLAYER_ITEM_OFFSET'] to be set to the confirmed
        item offset inside the battler struct. Raises until then so we never silently write
        to the wrong field. Use ``probe_field`` to help locate it.
        """
        if self._item_offset is None:
            raise NotImplementedError(
                "Held-item offset is not configured. Probe it on the ROM (read the battler "
                "struct on a battle whose lead's item is known, find the u16 holding that "
                "item id), then set RUN_BUN_PARTY_ITEM_OFFSET / MEMORY_OVERRIDES["
                "'PLAYER_ITEM_OFFSET'] in config.py."
            )
        base = self.memory.enemy_party_base if enemy else self.memory.player_party_base
        self.instance.write_u16(self._slot_addr(base, slot) + self._item_offset, item_id & 0xFFFF)

    def probe_field(self, slot: int, offset: int, *, enemy: bool = False) -> int:
        """Read the u16 at a candidate offset in a battler slot — used to locate fields
        (e.g. the held item) before wiring them into write_held_item."""
        base = self.memory.enemy_party_base if enemy else self.memory.player_party_base
        return self.instance.read_u16(self._slot_addr(base, slot) + offset)

    def _slot_addr(self, base: int, slot: int) -> int:
        if not 0 <= slot < 6:
            raise ValueError("slot must be 0..5")
        return base + slot * self.memory.party_struct_size


__all__ = ["StateWriter"]
