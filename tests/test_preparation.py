from __future__ import annotations

from emulator.mgba_instance import MGBAInstance
from emulator.preparation import rewrite_held_item
from optimizer.gen3_save import SUBSTRUCT_ORDERS, _checksum, decode_box_pokemon
from optimizer.box_optimizer import BoxScanResult, _apply_requests_for_team
from optimizer.gen3_save import DecodedPokemon, RomNameResolver, SavePointers


def test_run_bun_item_520_is_the_observed_oran_berry(tmp_path):
    resolver = RomNameResolver(tmp_path / "unused.gba")

    assert resolver.item_name(520) == "Oran Berry"


class _Resolver:
    def species_name(self, species_id, _species_by_num):
        return {1: "Bulbasaur"}[species_id]

    def move_name(self, move_id, moves_by_num, _moves):
        return moves_by_num.get(move_id, "")

    def item_name(self, item_id):
        return {520: "Sitrus Berry", 537: "Oran Berry"}.get(item_id, "")


def _record(personality: int, item_id: int = 520) -> bytes:
    logical = [bytearray(12) for _ in range(4)]
    logical[0][0:2] = (1).to_bytes(2, "little")
    logical[0][2:4] = item_id.to_bytes(2, "little")
    logical[0][4:8] = (32768).to_bytes(4, "little")
    for index, move_id in enumerate((1, 2, 3, 4)):
        logical[1][index * 2 : index * 2 + 2] = move_id.to_bytes(2, "little")
    logical[2][0:6] = bytes((1, 2, 3, 4, 5, 6))
    logical[3][4:8] = (31 | (30 << 5) | (29 << 10)).to_bytes(4, "little")

    physical = [bytearray(12) for _ in range(4)]
    for logical_index, physical_index in enumerate(SUBSTRUCT_ORDERS[personality % 24]):
        physical[physical_index] = logical[logical_index]
    secure = b"".join(physical)
    ot_id = 0x12345678
    key = personality ^ ot_id
    encrypted = bytearray(secure)
    for offset in range(0, 48, 4):
        word = int.from_bytes(encrypted[offset : offset + 4], "little") ^ key
        encrypted[offset : offset + 4] = word.to_bytes(4, "little")

    raw = bytearray(80)
    raw[0:4] = personality.to_bytes(4, "little")
    raw[4:8] = ot_id.to_bytes(4, "little")
    raw[8:18] = b"\xff" * 10
    raw[19] = 2
    raw[28:30] = _checksum(secure).to_bytes(2, "little")
    raw[32:80] = encrypted
    return bytes(raw)


def _decode(raw: bytes):
    return decode_box_pokemon(
        raw,
        _Resolver(),
        {1: "Bulbasaur"},
        {1: "Tackle", 2: "Growl", 3: "Vine Whip", 4: "Poison Powder"},
        {},
        box=1,
        slot=1,
        source="box",
    )


def test_all_gen3_substructure_permutations_decode_and_rewrite_items():
    for personality in range(24, 48):
        original = _decode(_record(personality))
        assert original is not None
        assert original.species == "Bulbasaur"
        assert original.held_item_id == 520
        assert original.move_ids == (1, 2, 3, 4)

        rewritten = _decode(rewrite_held_item(_record(personality), 537))
        assert rewritten is not None
        assert rewritten.held_item_id == 537
        assert rewritten.move_ids == original.move_ids
        assert rewritten.ivs == original.ivs


def test_large_bridge_writes_are_split_below_protocol_line_limit():
    instance = object.__new__(MGBAInstance)
    commands: list[str] = []
    instance._request = lambda command: commands.append(command) or "OK"  # type: ignore[attr-defined]

    instance.write_block(0x02000000, bytes(range(256)) * 40)

    assert len(commands) == 5
    assert all(len(command) < 4096 for command in commands)
    assert commands[0].startswith("WRITEBLOCK 33554432 ")
    assert commands[-1].startswith(f"WRITEBLOCK {0x02000000 + 8192} ")


def test_apply_plan_cannot_borrow_an_item_from_an_unselected_box():
    party = DecodedPokemon("Lead", 1, "bulbasaur", 10, slot=1, held_item_id=520, held_item="Sitrus Berry")
    selected = DecodedPokemon("Candidate", 1, "bulbasaur", 10, box=13, slot=1)
    graveyard_donor = DecodedPokemon(
        "Dead donor", 1, "bulbasaur", 10, box=14, slot=1,
        held_item_id=520, held_item="Sitrus Berry",
    )
    scan = BoxScanResult(
        [selected, graveyard_donor], [party], [], SavePointers(1, 2, 3, "test")
    )
    team = [
        {"slot": 1, "source": "party", "party_slot": 1, "item": "Sitrus Berry"},
        {"slot": 2, "source": "box", "box": 13, "box_slot": 1, "item": "Sitrus Berry"},
    ]

    requests, error = _apply_requests_for_team(team, scan)

    assert requests == []
    assert error == "The item plan asks for more held-item copies than the save owns."


def test_apply_plan_rejects_party_slots_missing_from_pre_fight_save():
    party = DecodedPokemon("Lead", 1, "bulbasaur", 10, slot=1)
    scan = BoxScanResult([], [party], [], SavePointers(1, 2, 3, "test"))

    requests, error = _apply_requests_for_team(
        [{"slot": 2, "source": "party", "party_slot": 2, "item": None}], scan
    )

    assert requests == []
    assert error == "Party slot 2 is not present in the selected pre-fight save."


def test_apply_plan_rejects_box_14_graveyard_member():
    dead = DecodedPokemon("Dead", 1, "bulbasaur", 10, box=14, slot=2)
    scan = BoxScanResult([dead], [], [], SavePointers(1, 2, 3, "test"))

    requests, error = _apply_requests_for_team(
        [{"slot": 1, "source": "box", "box": 14, "box_slot": 2, "item": None}], scan
    )

    assert requests == []
    assert error == "Box 14 is the Nuzlocke graveyard."
