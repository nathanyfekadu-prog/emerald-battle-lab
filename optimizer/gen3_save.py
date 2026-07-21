from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config
from emulator.mgba_instance import MGBAInstance
from emulator.state_reader import _TEXT_BYTES, _TEXT_CHARS


BOX_COUNT = 14
BOX_SLOTS = 30
BOX_MON_SIZE = 80
PARTY_MON_SIZE = 100
PARTY_SIZE = 6
STORAGE_BOXES_OFFSET = 4
STORAGE_BOXES_SIZE = BOX_COUNT * BOX_SLOTS * BOX_MON_SIZE
SAVE_BLOCK_1_PARTY_COUNT_OFFSET = 0x234
SAVE_BLOCK_1_PARTY_OFFSET = 0x238
SAVE_BLOCK_2_ENCRYPTION_KEY_OFFSET = 0xAC
IWRAM_START = 0x03005000
IWRAM_LENGTH = 0x3000
EWRAM_START = 0x02000000
EWRAM_CHUNK = 0x20000
# Vanilla US Emerald keeps the three save pointers consecutively in IWRAM.
# Run & Bun relocates data, so discovery remains the primary path; this explicit
# fallback lets an empty early-game PC still expose the player's party.
EMERALD_SAVE_BLOCK_1_PTR = 0x03005D8C
EMERALD_SAVE_BLOCK_2_PTR = 0x03005D90
EMERALD_POKEMON_STORAGE_PTR = 0x03005D94
SAVE_BLOCK_2_PLAYER_NAME_OFFSET = 0
ITEM_STRUCT_SIZE = 44
ITEM_NAME_LENGTH = 14
MOVE_NAME_LENGTH = 13
MAX_PLAUSIBLE_EXPERIENCE = 2_000_000

BAG_POCKETS: tuple[tuple[str, int, int], ...] = (
    ("Items", 0x560, 30),
    ("Key Items", 0x5D8, 30),
    ("Poke Balls", 0x650, 16),
    ("TMs/HMs", 0x690, 64),
    ("Berries", 0x790, 46),
)

SUBSTRUCT_ORDERS: tuple[tuple[int, int, int, int], ...] = (
    (0, 1, 2, 3),
    (0, 1, 3, 2),
    (0, 2, 1, 3),
    (0, 3, 1, 2),
    (0, 2, 3, 1),
    (0, 3, 2, 1),
    (1, 0, 2, 3),
    (1, 0, 3, 2),
    (2, 0, 1, 3),
    (3, 0, 1, 2),
    (2, 0, 3, 1),
    (3, 0, 2, 1),
    (1, 2, 0, 3),
    (1, 3, 0, 2),
    (2, 1, 0, 3),
    (3, 1, 0, 2),
    (2, 3, 0, 1),
    (3, 2, 0, 1),
    (1, 2, 3, 0),
    (1, 3, 2, 0),
    (2, 1, 3, 0),
    (3, 1, 2, 0),
    (2, 3, 1, 0),
    (3, 2, 1, 0),
)

NATURES: tuple[str, ...] = (
    "Hardy",
    "Lonely",
    "Brave",
    "Adamant",
    "Naughty",
    "Bold",
    "Docile",
    "Relaxed",
    "Impish",
    "Lax",
    "Timid",
    "Hasty",
    "Serious",
    "Jolly",
    "Naive",
    "Modest",
    "Mild",
    "Quiet",
    "Bashful",
    "Rash",
    "Calm",
    "Gentle",
    "Sassy",
    "Careful",
    "Quirky",
)


@dataclass(frozen=True)
class DecodedPokemon:
    name: str
    species_id: int
    species: str
    level: int
    box: int = 0
    slot: int = 0
    held_item_id: int = 0
    held_item: str | None = None
    moves: tuple[str, ...] = ()
    move_ids: tuple[int, ...] = ()
    evs: dict[str, int] | None = None
    ivs: dict[str, int] | None = None
    nature: str | None = None
    hp: int | None = None
    max_hp: int | None = None
    experience: int = 0
    source: str = "box"

    @property
    def display_name(self) -> str:
        if self.name and self.name.casefold() != self.species.casefold():
            return f"{self.name} ({self.species})"
        return self.name or self.species


@dataclass(frozen=True)
class BagItem:
    item_id: int
    name: str
    quantity: int
    pocket: str


@dataclass(frozen=True)
class SavePointers:
    save_block_1: int | None
    save_block_2: int | None
    pokemon_storage: int | None
    source: str


