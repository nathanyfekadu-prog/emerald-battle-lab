#!/usr/bin/env python3
"""Import the public Emerald Swampert trainer sheet into app-ready JSON.

The CSV supplies values; the HTML grid supplies cell formatting so bold trainer
blocks remain marked as story-required.  No Google credentials are required.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import urllib.request
from pathlib import Path


SPREADSHEET_ID = "1frqW2CeHop4o0NP6Ja_TAAPPkGIrvxkeQJBfyxFggyk"
SHEET_GID = "1064630895"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={SHEET_GID}"
HTML_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/htmlview/sheet?headers=true&gid={SHEET_GID}"

# Coordinates are percentages on the app's schematic Hoenn atlas. They follow
# the in-game geography closely enough to orient a route without redistributing
# copyrighted map artwork. Aliases keep interiors attached to their overworld pin.
MAP_POINTS = {
    "Route 102": (14, 69), "Route 103": (18, 82), "Route 104": (9, 59),
    "Petalburg Woods": (9, 46), "Rustboro City": (11, 33), "Route 116": (23, 34),
    "Rusturf Tunnel": (31, 34), "Route 115": (12, 20), "Meteor Falls": (20, 14),
    "Route 114": (29, 17), "Route 113": (43, 14), "Route 112": (46, 28),
    "Mt. Chimney": (44, 22), "Jagged Pass": (43, 34), "Lavaridge Town": (39, 40),
    "Route 111": (51, 34), "Route 117": (36, 48), "Mauville City": (51, 48),
    "Route 110": (52, 61), "Slateport City": (51, 75), "Route 109": (46, 82),
    "Route 106": (25, 76), "Dewford Town": (20, 83), "Route 105": (17, 70),
    "Route 107": (30, 83), "Route 108": (39, 82), "Route 118": (62, 48),
    "Route 119": (68, 37), "Route 120": (78, 32), "Fortree City": (76, 22),
    "Route 121": (86, 34), "Route 122": (80, 44), "Mt. Pyre": (82, 48),
    "Route 123": (73, 52), "Lilycove City": (91, 42), "Magma Hideout": (47, 24),
    "Aqua Hideout": (94, 48), "Route 124": (88, 58), "Mossdeep City": (94, 62),
    "Route 125": (94, 53), "Route 126": (82, 67), "Sootopolis City": (80, 68),
    "Route 127": (89, 70), "Route 128": (88, 78), "Seafloor Cavern": (84, 82),
    "Route 129": (80, 87), "Route 130": (71, 87), "Route 131": (63, 87),
    "Route 132": (53, 89), "Route 133": (43, 89), "Route 134": (34, 89),
    "Victory Road": (95, 76), "Pokemon League": (97, 69), "S.S. Tidal": (72, 74),
}

GYM_LEVEL_CAP_ORDER = (
    "Leader Roxanne",
    "Leader Brawly",
    "Leader Wattson",
    "Leader Flannery",
    "Leader Norman",
    "Leader Winona",
    "Leader Tate&Liza [2]",
    "Leader Juan",
)

LOCATION_ALIASES = {
    "Rustboro Gym": "Rustboro City", "Route 115 Main": "Route 115",
    "Route 115 Island": "Route 115", "Mauville Gym": "Mauville City",
    "Trick House": "Route 110", "Route 111 Desert": "Route 111",
    "Lavaridge Gym": "Lavaridge Town", "Dewford Gym": "Dewford Town",
    "Petalburg Gym": "Route 102", "Abandonded Ship": "Route 108",
    "Abandoned Ship": "Route 108", "Route 109 Surf": "Route 109",
    "Route 103 Surfing": "Route 103", "Weather Institute": "Route 119",
    "Fortree Gym": "Fortree City", "Mt.Pyre": "Mt. Pyre",
    "Mossdeep Gym": "Mossdeep City", "Space Center": "Mossdeep City",
    "Sootopolis Gym": "Sootopolis City", "Pokémon  League": "Pokemon League",
}


def _download(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Pokemon-Battle-Solver/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public source
        return response.read().decode("utf-8")


def _bold_rows(grid_html: str) -> set[int]:
    styles = {
        name
        for name, rules in re.findall(r"\.ritz \.waffle \.([\w-]+)\{([^}]*)\}", grid_html)
        if "font-weight:bold" in rules.replace(" ", "")
    }
    result: set[int] = set()
    for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", grid_html, re.DOTALL):
        row_html = row_match.group(1)
        row_number = re.search(r'class="row-header-wrapper"[^>]*>(\d+)</div>', row_html)
        first_cell = re.search(r'<td class="([^"]+)"[^>]*>(.*?)</td>', row_html, re.DOTALL)
        if row_number and first_cell and first_cell.group(1) in styles:
            result.add(int(row_number.group(1)))
    return result


def _clean(value: str | None) -> str:
    return html.unescape((value or "").strip())


def build_dataset(csv_text: str, grid_html: str) -> dict:
    rows = list(csv.reader(csv_text.splitlines()))
    bold_rows = _bold_rows(grid_html)
    trainers: list[dict] = []
    current: dict | None = None
    last_route = ""
    last_sublocation = ""

    for sheet_row, raw in enumerate(rows[1:], start=2):
        row = (raw + [""] * 17)[:17]
        name, money, route, sublocation, species, level, *tail = map(_clean, row)
        moves = [move for move in row[6:10] if _clean(move)]
        route = route or last_route
        sublocation = sublocation or last_sublocation
        if row[2].strip():
            last_route = route
            last_sublocation = _clean(row[3])

        continuation = not name or name == "[2]"
        if name in {"Starting", "Extra EXP"}:
            current = None
            continue
        if not continuation:
            current = {
                "id": len(trainers),
                "trainer_name": name,
                "money": int(money) if money.isdigit() else None,
                "route": route or "Unknown location",
                "sublocation": _clean(row[3]),
                "is_double": "[2]" in name,
                "required": sheet_row in bold_rows,
                "source_row": sheet_row,
                "party": [],
            }
            trainers.append(current)
        elif current is None:
            continue

        if sheet_row in bold_rows:
            current["required"] = True
        if species:
            stats = row[11:17]
            current["party"].append({
                "species": species,
                "level": int(level) if level.isdigit() else None,
                "moves": moves,
                "exp": int(row[10]) if row[10].isdigit() else None,
                "stats": {
                    "hp": int(stats[0]) if stats[0].isdigit() else None,
                    "attack": int(stats[1]) if stats[1].isdigit() else None,
                    "defense": int(stats[2]) if stats[2].isdigit() else None,
                    "sp_attack": int(stats[3]) if stats[3].isdigit() else None,
                    "sp_defense": int(stats[4]) if stats[4].isdigit() else None,
                    "speed": int(stats[5]) if stats[5].isdigit() else None,
                },
            })

    locations: dict[str, dict] = {}
    for trainer in trainers:
        raw_location = trainer["route"]
        map_location = LOCATION_ALIASES.get(raw_location, raw_location)
        trainer["map_location"] = map_location
        point = MAP_POINTS.get(map_location)
        if not point:
            # Keep every sheet location reachable even if it is an interior alias
            # added later. Unknowns appear in a compact atlas rail, never disappear.
            ordinal = len(locations)
            point = (5 + (ordinal % 10) * 9, 6 + (ordinal // 10) * 7)
        entry = locations.setdefault(map_location, {
            "name": map_location, "x": point[0], "y": point[1],
            "trainer_ids": [], "required_count": 0,
        })
        entry["trainer_ids"].append(trainer["id"])
        entry["required_count"] += int(trainer["required"])

    level_caps = []
    for badge, trainer_name in enumerate(GYM_LEVEL_CAP_ORDER, start=1):
        gym = next((trainer for trainer in trainers if trainer["trainer_name"] == trainer_name), None)
        if gym:
            level_caps.append({
                "badge": badge,
                "trainer_id": gym["id"],
                "trainer_name": trainer_name,
                "ace_level": max((mon.get("level") or 0 for mon in gym["party"]), default=0),
                "route": gym["route"],
            })

    return {
        "game": "pokemon-emerald",
        "label": "Pokémon Emerald",
        "source": {
            "spreadsheet_id": SPREADSHEET_ID,
            "sheet_gid": SHEET_GID,
            "sheet_name": "Emerald Swampert",
            "csv_url": CSV_URL,
        },
        "trainers": trainers,
        "locations": sorted(locations.values(), key=lambda item: min(item["trainer_ids"])),
        "level_caps": level_caps,
        "stats": {
            "trainers": len(trainers),
            "pokemon": sum(len(trainer["party"]) for trainer in trainers),
            "required_trainers": sum(1 for trainer in trainers if trainer["required"]),
            "locations": len(locations),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, help="Use an already-downloaded CSV")
    parser.add_argument("--html", type=Path, help="Use an already-downloaded HTML grid")
    parser.add_argument("--output", type=Path, default=Path("data/emerald_trainers.json"))
    parser.add_argument("--decomp", type=Path, help="Optional local pret/pokeemerald checkout for exact trainer event coordinates")
    args = parser.parse_args()
    csv_text = args.csv.read_text(encoding="utf-8") if args.csv else _download(CSV_URL)
    grid_html = args.html.read_text(encoding="utf-8") if args.html else _download(HTML_URL)
    dataset = build_dataset(csv_text, grid_html)
    if args.decomp:
        try:
            from tools.map_emerald_trainers import attach_events, extract_events
        except ModuleNotFoundError:
            from map_emerald_trainers import attach_events, extract_events

        attach_events(dataset, extract_events(args.decomp))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dataset["stats"], indent=2))


if __name__ == "__main__":
    main()
