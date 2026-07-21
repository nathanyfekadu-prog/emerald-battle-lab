from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def extract_events(decomp: Path) -> list[dict[str, Any]]:
    trainer_source = (decomp / "src/data/trainers.h").read_text(encoding="utf-8")
    names: dict[str, str] = {}
    layouts_raw = json.loads((decomp / "data/layouts/layouts.json").read_text(encoding="utf-8"))
    layouts = {item["id"]: item for item in layouts_raw.get("layouts", layouts_raw if isinstance(layouts_raw, list) else [])}
    block_pattern = re.compile(
        r"\[(TRAINER_[A-Z0-9_]+)\]\s*=\s*\{(.*?)(?=\n\s*\[TRAINER_|\Z)", re.S
    )
    for constant, block in block_pattern.findall(trainer_source):
        match = re.search(r'\.trainerName\s*=\s*_\("([^"]*)"\)', block)
        if match:
            names[constant] = match.group(1)

    events: list[dict[str, Any]] = []
    for map_json in sorted((decomp / "data/maps").glob("*/map.json")):
        map_data = json.loads(map_json.read_text(encoding="utf-8"))
        layout = layouts.get(map_data.get("layout"), {})
        scripts_path = map_json.with_name("scripts.inc")
        if not scripts_path.is_file():
            continue
        scripts = scripts_path.read_text(encoding="utf-8")
        script_to_constant: dict[str, str] = {}
        labels = list(re.finditer(r"^([A-Za-z0-9_]+)::\s*$", scripts, re.M))
        for index, label in enumerate(labels):
            end = labels[index + 1].start() if index + 1 < len(labels) else len(scripts)
            block = scripts[label.end():end]
            trainer = re.search(r"\btrainerbattle_[a-z_]+\s+(TRAINER_[A-Z0-9_]+)", block)
            if trainer:
                script_to_constant[label.group(1)] = trainer.group(1)
        object_events = map_data.get("object_events", [])
        captured_constants: set[str] = set()
        for event in object_events:
            constant = script_to_constant.get(str(event.get("script") or ""))
            if not constant:
                continue
            captured_constants.add(constant)
            events.append({
                "map_id": map_data.get("id") or map_data.get("name") or map_json.parent.name,
                "map_name": map_data.get("name") or map_json.parent.name,
                "x": int(event.get("x") or 0),
                "y": int(event.get("y") or 0),
                "elevation": int(event.get("elevation") or 0),
                "sight": int(event.get("trainer_sight_or_berry_tree_id") or 0),
                "map_width": int(layout.get("width") or 1),
                "map_height": int(layout.get("height") or 1),
                "script": event.get("script"),
                "graphics_id": event.get("graphics_id"),
                "trainer_constant": constant,
                "trainer_name": names.get(constant, ""),
            })
        # A handful of story battles (including the Petalburg Woods Aqua
        # grunt) start from a cutscene rather than the NPC object's own script.
        # Associate those trainer constants with the most descriptive object on
        # the same map so they still receive their real on-map position/sprite.
        scripted_constants = set(re.findall(r"\btrainerbattle_[a-z_]+\s+(TRAINER_[A-Z0-9_]+)", scripts))
        for constant in sorted(scripted_constants - captured_constants):
            constant_tokens = {
                token for token in constant.removeprefix("TRAINER_").split("_")
                if token and token not in {"1", "2", "3", "4", "5", "6"}
            }
            ranked_objects: list[tuple[int, dict[str, Any]]] = []
            for event in object_events:
                blob = "_".join(str(event.get(key) or "") for key in (
                    "local_id", "graphics_id", "script", "flag"
                )).upper()
                score = sum(4 for token in constant_tokens if token in blob)
                if "GRUNT" in constant_tokens and any(team in constant_tokens and team in blob for team in ("AQUA", "MAGMA")):
                    score += 12
                if score:
                    ranked_objects.append((score, event))
            if not ranked_objects:
                continue
            ranked_objects.sort(key=lambda row: -row[0])
            score, event = ranked_objects[0]
            if score < 8:
                continue
            events.append({
                "map_id": map_data.get("id") or map_data.get("name") or map_json.parent.name,
                "map_name": map_data.get("name") or map_json.parent.name,
                "x": int(event.get("x") or 0),
                "y": int(event.get("y") or 0),
                "elevation": int(event.get("elevation") or 0),
                "sight": int(event.get("trainer_sight_or_berry_tree_id") or 0),
                "map_width": int(layout.get("width") or 1),
                "map_height": int(layout.get("height") or 1),
                "script": f"story battle: {constant}",
                "graphics_id": event.get("graphics_id"),
                "trainer_constant": constant,
                "trainer_name": names.get(constant, ""),
            })
    return events


def attach_events(dataset: dict[str, Any], events: list[dict[str, Any]]) -> int:
    attached = 0
    for trainer in dataset.get("trainers", []):
        trainer_name = _norm(str(trainer.get("trainer_name") or ""))
        route = _norm(str(trainer.get("route") or ""))
        mapped_location = _norm(str(trainer.get("map_location") or ""))
        name_matches: list[dict[str, Any]] = []
        for event in events:
            event_name = _norm(str(event.get("trainer_name") or ""))
            if len(event_name) < 3 or event_name not in trainer_name:
                continue
            name_matches.append(event)
        # Generic names such as "Grunt" occur dozens of times. Once a sheet
        # location has an exact map match, never allow a same-name NPC from a
        # different route to outrank it.
        location_matches = [
            event for event in name_matches
            if mapped_location and mapped_location in _norm(str(event.get("map_name") or event.get("map_id") or ""))
        ]
        candidates = location_matches or name_matches
        ranked: list[tuple[int, dict[str, Any]]] = []
        for event in candidates:
            event_name = _norm(str(event.get("trainer_name") or ""))
            map_name = _norm(str(event.get("map_name") or event.get("map_id") or ""))
            score = 10 + min(10, len(event_name))
            if mapped_location and mapped_location in map_name:
                score += 25
            if route and route in map_name:
                score += 30
            if trainer_name.endswith(event_name):
                score += 8
            ranked.append((score, event))
        if ranked:
            ranked.sort(key=lambda value: (-value[0], str(value[1].get("map_id")), value[1].get("x", 0)))
            trainer["map_event"] = ranked[0][1]
            attached += 1
    dataset.setdefault("source", {})["map_source"] = "https://github.com/pret/pokeemerald"
    dataset.setdefault("stats", {})["exact_map_events"] = attached
    return attached


def main() -> None:
    parser = argparse.ArgumentParser(description="Attach official pokeemerald map event coordinates")
    parser.add_argument("--decomp", required=True, type=Path)
    parser.add_argument("--dataset", default="data/emerald_trainers.json", type=Path)
    args = parser.parse_args()
    data = json.loads(args.dataset.read_text(encoding="utf-8"))
    events = extract_events(args.decomp)
    attached = attach_events(data, events)
    args.dataset.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Attached {attached}/{len(data.get('trainers', []))} trainers to {len(events)} official map events")


if __name__ == "__main__":
    main()
