"""Headless smoke test for the ROM-free Emerald judge flow."""

from pathlib import Path

from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8000/?game=emerald&view=emerald")
    page.wait_for_load_state("networkidle")
    page.get_by_role("heading", name="The completed Emerald run, battle by battle.").wait_for()
    assert page.get_by_text("173", exact=True).first.is_visible()
    assert page.get_by_text("Boss fights and story walls", exact=True).is_visible()
    page.get_by_role("button", name="Simulator", exact=True).click()
    page.get_by_text("Captured battle planner", exact=False).wait_for()
    page.get_by_role("button", name="Gauntlet", exact=True).click()
    page.get_by_role("button", name="Load the Elite Four → Wallace Gauntlet", exact=False).click()
    assert page.get_by_text("Champion Wallace", exact=False).first.is_visible()
    assert page.locator('select').filter(has=page.locator('option[value="hardcore-nuzlocke"]')).first.is_visible()
    page.screenshot(path=str(Path("/tmp/emerald-judge-ui.png")), full_page=True)
    assert not errors, errors
    browser.close()