@dataclass(frozen=True)
class SaveSnapshot:
    pointers: SavePointers
    party: tuple[DecodedPokemon, ...]
    boxes: tuple[DecodedPokemon, ...]
    bag: tuple[BagItem, ...]


class RomNameResolver:
    def __init__(self, rom_path: str | Path):
        self.rom_path = Path(rom_path).expanduser().resolve()
        self._rom: bytes | None = None
        self._move_base: int | None = None
        self._item_base: int | None = None
        self._species_base: int | None = None

    def species_name(self, species_id: int, species_by_num: dict[int, str]) -> str:
        base = self._get_species_base()
        if base is not None and species_id > 0:
            decoded = _clean_decoded_name(
                self._decode_game_bytes(self._rom_bytes()[base + species_id * 11 : base + (species_id + 1) * 11])
            )
            if decoded:
                return decoded.casefold().replace(" ", "-")
        return species_by_num.get(species_id, f"Species {species_id}")

    def move_name(self, move_id: int, moves_by_num: dict[int, str], moves: dict[str, Any]) -> str:
        if move_id <= 0:
            return ""
        mapped = moves_by_num.get(move_id)
        if mapped:
            move = moves.get(mapped)
            return str(move.get("name") or mapped) if isinstance(move, dict) else mapped
        base = self._get_move_base()
        if base is None:
            return ""
        return _clean_decoded_name(
            self._decode_game_bytes(self._rom_bytes()[base + (move_id - 1) * MOVE_NAME_LENGTH : base + move_id * MOVE_NAME_LENGTH])
        )

    def item_name(self, item_id: int) -> str:
        if item_id <= 0:
            return ""
        override = getattr(config, "RUN_BUN_ITEM_NAME_OVERRIDES", {}).get(item_id)
        if override:
            return override
        base = self._get_item_base()
        if base is None:
            return ""
        name = self._decode_game_bytes(
            self._rom_bytes()[base + item_id * ITEM_STRUCT_SIZE : base + item_id * ITEM_STRUCT_SIZE + ITEM_NAME_LENGTH]
        )
        return _clean_decoded_name(name)

    def _get_move_base(self) -> int | None:
        if self._move_base is not None:
            return self._move_base
        rom = self._rom_bytes()
        for pound in ("Pound", "POUND"):
            hit = rom.find(_encode_game_string(pound))
            while hit >= 0:
                next_name = self._decode_game_bytes(
                    rom[hit + MOVE_NAME_LENGTH : hit + MOVE_NAME_LENGTH * 2]
                )
                if next_name.casefold() == "karate chop":
                    self._move_base = hit
                    return hit
                hit = rom.find(_encode_game_string(pound), hit + 1)
        self._move_base = None
        return self._move_base

    def _get_species_base(self) -> int | None:
        if self._species_base is not None:
            return self._species_base
        rom = self._rom_bytes()
        # Entry zero is "??????????"; Bulbasaur is entry one.
        hit = rom.find(_encode_game_string("BULBASAUR"))
        while hit >= 0:
            base = hit - 11
            if self._decode_game_bytes(rom[base + 22 : base + 33]) == "IVYSAUR":
                self._species_base = base
                return base
            hit = rom.find(_encode_game_string("BULBASAUR"), hit + 1)
        return None

    def _get_item_base(self) -> int | None:
        if self._item_base is not None:
            return self._item_base
        hit = self._rom_bytes().find(_encode_game_string("Master Ball"))
        if hit < ITEM_STRUCT_SIZE:
            self._item_base = None
        else:
            self._item_base = hit - ITEM_STRUCT_SIZE
        return self._item_base

    def _rom_bytes(self) -> bytes:
        if self._rom is None:
            self._rom = self.rom_path.read_bytes()
        return self._rom

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


def read_save_snapshot(
    instance: MGBAInstance,
    resolver: RomNameResolver,
    species_by_num: dict[int, str],
    moves_by_num: dict[int, str],
    moves: dict[str, Any],
) -> SaveSnapshot:
    pointers = discover_save_pointers(instance, resolver, species_by_num, moves_by_num, moves)
    boxes: tuple[DecodedPokemon, ...] = ()
    party: tuple[DecodedPokemon, ...] = ()
    bag: tuple[BagItem, ...] = ()
    if pointers.pokemon_storage is not None:
        boxes = tuple(read_storage_boxes(instance, pointers.pokemon_storage, resolver, species_by_num, moves_by_num, moves))
    if pointers.save_block_1 is not None:
        party = tuple(read_party(instance, pointers.save_block_1, resolver, species_by_num, moves_by_num, moves))
        if pointers.save_block_2 is not None:
            bag = tuple(read_bag(instance, pointers.save_block_1, pointers.save_block_2, resolver))
    return SaveSnapshot(pointers=pointers, party=party, boxes=boxes, bag=bag)


