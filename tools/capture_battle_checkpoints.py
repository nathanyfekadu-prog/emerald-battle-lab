#!/usr/bin/env python3
"""Name Pokémon Emerald mGBA battle save states after the opposing trainer.

Keep this command running while playing in mGBA.  When mGBA writes a new
``.ss*`` state into the watched folder, this tool opens a *separate headless*
emulator instance, recognizes the trainer from the battle state, and renames
the checkpoint.  It never changes the state mGBA has open.

Save at the battle command menu, after the opponent's lead is visible.  That
is still a clean pre-action checkpoint, and it is the first point at which the
trainer can be identified reliably from RAM.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# Direct execution (``python tools/capture_battle_checkpoints.py``) otherwise
# puts only tools/ on sys.path, while the emulator and trainer packages live at
# the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


STATE_SUFFIX_PREFIX = ".ss"

EMERALD_TRAINER_DATA = PROJECT_ROOT / "data" / "emerald_trainers.json"
# The overworld maps use group 0 in the retail Emerald map table.  This covers
# towns, routes, and the standard trainer fights encountered while progressing.
EMERALD_OVERWORLD_MAPS = (
    "PetalburgCity", "SlateportCity", "MauvilleCity", "RustboroCity", "FortreeCity",
    "LilycoveCity", "MossdeepCity", "SootopolisCity", "EverGrandeCity", "LittlerootTown",
    "OldaleTown", "DewfordTown", "LavaridgeTown", "FallarborTown", "VerdanturfTown",
    "PacifidlogTown", "Route101", "Route102", "Route103", "Route104", "Route105",
    "Route106", "Route107", "Route108", "Route109", "Route110", "Route111", "Route112",
    "Route113", "Route114", "Route115", "Route116", "Route117", "Route118", "Route119",
    "Route120", "Route121", "Route122", "Route123", "Route124", "Route125", "Route126",
    "Route127", "Route128", "Route129", "Route130", "Route131", "Route132", "Route133",
    "Route134", "Underwater_Route124", "Underwater_Route126", "Underwater_Route127",
    "Underwater_Route128", "Underwater_Route129", "Underwater_Route105", "Underwater_Route125",
)
# Indoor Gym floors (plus Petalburg Woods) use map groups separate from the
# overworld.  Keeping this table explicit means every standard Gym trainer is
# resolved from the same NPC-position/party logic as route trainers.
EMERALD_SPECIAL_MAPS = {
    (3, 3): "DewfordTown_Gym",
    (4, 1): "LavaridgeTown_Gym_1F",
    (4, 2): "LavaridgeTown_Gym_B1F",
    (8, 1): "PetalburgCity_Gym",
    (10, 0): "MauvilleCity_Gym",
    (11, 3): "RustboroCity_Gym",
    (12, 1): "FortreeCity_Gym",
    (14, 1): "MossdeepCity_Gym",
    (15, 0): "SootopolisCity_Gym_1F",
    (15, 1): "SootopolisCity_Gym_B1F",
    (9, 8): "SlateportCity_OceanicMuseum_2F",
    (14, 0): "MossdeepCity_Gym",
    (14, 9): "MossdeepCity_SpaceCenter_1F",
    (14, 10): "MossdeepCity_SpaceCenter_2F",
    (16, 0): "EverGrandeCity_SidneysRoom",
    (16, 1): "EverGrandeCity_PhoebesRoom",
    (16, 2): "EverGrandeCity_GlaciasRoom",
    (16, 3): "EverGrandeCity_DrakesRoom",
    (16, 4): "EverGrandeCity_ChampionsRoom",
    (24, 0): "MeteorFalls_1F_1R",
    (24, 4): "RusturfTunnel",
    (24, 7): "GraniteCave_1F",
    (24, 8): "GraniteCave_B1F",
    (24, 9): "GraniteCave_B2F",
    (24, 11): "PetalburgWoods",
    (24, 12): "MtChimney",
    (24, 13): "JaggedPass",
    (24, 14): "FieryPath",
    (24, 15): "MtPyre_1F",
    (24, 21): "MtPyre_Exterior",
    (24, 22): "MtPyre_Summit",
    (24, 23): "AquaHideout_1F",
    (24, 24): "AquaHideout_B1F",
    (24, 25): "AquaHideout_B2F",
    (24, 28): "SeafloorCavern_Room1",
    (24, 36): "SeafloorCavern_Room9",
    (24, 43): "VictoryRoad_1F",
    (24, 44): "VictoryRoad_B1F",
    (24, 45): "VictoryRoad_B2F",
    (24, 86): "MagmaHideout_1F",
    (24, 87): "MagmaHideout_2F_1R",
    (24, 88): "MagmaHideout_2F_2R",
    (24, 89): "MagmaHideout_3F_1R",
    (24, 91): "MagmaHideout_4F",
    (24, 94): "MirageTower_1F",
    (24, 95): "MirageTower_2F",
    (28, 0): "Route109_SeashoreHouse",
    (32, 0): "Route119_WeatherInstitute_1F",
    (32, 1): "Route119_WeatherInstitute_2F",
}
EMERALD_MAP_ONLY_TRAINERS = {
    # Wallace is started by the Champion-room script rather than an object event,
    # so the decomp catalog intentionally has no map_event record for him.
    (16, 4): "Champion Wallace",
}
EVENT_TRAINER_CLASSES = {"OBJ_EVENT_GFX_YOUNGSTER": "Youngster"}


def checkpoint_suffix(path: Path) -> str | None:
    """Return an mGBA state suffix (``.ss0``, ``.ss1``, …), if present."""
    suffix = path.suffix.lower()
    return suffix if suffix.startswith(STATE_SUFFIX_PREFIX) and suffix[3:].isdigit() else None


def safe_stem(name: str) -> str:
    """Make a readable filename stem without allowing path separators."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", name.strip()).strip("-").lower()
    return cleaned or "unrecognized-trainer"


