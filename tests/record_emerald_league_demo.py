"""Record the ROM-free Emerald judge flow and saved League validation audit."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


VIDEO_DIR = Path("/tmp/emerald-league-video")
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(VIDEO_DIR),
        record_video_size={"width": 1440, "height": 900},
    )
    page = context.new_page()
    page.goto("http://127.0.0.1:8000/?game=emerald&view=emerald")
    page.wait_for_load_state("networkidle")
    page.get_by_role("heading", name="The completed Emerald run, battle by battle.").wait_for()
    page.wait_for_timeout(1800)

    boss_rail = page.get_by_text("Boss fights and story walls", exact=True)
    boss_rail.scroll_into_view_if_needed()
    page.wait_for_timeout(1600)

    page.get_by_role("button", name="Simulator", exact=True).click()
    page.get_by_text("Captured battle planner", exact=False).wait_for()
    page.wait_for_timeout(1400)
    page.get_by_role("button", name="Gauntlet", exact=True).click()
    page.get_by_role("button", name="Load the Elite Four → Wallace Gauntlet", exact=False).click()
    page.wait_for_timeout(1600)
    page.get_by_role("button", name="Close gauntlet setup").click()

    page.get_by_text("ROM-FREE JUDGE RUN", exact=True).wait_for()
    page.wait_for_timeout(1800)
    for heading in ("1. Elite Four Sidney", "2. Elite Four Phoebe", "3. Elite Four Glacia", "4. Elite Four Drake"):
        target = page.get_by_role("heading", name=heading)
        target.scroll_into_view_if_needed()
        page.wait_for_timeout(1500)

    video = page.video
    context.close()
    browser.close()
    print(video.path())