def read_player_name(instance: MGBAInstance, save_block_2: int | None = None) -> str:
    """Read the active Gen III save's player name from live RAM."""
    pointer = save_block_2
    if pointer is None:
        pointer = instance.read_u32(EMERALD_SAVE_BLOCK_2_PTR)
    if pointer is None or not EWRAM_START <= pointer < EWRAM_START + EWRAM_CHUNK * 2:
        return ""
    return _clean_decoded_name(
        _decode_game_text(instance.read_block(pointer + SAVE_BLOCK_2_PLAYER_NAME_OFFSET, 8))
    )


def discover_save_pointers(
    instance: MGBAInstance,
    resolver: RomNameResolver,
    species_by_num: dict[int, str],
    moves_by_num: dict[int, str],
    moves: dict[str, Any],
) -> SavePointers:
    vanilla = SavePointers(
        save_block_1=instance.read_u32(EMERALD_SAVE_BLOCK_1_PTR),
        save_block_2=instance.read_u32(EMERALD_SAVE_BLOCK_2_PTR),
        pokemon_storage=instance.read_u32(EMERALD_POKEMON_STORAGE_PTR),
        source="vanilla-emerald-iwram",
    )
    if (
        vanilla.save_block_1 is not None
        and EWRAM_START <= vanilla.save_block_1 < EWRAM_START + 0x40000
        and vanilla.save_block_2 is not None
        and EWRAM_START <= vanilla.save_block_2 < EWRAM_START + 0x40000
    ):
        party_count = instance.read_u8(vanilla.save_block_1 + SAVE_BLOCK_1_PARTY_COUNT_OFFSET)
        if 0 <= party_count <= PARTY_SIZE:
            return vanilla
    iwram = instance.read_block(IWRAM_START, IWRAM_LENGTH)
    triples: list[SavePointers] = []
    for offset in range(0, len(iwram) - 12, 4):
        values = tuple(_u32(iwram, offset + index * 4) for index in range(3))
        if not all(EWRAM_START <= value < EWRAM_START + 0x40000 for value in values):
            continue
        for storage_index in range(3):
            storage = values[storage_index]
            if _storage_score(instance, storage, resolver, species_by_num, moves_by_num, moves) <= 0:
                continue
            remaining = [value for index, value in enumerate(values) if index != storage_index]
            save_block_1 = max(remaining)
            save_block_2 = min(remaining)
            triples.append(
                SavePointers(
                    save_block_1=save_block_1,
                    save_block_2=save_block_2,
                    pokemon_storage=storage,
                    source=f"iwram:{IWRAM_START + offset:#010x}",
                )
            )
    if triples:
        return max(triples, key=lambda item: _storage_score(instance, item.pokemon_storage or 0, resolver, species_by_num, moves_by_num, moves))

    storage = _discover_storage_by_records(instance, resolver, species_by_num, moves_by_num, moves)
    return SavePointers(None, None, storage, "record-scan" if storage else "not-found")


def read_storage_boxes(
    instance: MGBAInstance,
    storage_ptr: int,
    resolver: RomNameResolver,
    species_by_num: dict[int, str],
    moves_by_num: dict[int, str],
    moves: dict[str, Any],
) -> list[DecodedPokemon]:
    data = instance.read_block(storage_ptr + STORAGE_BOXES_OFFSET, STORAGE_BOXES_SIZE)
    roster: list[DecodedPokemon] = []
    for box in range(BOX_COUNT):
        for slot in range(BOX_SLOTS):
            offset = (box * BOX_SLOTS + slot) * BOX_MON_SIZE
            mon = decode_box_pokemon(
                data[offset : offset + BOX_MON_SIZE],
                resolver,
                species_by_num,
                moves_by_num,
                moves,
                box=box + 1,
                slot=slot + 1,
                source="box",
            )
            if mon is not None:
                roster.append(mon)
    return roster


