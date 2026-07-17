"""Create a real, inventory-conserving pre-fight party from party and PC records.

The planner can choose boxed Pokemon and held-item assignments, but a recommendation is not
useful until it exists in the emulated save.  This module performs the same record movement as
PC box swaps: selected boxed records move into the party, displaced party records occupy the
vacated box slots, and held items are redistributed without duplicating or deleting them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

import config
from battle.damage_calc import DamageCalculator
from emulator.mgba_instance import MGBAInstance
from emulator.team_builder import build_party_struct
from optimizer import gen3_save
from optimizer.gen3_save import (
    BOX_MON_SIZE,
    BOX_SLOTS,
    PARTY_MON_SIZE,
    PARTY_SIZE,
    SAVE_BLOCK_1_PARTY_COUNT_OFFSET,
    SAVE_BLOCK_1_PARTY_OFFSET,
    STORAGE_BOXES_OFFSET,
    DecodedPokemon,
    RomNameResolver,
    SaveSnapshot,
    read_save_snapshot,
)


@dataclass(frozen=True)
class TeamSlotRequest:
    source: Literal["party", "box"]
    party_slot: int | None = None
    box: int | None = None
    box_slot: int | None = None
    item_id: int | None = None

    @classmethod
    def party(cls, slot: int, *, item_id: int | None = None) -> "TeamSlotRequest":
        return cls("party", party_slot=slot, item_id=item_id)

    @classmethod
    def box_mon(
        cls, box: int, slot: int, *, item_id: int | None = None
    ) -> "TeamSlotRequest":
        return cls("box", box=box, box_slot=slot, item_id=item_id)


@dataclass(frozen=True)
class PreparationReport:
    party: tuple[DecodedPokemon, ...]
    moved_from_boxes: tuple[tuple[int, int], ...]
    item_changes: tuple[dict[str, object], ...]
    inventory_before: dict[int, int]
    inventory_after: dict[int, int]


SourceKey = tuple[str, int, int]


def _party_key(slot: int) -> SourceKey:
    return ("party", slot, 0)


def _box_key(box: int, slot: int) -> SourceKey:
    return ("box", box, slot)


def rewrite_held_item(raw: bytes, item_id: int) -> bytes:
    """Return a checksum-valid Gen-3 Pokemon record with a different held item."""
    if len(raw) < BOX_MON_SIZE:
        raise ValueError("Pokemon record must contain at least 80 bytes")
    personality = int.from_bytes(raw[0:4], "little")
    ot_id = int.from_bytes(raw[4:8], "little")
    secure = bytearray(gen3_save._decrypt_secure(raw[32:80], personality ^ ot_id))
    order = gen3_save.SUBSTRUCT_ORDERS[personality % 24]
    growth_offset = order[0] * 12
    secure[growth_offset + 2 : growth_offset + 4] = int(item_id).to_bytes(2, "little")

    encrypted = bytearray(secure)
    key = personality ^ ot_id
    for offset in range(0, 48, 4):
        word = int.from_bytes(encrypted[offset : offset + 4], "little") ^ key
        encrypted[offset : offset + 4] = word.to_bytes(4, "little")

    result = bytearray(raw)
    result[28:30] = gen3_save._checksum(bytes(secure)).to_bytes(2, "little")
    result[32:80] = encrypted
    return bytes(result)


def prepare_party(
    instance: MGBAInstance,
    requests: list[TeamSlotRequest],
    *,
    calculator: DamageCalculator | None = None,
    allow_item_donor_boxes: set[int] | None = None,
) -> PreparationReport:
    """Apply a requested party and item loadout to a live overworld state.

    Box pulls are true swaps: no Pokemon is cloned.  Explicit ``item_id`` requests are
    satisfied only from held items already owned by the current party or allowed box donors;
    their multiset is checked before and after the operation.
    """
    if not 1 <= len(requests) <= PARTY_SIZE:
        raise ValueError("prepared party must contain 1..6 Pokemon")
    calculator = calculator or DamageCalculator()
    resolver = RomNameResolver(instance.rom_path)
    snapshot = read_save_snapshot(
        instance, resolver, calculator.species_by_num, calculator.moves_by_num, calculator.moves
    )
    pointers = snapshot.pointers
    if pointers.save_block_1 is None or pointers.pokemon_storage is None:
        raise ValueError("could not locate live party and Pokemon Storage pointers")

    party_count = instance.read_u8(pointers.save_block_1 + SAVE_BLOCK_1_PARTY_COUNT_OFFSET)
    party_count = max(0, min(PARTY_SIZE, party_count))
    party_blob = instance.read_block(
        pointers.save_block_1 + SAVE_BLOCK_1_PARTY_OFFSET, PARTY_SIZE * PARTY_MON_SIZE
    )
    storage_blob = bytearray(
        instance.read_block(
            pointers.pokemon_storage + STORAGE_BOXES_OFFSET,
            gen3_save.STORAGE_BOXES_SIZE,
        )
    )

    raw_records: dict[SourceKey, bytes] = {
        _party_key(slot + 1): party_blob[slot * PARTY_MON_SIZE : (slot + 1) * PARTY_MON_SIZE]
        for slot in range(party_count)
    }
    decoded: dict[SourceKey, DecodedPokemon] = {
        _party_key(mon.slot): mon for mon in snapshot.party
    }
    for mon in snapshot.boxes:
        index = ((mon.box - 1) * BOX_SLOTS + (mon.slot - 1)) * BOX_MON_SIZE
        raw_records[_box_key(mon.box, mon.slot)] = bytes(storage_blob[index : index + BOX_MON_SIZE])
        decoded[_box_key(mon.box, mon.slot)] = mon

    selected: list[SourceKey] = []
    explicit_items: dict[SourceKey, int] = {}
    for request in requests:
        if request.source == "party":
            if request.party_slot is None:
                raise ValueError("party source requires party_slot")
            key = _party_key(request.party_slot)
        else:
            if request.box is None or request.box_slot is None:
                raise ValueError("box source requires box and box_slot")
            if request.box == config.NUZLOCKE_GRAVEYARD_BOX:
                raise ValueError(
                    f"Box {request.box} is the Nuzlocke graveyard; dead Pokemon cannot be prepared"
                )
            key = _box_key(request.box, request.box_slot)
        if key not in raw_records or key not in decoded:
            raise ValueError(f"Pokemon source is empty or undecodable: {key}")
        if key in selected:
            raise ValueError(f"Pokemon source selected twice: {key}")
        selected.append(key)
        if request.item_id is not None:
            explicit_items[key] = request.item_id

    donor_boxes = set(allow_item_donor_boxes or set())
    donor_boxes.discard(config.NUZLOCKE_GRAVEYARD_BOX)
    item_eligible = {
        key
        for key in raw_records
        if key[0] == "party" or key[1] in donor_boxes or key in selected
    }
    current_items = {key: decoded[key].held_item_id for key in item_eligible}
    before = Counter(item for item in current_items.values() if item)
    wanted = Counter(item for item in explicit_items.values() if item)
    missing = wanted - before
    if missing:
        raise ValueError(f"requested held items are not owned in the allowed inventory: {dict(missing)}")

    # Assign fixed target items, then preserve as many untouched owners as possible and
    # distribute the residual multiset among the remaining records.  This supports direct
    # swaps and longer cycles without ever cloning or deleting an item.
    final_items: dict[SourceKey, int] = dict(explicit_items)
    remaining = Counter(before)
    for item in explicit_items.values():
        if item:
            remaining[item] -= 1
    for key, item in current_items.items():
        if key in final_items or not item or remaining[item] <= 0:
            continue
        final_items[key] = item
        remaining[item] -= 1
    empty_keys = [key for key in item_eligible if key not in final_items]
    residual = [item for item, count in remaining.items() for _ in range(max(0, count))]
    if len(residual) > len(empty_keys):
        raise RuntimeError("not enough Pokemon records to preserve held-item inventory")
    for key, item in zip(empty_keys, residual):
        final_items[key] = item
    for key in item_eligible:
        final_items.setdefault(key, 0)

    item_changes: list[dict[str, object]] = []
    for key, new_item in final_items.items():
        old_item = current_items.get(key, 0)
        if old_item == new_item:
            continue
        raw_records[key] = rewrite_held_item(raw_records[key], new_item)
        item_changes.append({"source": key, "old_item_id": old_item, "new_item_id": new_item})

    selected_box_keys = [key for key in selected if key[0] == "box"]
    selected_party_slots = {key[1] for key in selected if key[0] == "party"}
    outgoing = [
        _party_key(slot)
        for slot in range(1, party_count + 1)
        if slot not in selected_party_slots
    ]
    if len(outgoing) > len(selected_box_keys):
        raise ValueError("shrinking a party requires explicit empty-box deposit support")

    # Each selected box slot is vacated. Put displaced party mons there; extra vacated slots
    # stay empty when a box mon fills a previously empty sixth party slot.
    for index, box_key in enumerate(selected_box_keys):
        box, slot = box_key[1], box_key[2]
        offset = ((box - 1) * BOX_SLOTS + (slot - 1)) * BOX_MON_SIZE
        replacement = raw_records[outgoing[index]][:BOX_MON_SIZE] if index < len(outgoing) else bytes(BOX_MON_SIZE)
        storage_blob[offset : offset + BOX_MON_SIZE] = replacement

    # Persist any item changes on box records that were not replaced by an outgoing mon.
    replaced_box_keys = set(selected_box_keys)
    for key, raw in raw_records.items():
        if key[0] != "box" or key in replaced_box_keys:
            continue
        box, slot = key[1], key[2]
        offset = ((box - 1) * BOX_SLOTS + (slot - 1)) * BOX_MON_SIZE
        storage_blob[offset : offset + BOX_MON_SIZE] = raw[:BOX_MON_SIZE]

    party_structs: list[bytes] = []
    for key in selected:
        raw = raw_records[key]
        if key[0] == "party":
            party_structs.append(raw[:PARTY_MON_SIZE])
        else:
            party_structs.append(build_party_struct(raw[:BOX_MON_SIZE], decoded[key], calculator))

    party_data = b"".join(party_structs) + bytes((PARTY_SIZE - len(party_structs)) * PARTY_MON_SIZE)
    instance.write_block(pointers.pokemon_storage + STORAGE_BOXES_OFFSET, bytes(storage_blob))
    instance.write_block(pointers.save_block_1 + SAVE_BLOCK_1_PARTY_OFFSET, party_data)
    instance.write_u8(pointers.save_block_1 + SAVE_BLOCK_1_PARTY_COUNT_OFFSET, len(party_structs))
    live_party = config.MEMORY_OVERRIDES["PLAYER_PARTY_BASE"] + 2
    instance.write_block(live_party, party_data)

    verified = read_save_snapshot(
        instance, resolver, calculator.species_by_num, calculator.moves_by_num, calculator.moves
    )
    # Inventory checking is scoped to the allowed donor universe.  The global after count is
    # still useful as an anti-duplication lower bound; exact allowed-scope equality is checked
    # by reconstructing the changed records above.
    final_allowed = Counter(item for item in final_items.values() if item)
    if final_allowed != before:
        raise RuntimeError(f"held-item inventory changed during preparation: {dict(before)} -> {dict(final_allowed)}")
    if len(verified.party) != len(requests):
        raise RuntimeError("prepared party did not decode back to the requested size")
    return PreparationReport(
        party=verified.party,
        moved_from_boxes=tuple((key[1], key[2]) for key in selected_box_keys),
        item_changes=tuple(item_changes),
        inventory_before=dict(before),
        inventory_after=dict(final_allowed),
    )


__all__ = [
    "PreparationReport",
    "TeamSlotRequest",
    "prepare_party",
    "rewrite_held_item",
]
