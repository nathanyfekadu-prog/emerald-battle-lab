"""End-to-end browser proof for the two late Emerald Gym Leader planners."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("POKEBATTLE_TEST_URL", "http://127.0.0.1:8767")


def solve_from_atlas(
    page, query: str, trainer_pattern: str, screenshot: str, turn_cap: int,
    expected_lead: str, expected_cap: int,
) -> str:
    page.goto(f"{BASE_URL}/?game=emerald&view=emerald", wait_until="domcontentloaded", timeout=60_000)
    page.get_by_role("heading", name="The completed Emerald run, battle by battle.").wait_for(timeout=60_000)
    page.locator("#emerald-search").fill(query)
    page.get_by_role("button", name=re.compile(trainer_pattern, re.I)).first.click()
    page.get_by_role("button", name="Plan this trainer", exact=True).click()
    page.get_by_text("Captured battle planner", exact=False).wait_for()
    assert page.locator("textarea.rnb-textarea-full").input_value().lstrip().startswith(expected_lead)
    cap_select = page.locator(".hint-setup-card label").filter(has_text="Current level cap").locator("select")
    assert int(cap_select.input_value()) == expected_cap
    page.get_by_text("Turn cap", exact=True).locator("..").locator("input").fill(str(turn_cap))
    page.get_by_role("button", name="Find Crit-Safe Line", exact=True).click()
    disclosure = page.get_by_text("BOSS-CAP PREPARATION APPLIED", exact=True)
    disclosure.wait_for(timeout=240_000)
    output = page.locator("section[aria-label='Sim Calc output']")
    text = output.inner_text()
    assert "Result: win-line" in text, text
    assert "BOSS-CAP PREPARATION APPLIED" in text
    page.screenshot(path=screenshot, full_page=True)
    return text


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    tate = solve_from_atlas(
        page, "Tate Liza", r"Leader Tate&Liza \[2\]", "/tmp/emerald-tate-liza-winning-line.png", 10,
        "MARSHTOMP", 42,
    )
    assert "Level cap 42" in tate
    assert "MARSHTOMP: Lv. 17 → 42" in tate

    juan = solve_from_atlas(
        page, "Leader Juan", r"Leader Juan", "/tmp/emerald-juan-winning-line.png", 15,
        "SWAMPERT", 46,
    )
    assert "Level cap 46" in juan
    assert "Open the all-enemy-crits stress result" in juan
    assert "unsafe (Kri fainted)" in juan

    glacia = solve_from_atlas(
        page, "Elite Four Glacia", r"Elite Four Glacia", "/tmp/emerald-glacia-winning-line.png", 20,
        "SWAMPERT", 53,
    )
    assert "Level cap 53" in glacia
    assert "Result: win-line" in glacia

    assert not errors, errors
    browser.close()

print(json.dumps({
    "tate_liza": "win-line, gym cap 42, deathless all-crits stress",
    "juan": "win-line, gym cap 46, deathless main line plus disclosed all-crits warning",
    "glacia": "win-line, boss cap 53, deathless main line from an unrelated Elite Four checkpoint",
    "screenshots": [
        "/tmp/emerald-tate-liza-winning-line.png",
        "/tmp/emerald-juan-winning-line.png",
        "/tmp/emerald-glacia-winning-line.png",
    ],
}, indent=2))
