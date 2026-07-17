from io import BytesIO

from pypdf import PdfReader

from output.pdf_plan import build_battle_plan_pdf


def test_battle_plan_pdf_is_a_real_pdf() -> None:
    result = {
        "result": "win",
        "confidence": 0.9,
        "realistic_confidence": 0.8,
        "turns": [{
            "turn": 1,
            "answer": "Arcanine",
            "enemy": "Skarmory",
            "action": "Use Wild Charge",
            "calc": "85-101 damage",
            "your_hp": "80/100",
            "enemy_hp": "0/100",
            "risks": ["low roll"],
        }],
        "strategy_report": [{
            "name": "Roll-proof sequencing",
            "description": "Covers a missed knockout roll.",
            "used": True,
            "where": ["Turn 1"],
        }],
        "team": [],
        "enemies": [],
        "heart_scales": 4,
        "optimized_item_line": {"item_changes": [{
            "pokemon": "Arcanine", "old_item": "Oran Berry",
            "new_item": "Sitrus Berry", "reason": "Survives the return hit.",
        }]},
        "threat_answers": [{
            "enemy": "Skarmory", "clean": True,
            "best": {"mon": "Arcanine", "move": "Wild Charge", "faster": True},
        }],
    }

    pdf = build_battle_plan_pdf(result, "Test Trainer")

    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 2_000
    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages).casefold()
    assert "before the battle" in text
    assert "recommended item loadout" in text
    assert "best answer to each enemy" in text


def test_doubles_pdf_prints_board_slots_and_per_slot_actions() -> None:
    result = {
        "result": "win-line",
        "confidence": 0.9,
        "is_doubles": True,
        "lead_positions": {
            "player": ["Arcanine", "Palpitoad"],
            "enemy": ["Manectric", "Skarmory"],
        },
        "turns": [{
            "turn": 1,
            "answer": "Arcanine + Palpitoad",
            "enemy": "Manectric + Skarmory",
            "action": "structured below",
            "calc": "Both actions resolve in speed order.",
            "your_hp": "100/100 | 110/110",
            "enemy_hp": "0/100 | 40/100",
            "slot_actions": [
                {"side": "player", "field_slot": 0, "actor": "Arcanine", "kind": "move", "move": "Wild Charge", "target": "Manectric"},
                {"side": "player", "field_slot": 1, "actor": "Palpitoad", "kind": "move", "move": "Surf", "target": "both enemy slots"},
            ],
        }],
        "team": [],
        "enemies": [],
        "strategy_report": [],
    }

    pdf = build_battle_plan_pdf(result, "Doubles Test")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages)

    assert "Doubles opening board" in text
    assert "Slot 1 (left)" in text
    assert "Your slot 1 - Arcanine" in text
    assert "Your slot 2 - Palpitoad" in text
