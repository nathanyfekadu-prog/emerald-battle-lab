from __future__ import annotations

from tools.import_trainer_battles import _parse_battle_sheet, _parse_dex


def test_parse_battle_sheet_reads_trainer_party() -> None:
    dex = _parse_dex(
        [
            ["Pokemon", "Name in file"],
            ["Poochyena", "poochyena"],
            ["Lillipup", "lillipup"],
        ]
    )
    rows = [
        ["", "Route 102"],
        ["Name", "Youngster Calvin"],
        ["Pokémon"],
        ["", "Poochyena", "Lillipup"],
        ["Level", "5 ", "6 "],
        ["Held Item", "", "Oran Berry"],
        ["Ability", "Rattled", "Vital Spirit"],
        ["Nature", "Bashful", "Jolly"],
        ["Moves", "Bite", "Tackle"],
        ["", "Quick Attack", "Bite"],
        ["", "Sand Attack", "Sand Attack"],
        [],
    ]

    battles = _parse_battle_sheet("Brawly Split", rows, dex)

    assert len(battles) == 1
    assert battles[0].location == "Route 102"
    assert battles[0].trainer_name == "Youngster Calvin"
    assert battles[0].party[0].species == "Poochyena"
    assert battles[0].party[0].level == 5
    assert battles[0].party[0].moves == ("Bite", "Quick Attack", "Sand Attack")
    assert battles[0].party[1].held_item == "Oran Berry"
    assert battles[0].party[1].dex_key == "lillipup"