def read_party(
    instance: MGBAInstance,
    save_block_1_ptr: int,
    resolver: RomNameResolver,
    species_by_num: dict[int, str],
    moves_by_num: dict[int, str],
    moves: dict[str, Any],
) -> list[DecodedPokemon]:
    count = instance.read_u8(save_block_1_ptr + SAVE_BLOCK_1_PARTY_COUNT_OFFSET)
    if count <= 0 or count > PARTY_SIZE:
        count = PARTY_SIZE
    data = instance.read_block(save_block_1_ptr + SAVE_BLOCK_1_PARTY_OFFSET, PARTY_SIZE * PARTY_MON_SIZE)
    party: list[DecodedPokemon] = []
    for slot in range(count):
        offset = slot * PARTY_MON_SIZE
        raw = data[offset : offset + PARTY_MON_SIZE]
        level = raw[84] if len(raw) > 84 else 0
        hp = _u16(raw, 86) if len(raw) >= 88 else None
        max_hp = _u16(raw, 88) if len(raw) >= 90 else None
        mon = decode_box_pokemon(
            raw[:BOX_MON_SIZE],
            resolver,
            species_by_num,
            moves_by_num,
            moves,
            box=0,
            slot=slot + 1,
            source="party",
            stored_level=level if 1 <= level <= 100 else None,
            hp=hp if hp and max_hp and hp <= max_hp <= 999 else None,
            max_hp=max_hp if max_hp and max_hp <= 999 else None,
        )
        if mon is not None:
            party.append(mon)
    return party


def read_bag(
    instance: MGBAInstance,
    save_block_1_ptr: int,
    save_block_2_ptr: int,
    resolver: RomNameResolver,
) -> list[BagItem]:
    key = instance.read_u32(save_block_2_ptr + SAVE_BLOCK_2_ENCRYPTION_KEY_OFFSET) & 0xFFFF
    items: list[BagItem] = []
    for pocket, offset, capacity in BAG_POCKETS:
        data = instance.read_block(save_block_1_ptr + offset, capacity * 4)
        for slot in range(capacity):
            item_id = _u16(data, slot * 4)
            encrypted_quantity = _u16(data, slot * 4 + 2)
            quantity = encrypted_quantity ^ key
            if item_id <= 0 or quantity <= 0 or quantity > 999:
                continue
            name = resolver.item_name(item_id)
            if not name:
                name = f"Item {item_id}"
            items.append(BagItem(item_id=item_id, name=name, quantity=quantity, pocket=pocket))
    return items


def decode_box_pokemon(
    raw: bytes,
    resolver: RomNameResolver,
    species_by_num: dict[int, str],
    moves_by_num: dict[int, str],
    moves: dict[str, Any],
    *,
    box: int,
    slot: int,
    source: str,
    stored_level: int | None = None,
    hp: int | None = None,
    max_hp: int | None = None,
) -> DecodedPokemon | None:
    if len(raw) < BOX_MON_SIZE:
        return None
    personality = _u32(raw, 0)
    ot_id = _u32(raw, 4)
    checksum = _u16(raw, 28)
    flags = raw[19]
    if personality in (0, 0xFFFFFFFF) or ot_id == 0xFFFFFFFF or checksum in (0, 0xFFFF):
        return None
    if not ((flags >> 1) & 1):
        return None
    secure = _decrypt_secure(raw[32:80], personality ^ ot_id)
    if _checksum(secure) != checksum:
        return None
    order = SUBSTRUCT_ORDERS[personality % 24]
    chunks = [secure[index * 12 : (index + 1) * 12] for index in range(4)]
    # The personality permutation is indexed by logical substructure
    # (Growth, Attacks, EVs, Misc) and stores that substructure's physical
    # chunk position. Treating it as physical -> logical happened to decode
    # the identity-like permutations, but silently dropped most real party
    # and box Pokemon and produced nonsense moves/items for the rest.
    substructs = {kind: chunks[physical_index] for kind, physical_index in enumerate(order)}
    growth = substructs[0]
    attacks = substructs[1]
    ev_misc = substructs[2]
    misc = substructs[3]
    species_id = _u16(growth, 0)
    if species_id <= 0 or species_id not in species_by_num:
        return None
    move_ids = tuple(move_id for move_id in (_u16(attacks, offset) for offset in range(0, 8, 2)) if move_id > 0)
    move_names = tuple(
        name
        for move_id in move_ids
        if (name := resolver.move_name(move_id, moves_by_num, moves))
    )
    held_item_id = _u16(growth, 2)
    experience = _u32(growth, 4)
    if experience > MAX_PLAUSIBLE_EXPERIENCE:
        return None
    iv_word = _u32(misc, 4)
    evs = {
        "hp": ev_misc[0],
        "atk": ev_misc[1],
        "def": ev_misc[2],
        "spe": ev_misc[3],
        "spa": ev_misc[4],
        "spd": ev_misc[5],
    }
    ivs = {
        "hp": iv_word & 31,
        "atk": (iv_word >> 5) & 31,
        "def": (iv_word >> 10) & 31,
        "spe": (iv_word >> 15) & 31,
        "spa": (iv_word >> 20) & 31,
        "spd": (iv_word >> 25) & 31,
    }
    level = stored_level or _estimate_level_from_experience(experience)
    if source == "box" and not any(move_id in moves_by_num for move_id in move_ids):
        return None
    return DecodedPokemon(
        name=_decode_game_text(raw[8:18]) or resolver.species_name(species_id, species_by_num),
        species_id=species_id,
        species=resolver.species_name(species_id, species_by_num),
        level=level,
        box=box,
        slot=slot,
        held_item_id=held_item_id,
        held_item=resolver.item_name(held_item_id) or None,
        moves=move_names,
        move_ids=move_ids,
        evs=evs,
        ivs=ivs,
        nature=NATURES[personality % 25],
        hp=hp,
        max_hp=max_hp,
        experience=experience,
        source=source,
    )