def _normalized_map_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold().removeprefix("map"))


def _trainer_from_map_event(
    map_id: tuple[int, int] | None,
    x: int,
    y: int,
    enemy_names: tuple[str, ...],
    enemy_max_hp: tuple[int, ...],
) -> str | None:
    """Resolve a trainer from the actual map NPC that initiated this battle."""
    if map_id is None:
        return None
    if map_id[0] == 0 and 0 <= map_id[1] < len(EMERALD_OVERWORLD_MAPS):
        map_name = EMERALD_OVERWORLD_MAPS[map_id[1]]
    else:
        map_name = EMERALD_SPECIAL_MAPS.get(map_id)
    if map_name is None:
        return None
    game_map = _normalized_map_name(map_name)
    data = json.loads(EMERALD_TRAINER_DATA.read_text(encoding="utf-8"))
    candidates: list[tuple[int, dict[str, object]]] = []
    for trainer in data.get("trainers", []):
        event = trainer.get("map_event") or {}
        event_map = _normalized_map_name(str(event.get("map_id") or event.get("map_name") or ""))
        if event_map != game_map:
            continue
        event_x, event_y = int(event.get("x", -999)), int(event.get("y", -999))
        sight = max(1, int(event.get("sight", 0)))
        # The player is normally one to `sight` tiles directly in front of the
        # NPC at battle start.  Restrict the candidate to that line, not merely
        # the map, so same-route trainers do not collide.
        in_sight_line = (event_x == x and 0 < abs(event_y - y) <= sight) or (
            event_y == y and 0 < abs(event_x - x) <= sight
        )
        party_names = tuple(str(mon.get("species") or "").casefold() for mon in trainer.get("party", []))
        observed_names = tuple(name.casefold() for name in enemy_names)
        exact_party = party_names == observed_names
        # Story cutscenes can move the player before the first battle frame,
        # while ordinary NPC encounters preserve the sight-line relationship.
        # Requiring an exact party match or a sight-line match avoids guessing.
        score = 100 * int(exact_party) + 25 * int(in_sight_line)
        if score:
            candidates.append((score, trainer))
    candidates.sort(key=lambda row: row[0], reverse=True)
    if not candidates or (len(candidates) > 1 and candidates[0][0] == candidates[1][0]):
        return None
    trainer = candidates[0][1]
    event = trainer.get("map_event") or {}
    event_name = str(event.get("trainer_name") or "").strip().title()
    trainer_class = EVENT_TRAINER_CLASSES.get(str(event.get("graphics_id") or ""))
    return f"{trainer_class} {event_name}" if trainer_class and event_name else str(trainer["trainer_name"])


