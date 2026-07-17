from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trainer_data.models import TrainerBattle, TrainerPokemon

NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

SKIPPED_SHEETS = {"Sprites"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Run & Bun trainer battle data from XLSX")
    parser.add_argument("workbook", help="Path to Trainer Battles.xlsx")
    parser.add_argument(
        "--output",
        default="data/trainer_battles.json",
        help="Path to write normalized JSON",
    )
    args = parser.parse_args()

    payload = import_workbook(Path(args.workbook))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(payload['battles'])} trainer battles to {output}")


def import_workbook(path: Path) -> dict[str, object]:
    sheets = read_xlsx(path)
    dex = _parse_dex(sheets.get("Dex", []))
    battles: list[TrainerBattle] = []
    for sheet_name, rows in sheets.items():
        if sheet_name == "Dex" or sheet_name in SKIPPED_SHEETS:
            continue
        battles.extend(_parse_battle_sheet(sheet_name, rows, dex))
    return {
        "source": str(path),
        "dex": dex,
        "battles": [_battle_to_dict(battle) for battle in battles],
    }


def read_xlsx(path: Path) -> dict[str, list[list[str]]]:
    with ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target_by_id = {rel.attrib["Id"]: rel.attrib["Target"] for rel in relationships}
        sheets: dict[str, list[list[str]]] = {}
        sheets_element = workbook.find("main:sheets", NS)
        if sheets_element is None:
            return {}
        for sheet in sheets_element:
            name = sheet.attrib["name"]
            rel_id = sheet.attrib[f"{{{NS['rel']}}}id"]
            target = target_by_id[rel_id]
            sheets[name] = _read_sheet(archive, "xl/" + target, shared_strings)
        return sheets


def _read_shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(text.text or "" for text in item.findall(".//main:t", NS)) for item in root.findall("main:si", NS)]


def _read_sheet(archive: ZipFile, target: str, shared_strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(archive.read(target))
    rows: list[list[str]] = []
    for row in root.findall(".//main:row", NS):
        values: list[str] = []
        for cell in row.findall("main:c", NS):
            index = _column_index(cell.attrib.get("r", "A1"))
            while len(values) <= index:
                values.append("")
            values[index] = _cell_text(cell, shared_strings).strip()
        while values and values[-1] == "":
            values.pop()
        rows.append(values)
    return rows


def _cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "s":
        value = cell.find("main:v", NS)
        if value is None or value.text is None:
            return ""
        return shared_strings[int(value.text)]
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//main:t", NS))
    value = cell.find("main:v", NS)
    return value.text if value is not None and value.text is not None else ""


def _column_index(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha())
    value = 0
    for char in letters:
        value = value * 26 + ord(char.upper()) - 64
    return value - 1


def _parse_dex(rows: list[list[str]]) -> dict[str, str]:
    dex: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) >= 2 and row[0] and row[1]:
            dex[_normalize_name(row[0])] = row[1]
    return dex


def _parse_battle_sheet(section: str, rows: list[list[str]], dex: dict[str, str]) -> list[TrainerBattle]:
    battles: list[TrainerBattle] = []
    location: str | None = None
    index = 0
    while index < len(rows):
        row = rows[index]
        if _is_location_row(row):
            location = row[1]
            index += 1
            continue
        if not row or _cell(row, 0) != "Name":
            index += 1
            continue

        trainer_name = _cell(row, 1)
        pokemon_row = _find_label_row(rows, index + 1, "Pokémon")
        level_row = _find_label_row(rows, index + 1, "Level")
        item_row = _find_label_row(rows, index + 1, "Held Item")
        ability_row = _find_label_row(rows, index + 1, "Ability")
        nature_row = _find_label_row(rows, index + 1, "Nature")
        moves_row = _find_label_row(rows, index + 1, "Moves")
        next_name = _find_next_name_row(rows, index + 1)
        if pokemon_row is None or moves_row is None:
            index += 1
            continue

        species_row = pokemon_row + 1
        if species_row >= len(rows):
            index += 1
            continue
        species = rows[species_row]
        party: list[TrainerPokemon] = []
        for column in range(1, len(species)):
            name = _cell(species, column)
            if not name:
                continue
            moves = [
                _cell(rows[row_index], column)
                for row_index in range(moves_row, min(moves_row + 4, len(rows)))
                if row_index < next_name
            ]
            party.append(
                TrainerPokemon(
                    species=name,
                    level=_parse_int(_cell(rows[level_row], column)) if level_row is not None else None,
                    held_item=_optional(_cell(rows[item_row], column)) if item_row is not None else None,
                    ability=_optional(_cell(rows[ability_row], column)) if ability_row is not None else None,
                    nature=_optional(_cell(rows[nature_row], column)) if nature_row is not None else None,
                    moves=tuple(move for move in moves if move),
                    dex_key=dex.get(_normalize_name(name)),
                )
            )

        if trainer_name and party:
            battles.append(
                TrainerBattle(
                    section=section,
                    location=location,
                    trainer_name=trainer_name,
                    is_double="[double]" in trainer_name.casefold(),
                    party=tuple(party),
                )
            )
        index = max(index + 1, next_name)
    return battles


def _battle_to_dict(battle: TrainerBattle) -> dict[str, object]:
    data = asdict(battle)
    data["party"] = [asdict(pokemon) for pokemon in battle.party]
    for pokemon in data["party"]:
        pokemon["moves"] = list(pokemon["moves"])
    return data


def _is_location_row(row: list[str]) -> bool:
    return len(row) >= 2 and not _cell(row, 0) and bool(_cell(row, 1))


def _find_label_row(rows: list[list[str]], start: int, label: str) -> int | None:
    stop = _find_next_name_row(rows, start)
    for index in range(start, stop):
        if _cell(rows[index], 0) == label:
            return index
    return None


def _find_next_name_row(rows: list[list[str]], start: int) -> int:
    for index in range(start, len(rows)):
        if _cell(rows[index], 0) == "Name":
            return index
    return len(rows)


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def _optional(value: str) -> str | None:
    return value or None


def _parse_int(value: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else None


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


if __name__ == "__main__":
    main()