def _storage_score(
    instance: MGBAInstance,
    storage_ptr: int,
    resolver: RomNameResolver,
    species_by_num: dict[int, str],
    moves_by_num: dict[int, str],
    moves: dict[str, Any],
) -> int:
    if not (EWRAM_START <= storage_ptr < EWRAM_START + 0x40000 - STORAGE_BOXES_OFFSET):
        return 0
    try:
        sample = instance.read_block(storage_ptr + STORAGE_BOXES_OFFSET, min(STORAGE_BOXES_SIZE, BOX_MON_SIZE * 12))
    except Exception:
        return 0
    return sum(
        1
        for slot in range(len(sample) // BOX_MON_SIZE)
        if decode_box_pokemon(
            sample[slot * BOX_MON_SIZE : (slot + 1) * BOX_MON_SIZE],
            resolver,
            species_by_num,
            moves_by_num,
            moves,
            box=1,
            slot=slot + 1,
            source="box",
        )
        is not None
    )


def _discover_storage_by_records(
    instance: MGBAInstance,
    resolver: RomNameResolver,
    species_by_num: dict[int, str],
    moves_by_num: dict[int, str],
    moves: dict[str, Any],
) -> int | None:
    data = instance.read_block(EWRAM_START, EWRAM_CHUNK) + instance.read_block(EWRAM_START + EWRAM_CHUNK, EWRAM_CHUNK)
    hits: set[int] = set()
    for offset in range(0, len(data) - BOX_MON_SIZE, 4):
        mon = decode_box_pokemon(
            data[offset : offset + BOX_MON_SIZE],
            resolver,
            species_by_num,
            moves_by_num,
            moves,
            box=1,
            slot=1,
            source="box",
        )
        if mon is not None:
            hits.add(EWRAM_START + offset)
    best: tuple[int, int] | None = None
    for address in sorted(hits):
        count = 1
        next_address = address + BOX_MON_SIZE
        while next_address in hits:
            count += 1
            next_address += BOX_MON_SIZE
        if best is None or count > best[0]:
            best = (count, address)
    if best is None:
        return None
    return best[1] - STORAGE_BOXES_OFFSET


def _decrypt_secure(raw: bytes, key: int) -> bytes:
    secure = bytearray(raw[:48])
    for offset in range(0, 48, 4):
        word = _u32(secure, offset) ^ key
        secure[offset : offset + 4] = word.to_bytes(4, "little")
    return bytes(secure)


def _checksum(raw: bytes) -> int:
    return sum(_u16(raw, offset) for offset in range(0, len(raw), 2)) & 0xFFFF


def _estimate_level_from_experience(experience: int) -> int:
    if experience <= 0:
        return 1
    return max(1, min(100, int(round(math.pow(experience, 1 / 3)))))


def _decode_game_text(raw: bytes) -> str:
    chars: list[str] = []
    for value in raw:
        if value == 0xFF:
            break
        if value not in _TEXT_CHARS:
            break
        chars.append(_TEXT_CHARS[value])
    return "".join(chars).strip()


def _clean_decoded_name(value: str) -> str:
    cleaned = "".join(char for char in value if char.isalnum() or char in " '-./").strip()
    if len(cleaned) < 2:
        return ""
    return cleaned


def _encode_game_string(value: str) -> bytes:
    return bytes(_TEXT_BYTES[char] for char in value if char in _TEXT_BYTES) + b"\xff"


def _u16(raw: bytes | bytearray, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "little")


def _u32(raw: bytes | bytearray, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "little")