def _trainer_from_unique_party(enemy_names: tuple[str, ...]) -> str | None:
    """Resolve a trainer whose complete party is unique in Emerald's data.

    This is the safe fallback for indoor/story maps that have not been added to
    the map-number table yet.  A party is only accepted when precisely one
    trainer in the game has that exact ordered party, so wild encounters and
    duplicate trainer parties remain unnamed instead of guessed.
    """
    if not enemy_names:
        return None
    observed_names = tuple(name.casefold() for name in enemy_names)
    matches: list[dict[str, object]] = []
    for trainer in json.loads(EMERALD_TRAINER_DATA.read_text(encoding="utf-8")).get("trainers", []):
        party_names = tuple(
            str(mon.get("species") or "").casefold() for mon in trainer.get("party", [])
        )
        if party_names == observed_names:
            matches.append(trainer)
    if len(matches) != 1:
        return None
    return str(matches[0]["trainer_name"])


def destination_for(source: Path, trainer_name: str) -> Path:
    """Choose a non-destructive destination beside *source*."""
    suffix = checkpoint_suffix(source)
    if suffix is None:
        raise ValueError(f"Not an mGBA save state: {source}")
    base = source.with_name(f"{safe_stem(trainer_name)}{suffix}")
    if base == source or not base.exists():
        return base
    for copy_number in range(2, 10_000):
        candidate = source.with_name(f"{safe_stem(trainer_name)}-{copy_number}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a free checkpoint filename for {trainer_name!r}")


def wait_until_complete(path: Path, timeout_seconds: float = 12.0) -> bool:
    """Wait for mGBA to finish writing the state, without reading a partial file."""
    deadline = time.monotonic() + timeout_seconds
    previous_size = -1
    stable_reads = 0
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size > 0 and size == previous_size:
            stable_reads += 1
            if stable_reads >= 2:
                return True
        else:
            stable_reads = 0
        previous_size = size
        time.sleep(0.25)
    return False


def recognize_trainer(rom_path: Path, state_path: Path, game_mode: str) -> dict[str, object]:
    """Read one checkpoint through the existing mGBA bridge and return its label."""
    # These imports intentionally happen only after CLI parsing so --help works
    # even on a machine that has not compiled the native mGBA bridge yet.
    from battle.damage_calc import DamageCalculator
    from emulator.game_state import WholeGameStateReader
    from emulator.mgba_instance import MGBAInstance
    from emulator.state_reader import StateReader

    with MGBAInstance(str(rom_path), str(state_path), instance_id=91) as instance:
        battle = StateReader(instance).read()
        calculator = DamageCalculator(game_mode=game_mode)
        snapshot = WholeGameStateReader(
            instance, calculator=calculator
        ).read()
        live_battle_flag = instance.read_u8(0x030026F9)
    trainer_name = snapshot.trainer_name
    trainer_confidence = snapshot.trainer_confidence
    trainer_location = snapshot.trainer_location
    enemy_names = tuple(
        (
            calculator.species_by_num.get(species_id, name)
            if name.startswith("Enemy ") and species_id
            else name
        )
        for name, species_id, max_hp in zip(
            getattr(battle, "enemy_names", ()),
            getattr(battle, "enemy_species", ()),
            battle.enemy_max_hp,
        )
        if max_hp > 0
    )
    if trainer_name is None:
        trainer_name = _trainer_from_map_event(
            snapshot.map_id,
            snapshot.x,
            snapshot.y,
            enemy_names,
            tuple(max_hp for max_hp in battle.enemy_max_hp if max_hp > 0),
        )
        if trainer_name:
            trainer_confidence = "exact map-event match"
            trainer_location = f"Map {snapshot.map_group}/{snapshot.map_number}"
    if trainer_name is None:
        trainer_name = _trainer_from_unique_party(enemy_names)
        if trainer_name:
            trainer_confidence = "unique opponent-party match"
            trainer_location = f"Map {snapshot.map_group}/{snapshot.map_number}"
    if trainer_name is None and snapshot.map_id in EMERALD_MAP_ONLY_TRAINERS:
        trainer_name = EMERALD_MAP_ONLY_TRAINERS[snapshot.map_id]
        trainer_confidence = "exclusive story-room match"
        trainer_location = f"Map {snapshot.map_group}/{snapshot.map_number}"
    return {
        "trainer_name": trainer_name,
        "trainer_location": trainer_location,
        "trainer_confidence": trainer_confidence,
        "mode": str(snapshot.mode),
        "is_known_trainer": trainer_name is not None,
        "is_wild_battle": snapshot.is_wild_battle,
        "live_battle": bool(live_battle_flag),
    }


