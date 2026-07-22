"""Browser smoke test for mandatory Tate & Liza Simulator and Gauntlet videos."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("POKEBATTLE_TEST_URL", "http://127.0.0.1:8767")


def assert_video_response(page, source: str) -> dict[str, object]:
    response = page.request.get(f"{BASE_URL}{source}")
    assert response.ok, f"video returned {response.status}: {source}"
    content_type = response.headers.get("content-type", "")
    assert "video/mp4" in content_type, content_type
    body = response.body()
    assert len(body) > 1_000
    assert body[4:8] == b"ftyp"
    return {"url": source, "bytes": len(body), "content_type": content_type}


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    page.goto(f"{BASE_URL}/?game=emerald&view=emerald")
    page.wait_for_load_state("networkidle")
    page.get_by_role("heading", name="The completed Emerald run, battle by battle.").wait_for()
    page.locator("#emerald-search").fill("Tate Liza")
    page.get_by_role("button", name=re.compile(r"Leader Tate&Liza \[2\]", re.I)).first.click()
    page.get_by_role("button", name="Plan this trainer", exact=True).click()
    page.get_by_text("Captured battle planner", exact=False).wait_for()
    turn_cap = page.get_by_text("Turn cap", exact=True).locator("..").locator("input")
    turn_cap.fill("6")
    page.get_by_role("button", name="Find Crit-Safe Line", exact=True).click()
    sim_watch = page.get_by_role("button", name="Watch · Simulator turn replay", exact=True)
    sim_watch.wait_for(timeout=120_000)
    sim_watch.click()
    sim_video = page.locator(".video-lightbox-player")
    sim_video.wait_for()
    simulator_evidence = assert_video_response(page, sim_video.get_attribute("src"))
    page.screenshot(path=str(Path("/tmp/tate-liza-simulator-video.png")), full_page=True)
    page.get_by_role("button", name="Close video", exact=True).click()

    gauntlet_result = page.evaluate("""async () => {
      const trainersResponse = await fetch('/api/calc/trainers?game_mode=pokemon-emerald');
      const trainersData = await trainersResponse.json();
      const tate = trainersData.trainers.find((trainer) => Number(trainer.id) === 362);
      const response = await fetch('/api/calc/gauntlet', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          trainer_ids: [356, 361, 362],
          game_mode: 'pokemon-emerald',
          imports: tate.checkpoint_roster_import,
          max_turns: 6,
          crit_safe: true,
          reuse_saved: false,
          heal_between: true,
          healing_mode: 'pokemon-center',
          ruleset: 'hardcore-nuzlocke',
          max_total_faints: 0
        })
      });
      if (!response.ok) throw new Error(await response.text());
      return await response.json();
    }""")
    assert gauntlet_result["video_ready"] is True
    assert gauntlet_result["videos"][0]["video_ready"] is True
    assert "Tate&Liza" in gauntlet_result["stopped_reason"]

    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="Gauntlet", exact=True).click()
    page.get_by_role("button", name="Close gauntlet setup", exact=True).click()
    page.get_by_text("Saved route logs", exact=True).wait_for()
    page.locator(".evidence-day-toggle").first.click()
    newest_run = page.locator(".evidence-run-row").first
    assert "Tate&Liza" in newest_run.inner_text()
    newest_run.get_by_role("button", name="Watch video", exact=True).click()
    gauntlet_video = page.locator(".video-lightbox-player")
    gauntlet_video.wait_for()
    gauntlet_evidence = assert_video_response(page, gauntlet_video.get_attribute("src"))
    page.screenshot(path=str(Path("/tmp/tate-liza-gauntlet-video.png")), full_page=True)

    progress = page.request.get(f"{BASE_URL}/api/calc/gauntlet/progress").json()
    assert progress["pct"] == 100.0
    assert progress["video_ready"] is True
    assert progress["phase"] == "Video and log saved"
    assert not page_errors, page_errors
    browser.close()

print(json.dumps({
    "simulator": simulator_evidence,
    "gauntlet": gauntlet_evidence,
    "gauntlet_result": gauntlet_result["result"],
    "gauntlet_progress": progress,
    "screenshots": [
        "/tmp/tate-liza-simulator-video.png",
        "/tmp/tate-liza-gauntlet-video.png",
    ],
}, indent=2))
