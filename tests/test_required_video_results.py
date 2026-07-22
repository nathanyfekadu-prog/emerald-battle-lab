from __future__ import annotations

import json
import subprocess
from pathlib import Path

import web.server as server
from tools.render_run_video import _ffmpeg_executable


def _assert_playable_mp4(path: Path) -> None:
    assert path.is_file()
    assert path.stat().st_size > 1_000
    assert path.read_bytes()[4:8] == b"ftyp"
    decoded = subprocess.run(
        [_ffmpeg_executable(), "-v", "error", "-i", str(path), "-f", "null", "-"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert decoded.returncode == 0, decoded.stderr


def test_tate_and_liza_simulator_partial_line_still_saves_video(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server, "_CALC_RUNS_DIR", tmp_path)
    request = server.CalcSimRequest(
        trainer_id=362,
        game_mode="pokemon-emerald",
        imports="MARSHTOMP\nLevel: 17\n- Water Gun",
    )
    result = {
        "trainer": "Leader Tate&Liza [2]",
        "location": "Mossdeep Gym",
        "result": "partial-line",
        "confidence": 0.001,
        "team": [{"name": "MARSHTOMP", "species": "marshtomp"}],
        "turns": [{"turn": 1, "action": "Use Water Gun into Solrock"}],
    }

    saved = server._save_calc_run(request, result)

    record = json.loads((tmp_path / f"{saved['id']}.json").read_text(encoding="utf-8"))
    video = record["result"]["videos"][0]
    assert record["result"]["video_ready"] is True
    assert video["video_ready"] is True
    assert video["size_bytes"] > 1_000
    assert video["duration_seconds"] == 9.6
    _assert_playable_mp4(tmp_path / Path(video["video_url"]).name)


def test_tate_and_liza_stopped_gauntlet_still_saves_video(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server, "_GAUNTLET_RUNS_DIR", tmp_path)
    request = server.GauntletSimRequest(
        trainer_ids=[356, 361, 362],
        game_mode="pokemon-emerald",
        imports="MARSHTOMP\nLevel: 17\n- Water Gun",
        reuse_saved=False,
    )
    result = {
        "result": "route-stopped",
        "completed": 3,
        "queued": 3,
        "stopped_reason": "The route stopped at Leader Tate&Liza [2].",
        "fights": [{
            "trainer": "Leader Tate&Liza [2]",
            "result": "partial-line",
            "turns": [{"turn": 1, "action": "Use Water Gun into Solrock"}],
            "ending_team": [{"name": "MARSHTOMP", "hp": 9, "max_hp": 50}],
        }],
    }

    saved = server._save_gauntlet_run(request, result)

    record = json.loads((tmp_path / f"{saved['id']}.json").read_text(encoding="utf-8"))
    video = record["result"]["videos"][0]
    assert record["result"]["video_ready"] is True
    assert video["video_ready"] is True
    assert video["size_bytes"] > 1_000
    assert video["duration_seconds"] == 9.6
    _assert_playable_mp4(tmp_path / Path(video["video_url"]).name)


def test_calc_progress_cannot_finish_before_required_video() -> None:
    original = dict(server._CALC_PROGRESS)
    try:
        server._CALC_PROGRESS.update(
            running=True, done=20, total=20, phase="planning",
            stage_start=0, stage_alloc=20, hold_for_video=True,
        )
        server._progress_finish()
        held = server._CALC_PROGRESS
        assert held["running"] is True
        assert held["done"] < held["total"]
        assert "video" in held["phase"].casefold()

        server._progress_video_complete()
        assert server._CALC_PROGRESS["running"] is False
        assert server._CALC_PROGRESS["done"] == server._CALC_PROGRESS["total"]
        assert server._CALC_PROGRESS["phase"] == "Video and log saved"
    finally:
        server._CALC_PROGRESS.clear()
        server._CALC_PROGRESS.update(original)


def test_raw_simulator_capture_failure_gets_labeled_video_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server, "_SIM_RUNS_DIR", tmp_path)
    monkeypatch.setattr(server, "_SIM_CHECKPOINTS_DIR", tmp_path / "checkpoints")
    monkeypatch.setattr(
        server,
        "record_simulator_line",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("mGBA capture stopped")),
    )

    class FakeSearchResult:
        validated_lines = []
        recommended_line = ["Use Surf into both targets"]
        win_probability = 0.5
        faint_probability = 0.25
        total_trials_run = 12
        search_time_seconds = 1.5

        @staticmethod
        def to_dict():
            return {"recommended_line": ["Use Surf into both targets"]}

    request = server.SolveRequest(rom="emerald.gba", state="tate-liza.ss0")
    record = server._record_completed_simulator(request, FakeSearchResult(), None)

    assert record["proof_complete"] is False
    assert record["video_ready"] is True
    assert record["fallback_video"] is True
    video = record["videos"][0]
    assert video["kind"] == "search-report"
    assert video["gameplay"] is False
    assert video["proof_eligible"] is False
    assert "mGBA capture stopped" in video["capture_error"]
    _assert_playable_mp4(tmp_path / Path(video["video_url"]).name)