def append_manifest(manifest: Path, source: Path, destination: Path, recognition: dict[str, object]) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "checkpoint": str(destination),
        **recognition,
    }
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def process_state(rom_path: Path, state_path: Path, manifest: Path, game_mode: str) -> Path | None:
    if not wait_until_complete(state_path):
        print(f"Skipped incomplete state: {state_path.name}", file=sys.stderr)
        return None
    recognition = recognize_trainer(rom_path, state_path, game_mode)
    if not recognition.get("live_battle"):
        append_manifest(manifest, state_path, state_path, recognition)
        print(f"Left unchanged (not a live battle): {state_path.name}")
        return None
    trainer_name = recognition.get("trainer_name")
    if not isinstance(trainer_name, str) or not trainer_name.strip():
        append_manifest(manifest, state_path, state_path, recognition)
        print(f"Left unchanged (trainer not recognized): {state_path.name}")
        return None
    # Make the purpose obvious when a folder contains both route checkpoints
    # and battle checkpoints: `fight-tibo.ss0`, not merely `tibo.ss0`.
    destination = destination_for(state_path, f"fight-{trainer_name}")
    if destination != state_path:
        shutil.move(state_path, destination)
    append_manifest(manifest, state_path, destination, recognition)
    location = recognition.get("trainer_location")
    detail = f" — {location}" if isinstance(location, str) and location else ""
    print(f"Saved {destination.name}  ({trainer_name}{detail})")
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch Pokémon Emerald mGBA save states and name battle checkpoints."
    )
    parser.add_argument("--rom", required=True, type=Path, help="Local Run & Bun .gba file")
    parser.add_argument(
        "--watch", required=True, type=Path,
        help="Folder where mGBA writes save states (use a dedicated folder)",
    )
    parser.add_argument(
        "--once", type=Path, metavar="STATE",
        help="Recognize and name one existing state, then exit",
    )
    parser.add_argument("--interval", type=float, default=0.5, help="Watch interval in seconds (default: 0.5)")
    parser.add_argument(
        "--game", default="pokemon-emerald", choices=("pokemon-emerald",),
        help="Game data used for trainer recognition (default: pokemon-emerald)",
    )
    parser.add_argument(
        "--copy", action="store_true",
        help="With --once, copy the state into --watch before naming it; preserves the original",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rom_path = args.rom.expanduser().resolve()
    watch = args.watch.expanduser().resolve()
    if not rom_path.is_file():
        raise SystemExit(f"ROM not found: {rom_path}")
    watch.mkdir(parents=True, exist_ok=True)
    manifest = watch / "checkpoint-manifest.jsonl"

    if args.once:
        state = args.once.expanduser().resolve()
        if not state.is_file():
            raise SystemExit(f"State not found: {state}")
        if args.copy:
            copied_state = watch / state.name
            if copied_state.exists():
                raise SystemExit(f"Refusing to overwrite existing copy: {copied_state}")
            shutil.copy2(state, copied_state)
            state = copied_state
        process_state(rom_path, state, manifest, args.game)
        return 0

    known = {path.resolve() for path in watch.iterdir() if path.is_file()}
    print(f"Watching {watch}")
    print("In mGBA, save at the battle command menu using your normal Save State hotkey.")
    try:
        while True:
            current = {path.resolve() for path in watch.iterdir() if path.is_file()}
            new_states = sorted(
                (path for path in current - known if checkpoint_suffix(path) is not None),
                key=lambda path: path.stat().st_mtime,
            )
            for state in new_states:
                try:
                    destination = process_state(rom_path, state, manifest, args.game)
                    if destination is not None:
                        known.add(destination.resolve())
                except Exception as error:
                    # A bad or unsupported state should not stop a long capture
                    # session. The original file remains intact for inspection.
                    print(f"Could not process {state.name}: {error}", file=sys.stderr)
            known.update(current)
            time.sleep(max(0.1, args.interval))
    except KeyboardInterrupt:
        print("\nStopped watching.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
