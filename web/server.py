from __future__ import annotations

import asyncio
import copy
import hashlib
import heapq
import itertools
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import re
from datetime import datetime, timezone
from dataclasses import asdict, fields as dataclass_fields, replace
from pathlib import Path
from typing import Any

import config
from battle.action import Action
from battle.battle_state import BattleState
from emulator.mgba_instance import MGBAInstance
from emulator.preparation import TeamSlotRequest, prepare_party
from emulator.autonomy import CheckpointedGameRunner, RouteAction
from emulator.game_state import GameMode, WholeGameStateReader
from emulator.mgba_pool import MGBAPool
from emulator.planner_policy import compile_planner_policy
from emulator.state_reader import StateReader
from emulator.sim_video import compose_split_screen, record_simulator_line
from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from search.action_enumerator import ActionEnumerator
from search.mcts import MCTS, SearchCancelled, SearchResult, _simulator_trial_data
from outcome import TrialSpec
from optimizer.box_optimizer import PrepareResult, run_prepare, scan_pc_boxes
from optimizer.gen3_save import read_player_name
from output.pdf_plan import build_battle_plan_pdf
from optimizer.turn_planner import (
    set_entry_turn,
    SLEEP_MOVES,
    _enemy_has_damaging_category,
    INTIMIDATE_IMMUNE_ABILITIES,
    MoveChoice,
    PlannedEnemy,
    PlannedMember,
    _ai_move_choices,
    _ai_hard_switch_target,
    _apply_enemy_action,
    _apply_entry_ability,
    _apply_player_action,
    _ai_branch_confidence,
    _active_can_stay_and_progress,
    _best_player_action,
    _branch_risk_notes,
    _choice_kills_current,
    _choice_risks,
    _doubles_enemy_target,
    _doubles_spread_penalty,
    _doubles_player_targets,
    _end_of_turn,
    _enemy_moves_before_player,
    _is_spread_in_doubles,
    _max_hp,
    _member_label,
    _normalize,
    _player_action_confidence,
    _ranked_known_damage,
    _refresh_player_action,
    _retarget_player_action,
    set_ignore_secondary,
    set_soften_reporting,
    crit_rate,
    _speed,
    _skip_turn,
    _will_skip_turn,
    _move_priority_for,
    _move_accuracy,
    PlayerAction,
    _status_disruption_risk,
    recommend_held_items,
)
from battle.damage_calc import (
    ATTACK_DROP_MOVES,
    BURN_MOVES,
    CONFUSION_MOVES,
    PARALYSIS_MOVES,
    POISON_MOVES,
    SETUP_MOVE_BOOSTS,
    SPECIAL_ATTACK_DROP_MOVES,
    SPEED_DROP_MOVES,
    TOXIC_MOVES,
    DamageCalculator,
    DamageContext,
    DamageRange,
    FieldState,
    PokemonCalcSet,
)
from trainer_data.loader import load_trainer_battles, load_trainer_battles_for_mode, normalize_game_mode
from trainer_data.models import TrainerBattle, TrainerPokemon
from tools.render_run_video import render_run_video
from web.ws_manager import WSManager

app = FastAPI()
manager = WSManager()
_SIM_RUNS_DIR = Path(__file__).resolve().parents[1] / "output" / "simulator_runs"
_SIM_RUNS_DIR.mkdir(parents=True, exist_ok=True)
_SIM_CHECKPOINTS_DIR = Path(__file__).resolve().parents[1] / "output" / "simulator_checkpoints"
_SIM_CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/sim-videos", StaticFiles(directory=_SIM_RUNS_DIR), name="sim-videos")
app.mount(
    "/demo",
    StaticFiles(directory=Path(__file__).resolve().parents[1] / "demo"),
    name="judge-demo",
)
app.mount(
    "/submission",
    StaticFiles(directory=Path(__file__).resolve().parents[1] / "submission"),
    name="submission-artifacts",
)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="app-static",
)
app.mount(
    "/rnbcalc",
    StaticFiles(directory=Path(__file__).parent / "static" / "rnbcalc", html=True),
    name="rnbcalc",
)

status_lock = threading.Lock()
status: dict[str, Any] = {"running": False, "phase": "idle", "message": ""}
last_result: SearchResult | PrepareResult | None = None
display_state: BattleState | None = None
job_lock = threading.Lock()
current_cancel_event: threading.Event | None = None
active_mcts: set[MCTS] = set()


class SolveRequest(BaseModel):
    rom: str
    state: str
    instances: int = 4
    turns: int = config.MAX_TURNS
    iterations: int = config.MCTS_ITERATIONS
    trials_per_node: int = config.TRIALS_PER_NODE
    final_line_trials: int = config.FINAL_LINE_TRIALS
    final_line_candidates: int = config.FINAL_LINE_CANDIDATES
    nuzlocke: bool = False
    game_mode: str = "run-and-bun"


class PrepareRequest(BaseModel):
    rom: str
    battle_state: str
    pc_state: str
    instances: int = 4
    iterations: int = config.MCTS_ITERATIONS
    nuzlocke: bool = False


class ApplyTeamSlotRequest(BaseModel):
    source: str
    party_slot: int | None = None
    box: int | None = None
    box_slot: int | None = None
    # Omitted means preserve the currently owned item. Zero is an explicit request
    # for no held item and must not be silently applied to every selected Pokemon.
    item_id: int | None = None


class ApplyPreparationRequest(BaseModel):
    rom: str
    pc_state: str
    output_state: str = ""
    team: list[ApplyTeamSlotRequest] = Field(min_length=1, max_length=6)


class OpenGBARequest(BaseModel):
    rom: str


class SimulatorInspectRequest(BaseModel):
    rom: str
    state: str
    game_mode: str = "run-and-bun"


class SimulatorCheckpointRequest(BaseModel):
    rom: str
    game_mode: str = "run-and-bun"
    source_state: str = ""
    output_state: str = ""
    settle_frames: int = Field(default=120, ge=0, le=3600)


class PlannerValidationRequest(BaseModel):
    rom: str
    state: str
    result: dict[str, Any]
    repetitions: int = Field(default=30, ge=1, le=100)
    instances: int = Field(default=4, ge=1, le=16)
    playbook_path: str = ""


class RouteActionRequest(BaseModel):
    kind: str
    value: str | None = None
    count: int = 1
    settle_frames: int = 12


class AutonomyRouteRequest(BaseModel):
    rom: str
    state: str
    route_name: str = "overworld route"
    actions: list[RouteActionRequest] = Field(default_factory=list)
    checkpoint_every: int = Field(default=1, ge=1, le=100)
    destination: str = ""
    trainer_id: int | None = None
    game_mode: str = "run-and-bun"


class CalcSimRequest(BaseModel):
    trainer_id: int
    game_mode: str = "run-and-bun"
    imports: str = ""
    max_turns: int = 30
    # New lines should be safe against enemy critical hits unless the user
    # deliberately opts out in the UI.
    crit_safe: bool = True
    weather: str | None = None
    reflect: bool = False
    light_screen: bool = False
    # Custom opponents use the same Showdown-style format as the player's team.
    # This doubles as a lightweight custom-tournament builder: reorder the roster
    # and lock the two opening field positions without editing trainer data files.
    custom_trainer_name: str = "Custom Doubles"
    custom_enemy_imports: str = ""
    custom_is_double: bool = True
    player_leads: list[int] = Field(default_factory=list)
    enemy_leads: list[int] = Field(default_factory=list)
    hint_mode: bool = False
    level_cap: int | None = Field(default=None, ge=1, le=100)
    ruleset: str = "hardcore-nuzlocke"
    items_in_battle: bool = False


class GauntletSimRequest(BaseModel):
    """Plan several trainers in order with an optional no-heal state handoff."""

    trainer_ids: list[int] = Field(min_length=1, max_length=512)
    game_mode: str = "run-and-bun"
    imports: str = ""
    max_turns: int = 30
    crit_safe: bool = True
    heal_between: bool = False
    weather: str | None = None
    reflect: bool = False
    light_screen: bool = False
    # When healing is allowed the route visits the Pokemon Center, so the planner may
    # withdraw a new six from the imported box before the next trainer.
    optimize_between_fights: bool = True
    reuse_saved: bool = True
    rom: str = ""
    pc_state: str = ""
    use_live_pc_box: bool = True
    # Judge-facing Emerald defaults: bag healing between League fights, no
    # Revives, no battle items, and progressive hints kept off unless requested.
    ruleset: str = "hardcore-nuzlocke"
    items_in_battle: bool = False
    allow_revives: bool = False
    healing_mode: str = "bag"
    hint_mode: bool = False
    leveling_policy: str = "party-max"
    # Always optimize for zero faints first. This is only the maximum number of
    # tactical sacrifices the user is willing to accept if no zero-faint route exists.
    max_total_faints: int = Field(default=0, ge=0, le=6)


class ApplyGauntletFightRequest(BaseModel):
    rom: str
    pc_state: str
    output_state: str = ""


class CompleteFlowchartRequest(CalcSimRequest):
    selected_team_names: list[str] = Field(default_factory=list)
    line_search_lead: int | None = None
    player_move_overrides: dict[int, str] = Field(default_factory=dict)


class PlanPdfRequest(BaseModel):
    result: dict[str, Any]
    trainer_label: str = "Battle Plan"
    game_mode: str = "run-and-bun"


_CALC_RUNS_DIR = Path(__file__).resolve().parents[1] / "output" / "line_runs"
_GAUNTLET_RUNS_DIR = Path(__file__).resolve().parents[1] / "output" / "gauntlet_runs"
_CALC_RUNS_DIR.mkdir(parents=True, exist_ok=True)
_GAUNTLET_RUNS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/calc-artifacts",
    StaticFiles(directory=_CALC_RUNS_DIR),
    name="calc-artifacts",
)
app.mount(
    "/gauntlet-artifacts",
    StaticFiles(directory=_GAUNTLET_RUNS_DIR),
    name="gauntlet-artifacts",
)
_CALC_ENGINE_VERSION = "2026-07-21-mandatory-video-v3"
_GAUNTLET_ENGINE_VERSION = "2026-07-21-route-coverage-video-v9"
_GAUNTLET_PLAYBOOKS_DIR = Path(__file__).resolve().parents[1] / "config" / "gauntlet_playbooks"


def _matching_gauntlet_playbook(trainers: list[TrainerBattle]) -> Path | None:
    requested = [trainer.trainer_name.casefold() for trainer in trainers]
    for path in sorted(_GAUNTLET_PLAYBOOKS_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expected = [str(item["name"]).casefold() for item in payload.get("trainers") or []]
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if len(requested) == len(expected) and all(
            want in actual or actual in want for want, actual in zip(expected, requested)
        ):
            return path
    return None


def _repair_gauntlet_battle(
    rom: Path, playbook: Path, failure: str, run_id: str, repair_number: int,
) -> tuple[Path, dict[str, Any]] | None:
    """Learn a replacement cartridge line from the last live pre-battle checkpoint."""
    battle_failure = any(token in failure.casefold() for token in (
        "turn", "nuzlocke death", "did not reproduce",
    ))
    checkpoints = sorted(
        _GAUNTLET_RUNS_DIR.glob("*-live-prebattle.ss0"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not battle_failure or not checkpoints:
        return None
    checkpoint = checkpoints[0]
    payload = json.loads(playbook.read_text(encoding="utf-8"))
    trainer_index = next((
        index for index, trainer in enumerate(payload.get("trainers") or [])
        if str(trainer.get("name", "")).casefold() in failure.casefold()
    ), None)
    if trainer_index is None:
        checkpoint_slug = checkpoint.name.removesuffix("-live-prebattle.ss0")
        trainer_index = next((
            index for index, trainer in enumerate(payload.get("trainers") or [])
            if checkpoint_slug in re.sub(r"[^a-z0-9]+", "-", str(trainer.get("name", "")).casefold()).strip("-")
        ), None)
    if trainer_index is None:
        return None
    repair_dir = _GAUNTLET_RUNS_DIR / "learned_lines" / f"{run_id}-repair-{repair_number}"
    command = [
        sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "run_checkpoint_beam.py"),
        "--rom", str(rom), "--state", str(checkpoint), "--output", str(repair_dir),
        "--workers", "8", "--beam", "16", "--actions", "16", "--turns", "40",
    ]
    searched = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[1], capture_output=True,
        text=True, timeout=2 * 60 * 60,
    )
    search_path = repair_dir / "search.json"
    if searched.returncode != 0 or not search_path.is_file():
        return None
    search = json.loads(search_path.read_text(encoding="utf-8"))
    if search.get("status") != "won":
        return None
    relative_search = str(search_path.relative_to(Path(__file__).resolve().parents[1]))
    old_lines = list(payload["trainers"][trainer_index].get("lines") or [])
    payload["trainers"][trainer_index]["lines"] = [relative_search]
    learned_dir = _GAUNTLET_RUNS_DIR / "learned_playbooks"
    learned_dir.mkdir(parents=True, exist_ok=True)
    learned = learned_dir / f"{payload.get('id', playbook.stem)}.json"
    temp = learned.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(learned)
    return learned, {
        "trainer": payload["trainers"][trainer_index]["name"],
        "checkpoint": str(checkpoint), "old_lines": old_lines,
        "learned_line": relative_search, "repair": repair_number,
    }


def _cartridge_replay_safety(payload: dict[str, Any]) -> dict[str, Any]:
    """Derive deathless/min-HP proof from raw recorded battle frames.

    Recorder completion alone only proves that the route reached the end. A Nuzlocke
    proof must also show that every occupied party slot stayed above zero throughout
    each fight. Empty party slots begin at zero and are intentionally ignored.
    """
    occupied: set[int] = set()
    minimum_hp: int | None = None
    battles_checked = 0
    for event in payload.get("events") or []:
        kind = event.get("event")
        values = event.get("party_hp") if kind in {"battle_start", "battle_won"} else event.get("player_hp")
        if kind == "battle_start":
            occupied = {index for index, hp in enumerate(values or []) if int(hp or 0) > 0}
            battles_checked += 1
        if not occupied or values is None:
            continue
        for index in occupied:
            hp = int(values[index] or 0) if index < len(values) else 0
            if hp <= 0:
                return {
                    "deathless": False, "minimum_player_hp": 0,
                    "battles_checked": battles_checked,
                    "failed_trainer": event.get("trainer"),
                    "failed_turn": event.get("turn"),
                    "failed_slot": index,
                }
            minimum_hp = hp if minimum_hp is None else min(minimum_hp, hp)
    return {
        "deathless": battles_checked > 0,
        "minimum_player_hp": minimum_hp or 0,
        "battles_checked": battles_checked,
    }


def _validation_with_recorded_safety(result: dict[str, Any]) -> dict[str, Any] | None:
    validation = copy.deepcopy(result.get("emulator_validation") or {})
    log_names = (result.get("emulator_artifacts") or {}).get("logs") or []
    summaries: list[dict[str, Any]] = []
    for name in log_names:
        path = Path(name)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        try:
            summaries.append(_cartridge_replay_safety(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, TypeError, json.JSONDecodeError):
            continue
    if summaries:
        validation["replay_summaries"] = summaries
        validation["fixed_replays"] = len(summaries)
        validation["fixed_deathless_wins"] = sum(bool(item.get("deathless")) for item in summaries)
        validation["minimum_player_hp"] = min(int(item.get("minimum_player_hp") or 0) for item in summaries)
    return validation or None


def _run_gauntlet_cartridge_proof(
    request: GauntletSimRequest,
    trainers: list[TrainerBattle],
    playbook: Path,
) -> dict[str, Any]:
    """Replay a planned route twice in mGBA and publish only complete evidence."""
    rom = Path(request.rom).expanduser().resolve()
    state = Path(request.pc_state).expanduser().resolve()
    if not rom.is_file() or not state.is_file():
        raise RuntimeError("A readable ROM and starting save state are required for cartridge proof")
    created = datetime.now(timezone.utc)
    run_id = created.strftime("%Y%m%dT%H%M%S%f")
    artifacts: list[dict[str, Any]] = []
    contingency_artifacts: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    base_payload = json.loads(playbook.read_text(encoding="utf-8"))
    learned = _GAUNTLET_RUNS_DIR / "learned_playbooks" / f"{base_payload.get('id', playbook.stem)}.json"
    active_playbook = playbook
    if learned.is_file():
        try:
            learned_payload = json.loads(learned.read_text(encoding="utf-8"))
            learned_lines = [
                Path(__file__).resolve().parents[1] / value
                for trainer in learned_payload.get("trainers") or []
                for value in trainer.get("lines") or []
            ]
            if learned_lines and all(path.is_file() for path in learned_lines):
                active_playbook = learned
        except (OSError, TypeError, json.JSONDecodeError):
            active_playbook = playbook
    expected = [trainer.trainer_name for trainer in trainers]
    replay = 1
    repair_attempts = 0
    while replay <= 2:
        stem = f"{run_id}-replay-{replay}"
        video = _GAUNTLET_RUNS_DIR / f"{stem}-normal-speed.mp4"
        log = _GAUNTLET_RUNS_DIR / f"{stem}-log.json"
        post_state = _GAUNTLET_RUNS_DIR / f"{stem}-final.ss0"
        command = [
            sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "record_full_connected_gauntlet.py"),
            "--rom", str(rom), "--state", str(state), "--playbook", str(active_playbook),
            "--output", str(video), "--post-state", str(post_state), "--log", str(log),
        ]
        completed = subprocess.run(
            command, cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, timeout=4 * 60 * 60,
        )
        if completed.returncode != 0:
            failure = (completed.stderr or completed.stdout)[-4000:]
            repair_attempts += 1
            repaired = (
                _repair_gauntlet_battle(rom, active_playbook, failure, run_id, repair_attempts)
                if repair_attempts <= 5 else None
            )
            if repaired is None:
                raise RuntimeError(f"Cartridge replay {replay} failed and automatic repair could not recover: {failure[-1200:]}")
            active_playbook, repair_log = repaired
            repairs.append(repair_log)
            for artifact in artifacts:
                for value in artifact.values():
                    Path(value).unlink(missing_ok=True)
            artifacts.clear()
            replay = 1
            _GAUNTLET_PROGRESS.update(
                running=True, stage="adaptive-repair",
                phase=f"Learned a new {repair_log['trainer']} line; restarting uncut proof",
                pct=58.0, completed_replays=0, total_replays=2, video_ready=False,
            )
            continue
        payload = json.loads(log.read_text(encoding="utf-8"))
        replay_safety = _cartridge_replay_safety(payload)
        actual = [str(value) for value in payload.get("trainers") or []]
        if not (
            payload.get("proof_complete") is True
            and payload.get("uncut") is True
            and int(payload.get("savestate_loads_after_start", -1)) == 0
            and payload.get("graveyard_used") is False
            and replay_safety["deathless"] is True
            and len(actual) == len(expected)
            and all(want.casefold() in got.casefold() or got.casefold() in want.casefold()
                    for want, got in zip(expected, actual))
            and video.is_file() and video.stat().st_size > 0
        ):
            raise RuntimeError(f"Cartridge replay {replay} did not satisfy the Nuzlocke proof gate")
        artifacts.append({"video": video, "log": log, "post_state": post_state, "safety": replay_safety})
        _GAUNTLET_PROGRESS.update(
            running=True, stage="cartridge-proof",
            phase=f"Completed in-game replay {replay} of 2",
            pct=58.0 + replay * 20.5, completed_replays=replay,
            total_replays=2, video_ready=replay == 2,
        )
        replay += 1

    # A risky status/AI branch gets its own full normal-speed cartridge video. The
    # contingency starts from a deterministic pre-battle RNG checkpoint and uses a
    # separately searched continuation; it is evidence, not a slideshow or annotation.
    for index, reference in enumerate(base_payload.get("contingency_playbooks") or [], 1):
        contingency_path = (Path(__file__).resolve().parents[1] / reference).resolve()
        contingency = json.loads(contingency_path.read_text(encoding="utf-8"))
        contingency_state = (
            Path(__file__).resolve().parents[1] / str(contingency["starting_state"])
        ).resolve()
        stem = f"{run_id}-contingency-{index}"
        video = _GAUNTLET_RUNS_DIR / f"{stem}-normal-speed.mp4"
        log = _GAUNTLET_RUNS_DIR / f"{stem}-log.json"
        post_state = _GAUNTLET_RUNS_DIR / f"{stem}-final.ss0"
        _GAUNTLET_PROGRESS.update(
            running=True, stage="contingency-proof",
            phase=f"Recording backup plan {index}", pct=96.0,
            completed_replays=2, total_replays=2, video_ready=False,
        )
        completed = subprocess.run([
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "tools" / "record_full_connected_gauntlet.py"),
            "--rom", str(rom), "--state", str(contingency_state),
            "--playbook", str(contingency_path), "--output", str(video),
            "--post-state", str(post_state), "--log", str(log),
        ], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=4 * 60 * 60)
        if completed.returncode != 0:
            raise RuntimeError(
                f"Contingency replay {index} failed: {(completed.stderr or completed.stdout)[-1200:]}"
            )
        payload = json.loads(log.read_text(encoding="utf-8"))
        if not payload.get("proof_complete") or not video.is_file() or video.stat().st_size <= 0:
            raise RuntimeError(f"Contingency replay {index} did not satisfy the proof gate")
        contingency_artifacts.append({
            "video": video, "log": log, "post_state": post_state,
            "label": str(contingency.get("label") or f"Backup plan {index}"),
        })
    return {
        "proof_complete": True,
        "emulator_result": "connected-cartridge-verified",
        "emulator_validation": {
            "fixed_replays": 2, "fixed_deathless_wins": 2,
            "sampled_replays": 0, "sampled_deathless_wins": 0,
            "uncut_route_replays": 2,
            "minimum_player_hp": min(int(item["safety"]["minimum_player_hp"]) for item in artifacts),
            "replay_summaries": [item["safety"] for item in artifacts],
        },
        "videos": [
            {
                "kind": "approved_connected_gauntlet" if index == 0 else "confidence_replay",
                "label": "Approved uncut cartridge replay" if index == 0 else "Full confidence replay 2",
                "video_url": f"/gauntlet-artifacts/{item['video'].name}", "video_ready": True,
            }
            for index, item in enumerate(artifacts)
        ] + [
            {
                "kind": "contingency_replay",
                "label": item["label"],
                "video_url": f"/gauntlet-artifacts/{item['video'].name}",
                "video_ready": True,
            }
            for item in contingency_artifacts
        ],
        "emulator_artifacts": {
            "playbook": str(playbook.relative_to(Path(__file__).resolve().parents[1])),
            "active_playbook": str(active_playbook),
            "automatic_repairs": repairs,
            "logs": [str(item["log"]) for item in artifacts],
            "post_states": [str(item["post_state"]) for item in artifacts],
            "contingency_logs": [str(item["log"]) for item in contingency_artifacts],
            "contingency_post_states": [str(item["post_state"]) for item in contingency_artifacts],
        },
    }


def _canonical_connected_proof() -> dict[str, Any] | None:
    """Return the approved cartridge artifact after validating both recorder logs."""
    expected_count = 8
    log_path = _GAUNTLET_RUNS_DIR / "corgi-through-chelle-uncut-log.json"
    video_path = _GAUNTLET_RUNS_DIR / "corgi-through-chelle-uncut-normal-speed.mp4"
    repeat_log_path = _GAUNTLET_RUNS_DIR / "corgi-through-chelle-repeat-2-log.json"
    repeat_video_path = _GAUNTLET_RUNS_DIR / "corgi-through-chelle-repeat-2-normal-speed.mp4"
    if not all(path.is_file() and path.stat().st_size > 0 for path in (
        log_path, video_path, repeat_log_path, repeat_video_path
    )):
        return None
    try:
        log = json.loads(log_path.read_text(encoding="utf-8"))
        repeat_log = json.loads(repeat_log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not (
        log.get("proof_complete") is True
        and log.get("uncut") is True
        and int(log.get("savestate_loads_after_start", -1)) == 0
        and log.get("graveyard_used") is False
        and len(log.get("trainers") or []) == expected_count
        and repeat_log.get("proof_complete") is True
        and repeat_log.get("uncut") is True
        and int(repeat_log.get("savestate_loads_after_start", -1)) == 0
        and repeat_log.get("graveyard_used") is False
    ):
        return None
    return {
        "proof_complete": True,
        "emulator_result": "connected-cartridge-verified",
        "emulator_validation": {
            "fixed_replays": 2,
            "fixed_deathless_wins": 2,
            "sampled_replays": 0,
            "sampled_deathless_wins": 0,
            "uncut_route_replays": 2,
        },
        "videos": [
            {
                "kind": "approved_connected_gauntlet",
                "label": "Approved uncut Corgi through Chelle — Centers and preparation included",
                "video_url": "/gauntlet-artifacts/corgi-through-chelle-uncut-normal-speed.mp4",
                "video_ready": True,
            },
            {
                "kind": "confidence_replay",
                "label": "Full confidence replay 2 — same route, no cuts",
                "video_url": "/gauntlet-artifacts/corgi-through-chelle-repeat-2-normal-speed.mp4",
                "video_ready": True,
            },
        ],
        "emulator_artifacts": {
            "log": "output/gauntlet_runs/corgi-through-chelle-uncut-log.json",
            "post_state": "output/gauntlet_runs/corgi-through-chelle-final.ss0",
            "repeat_log": "output/gauntlet_runs/corgi-through-chelle-repeat-2-log.json",
        },
    }


def _connected_corgi_chelle_proof(trainers: list[TrainerBattle]) -> dict[str, Any] | None:
    """Return proof only for the exact requested Corgi-through-Chelle route."""
    expected = [
        "Breeder Corgi", "Brandi", "Luna", "Dylan", "Maria", "Isaac",
        "Anna", "Chelle",
    ]
    requested = [trainer.trainer_name for trainer in trainers]
    if len(requested) != len(expected) or any(
        token.casefold() not in name.casefold()
        for token, name in zip(expected, requested)
    ):
        return None
    return _canonical_connected_proof()


def _canonical_connected_proof_record() -> dict[str, Any] | None:
    """Expose the recorder's permanent log through the normal Gauntlet log UI."""
    proof = _canonical_connected_proof()
    log_path = _GAUNTLET_RUNS_DIR / "corgi-through-chelle-uncut-log.json"
    if proof is None or not log_path.is_file():
        return None
    try:
        log = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    trainers = list(log.get("trainers") or [])
    result = {
        "result": "route-complete",
        "completed": len(trainers),
        "queued": len(trainers),
        "stopped_reason": None,
        "fights": log.get("fights") or [],
        "center_heals": log.get("center_heals") or [],
        "preparations": log.get("preparations") or [],
        "nuzlocke_rules": {
            "graveyard_box": 14,
            "graveyard_excluded": True,
            "graveyard_items_excluded": True,
        },
        **proof,
    }
    return {
        "id": "connected-corgi-chelle-proof",
        "engine_version": _GAUNTLET_ENGINE_VERSION,
        "created_at": log.get("created_at"),
        "request": {
            "route": trainers,
            "heal_between": True,
            "optimize_between_fights": True,
            "proof_replays": 2,
        },
        "result": result,
        "recorder_log": log,
    }


def _gauntlet_run_path(run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise HTTPException(status_code=400, detail="Invalid gauntlet run id")
    return _GAUNTLET_RUNS_DIR / f"{run_id}.json"


def _save_gauntlet_run(request: GauntletSimRequest, result: dict[str, Any]) -> dict[str, Any]:
    """Persist the full route and its mandatory complementary MP4 replay."""
    _GAUNTLET_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc)
    payload = _request_payload(request)
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:10]
    run_id = f"{created_at.strftime('%Y%m%dT%H%M%S%f')}-{digest}"
    video_path = _GAUNTLET_RUNS_DIR / f"{run_id}-planner-replay.mp4"
    video = render_run_video(result, video_path, kind="gauntlet")
    video["video_url"] = f"/gauntlet-artifacts/{video_path.name}"
    if not video.get("video_ready") or not video_path.is_file() or video_path.stat().st_size <= 0:
        raise RuntimeError("Gauntlet finished without its required MP4")
    result.setdefault("videos", []).append(video)
    result["video_ready"] = any(item.get("video_ready") for item in result["videos"])
    result["evidence_policy"] = "video-and-text-required"
    record = {
        "id": run_id,
        "engine_version": _GAUNTLET_ENGINE_VERSION,
        "created_at": created_at.isoformat(),
        "request": payload,
        "result": result,
        "game_mode": payload.get("game_mode", "run-and-bun"),
    }
    path = _gauntlet_run_path(run_id)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)
    return {
        "id": run_id,
        "created_at": record["created_at"],
        "result": result.get("result"),
        "completed": result.get("completed", 0),
        "queued": result.get("queued", 0),
        "stopped_reason": result.get("stopped_reason"),
        "game_mode": record["game_mode"],
    }


def _cached_gauntlet_run(request: GauntletSimRequest) -> dict[str, Any] | None:
    """Return the exact prior result for an identical route request."""
    # A cartridge-backed request is an instruction to prove the route again.
    # Reusing yesterday's video would violate the UI's "always run it in game"
    # contract, even when the planner inputs happen to be identical.
    if request.rom and request.pc_state:
        return None
    if not request.reuse_saved or not _GAUNTLET_RUNS_DIR.is_dir():
        return None
    wanted = _request_payload(request)
    for path in sorted(_GAUNTLET_RUNS_DIR.glob("*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("engine_version") != _GAUNTLET_ENGINE_VERSION or record.get("request") != wanted:
            continue
        result = copy.deepcopy(record.get("result") or {})
        # A calculator-only route is not a finished gauntlet. Re-run it so the
        # cartridge/video gate can be attempted instead of serving a false 100%.
        if not result.get("proof_complete"):
            continue
        result["saved_run"] = {
            "id": record.get("id"), "created_at": record.get("created_at"),
            "result": result.get("result"), "completed": result.get("completed", 0),
            "queued": result.get("queued", 0), "stopped_reason": result.get("stopped_reason"),
        }
        result["cache_hit"] = True
        return result
    return None


def _request_payload(request: BaseModel) -> dict[str, Any]:
    return request.model_dump() if hasattr(request, "model_dump") else request.dict()


def _sim_run_path(run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise HTTPException(status_code=400, detail="Invalid simulator run id")
    return _SIM_RUNS_DIR / f"{run_id}.json"


def _sim_run_summary(record: dict[str, Any]) -> dict[str, Any]:
    summary = {key: record.get(key) for key in (
        "id", "created_at", "trainer", "status", "won", "deathless", "player_faints",
        "turn_count", "video_url", "video_ready", "error", "final_player_hp", "final_enemy_hp",
        "strategy_status", "normal_clear_rate", "confidence_replays",
        "videos", "team", "comparison", "critical_analysis", "proof_complete",
        "output_state",
    )}
    summary["game_mode"] = normalize_game_mode((record.get("request") or {}).get("game_mode"))
    return summary


def _actions_from_validation(payload: dict[str, Any]) -> list[tuple[Action, ...]]:
    return [
        tuple(Action(**action) for action in turn)
        for turn in payload.get("actions", [])
    ]


def _solve_crit_diversion(
    request: SolveRequest,
    run_id: str,
    line: list[tuple[Action, ...]],
    diversion: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Checkpoint an observed divergent turn and search a fresh continuation from it."""
    if not diversion:
        return None
    turn = max(1, min(len(line), int(diversion.get("turn") or 1)))
    offset = int(diversion.get("rng_frames") or 0)
    checkpoint = _SIM_CHECKPOINTS_DIR / f"{run_id}-crit-turn-{turn}.ss0"
    pool = MGBAPool(request.rom, request.state, 1, game_mode=request.game_mode)
    try:
        outcomes = pool.run_trials([TrialSpec(
            trial_id=900_000 + turn,
            actions=list(line[:turn]),
            rng_advance_frames=offset,
            max_turns=turn,
            capture_screens=False,
            output_state_path=str(checkpoint),
        )])
    finally:
        pool.shutdown()
    if (
        not outcomes or outcomes[0].error or outcomes[0].final_state.battle_over
        or outcomes[0].player_fainted_count > 0 or not checkpoint.is_file()
    ):
        checkpoint.unlink(missing_ok=True)
        return None

    branch = MCTS(
        request.rom, str(checkpoint),
        pool_size=max(1, min(request.instances, 4)),
        max_turns=max(1, request.turns - turn),
        trials_per_node=max(1, request.trials_per_node),
        final_line_trials=max(64, min(128, request.final_line_trials // 4)),
        final_line_candidates=max(2, min(4, request.final_line_candidates)),
    )
    try:
        branch_result = branch.search(iterations=max(30, min(80, request.iterations // 2)))
    finally:
        branch.shutdown()
    branch_validation = max(
        branch_result.validated_lines,
        key=lambda item: (
            item.get("clear_rate", 0),
            -float(item.get("faint_rate", 1)),
            -int(item.get("errors", 1)),
        ),
        default={},
    )
    branch_line = _actions_from_validation(branch_validation) or branch_result.recommended_line
    if not branch_line:
        return None
    return {
        "line": list(line[:turn]) + list(branch_line),
        "checkpoint": str(checkpoint),
        "turn": turn,
        "rng_frames": offset,
        "continuation_clear_rate": float(branch_validation.get("clear_rate", 0)),
        "continuation_faint_rate": float(branch_validation.get("faint_rate", 1)),
        "continuation_errors": int(branch_validation.get("errors", 1)),
        "continuation_trials": int(branch_validation.get("trials", 0)),
        "continuation": branch_validation.get("line", []),
    }


def _record_completed_simulator(request: SolveRequest, result: SearchResult, state: BattleState | None) -> dict[str, Any]:
    created = datetime.now(timezone.utc)
    run_id = created.strftime("%Y%m%dT%H%M%S%f")
    discovery_path = _SIM_RUNS_DIR / f"{run_id}-a.mp4"
    approved_path = _SIM_RUNS_DIR / f"{run_id}-b.mp4"
    approved_state_path = _SIM_CHECKPOINTS_DIR / f"{run_id}-post-battle.ss0"
    diversion_path = _SIM_RUNS_DIR / f"{run_id}-crit-branch.mp4"
    split_path = _SIM_RUNS_DIR / f"{run_id}-crit-split.mp4"
    match = (
        DamageCalculator(game_mode=request.game_mode).matched_trainer(state)
        if state is not None else None
    )
    trainer = match.battle.trainer_name if match else "Unrecognized trainer"
    best_validation = max(
        result.validated_lines,
        key=lambda item: (
            item.get("clear_rate", 0) >= 0.90
            and float(item.get("faint_rate", 1)) == 0.0
            and int(item.get("errors", 1)) == 0,
            bool((item.get("critical_analysis") or {}).get("critical_safe")),
            -float((item.get("critical_analysis") or {}).get("critical_failure_rate", 1)),
            item.get("clear_rate", 0), -item.get("faint_rate", 1),
        ),
        default={},
    )
    confidence_replays = int(best_validation.get("trials", 0))
    normal_clear_rate = float(best_validation.get("clear_rate", 0))
    base_ordinary_line = (
        confidence_replays >= min(10, request.final_line_trials)
        and normal_clear_rate >= 0.90
        and float(best_validation.get("faint_rate", 1)) == 0.0
        and int(best_validation.get("errors", 1)) == 0
    )
    validated_actions = _actions_from_validation(best_validation) or result.recommended_line
    winning_offsets = list(best_validation.get("deathless_winning_rng_frames", []))
    discovery_offset = int(best_validation.get("winning_rng_frames") or 0)
    critical_analysis = best_validation.get("critical_analysis") or {}
    approved_offset = int(critical_analysis.get("baseline_rng_frames") or (winning_offsets[-1] if winning_offsets else discovery_offset))
    shared_approved_replay = False
    diversion_offset = critical_analysis.get("diversion_rng_frames")
    adaptive_diversion = None
    adaptive_diversion_error = None
    if base_ordinary_line and diversion_offset is not None:
        try:
            adaptive_diversion = _solve_crit_diversion(
                request, run_id, validated_actions,
                critical_analysis.get("diversion"),
            )
        except Exception as exc:
            adaptive_diversion_error = str(exc)
    adaptive_clear_rate = float((adaptive_diversion or {}).get("continuation_clear_rate", 0))
    adaptive_faint_rate = float((adaptive_diversion or {}).get("continuation_faint_rate", 1))
    adaptive_errors = int((adaptive_diversion or {}).get("continuation_errors", 1))
    adaptive_trials = int((adaptive_diversion or {}).get("continuation_trials", 0))
    conditionally_crit_safe = bool(
        adaptive_diversion
        and adaptive_clear_rate >= 0.90
        and adaptive_faint_rate == 0.0
        and adaptive_errors == 0
        and adaptive_trials >= 64
    )
    critical_safe = bool(critical_analysis.get("critical_safe", False))
    ordinary_line = base_ordinary_line and (critical_safe or conditionally_crit_safe)
    shared_approved_replay = ordinary_line and approved_offset == discovery_offset
    critical_analysis = {
        **critical_analysis,
        "conditionally_safe": conditionally_crit_safe,
        "adaptive_clear_rate": adaptive_clear_rate if adaptive_diversion else None,
        "adaptive_faint_rate": adaptive_faint_rate if adaptive_diversion else None,
        "adaptive_errors": adaptive_errors if adaptive_diversion else None,
        "adaptive_trials": adaptive_trials if adaptive_diversion else None,
        "adaptive_pivot": adaptive_diversion,
    }
    team = [
        name for index, name in enumerate(state.player_names if state else [])
        if index < len(state.player_max_hp) and state.player_max_hp[index] > 0
    ]
    record: dict[str, Any] = {
        "id": run_id, "created_at": created.isoformat(), "trainer": trainer,
        "request": _request_payload(request), "recommended_line": result.to_dict()["recommended_line"],
        "search": {
            "win_probability": result.win_probability, "faint_probability": result.faint_probability,
            "trials": result.total_trials_run, "seconds": result.search_time_seconds,
        },
        "status": "recording", "won": False, "deathless": False, "player_faints": 0,
        "turn_count": 0, "video_url": f"/sim-videos/{run_id}-a.mp4", "video_ready": False,
        "error": None,
        "strategy_status": (
            "conditionally_approved" if ordinary_line and not critical_safe else
            "approved" if ordinary_line else "team_change_required"
        ),
        "normal_clear_rate": normal_clear_rate,
        "confidence_replays": confidence_replays,
        "validation": best_validation,
        "critical_analysis": critical_analysis,
        "adaptive_diversion_error": adaptive_diversion_error,
        "team": team,
        "videos": [],
    }
    try:
        _sim_proof_update(
            running=True, phase="Recording the full discovery replay in game",
            pct=88.0, video_ready=False, verified=False,
        )
        capture = record_simulator_line(
            request.rom, request.state, validated_actions, discovery_path,
            rng_pre_roll_frames=discovery_offset,
            output_state_path=approved_state_path if shared_approved_replay else None,
        )
        record.update(capture)
        record["turn_count"] = len(capture.get("turns", []))
        record["video_ready"] = discovery_path.is_file() and discovery_path.stat().st_size > 0
        record["videos"].append({
            "kind": "approved" if shared_approved_replay else "discovery",
            "label": "Approved ordinary line" if shared_approved_replay else "A — discovery team / lucky line",
            "video_url": record["video_url"], "video_ready": record["video_ready"],
            "rng_pre_roll_frames": discovery_offset, **capture,
        })
        if capture.get("output_state"):
            record["output_state"] = capture["output_state"]
        if ordinary_line and not shared_approved_replay:
            _sim_proof_update(
                running=True, phase="Recording the approved ordinary line in game",
                pct=94.0, video_ready=record["video_ready"], verified=False,
            )
            approved_capture = record_simulator_line(
                request.rom, request.state, validated_actions, approved_path,
                instance_id=92, rng_pre_roll_frames=approved_offset,
                output_state_path=approved_state_path,
            )
            record["videos"].append({
                "kind": "approved", "label": (
                    "B — approved base line with crit pivot"
                    if conditionally_crit_safe and not critical_safe
                    else "B — approved ordinary line"
                ),
                "video_url": f"/sim-videos/{run_id}-b.mp4",
                "video_ready": approved_path.is_file() and approved_path.stat().st_size > 0,
                "rng_pre_roll_frames": approved_offset, **approved_capture,
            })
            if approved_capture.get("output_state"):
                record["output_state"] = approved_capture["output_state"]
            if diversion_offset is not None:
                _sim_proof_update(
                    running=True, phase="Recording the real-game crit diversion",
                    pct=97.0, video_ready=record["video_ready"], verified=False,
                )
                diversion_actions = adaptive_diversion["line"] if adaptive_diversion else validated_actions
                diversion_capture = record_simulator_line(
                    request.rom, request.state, diversion_actions, diversion_path,
                    instance_id=93, rng_pre_roll_frames=int(diversion_offset),
                )
                compose_split_screen(approved_path, diversion_path, split_path)
                record["videos"].append({
                    "kind": "crit_diversion",
                    "label": "CRIT-LIKE DIVERSION — BASELINE / ADAPTIVE BRANCH",
                    "video_url": f"/sim-videos/{run_id}-crit-split.mp4",
                    "video_ready": split_path.is_file() and split_path.stat().st_size > 0,
                    "rng_pre_roll_frames": int(diversion_offset),
                    "diversion": critical_analysis.get("diversion"),
                    "adaptive_pivot": adaptive_diversion,
                    **diversion_capture,
                })
        elif ordinary_line and diversion_offset is not None:
            _sim_proof_update(
                running=True, phase="Recording the real-game crit diversion",
                pct=97.0, video_ready=record["video_ready"], verified=False,
            )
            diversion_actions = adaptive_diversion["line"] if adaptive_diversion else validated_actions
            diversion_capture = record_simulator_line(
                request.rom, request.state, diversion_actions, diversion_path,
                instance_id=93, rng_pre_roll_frames=int(diversion_offset),
            )
            compose_split_screen(discovery_path, diversion_path, split_path)
            record["videos"].append({
                "kind": "crit_diversion",
                "label": "CRIT-LIKE DIVERSION — BASELINE / ADAPTIVE BRANCH",
                "video_url": f"/sim-videos/{run_id}-crit-split.mp4",
                "video_ready": split_path.is_file() and split_path.stat().st_size > 0,
                "rng_pre_roll_frames": int(diversion_offset),
                "diversion": critical_analysis.get("diversion"),
                "adaptive_pivot": adaptive_diversion,
                **diversion_capture,
            })
    except Exception as exc:
        record.update({"status": "video_failed", "error": str(exc), "video_ready": False})
        discovery_path.unlink(missing_ok=True)
        approved_path.unlink(missing_ok=True)
        diversion_path.unlink(missing_ok=True)
        split_path.unlink(missing_ok=True)
        # Preserve an honest, playable result even when raw gameplay capture fails.
        # This is labeled as a search report and never satisfies cartridge proof.
        fallback_path = _SIM_RUNS_DIR / f"{run_id}-search-report.mp4"
        fallback_result = {
            "trainer": trainer,
            "location": "Local mGBA checkpoint",
            "result": "gameplay-recording-failed",
            "confidence": float(result.win_probability or 0.0),
            "team": [{"name": name, "species": name} for name in team],
            "turns": [
                {"turn": index, "action": str(action)}
                for index, action in enumerate(validated_actions, 1)
            ],
        }
        try:
            fallback_video = render_run_video(fallback_result, fallback_path, kind="simulator")
            fallback_video.update({
                "kind": "search-report",
                "label": "Search report — gameplay recording unavailable",
                "video_url": f"/sim-videos/{fallback_path.name}",
                "gameplay": False,
                "proof_eligible": False,
                "capture_error": str(exc),
            })
            record["videos"].append(fallback_video)
            record["video_ready"] = True
            record["fallback_video"] = True
        except Exception as fallback_exc:
            fallback_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Gameplay recording failed ({exc}) and the required result video also failed ({fallback_exc})"
            ) from fallback_exc
    # When a changed team finally passes, pair it with the most recent rejected
    # team's discovery replay so the app presents the requested A/B evidence.
    if ordinary_line and record.get("videos"):
        for old_path in sorted(_SIM_RUNS_DIR.glob("*.json"), reverse=True):
            try:
                old = json.loads(old_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                old.get("strategy_status") == "team_change_required"
                and old.get("team") != team
                and not old.get("comparison")
                and old.get("videos")
            ):
                approved_video = next((item for item in record["videos"] if item.get("kind") == "approved"), record["videos"][-1])
                comparison = {
                    "original_run_id": old.get("id"), "original_team": old.get("team", []),
                    "original_video_url": old["videos"][0].get("video_url"),
                    "approved_run_id": run_id, "approved_team": team,
                    "approved_video_url": approved_video.get("video_url"),
                }
                record["comparison"] = comparison
                old["comparison"] = comparison
                old_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
                break
    path = _sim_run_path(run_id)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)
    approved_video = next(
        (item for item in record.get("videos", []) if item.get("kind") == "approved"),
        None,
    )
    proof_complete = bool(
        ordinary_line
        and confidence_replays >= 2
        and approved_video
        and approved_video.get("video_ready")
        and approved_video.get("won")
        and approved_video.get("deathless")
    )
    record["proof_complete"] = proof_complete
    record["status"] = "verified" if proof_complete else (
        "video_failed" if record.get("error") else "game_proof_rejected"
    )
    # Persist the final proof gate, not the earlier recording placeholder.
    temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)
    any_video_ready = any(item.get("video_ready") for item in record.get("videos", []))
    _sim_proof_update(
        running=False,
        phase="Cartridge proof complete" if proof_complete else "Video saved; cartridge proof did not pass",
        pct=100.0 if any_video_ready else 99.0,
        video_ready=any_video_ready,
        verified=proof_complete,
    )
    return record


@app.get("/api/simulator/runs")
async def api_simulator_runs() -> dict[str, Any]:
    runs = []
    for path in sorted(_SIM_RUNS_DIR.glob("*.json"), reverse=True):
        try:
            runs.append(_sim_run_summary(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    # Submission/demo views should lead with evidence that passed the cartridge
    # proof gate, not whichever diagnostic experiment happened most recently.
    runs.sort(
        key=lambda run: (
            bool(run.get("proof_complete")),
            int(run.get("completed") or 0),
            str(run.get("created_at") or ""),
        ),
        reverse=True,
    )
    return {"runs": runs[:100]}


@app.post("/api/simulator/open-folder")
async def api_open_simulator_folder() -> dict[str, str]:
    """Reveal the fixed simulator output directory in the desktop file manager."""
    _SIM_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        command = ["explorer", str(_SIM_RUNS_DIR)]
    elif shutil.which("open"):
        command = ["open", str(_SIM_RUNS_DIR)]
    elif shutil.which("xdg-open"):
        command = ["xdg-open", str(_SIM_RUNS_DIR)]
    else:
        raise HTTPException(status_code=501, detail="No desktop folder opener is available.")
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open recordings folder: {exc}") from exc
    return {"status": "opened", "path": str(_SIM_RUNS_DIR)}


@app.get("/api/simulator/runs/{run_id}")
async def api_simulator_run(run_id: str) -> dict[str, Any]:
    path = _sim_run_path(run_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Simulator run not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _saved_run_path(run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise HTTPException(status_code=400, detail="Invalid saved run id")
    return _CALC_RUNS_DIR / f"{run_id}.json"


def _save_calc_run(request: CalcSimRequest, result: dict[str, Any]) -> dict[str, Any]:
    """Persist a line-finder result together with its mandatory MP4 replay."""
    _CALC_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).isoformat()
    payload = _request_payload(request)
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:10]
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}-{digest}"
    video_path = _CALC_RUNS_DIR / f"{run_id}-turn-replay.mp4"
    video = render_run_video(result, video_path, kind="simulator")
    video["video_url"] = f"/calc-artifacts/{video_path.name}"
    if not video.get("video_ready") or not video_path.is_file() or video_path.stat().st_size <= 0:
        raise RuntimeError("Simulator finished without its required MP4")
    result.setdefault("videos", []).append(video)
    result["video_ready"] = any(item.get("video_ready") for item in result["videos"])
    result["evidence_policy"] = "video-and-text-required"
    record = {
        "id": run_id,
        "engine_version": _CALC_ENGINE_VERSION,
        "created_at": created_at,
        "trainer": result.get("trainer", "Trainer"),
        "location": result.get("location", ""),
        "result_label": result.get("result", "complete"),
        "confidence": result.get("confidence"),
        "team_names": [m.get("name") or m.get("species") for m in result.get("team", [])],
        "request": payload,
        "result": result,
        "game_mode": payload.get("game_mode", "run-and-bun"),
    }
    path = _saved_run_path(run_id)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)
    return {key: record[key] for key in (
        "id", "created_at", "trainer", "location", "result_label", "confidence", "team_names", "game_mode"
    )}


def _saved_run_summary(record: dict[str, Any]) -> dict[str, Any]:
    summary = {key: record.get(key) for key in (
        "id", "created_at", "trainer", "location", "result_label", "confidence", "team_names", "game_mode"
    )}
    summary["game_mode"] = summary.get("game_mode") or (record.get("request") or {}).get("game_mode", "run-and-bun")
    summary["videos"] = (record.get("result") or {}).get("videos") or []
    summary["video_ready"] = any(video.get("video_ready") for video in summary["videos"])
    return summary


@app.get("/api/calc/runs")
async def api_calc_runs() -> dict[str, Any]:
    _CALC_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    for path in sorted(_CALC_RUNS_DIR.glob("*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("engine_version") != _CALC_ENGINE_VERSION:
                continue
            runs.append(_saved_run_summary(record))
        except (OSError, json.JSONDecodeError):
            continue
    return {"runs": runs[:100]}


@app.get("/api/calc/runs/{run_id}")
async def api_calc_run(run_id: str) -> dict[str, Any]:
    path = _saved_run_path(run_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Saved run not found")
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("engine_version") != _CALC_ENGINE_VERSION:
        raise HTTPException(status_code=409, detail="Saved run uses older battle logic; run it once again to refresh.")
    return record


@app.delete("/api/calc/runs/{run_id}")
async def api_delete_calc_run(run_id: str) -> dict[str, str]:
    path = _saved_run_path(run_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Saved run not found")
    path.unlink()
    return {"status": "deleted"}


class CoachStepRequest(BaseModel):
    """One step of the interactive co-pilot: given the live battle state, what to do now."""

    trainer_id: int
    game_mode: str = "run-and-bun"
    imports: str = ""
    crit_safe: bool = True
    # Current state. player_active = None means "recommend the lead" (battle start).
    player_active: int | None = None
    player_hp: int | None = None
    player_status: str | None = None
    enemy_active: int = 0
    enemy_hp: int | None = None
    enemy_status: str | None = None
    player_consumed_items: list[bool] = Field(default_factory=list)
    enemy_consumed_items: list[bool] = Field(default_factory=list)


_EMBEDDED_TRAINER_CATALOG: str | None = None
_EMBEDDED_EMERALD_DATA: str | None = None


def _emerald_checkpoint_library() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "data" / "emerald_checkpoint_library.json"
    if not path.is_file():
        return {"entries": [], "stats": {"checkpoints": 0, "unique_trainers": 0, "boss_checkpoints": 0}}
    return json.loads(path.read_text(encoding="utf-8"))


def _local_testing_config() -> dict[str, str]:
    """Load private local paths without committing them to the browser bundle."""
    env_file = Path(__file__).resolve().parents[1] / ".env.local"
    file_values: dict[str, str] = {}
    if env_file.is_file():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            file_values[key.strip()] = value.strip().strip('"').strip("'")
    keys = {
        "rom": "POKEBATTLE_ROM",
        "state": "POKEBATTLE_STATE",
        "battle_state": "POKEBATTLE_BATTLE_STATE",
        "pc_state": "POKEBATTLE_PC_STATE",
        "emerald_rom": "POKEBATTLE_EMERALD_ROM",
        "emerald_state": "POKEBATTLE_EMERALD_STATE",
        "emerald_battle_state": "POKEBATTLE_EMERALD_BATTLE_STATE",
        "emerald_pc_state": "POKEBATTLE_EMERALD_PC_STATE",
    }
    values: dict[str, str] = {}
    for field, env_key in keys.items():
        candidate = os.environ.get(env_key) or file_values.get(env_key, "")
        if candidate:
            path = Path(candidate).expanduser().resolve()
            values[field] = str(path) if path.is_file() else ""
        else:
            values[field] = ""
    return values


@app.get("/")
async def index() -> HTMLResponse:
    """Serve the trainer catalog with the app instead of fetching it after render."""
    global _EMBEDDED_TRAINER_CATALOG, _EMBEDDED_EMERALD_DATA
    if _EMBEDDED_TRAINER_CATALOG is None:
        calculator = DamageCalculator()
        catalog = [
            _trainer_summary(index, battle, calculator)
            for index, battle in enumerate(load_trainer_battles())
        ]
        # Prevent trainer text from ever terminating the inline script element.
        _EMBEDDED_TRAINER_CATALOG = json.dumps(catalog, separators=(",", ":")).replace("<", "\\u003c")
    if _EMBEDDED_EMERALD_DATA is None:
        emerald_path = Path(__file__).resolve().parents[1] / "data" / "emerald_trainers.json"
        emerald_data = json.loads(emerald_path.read_text(encoding="utf-8"))
        emerald_data["checkpoint_library"] = _emerald_checkpoint_library()
        _EMBEDDED_EMERALD_DATA = json.dumps(emerald_data, separators=(",", ":")).replace("<", "\\u003c")
    html = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    local_config = json.dumps(_local_testing_config(), separators=(",", ":")).replace("<", "\\u003c")
    html = html.replace("__TRAINER_CATALOG_JSON__", _EMBEDDED_TRAINER_CATALOG)
    html = html.replace("__EMERALD_DATA_JSON__", _EMBEDDED_EMERALD_DATA)
    return HTMLResponse(html.replace("__LOCAL_TESTING_CONFIG_JSON__", local_config))


@app.get("/api/games/emerald/trainers")
async def api_emerald_trainers() -> dict[str, Any]:
    """Return the source-grounded Pokémon Emerald trainer atlas."""
    path = Path(__file__).resolve().parents[1] / "data" / "emerald_trainers.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["checkpoint_library"] = _emerald_checkpoint_library()
    return payload


@app.get("/api/games/emerald/local-save")
async def api_emerald_local_save() -> dict[str, Any]:
    """Inspect the configured user-owned Emerald checkpoint without copying its ROM."""
    local = _local_testing_config()
    rom = local.get("emerald_rom", "")
    state = local.get("emerald_state", "")
    if not rom or not state:
        raise HTTPException(status_code=404, detail="Add a local Emerald ROM and checkpoint first.")
    calculator = DamageCalculator(game_mode="pokemon-emerald")
    report = await asyncio.to_thread(scan_pc_boxes, rom, state, calculator=calculator)
    party = [
        {
            "name": mon.name,
            "species": mon.species,
            "level": mon.level,
            "hp": mon.hp,
            "max_hp": mon.max_hp,
            "moves": list(mon.moves),
            "nature": mon.nature,
            "ivs": mon.ivs or {},
            "evs": mon.evs or {},
            "item": mon.held_item,
        }
        for mon in report.party
    ]
    imports = "\n\n".join(
        "\n".join([
            f"{mon.display_name}{f' @ {mon.held_item}' if mon.held_item else ''}",
            f"Level: {mon.level}",
            f"{mon.nature} Nature" if mon.nature else "",
            "IVs: " + " / ".join(f"{value} {stat.upper()}" for stat, value in (mon.ivs or {}).items()),
            "EVs: " + " / ".join(f"{value} {stat.upper()}" for stat, value in (mon.evs or {}).items()),
            *[f"- {move}" for move in mon.moves],
        ]).strip()
        for mon in report.party
    )
    return {
        "rom": rom, "state": state, "party": party, "imports": imports,
        "party_count": len(party), "ready": bool(party),
        "note": "The ROM remains in its original local folder; only decoded party data is shown here.",
    }


@app.get("/api/games/emerald/league-proof")
async def api_emerald_league_proof() -> dict[str, Any]:
    """Return the committed ROM-free Elite Four judge validation, when built."""
    path = Path(__file__).resolve().parents[1] / "submission" / "emerald-league-gauntlet.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Run tools/validate_emerald_league.py first.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    video = path.with_name("emerald-league-gauntlet-demo.mp4")
    if video.is_file() and video.stat().st_size > 0:
        accepted_fights = sum(
            1 for fight in payload.get("fights") or []
            if fight.get("result") == "win-line"
        )
        video_label = (
            "Elite Four → Wallace Gauntlet demo"
            if payload.get("result") == "route-complete"
            else f"League validation audit · {accepted_fights}/{payload.get('queued', 5)} accepted fights"
        )
        payload["videos"] = [{
            "kind": "judge-ui-validation", "label": video_label,
            "video_url": "/submission/emerald-league-gauntlet-demo.mp4", "video_ready": True,
        }]
        payload["video_ready"] = True
    return payload


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    with status_lock:
        return dict(status)


@app.post("/api/solve")
async def api_solve(request: SolveRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    _ensure_idle()
    background_tasks.add_task(_run_solve_thread, request)
    return {"status": "started"}


@app.post("/api/prepare")
async def api_prepare(request: PrepareRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    _ensure_idle()
    background_tasks.add_task(_run_prepare_thread, request)
    return {"status": "started"}


@app.post("/api/prepare/apply")
async def api_apply_preparation(request: ApplyPreparationRequest) -> dict[str, Any]:
    """Create a resumable pre-fight state with the chosen party and held items."""
    _validate_paths(request.rom, request.pc_state)
    source = Path(request.pc_state).expanduser().resolve()
    destination = (
        Path(request.output_state).expanduser().resolve()
        if request.output_state
        else source.with_name(f"{source.stem}-prepared.ss0")
    )
    slots: list[TeamSlotRequest] = []
    for slot in request.team:
        if slot.source == "box":
            if slot.box is None or slot.box_slot is None:
                raise HTTPException(status_code=400, detail="A boxed Pokemon needs a box and slot.")
            slots.append(TeamSlotRequest.box_mon(slot.box, slot.box_slot, item_id=slot.item_id))
        elif slot.source == "party":
            if slot.party_slot is None:
                raise HTTPException(status_code=400, detail="A current party Pokemon needs its party slot.")
            slots.append(TeamSlotRequest.party(slot.party_slot, item_id=slot.item_id))
        else:
            raise HTTPException(status_code=400, detail=f"Unknown team source: {slot.source}")

    instance = MGBAInstance(request.rom, str(source), 71)
    try:
        report = prepare_party(instance, slots)
        instance.save_state(destination)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        instance.shutdown()
    return {
        "status": "prepared",
        "state": str(destination),
        "party": [
            {
                "slot": mon.slot,
                "name": mon.display_name,
                "level": mon.level,
                "item": mon.held_item,
                "hp": mon.hp,
                "max_hp": mon.max_hp,
            }
            for mon in report.party
        ],
        "box_swaps": [list(value) for value in report.moved_from_boxes],
        "item_changes": list(report.item_changes),
        "inventory_before": report.inventory_before,
        "inventory_after": report.inventory_after,
    }


@app.post("/api/kill")
async def api_kill() -> dict[str, str]:
    cancelled = _kill_current_job()
    return {"status": "killed" if cancelled else "idle"}


@app.post("/api/open-gba")
async def api_open_gba(request: OpenGBARequest) -> dict[str, str]:
    rom_path = Path(request.rom).expanduser().resolve()
    if not rom_path.is_file():
        raise HTTPException(status_code=404, detail=f"ROM not found: {rom_path}")
    _open_gba_rom(rom_path)
    return {"status": "opened", "rom": str(rom_path)}


@app.post("/api/simulator/inspect")
async def api_simulator_inspect(request: SimulatorInspectRequest) -> dict[str, Any]:
    """Decode the exact live battle before spending time on simulations."""
    _validate_paths(request.rom, request.state)
    instance = MGBAInstance(request.rom, request.state, 66)
    try:
        state = StateReader(instance).read()
        screen = instance.screenshot()
    finally:
        instance.shutdown()
    calculator = DamageCalculator(game_mode=request.game_mode)
    match = calculator.matched_trainer(state)
    return _live_battle_payload(state, match, screen)


@app.post("/api/simulator/checkpoint")
async def api_simulator_checkpoint(request: SimulatorCheckpointRequest) -> dict[str, Any]:
    """Create our own fast-load checkpoint from a savestate or adjacent battery save."""
    rom = Path(request.rom).expanduser().resolve()
    if not rom.is_file():
        raise HTTPException(status_code=404, detail=f"ROM not found: {rom}")
    if request.source_state.strip():
        source = Path(request.source_state).expanduser().resolve()
        if not source.is_file():
            raise HTTPException(status_code=404, detail=f"Source save not found: {source}")
    else:
        candidates = [rom.with_suffix(".sav"), rom.with_name(f"{rom.stem}.sav")]
        source = next((candidate for candidate in candidates if candidate.is_file()), None)
        if source is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "A ROM does not contain the current run. Put its .sav battery file beside the GBA "
                    "or choose an existing .sav/.ss state once; the simulator can create all later checkpoints itself."
                ),
            )
    destination = (
        Path(request.output_state).expanduser().resolve()
        if request.output_state.strip()
        else _SIM_CHECKPOINTS_DIR / f"{rom.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.ss0"
    )
    instance = MGBAInstance(str(rom), str(source), 67)
    try:
        if source.suffix.casefold() == ".sav":
            # A battery save is not an instant frozen frame. Boot the cartridge,
            # choose Continue, and only then create the fast-load simulator state.
            instance.advance_frames(600)
            instance.send_input("START", 2)
            instance.advance_frames(120)
            for _ in range(3):
                instance.send_input("A", 2)
                instance.advance_frames(120)
        elif request.settle_frames:
            instance.advance_frames(request.settle_frames)
        state = StateReader(instance).read()
        game = WholeGameStateReader(
            instance, DamageCalculator(game_mode=request.game_mode)
        ).read()
        player_name = read_player_name(instance)
        screen = instance.screenshot()
        instance.save_state(destination)
    finally:
        instance.shutdown()
    match = DamageCalculator(game_mode=request.game_mode).matched_trainer(state)
    return {
        "status": "battle-ready" if game.mode == GameMode.BATTLE_COMMAND and not state.battle_over else "checkpoint-created",
        "state": str(destination),
        "source": str(source),
        "game_mode": game.mode.value,
        "battle_ready": bool(game.mode == GameMode.BATTLE_COMMAND and not state.battle_over),
        "recognized_trainer": match.battle.trainer_name if match else None,
        "player_name": player_name or None,
        "position": [game.x, game.y],
        "map_id": list(game.map_id) if game.map_id else None,
        "screen": screen,
        "note": (
            "This checkpoint is ready for the battle solver."
            if game.mode == GameMode.BATTLE_COMMAND and not state.battle_over
            else (
                f"Loaded {player_name}'s save and created a playable overworld checkpoint."
                if player_name
                else "Checkpoint saved, but no active player save was recognized."
            )
        ),
    }


@app.post("/api/simulator/validate-planner-line")
async def api_validate_planner_line(request: PlannerValidationRequest) -> dict[str, Any]:
    """Replay a calculator line by move/Pokemon name against the real GBA battle."""
    _validate_paths(request.rom, request.state)
    policy, warnings = compile_planner_policy(request.result)
    if request.playbook_path.strip():
        root = Path(__file__).resolve().parents[1]
        playbook_path = Path(request.playbook_path).expanduser()
        playbook_path = playbook_path.resolve() if playbook_path.is_absolute() else (root / playbook_path).resolve()
        if root not in playbook_path.parents or not playbook_path.is_file():
            raise HTTPException(status_code=400, detail="Playbook must be a readable project file.")
        playbook = json.loads(playbook_path.read_text(encoding="utf-8"))
        references = [
            value for trainer in playbook.get("trainers") or []
            for value in trainer.get("lines") or []
        ]
        raw_policy: list[tuple[Action, ...]] = []
        for reference in references:
            manifest_path = (root / reference).resolve()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw_line = manifest.get("line") or (manifest.get("result") or {}).get("line") or []
            raw_policy.extend(tuple(Action(**action) for action in turn) for turn in raw_line)
        policy = raw_policy
        warnings = [f"Loaded {len(policy)} cartridge turns from {playbook_path.name}."]
    if not policy:
        raise HTTPException(status_code=400, detail="The planner result has no safely replayable turns.")
    trials = [
        TrialSpec(trial_id=index, actions=policy, rng_advance_frames=index, max_turns=len(policy))
        for index in range(request.repetitions)
    ]
    pool = MGBAPool(request.rom, request.state, min(request.instances, request.repetitions))
    try:
        outcomes = await asyncio.to_thread(pool.run_trials, trials)
    finally:
        pool.shutdown()
    wins = sum(outcome.battle_won for outcome in outcomes)
    blackouts = sum(outcome.final_state.battle_over and not outcome.final_state.player_won for outcome in outcomes)
    errors = sum(outcome.error is not None for outcome in outcomes)
    unfinished = len(outcomes) - wins - blackouts - errors
    def actual_deaths(outcome: Any) -> int:
        return sum(
            max_hp > 0 and hp <= 0
            for hp, max_hp in zip(outcome.final_state.player_hp, outcome.final_state.player_max_hp)
        )
    deathless = sum(outcome.battle_won and actual_deaths(outcome) == 0 for outcome in outcomes)
    failed_rng_frames = [
        outcome.trial_id for outcome in outcomes
        if not outcome.battle_won or actual_deaths(outcome) > 0 or outcome.error is not None
    ]
    replays = [_simulator_trial_data(outcome) for outcome in outcomes]
    await manager.broadcast({"type": "simulators", "data": replays[-min(8, len(replays)):]})
    payload = {
        "status": "validated" if wins else "rejected",
        "planner_confidence": request.result.get("confidence"),
        "repetitions": len(outcomes),
        "wins": wins,
        "deathless_wins": deathless,
        "blackouts": blackouts,
        "unfinished": unfinished,
        "errors": errors,
        "clear_rate": wins / len(outcomes),
        "deathless_clear_rate": deathless / len(outcomes),
        "warnings": warnings,
        "failed_rng_frames": failed_rng_frames,
        "needs_backup_plan": bool(failed_rng_frames),
        "replays": replays,
    }
    validation_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f-playbook-validation")
    validation_path = _SIM_RUNS_DIR / f"{validation_id}.json"
    validation_record = {
        "id": validation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rom": request.rom,
        "state": request.state,
        "playbook_path": request.playbook_path,
        **{key: value for key, value in payload.items() if key != "replays"},
        "replays": [
            {key: replay.get(key) for key in (
                "trial_id", "status", "reason", "error", "player_hp", "enemy_hp",
            )}
            for replay in replays
        ],
    }
    validation_path.write_text(json.dumps(validation_record, ensure_ascii=False), encoding="utf-8")
    payload["validation_log_id"] = validation_id
    payload["validation_log_path"] = str(validation_path)
    payload["robustness_status"] = (
        "broad-rng-verified" if deathless == len(outcomes)
        else "contingencies-required"
    )
    return payload


@app.post("/api/autonomy/route")
async def api_autonomy_route(request: AutonomyRouteRequest) -> dict[str, Any]:
    """Execute a checkpointed overworld/menu route and return its durable run log."""
    _validate_paths(request.rom, request.state)
    instance = MGBAInstance(request.rom, request.state, 67)
    try:
        runner = CheckpointedGameRunner(instance, Path(__file__).resolve().parents[1] / "output" / "autonomy")
        actions = [RouteAction(**(_request_payload(action))) for action in request.actions]
        resolved_route = None
        if request.destination.strip() or request.trainer_id is not None:
            live = WholeGameStateReader(instance).read()
            start = (*tuple(live.map_id or ()), live.x, live.y)
            trainer_token = ""
            if request.trainer_id is not None:
                battles = load_trainer_battles_for_mode(normalize_game_mode(request.game_mode))
                if request.trainer_id < 0 or request.trainer_id >= len(battles):
                    raise HTTPException(status_code=404, detail="Unknown trainer destination")
                trainer_token = re.sub(
                    r"[^a-z0-9]+", "-", battles[request.trainer_id].trainer_name.casefold()
                ).strip("-")
            route_dir = Path(__file__).resolve().parents[1] / "output" / "gauntlet_runs" / "routes"
            candidates = []
            for path in route_dir.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if tuple(payload.get("start") or ()) != start:
                    continue
                goal = tuple(payload.get("goal") or ())
                destination = request.destination.casefold().strip()
                route_target = (
                    path.stem.casefold()
                    .removeprefix("center-to-")
                    .removeprefix("route-to-")
                )
                matches = destination == "pokemon_center" and goal[:2] == (6, 4)
                if destination != "pokemon_center":
                    trainer_route_match = bool(
                        request.trainer_id is not None
                        and (
                            route_target in trainer_token
                            or trainer_token in route_target
                            or route_target.removesuffix("-coord").removesuffix("-adjacent") in trainer_token
                        )
                    )
                    matches = (
                        trainer_route_match
                        or bool(destination) and destination.replace("_", "-") in path.stem.casefold()
                    )
                if matches:
                    candidates.append((path, payload))
            if len(candidates) != 1:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"No unique verified route from {start} to trainer {request.trainer_id}."
                        if request.trainer_id is not None
                        else f"No unique verified route from {start} to {request.destination!r}."
                    ),
                )
            route_path, route_payload = candidates[0]
            actions = [RouteAction(kind="path", value=",".join(route_payload["directions"]))]
            if request.trainer_id is not None:
                if route_payload.get("end_face"):
                    actions.append(RouteAction(kind="face", value=str(route_payload["end_face"])))
                actions.append(RouteAction(kind="enter_battle", count=40, settle_frames=60))
            resolved_route = str(route_path)
        run = await asyncio.to_thread(
            runner.run, request.route_name, actions, checkpoint_every=request.checkpoint_every
        )
        screen = instance.screenshot()
        return {**asdict(run), "screen": screen, "resolved_route": resolved_route}
    finally:
        instance.shutdown()


@app.get("/api/autonomy/runs")
async def api_autonomy_runs() -> dict[str, Any]:
    """Return checkpointed travel/PC logs for the Simulator mission history."""
    root = Path(__file__).resolve().parents[1] / "output" / "autonomy"
    runs: list[dict[str, Any]] = []
    for path in root.glob("*/run.json"):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        run["log_path"] = str(path)
        runs.append(run)
    runs.sort(key=lambda run: str(run.get("run_id") or ""), reverse=True)
    return {"runs": runs[:50]}


@app.get("/api/calc/trainers")
async def api_calc_trainers(game_mode: str = "run-and-bun") -> dict[str, Any]:
    mode = normalize_game_mode(game_mode)
    calculator = DamageCalculator(game_mode=mode)
    battles = load_trainer_battles_for_mode(mode)
    trainer_rows = [_trainer_summary(index, battle, calculator) for index, battle in enumerate(battles)]
    checkpoint_library: dict[str, Any] | None = None
    if mode == "pokemon-emerald":
        checkpoint_library = _emerald_checkpoint_library()
        entries_by_trainer: dict[int, list[dict[str, Any]]] = {}
        for entry in checkpoint_library.get("entries") or []:
            entries_by_trainer.setdefault(int(entry["trainer_id"]), []).append(entry)
        trainer_rows = [
            {
                **trainer_rows[trainer_id],
                "checkpoint_count": len(entries),
                "checkpoint_ids": [entry["checkpoint_id"] for entry in entries],
                "tier": "boss" if any(entry.get("tier") == "boss" for entry in entries) else (
                    "story" if any(entry.get("tier") == "story" for entry in entries) else "trainer"
                ),
                "recommended_test": any(entry.get("recommended_test") for entry in entries),
                "checkpoint_team_import": entries[-1].get("team_import") or "",
                "checkpoint_roster_import": entries[-1].get("roster_import") or entries[-1].get("team_import") or "",
            }
            for trainer_id, entries in sorted(entries_by_trainer.items())
            if 0 <= trainer_id < len(trainer_rows)
        ]
    return {
        "trainers": trainer_rows,
        "sample_import": EMERALD_SAMPLE_CALC_IMPORT if mode == "pokemon-emerald" else SAMPLE_CALC_IMPORT,
        "game_mode": mode,
        "game_label": "Pokémon Emerald" if mode == "pokemon-emerald" else "Run & Bun",
        "checkpoint_library": checkpoint_library,
    }


# ---- Line-finder progress tracking ----
# The sim is staged with known per-stage simulation budgets, so progress is the
# count of completed single-line simulations against the planned total. Stages
# that finish under budget snap forward, so the bar never stalls or runs back.
_CALC_PROGRESS: dict[str, Any] = {
    "running": False, "done": 0, "total": 0, "phase": "", "stage_start": 0, "stage_alloc": 0,
    "hold_for_video": False,
}

_GAUNTLET_PROGRESS: dict[str, Any] = {
    "running": False, "completed": 0, "total": 0, "current": 0,
    "trainer": "", "phase": "idle", "pct": 0.0, "stage": "idle",
    "completed_replays": 0, "total_replays": 2, "video_ready": False,
}

_SIM_PROOF_PROGRESS: dict[str, Any] = {
    "running": False, "phase": "idle", "pct": 0.0,
    "completed_replays": 0, "total_replays": 0,
    "video_ready": False, "verified": False,
}


def _sim_proof_update(**changes: Any) -> None:
    _SIM_PROOF_PROGRESS.update(changes)
    manager.broadcast_sync({"type": "proof_progress", "data": dict(_SIM_PROOF_PROGRESS)})

_FLOWCHART_PROGRESS: dict[str, Any] = {
    "running": False, "phase": "idle", "expanded": 0, "queued": 0,
    "pct": 0.0, "started_at": None, "elapsed_s": 0.0, "complete": False,
    "positions_per_s": 0.0, "eta_s": None,
}
_FLOWCHART_CANCEL_EVENT: threading.Event | None = None


def _flowchart_progress_reset() -> None:
    _FLOWCHART_PROGRESS.update(
        running=True, phase="selecting the playable team", expanded=0, queued=0,
        pct=2.0, started_at=time.monotonic(), elapsed_s=0.0, complete=False,
        positions_per_s=0.0, eta_s=None,
    )


def _flowchart_progress_update(expanded: int, queued: int, *, complete: bool = False) -> None:
    started = _FLOWCHART_PROGRESS.get("started_at") or time.monotonic()
    if complete:
        pct = 100.0
    else:
        frontier_estimate = max(1, expanded + queued)
        observed = min(95.0, 10.0 + 85.0 * expanded / frontier_estimate)
        pct = max(float(_FLOWCHART_PROGRESS.get("pct") or 0.0), observed)
    elapsed = max(0.001, time.monotonic() - started)
    rate = expanded / elapsed
    _FLOWCHART_PROGRESS.update(
        running=not complete, phase="complete" if complete else "expanding battle positions",
        expanded=expanded, queued=queued, pct=round(pct, 1),
        elapsed_s=round(elapsed, 1), complete=complete,
        positions_per_s=round(rate, 1), eta_s=round(queued / rate, 1) if queued and rate > 0 else 0.0,
    )


@app.get("/api/calc/flowchart/progress")
async def api_calc_flowchart_progress() -> dict[str, Any]:
    progress = dict(_FLOWCHART_PROGRESS)
    if progress.get("running") and progress.get("started_at"):
        progress["elapsed_s"] = round(time.monotonic() - progress["started_at"], 1)
    progress.pop("started_at", None)
    return progress


@app.post("/api/calc/flowchart/cancel")
async def api_cancel_calc_flowchart() -> dict[str, str]:
    if _FLOWCHART_CANCEL_EVENT is not None:
        _FLOWCHART_CANCEL_EVENT.set()
        return {"status": "stopping"}
    return {"status": "idle"}


def _progress_reset(plan: list[tuple[str, int]]) -> None:
    _CALC_PROGRESS.update(
        running=True, done=0, total=sum(alloc for _, alloc in plan),
        phase=plan[0][0] if plan else "", stage_start=0, stage_alloc=0,
    )


def _progress_stage(phase: str, alloc: int) -> None:
    progress = _CALC_PROGRESS
    if not progress["running"]:
        return
    progress["done"] = progress["stage_start"] + progress["stage_alloc"]
    progress["stage_start"] = progress["done"]
    progress["stage_alloc"] = alloc
    if progress["done"] + alloc > progress["total"]:
        progress["total"] = progress["done"] + alloc
    progress["phase"] = phase


def _progress_tick() -> None:
    progress = _CALC_PROGRESS
    if progress["running"] and progress["done"] < progress["stage_start"] + progress["stage_alloc"]:
        progress["done"] += 1


def _progress_finish() -> None:
    total = max(1, int(_CALC_PROGRESS.get("total") or 0))
    if _CALC_PROGRESS.get("hold_for_video"):
        _CALC_PROGRESS.update(
            running=True, done=max(0, total - 1), total=total,
            phase="Rendering required video",
        )
        return
    _CALC_PROGRESS.update(running=False, done=total, total=total, phase="complete")


def _progress_video_pending() -> None:
    total = max(1, int(_CALC_PROGRESS.get("total") or 0))
    _CALC_PROGRESS.update(
        running=True, done=max(0, total - 1), total=total,
        phase="Rendering and checking required video", hold_for_video=True,
    )


def _progress_video_complete() -> None:
    total = max(1, int(_CALC_PROGRESS.get("total") or 0))
    _CALC_PROGRESS.update(
        running=False, done=total, total=total,
        phase="Video and log saved", hold_for_video=False,
    )


def _progress_video_failed() -> None:
    total = max(1, int(_CALC_PROGRESS.get("total") or 0))
    _CALC_PROGRESS.update(
        running=False, done=max(0, total - 1), total=total,
        phase="Required video failed", hold_for_video=False,
    )


@app.get("/api/calc/progress")
async def api_calc_progress() -> dict[str, Any]:
    progress = _CALC_PROGRESS
    total = max(1, progress["total"])
    return {
        "running": progress["running"],
        "phase": progress["phase"],
        "done": progress["done"],
        "total": progress["total"],
        "pct": round(min(1.0, progress["done"] / total) * 100, 1),
    }


@app.get("/api/calc/gauntlet/progress")
async def api_calc_gauntlet_progress() -> dict[str, Any]:
    progress = dict(_GAUNTLET_PROGRESS)
    total_fights = max(1, int(progress.get("total") or 0))
    if progress.get("running") and progress.get("stage") == "planning":
        local_total = max(1, int(_CALC_PROGRESS.get("total") or 0))
        local_fraction = min(1.0, max(0.0, float(_CALC_PROGRESS.get("done") or 0) / local_total))
        progress["phase"] = _CALC_PROGRESS.get("phase") or progress.get("phase") or "starting fight"
        progress["pct"] = round(55 * (int(progress.get("completed") or 0) + local_fraction) / total_fights, 1)
    return progress


@app.get("/api/calc/gauntlet/playbooks")
async def api_calc_gauntlet_playbooks() -> dict[str, Any]:
    """Report executable cartridge routes and validate every referenced asset."""
    playbooks: list[dict[str, Any]] = []
    root = Path(__file__).resolve().parents[1]
    for path in sorted(_GAUNTLET_PLAYBOOKS_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            references = [
                value for trainer in payload.get("trainers") or []
                for value in trainer.get("lines") or []
            ] + [
                transition[key] for transition in payload.get("transitions") or []
                for key in ("to_center", "to_trainer")
            ]
            if payload.get("starting_state"):
                references.append(payload["starting_state"])
            missing = [value for value in references if not (root / value).is_file()]
            graveyard_safe = all(
                int(member.get("box", -1)) != config.NUZLOCKE_GRAVEYARD_BOX
                for transition in payload.get("transitions") or []
                for member in (transition.get("preparation") or {}).get("party", [])
            ) and all(
                int(box) != config.NUZLOCKE_GRAVEYARD_BOX
                for transition in payload.get("transitions") or []
                for box in (transition.get("preparation") or {}).get("item_donor_boxes", [])
            )
            playbooks.append({
                "id": payload.get("id"), "label": payload.get("label"),
                "trainer_count": len(payload.get("trainers") or []),
                "trainer_ids": payload.get("trainer_ids") or (
                    list(range(47, 55)) if payload.get("id") == "corgi-to-chelle" else []
                ),
                "starting_state": payload.get("starting_state"),
                "ready": not missing and graveyard_safe,
                "missing": missing, "graveyard_safe": graveyard_safe,
                "features": [
                    "automatic battle inputs", "Center healing and overworld navigation",
                    "legal Box and held-item preparation", "two fresh deathless cartridge replays",
                    "uncut normal-speed video and permanent logs",
                    "automatic checkpoint repair and full-proof restart after battle divergence",
                    "live berry, recoil, set-mode, and forced-faint state checks",
                    "doubles slot and switch-target remapping after party reordering",
                ],
            })
        except Exception as exc:
            playbooks.append({"id": path.stem, "ready": False, "error": str(exc)})
    return {"playbooks": playbooks}


@app.post("/api/calc/sim")
async def api_calc_sim(request: CalcSimRequest) -> dict[str, Any]:
    mode = normalize_game_mode(request.game_mode)
    calculator = DamageCalculator(default_field=FieldState(
        weather=request.weather,
        is_reflect=request.reflect,
        is_light_screen=request.light_screen,
    ), game_mode=mode)
    battles = load_trainer_battles_for_mode(mode)
    imported = _parse_imported_sets(request.imports, calculator)
    if not imported:
        raise HTTPException(status_code=400, detail="Import at least one Pokemon set.")
    trainer = _trainer_for_calc_request(request, calculator, battles)
    forced_doubles_leads = _requested_doubles_leads(request, len(imported)) if trainer.is_double else None
    _CALC_PROGRESS["hold_for_video"] = True
    try:
        result = await asyncio.to_thread(
            _run_text_calc_sim_with_team_select,
            imported,
            trainer,
            calculator,
            max_turns=max(1, min(60, request.max_turns)),
            force_enemy_crits=request.crit_safe,
            forced_doubles_leads=forced_doubles_leads,
        )
        active_conditions = []
        if request.weather:
            active_conditions.append(f"{request.weather} weather")
        if request.reflect:
            active_conditions.append("Reflect")
        if request.light_screen:
            active_conditions.append("Light Screen")
        result["field_conditions"] = active_conditions
        result["trainer"] = trainer.trainer_name
        result["location"] = trainer.location
        result["is_doubles"] = trainer.is_double
        result["game_mode"] = mode
        result["game_label"] = "Pokémon Emerald" if mode == "pokemon-emerald" else "Run & Bun"
        result["mechanics"] = "Generation III" if mode == "pokemon-emerald" else "Run & Bun ruleset"
        result["alternate_answer_lines"] = await asyncio.to_thread(
            _alternate_answer_lines,
            imported,
            trainer,
            calculator,
            result,
            max_turns=max(1, min(60, request.max_turns)),
            force_enemy_crits=request.crit_safe,
        )
        if mode == "pokemon-emerald":
            result["rules"] = {
                "ruleset": request.ruleset,
                "items_in_battle": bool(request.items_in_battle),
                "revives_allowed": request.ruleset == "standard",
                "hint_mode": bool(request.hint_mode),
            }
            result["level_guidance"] = _emerald_level_guidance(
                imported, trainer, result, request.level_cap, calculator
            )
            result["failure_diagnosis"] = _emerald_failure_diagnosis(
                imported, trainer, result, result["level_guidance"], calculator
            )
            result["progressive_hints"] = _emerald_progressive_hints(
                imported, trainer, result, result["level_guidance"], calculator
            )
            result["hint_mode"] = bool(request.hint_mode)
        if trainer.is_double and not result.get("contingency_flowchart"):
            chosen_names = {
                _normalize(str(member.get("name") or member.get("species") or ""))
                for member in result.get("team") or []
            }
            chart_team = [member for member in imported if _normalize(member.name) in chosen_names][:6]
            if len(chart_team) < 2:
                chart_team = imported[:6]
            searched_leads = (result.get("line_search") or {}).get("leads")
            chart_leads = (
                (int(searched_leads[0]), int(searched_leads[1]))
                if isinstance(searched_leads, list) and len(searched_leads) >= 2 else None
            )
            result["contingency_flowchart"] = await asyncio.to_thread(
                _contingency_flowchart,
                chart_team,
                trainer,
                calculator,
                max_turns=max(1, min(60, request.max_turns)),
                force_enemy_crits=request.crit_safe,
                forced_doubles_leads=chart_leads,
                node_budget=_CONTINGENCY_NODE_BUDGET,
                time_budget_s=_CONTINGENCY_TIME_BUDGET_S,
            )
            result["contingency_flowchart_note"] = (
                "Doubles board tree: enemy field slot, move, and target choices are separate replayed branches; "
                "equivalent two-slot positions rejoin. Use Explore every branch to remove the initial budget."
            )
        _progress_video_pending()
        result["saved_run"] = _save_calc_run(request, result)
        _progress_video_complete()
        return result
    except Exception:
        _progress_video_failed()
        raise
    finally:
        _CALC_PROGRESS["hold_for_video"] = False


@app.post("/api/calc/gauntlet")
async def api_calc_gauntlet(request: GauntletSimRequest) -> dict[str, Any]:
    """Solve an ordered trainer queue, carrying the chosen party between fights."""
    cached = _cached_gauntlet_run(request)
    if cached is not None:
        return cached
    mode = normalize_game_mode(request.game_mode)
    battles = load_trainer_battles_for_mode(mode)
    invalid = [trainer_id for trainer_id in request.trainer_ids if trainer_id < 0 or trainer_id >= len(battles)]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown trainer id: {invalid[0]}")
    calculator = DamageCalculator(default_field=FieldState(
        weather=request.weather,
        is_reflect=request.reflect,
        is_light_screen=request.light_screen,
    ), game_mode=mode)
    imported = _parse_imported_sets(request.imports, calculator)
    roster_source = "text import"
    if request.use_live_pc_box and request.rom and request.pc_state:
        try:
            abilities = {
                _normalize(member.species): member.ability
                for member in imported if member.ability
            }
            live_roster = await asyncio.to_thread(
                _planned_box_from_pc_state, request.rom, request.pc_state, calculator,
                abilities,
            )
            if live_roster:
                imported = live_roster
                roster_source = str(Path(request.pc_state).expanduser().resolve())
            elif imported:
                roster_source = "imported team (the selected save had no readable PC roster)"
        except Exception as exc:
            if imported:
                roster_source = f"imported team (the selected save could not be read: {exc})"
            else:
                raise HTTPException(status_code=400, detail=f"Could not read a party from the selected save: {exc}") from exc
    if not imported:
        raise HTTPException(
            status_code=400,
            detail="No usable party was found. Choose an Emerald .sav or mGBA checkpoint, or paste a Showdown team in Party source.",
        )
    try:
        healing_mode = request.healing_mode if request.healing_mode in {"none", "bag", "pokemon-center"} else (
            "pokemon-center" if request.heal_between else "none"
        )
        result = await asyncio.to_thread(
            _run_calc_gauntlet,
            imported,
            [battles[trainer_id] for trainer_id in request.trainer_ids],
            calculator,
            max_turns=max(1, min(60, request.max_turns)),
            force_enemy_crits=request.crit_safe,
            heal_between=healing_mode != "none",
            optimize_between_fights=(
                request.optimize_between_fights and healing_mode == "pokemon-center"
            ),
            leveling_policy=request.leveling_policy if not request.hint_mode else "none",
            deathless_required=request.ruleset in {"hardcore-nuzlocke", "nuzlocke"},
            max_total_faints=request.max_total_faints,
            allow_revives=bool(request.allow_revives),
        )
        result["roster_source"] = roster_source
        result["game_mode"] = mode
        result["game_label"] = "Pokémon Emerald" if mode == "pokemon-emerald" else "Run & Bun"
        result["mechanics"] = "Generation III" if mode == "pokemon-emerald" else "Run & Bun ruleset"
        result["nuzlocke_rules"] = {
            "graveyard_box": config.NUZLOCKE_GRAVEYARD_BOX,
            "graveyard_excluded": True,
            "graveyard_items_excluded": True,
            "ruleset": request.ruleset,
            "items_in_battle": bool(request.items_in_battle),
            "revives_allowed": bool(request.allow_revives),
            "healing_mode": healing_mode,
            "hint_mode": bool(request.hint_mode),
            "leveling_policy": request.leveling_policy if not request.hint_mode else "disabled-with-hints",
            "max_total_faints": request.max_total_faints,
        }
        _GAUNTLET_PROGRESS.update(
            running=mode == "run-and-bun", stage="cartridge-proof" if mode == "run-and-bun" else "planner-complete",
            phase="Replaying the full route in the game" if mode == "run-and-bun" else "Planner route finished",
            pct=58.0 if mode == "run-and-bun" else 100.0,
            completed_replays=0, total_replays=2 if mode == "run-and-bun" else 0, video_ready=False,
        )
        requested_trainers = [battles[trainer_id] for trainer_id in request.trainer_ids]
        # Cartridge playbooks contain ROM-specific memory checks and inputs. Never
        # apply a Run & Bun route to a vanilla Emerald request merely because trainer
        # names or party fingerprints happen to overlap.
        playbook = _matching_gauntlet_playbook(requested_trainers) if mode == "run-and-bun" else None
        proof = None
        proof_failure = None
        calculator_missed_playbook = bool(
            playbook and result.get("result") != "route-complete"
        )
        if request.heal_between and playbook:
            try:
                proof = await asyncio.to_thread(
                    _run_gauntlet_cartridge_proof, request, requested_trainers, playbook
                )
            except Exception as exc:
                proof_failure = str(exc)
        if proof:
            if calculator_missed_playbook:
                result.update({
                    "result": "route-complete",
                    "completed": len(requested_trainers),
                    "queued": len(requested_trainers),
                    "stopped_reason": None,
                    "planner_reconciliation": {
                        "status": "cartridge-playbook-promoted",
                        "calculator_mismatch": True,
                        "note": (
                            "The abstract planner missed this line. The app ran the saved "
                            "cartridge policy instead and promoted it only after the full proof gate passed."
                        ),
                    },
                })
                for fight in result.get("fights") or []:
                    fight["result"] = "cartridge-verified-line"
            result.update(proof)
            _GAUNTLET_PROGRESS.update(
                running=True, stage="video", phase="Rendering and checking required route video",
                pct=96.0, completed_replays=2, total_replays=2, video_ready=True,
            )
        else:
            planner_complete = result.get("result") == "route-complete"
            result.update({
                "proof_complete": False,
                "emulator_result": "planner-only" if mode == "pokemon-emerald" else "game-proof-required",
                "proof_error": None if not planner_complete else (
                    proof_failure or (
                        "The Emerald planner finished, but this route does not have an executable overworld replay yet."
                        if mode == "pokemon-emerald" else
                        "No executable playbook exists for this route yet. The planner result "
                        "is saved, but completion still requires two uncut in-game replays."
                    )
                ),
                "videos": [],
            })
            _GAUNTLET_PROGRESS.update(
                running=True, stage="video",
                phase="Rendering and checking required route video",
                pct=96.0, video_ready=False,
            )
        result["saved_run"] = _save_gauntlet_run(request, result)
        ready_video = any(item.get("video_ready") for item in result.get("videos", []))
        if not ready_video:
            raise RuntimeError("Gauntlet finished without its required video result")
        _GAUNTLET_PROGRESS.update(
            running=False,
            stage="verified" if result.get("proof_complete") else (
                "planner-complete" if result.get("result") == "route-complete" else "stopped"
            ),
            phase="Video and log saved",
            pct=100.0, video_ready=True,
        )
        return result
    except Exception:
        video_stage = _GAUNTLET_PROGRESS.get("stage") == "video"
        _GAUNTLET_PROGRESS.update(
            running=False,
            stage="video-error" if video_stage else "setup-error",
            phase="Required route video failed" if video_stage else "Check the route inputs",
            pct=99.0 if video_stage else 0.0,
            video_ready=False,
        )
        raise
    finally:
        _progress_finish()


@app.get("/api/calc/gauntlet/runs")
async def api_calc_gauntlet_runs() -> dict[str, Any]:
    _GAUNTLET_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    canonical = _canonical_connected_proof_record()
    if canonical:
        result = canonical["result"]
        runs.append({
            "id": canonical["id"], "created_at": canonical.get("created_at"),
            "result": result.get("result"), "completed": result.get("completed", 0),
            "queued": result.get("queued", 0), "stopped_reason": result.get("stopped_reason"),
            "emulator_result": result.get("emulator_result"),
            "emulator_validation": _validation_with_recorded_safety(result),
            "videos": result.get("videos") or [], "proof_complete": True,
            "proof_error": None,
            "game_mode": "run-and-bun",
        })
    for path in sorted(_GAUNTLET_RUNS_DIR.glob("*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("engine_version") != _GAUNTLET_ENGINE_VERSION:
            continue
        result = record.get("result") or {}
        runs.append({
            "id": record.get("id"), "created_at": record.get("created_at"),
            "result": result.get("result"), "completed": result.get("completed", 0),
            "queued": result.get("queued", 0), "stopped_reason": result.get("stopped_reason"),
            "emulator_result": result.get("emulator_result"),
            "emulator_validation": _validation_with_recorded_safety(result),
            "videos": result.get("videos") or [],
            "proof_complete": bool(result.get("proof_complete")),
            "proof_error": result.get("proof_error"),
            "game_mode": record.get("game_mode") or (record.get("request") or {}).get("game_mode", "run-and-bun"),
        })
    # Keep completed cartridge proof above newer planner-only diagnostics.
    runs.sort(
        key=lambda run: (
            bool(run.get("proof_complete")),
            int(run.get("completed") or 0),
            str(run.get("created_at") or ""),
        ),
        reverse=True,
    )
    return {"runs": runs[:100]}


@app.get("/api/calc/gauntlet/runs/{run_id}")
async def api_calc_gauntlet_run(run_id: str) -> dict[str, Any]:
    if run_id == "connected-corgi-chelle-proof":
        record = _canonical_connected_proof_record()
        if record:
            return record
    path = _gauntlet_run_path(run_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Saved gauntlet run not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/calc/gauntlet/runs/{run_id}/prepare/{position}")
async def api_apply_gauntlet_fight(
    run_id: str, position: int, request: ApplyGauntletFightRequest
) -> dict[str, Any]:
    """Apply one logged Center/PC preparation to a real save state."""
    path = _gauntlet_run_path(run_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Saved gauntlet run not found")
    record = json.loads(path.read_text(encoding="utf-8"))
    fights = (record.get("result") or {}).get("fights") or []
    if position < 1 or position > len(fights):
        raise HTTPException(status_code=404, detail="Fight position is not in this route")
    preparation = fights[position - 1].get("preparation") or {}
    chosen = list(preparation.get("chosen") or [])
    held_items = dict(preparation.get("held_items") or {})
    if not chosen:
        raise HTTPException(status_code=409, detail="This logged fight has no prepared party")

    _validate_paths(request.rom, request.pc_state)
    calculator = DamageCalculator()
    scan = await asyncio.to_thread(scan_pc_boxes, request.rom, request.pc_state, calculator=calculator)
    decoded = list(scan.party) + [
        mon for mon in scan.roster if mon.box != config.NUZLOCKE_GRAVEYARD_BOX
    ]
    available = {_normalize(mon.display_name): mon for mon in decoded}
    item_ids = {
        _normalize(mon.held_item): mon.held_item_id
        for mon in decoded if mon.held_item and mon.held_item_id
    }
    slots: list[TeamSlotRequest] = []
    for name in chosen:
        mon = available.get(_normalize(name))
        if mon is None:
            raise HTTPException(status_code=409, detail=f"{name} is not present in the supplied PC state")
        item_name = held_items.get(name)
        item_id = item_ids.get(_normalize(item_name), 0) if item_name else 0
        if item_name and not item_id:
            raise HTTPException(status_code=409, detail=f"The save does not own the logged item {item_name}")
        if mon.source == "party" or mon.box == 0:
            slots.append(TeamSlotRequest.party(mon.slot, item_id=item_id))
        else:
            slots.append(TeamSlotRequest.box_mon(mon.box, mon.slot, item_id=item_id))

    source = Path(request.pc_state).expanduser().resolve()
    destination = (
        Path(request.output_state).expanduser().resolve()
        if request.output_state
        else _GAUNTLET_RUNS_DIR / f"{run_id}-fight-{position:02d}-prepared.ss0"
    )
    instance = MGBAInstance(request.rom, str(source), 74)
    try:
        report = prepare_party(
            instance, slots, calculator=calculator,
            allow_item_donor_boxes={
                box for box in range(1, 15)
                if box != config.NUZLOCKE_GRAVEYARD_BOX
            },
        )
        instance.save_state(destination)
    finally:
        instance.shutdown()
    applied = {
        "fight": position, "trainer": fights[position - 1].get("trainer"),
        "source_state": str(source), "prepared_state": str(destination),
        "party": [mon.display_name for mon in report.party],
        "box_swaps": [list(value) for value in report.moved_from_boxes],
        "item_changes": list(report.item_changes),
    }
    record.setdefault("applied_preparations", []).append(applied)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return applied


@app.post("/api/calc/flowchart/complete")
async def api_complete_calc_flowchart(request: CompleteFlowchartRequest) -> dict[str, Any]:
    global _FLOWCHART_CANCEL_EVENT
    """Build the uncapped contingency tree only when the user explicitly requests it.

    The normal calc stays responsive under its node/time budget. This endpoint removes
    both caps and relies on battle-state memoization plus the depth safety limit, making
    every non-zero modeled move/crit/KO branch navigable instead of leaving rare tails.
    """
    _FLOWCHART_CANCEL_EVENT = threading.Event()
    _flowchart_progress_reset()
    mode = normalize_game_mode(request.game_mode)
    calculator = DamageCalculator(default_field=FieldState(
        weather=request.weather,
        is_reflect=request.reflect,
        is_light_screen=request.light_screen,
    ), game_mode=mode)
    battles = load_trainer_battles_for_mode(mode)
    imported = _parse_imported_sets(request.imports, calculator)
    if not imported:
        raise HTTPException(status_code=400, detail="Import at least one Pokemon set.")
    trainer = _trainer_for_calc_request(request, calculator, battles)

    # Reuse team selection so boxes larger than six produce the same playable squad as
    # the line finder. Then map the selected payload back to live planner members.
    try:
        selected_names = {_normalize(name) for name in request.selected_team_names if name}
        selected_team = [m for m in imported if _normalize(m.name) in selected_names] if selected_names else imported[:6]
        if not selected_team:
            selected_team = imported[:6]
        _FLOWCHART_PROGRESS.update(phase="starting exhaustive expansion", pct=max(8.0, _FLOWCHART_PROGRESS["pct"]))
        tree = await asyncio.to_thread(
            _contingency_flowchart,
            selected_team, trainer, calculator,
            max_turns=max(1, min(60, request.max_turns)),
            force_enemy_crits=request.crit_safe,
            forced_lead=request.line_search_lead,
            player_move_overrides=request.player_move_overrides,
            forced_doubles_leads=_requested_doubles_leads(request, len(selected_team)) if trainer.is_double else None,
            exhaustive=True,
            node_budget=-1,
            time_budget_s=None,
            progress_callback=_flowchart_progress_update,
            cancel_event=_FLOWCHART_CANCEL_EVENT,
        )
        meta = tree.get("_meta", {})
        if meta.get("cancelled"):
            _FLOWCHART_PROGRESS.update(running=False, phase="cancelled", complete=False)
        else:
            _flowchart_progress_update(meta.get("expanded_nodes", 0), 0, complete=True)
        return {
            "contingency_flowchart": tree,
            "game_mode": mode,
            "contingency_flowchart_note": (
                "Expansion stopped early; the explored routes are preserved and remaining tails are marked."
                if meta.get("cancelled") else
                "Complete uncapped model: every non-zero AI move, player/enemy crit, miss, and distinct damage-roll HP state was expanded; equivalent guaranteed outcomes were merged."
            ),
        }
    except Exception:
        _FLOWCHART_PROGRESS.update(running=False, phase="failed")
        raise
    finally:
        _FLOWCHART_CANCEL_EVENT = None


@app.post("/api/calc/plan.pdf")
async def api_calc_plan_pdf(request: PlanPdfRequest) -> Response:
    # The flowchart can be enormous and is intentionally a separate interactive view.
    compact_result = dict(request.result)
    mode = normalize_game_mode(request.game_mode)
    compact_result.setdefault("game_mode", mode)
    compact_result.setdefault("game_label", "Pokémon Emerald" if mode == "pokemon-emerald" else "Run & Bun")
    compact_result.setdefault("mechanics", "Generation III" if mode == "pokemon-emerald" else "Run & Bun ruleset")
    compact_result.pop("contingency_flowchart", None)
    pdf = await asyncio.to_thread(build_battle_plan_pdf, compact_result, request.trainer_label)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", request.trainer_label).strip("-") or "battle-plan"
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-battle-plan.pdf"'},
    )


@app.post("/api/coach/step")
async def api_coach_step(request: CoachStepRequest) -> dict[str, Any]:
    mode = normalize_game_mode(request.game_mode)
    calculator = DamageCalculator(game_mode=mode)
    battles = load_trainer_battles_for_mode(mode)
    if request.trainer_id < 0 or request.trainer_id >= len(battles):
        raise HTTPException(status_code=404, detail="Trainer not found")
    team = _parse_imported_sets(request.imports, calculator)
    if not team:
        raise HTTPException(status_code=400, detail="Import at least one Pokemon set.")
    trainer = battles[request.trainer_id]
    enemies = _planned_enemies_for_trainer(trainer, calculator)
    if not enemies:
        raise HTTPException(status_code=400, detail="This trainer has no usable enemy sets.")
    return await asyncio.to_thread(_coach_step, team, enemies, trainer, request, calculator)


# ---- Interactive co-pilot ---------------------------------------------------------------
# A stateless "what do I do now?" step. The frontend owns the running battle state and reports
# it each turn (who is active, current HP/status, what the foe just did); this reuses the exact
# decision engine the flowchart/line-finder use (_best_calc_answer / _calc_switch_target /
# _best_player_action / _calc_enemy_choices) to answer for that one live position, then projects
# the resulting HP for each plausible enemy move so the next turn can pre-fill.

def _coach_roster_member(member: PlannedMember, slot: int) -> dict[str, Any]:
    return {
        "slot": slot,
        "name": member.name,
        "species": member.species,
        "max_hp": member.max_hp,
        "moves": [m for m in member.known_moves] or list(member.moves),
        "item": member.item,
    }


def _coach_roster_enemy(enemy: PlannedEnemy, slot: int) -> dict[str, Any]:
    return {
        "slot": slot,
        "name": enemy.name,
        "species": enemy.pokemon.species,
        "max_hp": enemy.max_hp,
        "moves": list(enemy.moves),
    }


def _coach_damage(dr: "DamageRange | None") -> dict[str, Any] | None:
    if dr is None:
        return None
    return {
        "move_name": dr.move_name,
        "min": dr.min_damage,
        "max": dr.max_damage,
        "min_pct": round(dr.min_percent * 100),
        "max_pct": round(dr.max_percent * 100),
        "ko_chance": round(dr.ko_chance * 100),
        "accuracy": round(dr.accuracy * 100),
    }


def _coach_ko_label(dr: "DamageRange | None", target_hp: int) -> str:
    if dr is None or dr.max_damage <= 0:
        return "no damage"
    if dr.min_damage >= target_hp:
        return "guaranteed KO"
    if dr.max_damage >= target_hp:
        return f"{round(dr.ko_chance * 100)}% to KO"
    hits = max(2, -(-target_hp // max(1, dr.max_damage)))  # ceil
    return f"~{hits}HKO"


def _coach_step(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    trainer: TrainerBattle,
    req: CoachStepRequest,
    calculator: DamageCalculator,
) -> dict[str, Any]:
    crit = req.crit_safe
    for index, consumed in enumerate(req.player_consumed_items):
        if index < len(team):
            team[index].consumed_item = bool(consumed)
    for index, consumed in enumerate(req.enemy_consumed_items):
        if index < len(enemies):
            enemies[index].consumed_item = bool(consumed)
    enemy_index = max(0, min(req.enemy_active, len(enemies) - 1))
    enemy = enemies[enemy_index]
    if req.enemy_hp is not None:
        enemy.hp = max(0, min(enemy.max_hp, req.enemy_hp))
    if req.enemy_status is not None:
        enemy.status = req.enemy_status or None

    # Battle start: recommend the lead the line-finder would pick.
    if req.player_active is None:
        lead = _best_calc_answer(team, enemy, calculator, force_enemy_crits=crit, allow_sac=True, enemy_will_intimidate=True)
        active_index = lead if lead is not None else 0
    else:
        active_index = max(0, min(req.player_active, len(team) - 1))
    active = team[active_index]
    if req.player_hp is not None:
        active.hp = max(0, min(active.max_hp, req.player_hp))
    if req.player_status is not None:
        active.status = req.player_status or None

    # Recommendation: mirror the sim — prefer a worthwhile switch, else the best move.
    switch_target = _calc_switch_target(team, active_index, enemy, calculator, force_enemy_crits=crit)
    best_move = _best_player_action(active, enemy, team, calculator)
    if switch_target is not None and switch_target != active_index:
        incoming = team[switch_target]
        recommendation = {
            "kind": "switch",
            "label": f"Switch to {incoming.name}",
            "switch_to": switch_target,
            "switch_to_name": incoming.name,
            "reason": f"{active.name} is a poor matchup here; {incoming.name} is the safer answer.",
            "damage": None,
            "consistency": "pivot",
        }
    else:
        dmg = best_move.damage
        recommendation = {
            "kind": "move",
            "label": f"Use {best_move.move_name}",
            "move_name": best_move.move_name,
            "reason": best_move.reason or "best available action",
            "damage": _coach_damage(dmg),
            "consistency": _coach_ko_label(dmg, enemy.hp) if dmg else "status / setup",
        }

    # Ranked alternative moves (so the user can deviate knowingly).
    alternatives: list[dict[str, Any]] = []
    for dr in _ranked_known_damage(calculator, active.calc_set(), enemy.calc_set(), active.known_moves)[:4]:
        if recommendation.get("move_name") == dr.move_name:
            continue
        alternatives.append({
            "move_name": dr.move_name,
            "damage": _coach_damage(dr),
            "consistency": _coach_ko_label(dr, enemy.hp),
        })

    # What the foe is likely to do, with a projected result per move.
    choices = [c for c in _calc_enemy_choices(enemy, active, team, calculator, force_enemy_crits=crit) if c.score > 0]
    if not choices:
        choices = _calc_enemy_choices(enemy, active, team, calculator, force_enemy_crits=crit)[:1]
    enemy_prediction: list[dict[str, Any]] = []
    warnings: list[str] = []
    for choice in choices[:4]:
        proj = _coach_project(team, enemies, active_index, enemy_index, recommendation, best_move, choice, calculator, crit)
        edmg = choice.damage
        if edmg is not None and edmg.max_damage >= active.hp and recommendation["kind"] == "move":
            warnings.append(f"{enemy.name}'s {choice.move_name} can KO {active.name} ({round(edmg.ko_chance * 100)}%).")
        enemy_prediction.append({
            "move": choice.move_name,
            "probability": round(choice.probability, 3),
            "damage": _coach_damage(edmg),
            "reason": choice.reason,
            "projection": proj,
        })

    return {
        "trainer": trainer.trainer_name,
        "team": [_coach_roster_member(m, i) for i, m in enumerate(team)],
        "enemies": [_coach_roster_enemy(e, i) for i, e in enumerate(enemies)],
        "player_active": active_index,
        "player_hp": max(0, active.hp),
        "player_max_hp": active.max_hp,
        "player_status": active.status,
        "enemy_active": enemy_index,
        "enemy_hp": max(0, enemy.hp),
        "enemy_max_hp": enemy.max_hp,
        "enemy_status": enemy.status,
        "player_consumed_items": [bool(m.consumed_item) for m in team],
        "enemy_consumed_items": [bool(e.consumed_item) for e in enemies],
        "recommendation": recommendation,
        "alternatives": alternatives,
        "enemy_prediction": enemy_prediction,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _coach_project(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    active_index: int,
    enemy_index: int,
    recommendation: dict[str, Any],
    best_move: PlayerAction,
    enemy_choice: MoveChoice,
    calculator: DamageCalculator,
    crit: bool,
) -> dict[str, Any]:
    """Resolve one turn (your recommended action + this enemy move) on clones, for HP pre-fill."""
    team = copy.deepcopy(team)
    enemies = copy.deepcopy(enemies)
    enemy = enemies[enemy_index]
    enemy_hp_at_turn_start = enemy.hp
    enemy_hp_before_player = enemy.hp
    enemy_acted_before_player = False
    enemy_recoil_damage = 0

    if recommendation["kind"] == "switch":
        enemy_acted_before_player = True
        active_index = recommendation["switch_to"]
        active = team[active_index]
        retargeted = _retarget_choice_for_calc(enemy, active, enemy_choice, calculator, force_enemy_crits=crit)
        if active.alive and not _skip_turn(active):
            dealt = _apply_enemy_action(enemy, active, _choice_as_critical(enemy, active, retargeted, calculator) if crit else retargeted, calculator)
            enemy_recoil_damage = _recoil_for_applied_hit(enemy, retargeted.move_name if retargeted else "", dealt, calculator)
        enemy_hp_before_player = enemy.hp
    else:
        active = team[active_index]
        move = _refresh_player_action(active, enemy, best_move, calculator)
        enemy_first = _enemy_moves_before_player(enemy, active, enemy_choice.move_name, move.move_name, calculator)
        def do_enemy() -> None:
            nonlocal enemy_hp_before_player, enemy_acted_before_player, enemy_recoil_damage
            if enemy.alive and active.alive and not _skip_turn(enemy):
                dealt = _apply_enemy_action(enemy, active, _choice_as_critical(enemy, active, enemy_choice, calculator) if crit else enemy_choice, calculator)
                enemy_recoil_damage = _recoil_for_applied_hit(enemy, enemy_choice.move_name, dealt, calculator)
            if enemy_first:
                enemy_acted_before_player = True
                enemy_hp_before_player = enemy.hp
        def do_player() -> None:
            if active.alive and not _skip_turn(active):
                _apply_player_action(active, enemy, move, calculator)
        if enemy_first:
            do_enemy(); do_player()
        else:
            do_player(); do_enemy()

    _end_of_turn(active, enemy, calculator)
    enemy_fainted = not enemy.alive
    player_fainted = not active.alive
    enemy_next = enemy_index
    if enemy_fainted:
        _mark_enemy_allies_fainted(enemies, enemy_index)
        nxt = _next_calc_enemy(enemies, team, active_index, calculator, force_enemy_crits=crit)
        if nxt is not None:
            enemy_next = nxt
    your_next = active_index
    if player_fainted:
        repl = _best_calc_answer(team, enemies[enemy_next], calculator, force_enemy_crits=crit, allow_sac=True)
        if repl is not None:
            your_next = repl
    return {
        "player_hp": max(0, active.hp),
        "enemy_hp": max(0, enemy.hp),
        "player_fainted": player_fainted,
        "enemy_fainted": enemy_fainted,
        "enemy_next_slot": enemy_next,
        "your_next_slot": your_next,
        "battle_won": enemy_fainted and not any(e.alive for e in enemies),
        "enemy_self_damage": max(0, enemy_hp_at_turn_start - enemy_hp_before_player) if enemy_acted_before_player else 0,
        "enemy_recoil_damage": enemy_recoil_damage,
        "enemy_hp_before_player": max(0, enemy_hp_before_player),
        "recoil_flips_player_ko": bool(
            recommendation.get("kind") == "move"
            and enemy_acted_before_player
            and best_move.damage is not None
            and best_move.damage.min_damage < enemy_hp_at_turn_start
            and best_move.damage.min_damage >= max(1, enemy_hp_before_player)
        ),
        "player_consumed_items": [bool(m.consumed_item) for m in team],
        "enemy_consumed_items": [bool(e.consumed_item) for e in enemies],
    }


def _recoil_for_applied_hit(
    attacker: PlannedMember | PlannedEnemy,
    move_name: str,
    damage_dealt: int,
    calculator: DamageCalculator,
) -> int:
    """Return the recoil caused by this hit, even if a berry heals it immediately."""
    move = calculator.moves.get(_normalize(move_name), {})
    recoil = move.get("recoil")
    if not recoil or damage_dealt <= 0 or _normalize(attacker.calc_set().ability) == "rockhead":
        return 0
    num, den = recoil
    return max(1, int(damage_dealt * abs(num) / max(1, den)))


def _choice_as_critical(
    enemy: PlannedEnemy,
    target: PlannedMember,
    choice: MoveChoice | None,
    calculator: DamageCalculator,
) -> MoveChoice | None:
    """Return the same AI choice with critical-hit damage for crit-aware previews."""
    if choice is None or choice.damage is None or choice.damage.max_damage <= 0:
        return choice
    damage = calculator.estimate_move(
        enemy.calc_set(), target.calc_set(), choice.move_name, DamageContext(critical=True)
    )
    if damage is None:
        return choice
    return MoveChoice(
        choice.move_name, choice.score, choice.probability, damage,
        f"{choice.reason}; crit-aware",
    )


@app.get("/api/result")
async def api_result() -> Any:
    if last_result is None:
        # A fresh installation has no previous solve. This is an ordinary empty
        # state, not a failed request (and should not pollute the browser log).
        return Response(status_code=204)
    data = last_result.to_dict()
    if isinstance(last_result, SearchResult):
        data["battle_plan"] = _battle_plan(last_result, display_state)
    return data


def _open_gba_rom(rom_path: Path) -> None:
    stdout = subprocess.DEVNULL
    stderr = subprocess.DEVNULL
    executable = os.environ.get("MGBA_EXECUTABLE", config.MGBA_EXECUTABLE)
    executable_path = shutil.which(executable) if executable else None
    if executable_path:
        subprocess.Popen([executable_path, str(rom_path)], stdout=stdout, stderr=stderr)
        return
    if os.uname().sysname == "Darwin":
        try:
            subprocess.Popen(["open", "-a", "mGBA", str(rom_path)], stdout=stdout, stderr=stderr)
            return
        except Exception:
            subprocess.Popen(["open", str(rom_path)], stdout=stdout, stderr=stderr)
            return
    raise HTTPException(
        status_code=500,
        detail=f"mGBA executable {executable!r} was not found. Set MGBA_EXECUTABLE.",
    )


EMERALD_SAMPLE_CALC_IMPORT = """Swampert @ Mystic Water
Ability: Torrent
Level: 36
Adamant Nature
- Surf
- Mud Shot
- Rock Tomb
- Protect

Gardevoir @ Twisted Spoon
Ability: Synchronize
Level: 34
Modest Nature
- Psychic
- Calm Mind
- Thunderbolt
- Hypnosis

Breloom @ Miracle Seed
Ability: Effect Spore
Level: 33
Adamant Nature
- Mach Punch
- Sky Uppercut
- Leech Seed
- Headbutt

Manectric @ Magnet
Ability: Static
Level: 33
Timid Nature
- Thunderbolt
- Bite
- Thunder Wave
- Quick Attack

Crobat @ Sharp Beak
Ability: Inner Focus
Level: 34
Jolly Nature
- Aerial Ace
- Sludge Bomb
- Confuse Ray
- Bite

Torkoal @ Charcoal
Ability: White Smoke
Level: 33
Bold Nature
- Flamethrower
- Body Slam
- Protect
- Smokescreen"""


SAMPLE_CALC_IMPORT = """Nidoqueen @ Oran Berry
Ability: Poison Point
Level: 32
Mild Nature
IVs: 30 HP / 13 Atk / 19 Def / 3 SpA / 18 SpD / 7 Spe
- Mud Shot
- Bite
- Double Kick
- Sludge Bomb

Palpitoad @ Oran Berry
Ability: Poison Touch
Level: 32
Serious Nature
IVs: 15 HP / 11 Atk / 30 Def / 26 SpA / 27 SpD / 18 Spe
- Muddy Water
- Growl
- Mud Shot
- Hyper Voice

Drednaw
Ability: Shell Armor
Level: 32
Brave Nature
IVs: 0 HP / 10 Atk / 21 Def / 23 SpA / 18 SpD / 8 Spe
- Rock Slide
- Razor Shell
- Bite
- Ice Fang

Salazzle
Ability: Corrosion
Level: 32
Sassy Nature
IVs: 31 HP / 22 Atk / 5 Def / 4 SpA / 8 SpD / 9 Spe
- Dragon Rage
- Toxic
- Flame Burst
- Venoshock

Monferno @ Oran Berry
Ability: Vital Spirit
Level: 32
Lax Nature
IVs: 31 HP / 31 Atk / 31 Def / 31 SpA / 19 SpD / 24 Spe
- Low Sweep
- Fire Spin
- Flame Wheel
- Mach Punch

Shellder @ Oran Berry
Ability: Shell Armor
Level: 32
Calm Nature
IVs: 29 HP / 31 Atk / 16 Def / 13 SpA / 2 SpD / 2 Spe
- Aurora Beam
- Ice Shard
- Razor Shell
- Icicle Spear

Staravia @ Oran Berry
Ability: Intimidate
Level: 32
Brave Nature
IVs: 19 HP / 12 Atk / 21 Def / 12 SpA / 1 SpD / 5 Spe
- Dual Wingbeat
- Endeavor
- Quick Attack
- Aerial Ace

Growlithe-Hisui @ Oran Berry
Ability: Rock Head
Level: 32
Relaxed Nature
IVs: 31 HP / 31 Atk / 31 Def / 5 SpA / 26 SpD / 3 Spe
- Fire Fang
- Odor Sleuth
- Flame Wheel
- Rock Slide

Eldegoss
Ability: Cotton Down
Level: 32
Quiet Nature
IVs: 22 HP / 14 Atk / 0 Def / 31 SpA / 11 SpD / 31 Spe
- Synthesis
- Sing
- Rapid Spin
- Leaf Tornado

Seadra @ Oran Berry
Ability: Poison Point
Level: 32
Brave Nature
IVs: 6 HP / 23 Atk / 19 Def / 24 SpA / 4 SpD / 4 Spe
- Octazooka
- Smokescreen
- Clear Smog
- Aurora Beam

Carnivine @ Oran Berry
Ability: Levitate
Level: 32
Quiet Nature
IVs: 18 HP / 10 Atk / 23 Def / 28 SpA / 15 SpD / 3 Spe
- Leaf Blade
- Acid Spray
- Crunch
- Leaf Tornado

Tirtouga @ Oran Berry
Ability: Swift Swim
Level: 32
Sassy Nature
IVs: 9 HP / 9 Atk / 26 Def / 17 SpA / 27 SpD / 8 Spe
- Crunch
- Rock Slide
- Protect
- Aqua Jet

Hariyama
Ability: Thick Fat
Level: 32
Impish Nature
IVs: 16 HP / 13 Atk / 14 Def / 30 SpA / 5 SpD / 10 Spe
- Fake Out
- Vital Throw
- Smelling Salts
- Force Palm

Vibrava
Ability: Levitate
Level: 32
Modest Nature
IVs: 4 HP / 1 Atk / 9 Def / 28 SpA / 16 SpD / 26 Spe
- Crunch
- Bug Bite
- Rock Slide
- Dig"""


def _trainer_summary(index: int, battle: TrainerBattle, calculator: DamageCalculator) -> dict[str, Any]:
    return {
        "id": index,
        "label": f"{battle.trainer_name} - {battle.location or battle.section}",
        "trainer_name": battle.trainer_name,
        "location": battle.location,
        "section": battle.section,
        "is_double": battle.is_double,
        "required": battle.required,
        "map_location": battle.map_location,
        "sublocation": battle.sublocation,
        "source_row": battle.source_row,
        "party": [_trainer_mon_payload(mon, calculator) for mon in battle.party],
    }


def _trainer_mon_payload(mon: TrainerPokemon, calculator: DamageCalculator) -> dict[str, Any]:
    pokemon = calculator._known_set_from_trainer_mon(mon).pokemon  # noqa: SLF001
    return {
        "species": mon.species,
        "level": mon.level,
        "item": mon.held_item,
        "ability": mon.ability,
        "nature": mon.nature,
        "moves": list(mon.moves),
        "max_hp": pokemon.max_hp,
    }


def _emerald_level_guidance(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    result: dict[str, Any],
    requested_cap: int | None,
    calculator: DamageCalculator,
) -> dict[str, Any]:
    enemy_ace = max((mon.level or 1 for mon in trainer.party), default=1)
    caps_path = Path(__file__).resolve().parents[1] / "data" / "emerald_trainers.json"
    cap_rows = json.loads(caps_path.read_text(encoding="utf-8")).get("level_caps", [])
    inferred = next((int(row["ace_level"]) for row in cap_rows if int(row["ace_level"]) >= enemy_ace), 100)
    cap = int(requested_cap or inferred)
    over_cap = [member.name for member in team if member.level > cap]
    current_high = max((member.level for member in team), default=1)
    won = result.get("result") == "win-line"
    if won:
        recommendation = "No level grinding is required by the modeled line."
        target_min = None
        target_max = None
    elif current_high < min(cap, enemy_ace):
        target_min = min(cap, max(current_high + 1, enemy_ace - 2))
        target_max = min(cap, max(target_min, enemy_ace))
        recommendation = f"Try levels {target_min}-{target_max}; do not exceed the level-{cap} cap."
    else:
        target_min = None
        target_max = None
        recommendation = (
            "More levels are unlikely to be the main fix within this cap. Change the matchup, moves, "
            "lead, or party composition before EV training."
        )

    iv_notes: list[str] = []
    for member in team:
        ivs = member.ivs or {}
        weak = [stat.upper() for stat in ("hp", "def", "spd") if stat in ivs and ivs[stat] <= 5]
        if weak:
            iv_notes.append(f"{member.name} has low immutable {'/'.join(weak)} IVs; use a different answer if those stats decide survival.")
    return {
        "cap": cap,
        "cap_source": "Next Emerald Gym Leader's ace level",
        "enemy_ace": enemy_ace,
        "over_cap": over_cap,
        "legal": not over_cap,
        "target_min": target_min,
        "target_max": target_max,
        "recommendation": recommendation,
        "iv_notes": iv_notes[:3],
        "ev_note": "EV training is optional and is never the first recommendation; levels, typing, moves, and party swaps are checked first.",
    }


def _emerald_progressive_hints(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    result: dict[str, Any],
    levels: dict[str, Any],
    calculator: DamageCalculator,
) -> list[dict[str, Any]]:
    weakness_counts: dict[str, int] = {}
    for mon in trainer.party:
        species = calculator._species_data(mon.species) or {}  # noqa: SLF001
        defender_types = list(species.get("types") or [])
        for attack_type in calculator.type_chart:
            multiplier = calculator._type_multiplier(attack_type, defender_types)  # noqa: SLF001
            if multiplier > 1:
                weakness_counts[attack_type] = weakness_counts.get(attack_type, 0) + 1
    # Do not arbitrarily hide tied weaknesses. Roxanne, for example, has five
    # equally useful attacking types; cutting the list to four hid Water, the
    # most immediately obtainable answer for a Mudkip player.
    useful_types = [name for name, _ in sorted(weakness_counts.items(), key=lambda row: (-row[1], row[0]))]
    chosen = [str(mon.get("name") or mon.get("species")) for mon in result.get("team") or []]
    lead = (result.get("line_search") or {}).get("lead")
    lead_name = None
    if isinstance(lead, int) and 0 <= lead < len(chosen):
        lead_name = chosen[lead]
    elif isinstance(lead, str) and lead.strip():
        lead_name = lead.strip()
    first_turn = (result.get("turns") or [{}])[0]
    diagnosis = result.get("failure_diagnosis") or {}
    opening_text = str(first_turn.get("action") or first_turn.get("plan") or levels["recommendation"])
    if result.get("result") != "win-line":
        opening_text = str((diagnosis.get("recommended_steps") or [levels["recommendation"]])[0])
    return [
        {
            "level": 1,
            "title": "Matchup direction",
            "text": (
                f"Look for reliable {' / '.join(useful_types)} damage into this party. "
                f"The current legal cap is {levels['cap']}."
                if useful_types else f"Prioritize neutral damage and defensive consistency. The current cap is {levels['cap']}."
            ),
        },
        {
            "level": 2,
            "title": "What the party needs",
            "text": (
                "Bring at least one fast finisher, one safe switch for the opponent's strongest STAB, "
                "and a backup answer that does not rely on a critical hit or flinch."
            ),
        },
        {
            "level": 3,
            "title": "Suggested party",
            "text": (
                f"The solver's safest available group is: {', '.join(chosen) or 'no complete legal group found'}."
                + (f" Start by considering {lead_name}." if lead_name else "")
            ),
        },
        {
            "level": 4,
            "title": "Next action" if result.get("result") != "win-line" else "Opening action",
            "text": opening_text,
        },
    ]


def _emerald_failure_diagnosis(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    result: dict[str, Any],
    levels: dict[str, Any],
    calculator: DamageCalculator,
) -> dict[str, Any]:
    """Explain a failed Emerald solve as concrete, legal preparation steps.

    This deliberately distinguishes trainable levels/EVs from immutable IVs and
    never labels a fight impossible merely because the current import failed.
    """
    if result.get("result") == "win-line":
        return {
            "status": "ready",
            "summary": "The current party has a complete modeled line; no grinding is required.",
            "blockers": [],
            "recommended_steps": [],
        }

    enemies = _planned_enemies_for_trainer(trainer, calculator)
    answers = _threat_answers(team, enemies, calculator)
    no_clean = [row for row in answers if not row.get("clean")]
    weakness_counts: dict[str, int] = {}
    for mon in trainer.party:
        species = calculator._species_data(mon.species) or {}  # noqa: SLF001
        for attack_type in calculator.type_chart:
            if calculator._type_multiplier(attack_type, list(species.get("types") or [])) > 1:  # noqa: SLF001
                weakness_counts[attack_type] = weakness_counts.get(attack_type, 0) + 1
    useful_types = [name for name, _ in sorted(weakness_counts.items(), key=lambda row: (-row[1], row[0]))]
    owned_attack_types: set[str] = set()
    for member in team:
        for move_name in member.moves:
            move = calculator.moves.get(_normalize(move_name)) or {}
            if int(move.get("basePower") or move.get("power") or 0) > 0:
                owned_attack_types.add(_normalize(str(move.get("type") or "")))
    missing_coverage = [name for name in useful_types if _normalize(name) not in owned_attack_types]

    blockers: list[dict[str, str]] = []
    steps: list[str] = []
    cap = int(levels.get("cap") or 100)
    mudkip = next((member for member in team if _normalize(member.species) == "mudkip"), None)
    knows_water_gun = bool(mudkip and any(_normalize(move) == "watergun" for move in mudkip.moves))
    is_roxanne = _normalize(trainer.trainer_name) in {"leaderroxanne", "roxanne"}
    if is_roxanne and mudkip and (mudkip.level < 10 or not knows_water_gun) and cap >= 10:
        action = "Train Mudkip to level 10 and keep/learn Water Gun, then run the fight again. This stays below the level-15 Roxanne cap."
        blockers.append({
            "kind": "move-and-level",
            "title": "The party is missing its early Rock answer",
            "evidence": f"Mudkip is level {mudkip.level} and {'already knows' if knows_water_gun else 'does not know'} Water Gun; it learns Water Gun at level 10 in Emerald.",
            "action": action,
        })
        steps.append(action)
    elif levels.get("target_min"):
        action = str(levels["recommendation"])
        blockers.append({
            "kind": "levels",
            "title": "The party is below the fight's safe level range",
            "evidence": f"The opponent's ace is level {levels.get('enemy_ace')}; your highest current level is {max((member.level for member in team), default=1)}.",
            "action": action,
        })
        steps.append(action)

    if missing_coverage:
        coverage = " / ".join(missing_coverage[:6])
        action = f"Teach or bring a reliable damaging {missing_coverage[0]}-type move; other useful coverage here is {coverage}."
        blockers.append({
            "kind": "coverage",
            "title": "No current damaging move covers a key weakness",
            "evidence": f"The imported moves do not include these super-effective damage types: {coverage}.",
            "action": action,
        })
        if not steps:
            steps.append(action)

    if no_clean:
        names = ", ".join(str(row.get("enemy")) for row in no_clean[:4])
        best_bits = []
        for row in no_clean[:3]:
            best = row.get("best") or {}
            best_bits.append(
                f"{row.get('enemy')}: best current answer {best.get('mon') or 'none'} "
                f"takes up to {round(float(best.get('incoming') or 1) * 100)}% and deals up to {round(float(best.get('outgoing') or 0) * 100)}%"
            )
        action = f"Replace or improve the answer to {names}; re-run after the level/move change before doing EV training."
        if is_roxanne and knows_water_gun:
            action = (
                "Bring a second Rock-type answer—prefer a Grass attacker available before Rustboro—and train it within "
                f"the level-{cap} cap. Water Gun helps with Geodude, but the current party still has no safe Nosepass backup."
            )
        blockers.append({
            "kind": "survival",
            "title": "The solver found no clean answer to part of the opposing party",
            "evidence": "; ".join(best_bits),
            "action": action,
        })
        if action not in steps:
            steps.append(action)

    iv_notes = list(levels.get("iv_notes") or [])
    if iv_notes:
        blockers.append({
            "kind": "ivs",
            "title": "IVs cannot be trained in Pokémon Emerald",
            "evidence": " ".join(iv_notes),
            "action": "Do not grind IVs. If a damage threshold still fails after legal levels and moves, use a different Pokémon with better natural bulk.",
        })
    steps.append("Run the updated party again. Only consider targeted EV training if the new line still misses a specific damage or survival threshold.")
    return {
        "status": "needs-preparation",
        "summary": f"No safe complete line was found for the current party against {trainer.trainer_name}. The fight is not being called impossible; the app found preparation gaps.",
        "blockers": blockers,
        "recommended_steps": steps,
        "level_cap": cap,
        "ev_policy": "EVs are trainable, but they are a last resort after legal levels, moves, and party composition.",
    }


def _parse_imported_sets(raw: str, calculator: DamageCalculator) -> list[PlannedMember]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", raw or "") if block.strip()]
    members: list[PlannedMember] = []
    for slot, block in enumerate(blocks):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        header = lines[0]
        item = None
        if "@" in header:
            header, item = [part.strip() for part in header.split("@", 1)]
        name = header
        species = header
        paren = re.search(r"^(.*?)\s*\((.*?)\)\s*$", header)
        if paren:
            name = paren.group(1).strip() or paren.group(2).strip()
            species = paren.group(2).strip()
        level = 50
        ability = None
        nature = None
        ivs: dict[str, int] | None = None
        evs: dict[str, int] | None = None
        hp_override: tuple[int, int | None] | None = None
        moves: list[str] = []
        for line in lines[1:]:
            low = line.casefold()
            if low.startswith("level:"):
                try:
                    level = int(re.sub(r"[^0-9]", "", line.split(":", 1)[1]) or "50")
                except ValueError:
                    level = 50
            elif low.startswith("ability:"):
                ability = line.split(":", 1)[1].strip()
            elif low.startswith("ivs:"):
                ivs = _parse_stat_spread(line.split(":", 1)[1])
            elif low.startswith("evs:"):
                evs = _parse_stat_spread(line.split(":", 1)[1])
            elif low.startswith("hp:"):
                hp_override = _parse_hp_line(line.split(":", 1)[1])
            elif low.endswith("nature"):
                nature = line.removesuffix("Nature").strip()
            elif low.startswith("-"):
                moves.append(line[1:].strip())
            elif "|" in line:
                moves.extend(part.strip() for part in line.split("|") if part.strip())
        species_data = calculator._species_data(species) or calculator._species_data(name)  # noqa: SLF001
        if not species_data:
            continue
        species = str(species_data.get("name", species))
        max_hp = calculator._stat(species_data, "hp", level, nature, evs, ivs)  # noqa: SLF001
        hp = max_hp
        if hp_override is not None:
            current_hp, imported_max_hp = hp_override
            if imported_max_hp:
                max_hp = imported_max_hp
            hp = max(1, min(max_hp, current_hp))
        members.append(
            PlannedMember(
                name=name,
                species=species,
                level=level,
                max_hp=max_hp,
                hp=hp,
                moves=tuple(moves[:4]),
                item=item,
                ability=ability,
                nature=nature,
                evs=evs,
                ivs=ivs,
                source="import",
                slot=slot,
            )
        )
    return members


_STAT_SPREAD_KEYS = {
    "hp": "hp",
    "atk": "atk",
    "def": "def",
    "spa": "spa",
    "spd": "spd",
    "spe": "spe",
}


def _parse_stat_spread(raw: str) -> dict[str, int] | None:
    spread: dict[str, int] = {}
    for part in raw.split("/"):
        match = re.search(r"(\d+)\s*([A-Za-z]+)", part.strip())
        if not match:
            continue
        key = _STAT_SPREAD_KEYS.get(match.group(2).casefold())
        if key:
            spread[key] = int(match.group(1))
    return spread or None


def _parse_hp_line(raw: str) -> tuple[int, int | None] | None:
    match = re.search(r"(\d+)\s*(?:/\s*(\d+))?", raw)
    if not match:
        return None
    current = int(match.group(1))
    max_hp = int(match.group(2)) if match.group(2) else None
    return current, max_hp


def _trainer_for_calc_request(
    request: CalcSimRequest,
    calculator: DamageCalculator,
    battles: list[TrainerBattle] | None = None,
) -> TrainerBattle:
    """Resolve either a built-in trainer or a user-authored opponent roster."""
    if request.custom_enemy_imports.strip():
        custom = _parse_imported_sets(request.custom_enemy_imports, calculator)
        if not custom:
            raise HTTPException(status_code=400, detail="Add at least one valid opponent Pokemon to the custom battle.")
        trainer = TrainerBattle(
            section="Custom tournament",
            location="Custom battle",
            trainer_name=request.custom_trainer_name.strip() or "Custom Doubles",
            is_double=bool(request.custom_is_double),
            party=tuple(
                TrainerPokemon(
                    species=member.species,
                    level=member.level,
                    held_item=member.item,
                    ability=member.ability,
                    nature=member.nature,
                    moves=member.moves,
                )
                for member in custom
            ),
        )
    else:
        available = battles if battles is not None else load_trainer_battles()
        if request.trainer_id < 0 or request.trainer_id >= len(available):
            raise HTTPException(status_code=404, detail="Trainer not found")
        trainer = available[request.trainer_id]

    # The two chosen opponent leads become field slot 1 and field slot 2 while
    # every other party member retains relative bench order.
    if trainer.is_double and len(request.enemy_leads) >= 2:
        try:
            first, second = int(request.enemy_leads[0]), int(request.enemy_leads[1])
        except (TypeError, ValueError):
            first, second = -1, -1
        if first != second and 0 <= first < len(trainer.party) and 0 <= second < len(trainer.party):
            order = [first, second] + [i for i in range(len(trainer.party)) if i not in {first, second}]
            trainer = TrainerBattle(
                section=trainer.section,
                location=trainer.location,
                trainer_name=trainer.trainer_name,
                is_double=True,
                party=tuple(trainer.party[i] for i in order),
            )
    return trainer


def _requested_doubles_leads(request: CalcSimRequest, team_size: int) -> tuple[int, int] | None:
    if len(request.player_leads) < 2:
        return None
    try:
        first, second = int(request.player_leads[0]), int(request.player_leads[1])
    except (TypeError, ValueError):
        return None
    if first == second or first < 0 or second < 0 or first >= team_size or second >= team_size:
        return None
    return first, second


def _planned_enemies_for_trainer(trainer: TrainerBattle, calculator: DamageCalculator) -> list[PlannedEnemy]:
    return [
        PlannedEnemy(
            name=known.pokemon.species,
            pokemon=known.pokemon,
            moves=known.moves,
            max_hp=known.pokemon.max_hp or _max_hp(known.pokemon, calculator),
            hp=known.pokemon.max_hp or _max_hp(known.pokemon, calculator),
        )
        for known in (calculator._known_set_from_trainer_mon(mon) for mon in trainer.party)  # noqa: SLF001
    ]


def _line_quality_key(result: dict[str, Any]) -> tuple[float, ...]:
    win = 1.0 if result.get("result") == "win-line" else 0.0
    deaths = sum(1.0 for member in result.get("team", []) if member.get("hp", 0) <= 0)
    remaining = sum(member.get("hp", 0) / max(1, member.get("max_hp", 1)) for member in result.get("team", []))
    confidence = float(result.get("confidence") or 0.0)
    deathless = 1.0 if deaths == 0 else 0.0
    range_safe = 1.0 if deathless and confidence >= 0.999 else 0.0
    fragile = _fragile_answer_analysis(result)
    no_fragile_answers = 1.0 if not fragile["events"] else 0.0
    # Lowest-priority tiebreaker: how much enemy HP is left. Between two otherwise-equal
    # lines (same win/confidence/deaths/own-HP), the one that chipped the enemies more is
    # better — this lets offensive item upgrades register even on non-winning partial lines.
    enemy_remaining = sum(
        e.get("hp", 0) / max(1, e.get("max_hp", 1)) for e in result.get("enemies", [])
    )
    enemy_count = max(1, len(result.get("enemies", [])))
    enemy_progress = enemy_count - enemy_remaining
    # Sacrifices are already counted as deaths; ranking labeled sacs separately let
    # the search prefer unlabeled deliberate deaths over higher-confidence lines.
    # Confidence is banded to one decimal so a clearly more stable line wins even
    # at the cost of a death, while deaths still decide within a band.
    # Team optimization is Nuzlocke-first: among winning candidates, a deathless
    # roster always beats a risky one, and a range/crit-safe line always beats a
    # merely workable clear. Confidence and retained HP only break ties afterward.
    return (
        # On incomplete lines, progress must precede survivor count. Otherwise the
        # search can prefer a truncated early sacrifice (one teammate still alive,
        # most enemies untouched) over a line that nearly clears the fight. Winning
        # lines all have identical full progress, so the Nuzlocke safety ordering
        # below remains unchanged for actual clears.
        win, enemy_progress, deathless, no_fragile_answers, range_safe, -deaths,
        -float(fragile["events"]), float(fragile["minimum_hp_ratio"]),
        confidence, remaining, -enemy_remaining,
    )


def _fragile_answer_analysis(result: dict[str, Any]) -> dict[str, Any]:
    """Find answers that repeatedly finish an exchange in crit-death range.

    A low-HP survivor is only marked fragile when the same incoming roll would become
    lethal after the Gen-3 critical multiplier. This prevents harmless 1 HP endings after
    the opponent faints from disqualifying an otherwise safe answer.
    """
    events = 0
    minimum_ratio = 1.0
    pokemon: set[str] = set()
    for turn in result.get("turns") or []:
        board = (turn.get("board_after") or {}).get("player") or []
        low_survivors: list[tuple[str, int, int]] = []
        for member in board:
            if not member or int(member.get("hp") or 0) <= 0:
                continue
            hp = int(member.get("hp") or 0)
            max_hp = max(1, int(member.get("max_hp") or hp))
            minimum_ratio = min(minimum_ratio, hp / max_hp)
            if hp == 1:
                low_survivors.append((str(member.get("name") or "unknown"), hp, max_hp))
        if not low_survivors:
            continue
        incoming = [
            option
            for branch in (turn.get("fork") or {}).get("doubles_damage_options") or []
            if str(branch.get("axis") or "").startswith("e")
            for option in branch.get("options") or []
        ]
        incoming.extend(
            option
            for option in (turn.get("fork") or {}).get("enemy_damage_outcomes") or []
        )
        forced_critical = bool((turn.get("fork") or {}).get("enemy_crits_forced"))
        crit_exposed = not forced_critical and any(
            int(option.get("damage") or 0) >= int(option.get("remaining_hp") or 0) > 0
            for option in incoming
        )
        if crit_exposed:
            events += len(low_survivors)
            pokemon.update(name for name, _hp, _max_hp in low_survivors)
    return {
        "events": events,
        "pokemon": sorted(pokemon),
        "minimum_hp_ratio": round(minimum_ratio, 6),
        "requires_box_alternative": events > 0,
    }


def _candidate_team_indices(
    imported: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    *,
    force_enemy_crits: bool = False,
    team_size: int = 6,
) -> list[tuple[int, ...]]:
    """Build candidate 6-member rosters from a larger box of imported sets."""
    enemies = _planned_enemies_for_trainer(trainer, calculator)
    per_enemy: list[list[tuple[float, int, bool]]] = []
    overall: dict[int, float] = {index: 0.0 for index in range(len(imported))}
    for enemy in enemies:
        team_clone = _clone_calc_team(imported)
        ranked = _rank_calc_answers(
            team_clone,
            enemy,
            calculator,
            force_enemy_crits=force_enemy_crits,
            enemy_will_intimidate=True,
        )
        rows = [(score, index, dies) for score, index, _, _, dies in ranked]
        per_enemy.append(rows)
        for score, index, dies in rows:
            overall[index] += score - (300.0 if dies else 0.0)

    overall_order = sorted(overall, key=lambda index: overall[index], reverse=True)

    def fill(team: list[int]) -> tuple[int, ...]:
        for index in overall_order:
            if len(team) >= team_size:
                break
            if index not in team:
                team.append(index)
        return tuple(sorted(team[:team_size]))

    candidates: list[tuple[int, ...]] = []

    def add(team: tuple[int, ...]) -> None:
        if team and team not in candidates:
            candidates.append(team)

    # Greedy by enemy coverage: best safe answer per enemy, then best overall fillers.
    for depth in (0, 1):
        greedy: list[int] = []
        for rows in per_enemy:
            safe = [index for _, index, dies in rows if not dies and index not in greedy]
            if len(safe) > depth:
                greedy.append(safe[depth])
            elif safe:
                greedy.append(safe[0])
        add(fill(greedy))

    # Top overall scorers.
    add(fill([]))

    # Two safe answers per enemy, dedup, best-first.
    doubled: list[int] = []
    for rows in per_enemy:
        safe = [index for _, index, dies in rows if not dies]
        for index in safe[:2]:
            if index not in doubled:
                doubled.append(index)
    add(fill(doubled))

    # Resist/answer-coverage roster: for each enemy take the imported mons that best
    # ANSWER it (tank its best hit, outspeed, hit hard), then fill. Added as an extra
    # candidate only — the selector still keeps whichever roster sims the cleanest line,
    # so this can lift hard fights (Ghost/Steel/bulky walls) without hurting any other.
    coverage: list[int] = []
    answer_rank = _rank_answer_coverage(imported, enemies, calculator)
    for enemy_best in answer_rank:
        for index in enemy_best[:2]:
            if index not in coverage:
                coverage.append(index)
    add(fill(coverage))

    # User order baseline (what the old behavior would have used).
    add(tuple(sorted(range(min(team_size, len(imported))))))
    return candidates


def _route_coverage_candidates(
    imported: list[PlannedMember],
    trainers: list[TrainerBattle],
    calculator: DamageCalculator,
    *,
    force_enemy_crits: bool,
    leveling_policy: str,
    team_size: int = 6,
    beam_width: int = 96,
) -> tuple[list[tuple[int, ...]], dict[str, Any]]:
    """Deterministically pair Pokémon that answer several route threats.

    Each member receives a normalized answer score against every opposing Pokémon.
    A bounded beam then builds six-member teams by rewarding primary coverage,
    a second safe answer, and members that cover threats across multiple trainers.
    This is faster than simulating all nC6 teams while remaining stable across runs.
    """
    if not imported or not trainers:
        return [], {"policy": "paired-route-coverage", "candidates": 0}

    foe_scores: list[list[float]] = []
    foe_trainers: list[int] = []
    member_good_answers = [0 for _ in imported]
    member_trainer_answers = [set() for _ in imported]
    for trainer_index, trainer in enumerate(trainers):
        preview = imported
        if leveling_policy == "boss-cap":
            ace = max((mon.level or 1 for mon in trainer.party), default=1)
            preview = [_emerald_rare_candy_raise(member, ace, calculator) for member in imported]
        enemies = _planned_enemies_for_trainer(trainer, calculator)
        for enemy in enemies:
            ranked = _rank_calc_answers(
                _clone_calc_team(preview), enemy, calculator,
                force_enemy_crits=force_enemy_crits,
                enemy_will_intimidate=True,
            )
            count = max(1, len(ranked) - 1)
            scores = [0.0 for _ in imported]
            for rank, (_raw, member_index, _action, _choice, dies) in enumerate(ranked):
                normalized = max(0.0, 1.0 - rank / count)
                if dies:
                    normalized *= 0.2
                scores[member_index] = normalized
                if normalized >= 0.65:
                    member_good_answers[member_index] += 1
                    member_trainer_answers[member_index].add(trainer_index)
            foe_scores.append(scores)
            foe_trainers.append(trainer_index)

    score_cache: dict[tuple[int, ...], tuple[float, ...]] = {}

    def coverage_key(indices: tuple[int, ...]) -> tuple[float, ...]:
        cached = score_cache.get(indices)
        if cached is not None:
            return cached
        primary = 0.0
        backup = 0.0
        strong_foes = 0
        trainer_covered: set[int] = set()
        for foe_index, scores in enumerate(foe_scores):
            answers = sorted((scores[index] for index in indices), reverse=True)
            best = answers[0] if answers else 0.0
            second = answers[1] if len(answers) > 1 else 0.0
            primary += best
            backup += second
            if best >= 0.65:
                strong_foes += 1
                trainer_covered.add(foe_trainers[foe_index])
        multi_route = sum(
            max(0, len(member_trainer_answers[index]) - 1)
            for index in indices
        )
        multi_foe = sum(max(0, member_good_answers[index] - 1) for index in indices)
        # Tuple order expresses policy: cover every trainer first, then every foe,
        # then reward compact multi-fight answers and reliable backups.
        key = (
            float(len(trainer_covered)), float(strong_foes),
            round(primary, 6), float(multi_route), float(multi_foe),
            round(backup, 6),
        )
        score_cache[indices] = key
        return key

    beam: list[tuple[int, ...]] = [tuple()]
    for _size in range(1, min(team_size, len(imported)) + 1):
        expanded = {
            tuple(sorted((*indices, outsider)))
            for indices in beam
            for outsider in range(len(imported))
            if outsider not in indices
        }
        beam = sorted(
            expanded,
            key=lambda indices: (coverage_key(indices), tuple(-index for index in indices)),
            reverse=True,
        )[:beam_width]

    # Keep the complete bounded beam.  Thirty-two candidates was too narrow for
    # complementary late-route teams: a member that is merely adequate across the
    # route can be the exact bridge that preserves the Wallace answer through Drake.
    # The downstream cheap scorer still refines only its strongest bounded subset.
    candidates = beam[: min(beam_width, len(beam))]
    best = candidates[0] if candidates else tuple()
    member_payload = sorted((
        {
            "index": index,
            "pokemon": member.name,
            "foes_answered": member_good_answers[index],
            "trainers_answered": len(member_trainer_answers[index]),
        }
        for index, member in enumerate(imported)
    ), key=lambda row: (-row["trainers_answered"], -row["foes_answered"], row["index"]))
    return candidates, {
        "policy": "paired-route-coverage",
        "beam_width": beam_width,
        "candidates": len(candidates),
        "opponents_scored": len(foe_scores),
        "best_indices": list(best),
        "best_score": list(coverage_key(best)) if best else [],
        "multi_fight_answers": member_payload,
    }


def _future_route_protected_slots(
    team: list[PlannedMember],
    future_trainers: list[TrainerBattle],
    calculator: DamageCalculator,
    *,
    force_enemy_crits: bool,
    limit: int = 3,
) -> set[int]:
    """Return the party slots with the most answer coverage in later fights."""
    if not future_trainers:
        return set()
    points = {member.slot: 0.0 for member in team if member.hp > 0}
    for trainer in future_trainers:
        for enemy in _planned_enemies_for_trainer(trainer, calculator):
            ranked = _rank_calc_answers(
                _clone_calc_team(team), enemy, calculator,
                force_enemy_crits=force_enemy_crits,
                enemy_will_intimidate=True,
            )
            for rank, (_score, member_index, _action, _choice, dies) in enumerate(ranked[:4]):
                member = team[member_index]
                if member.hp <= 0:
                    continue
                points[member.slot] = points.get(member.slot, 0.0) + max(0.0, 4.0 - rank) * (0.25 if dies else 1.0)
    ordered = sorted(points, key=lambda slot: (-points[slot], slot))
    return set(ordered[:limit])


def _future_preserving_quality(protected_slots: set[int]):
    """Build a deterministic line key that saves later-route answers on ties."""
    def quality(result: dict[str, Any]) -> tuple[float, ...]:
        base = _line_quality_key(result)
        team = result.get("team") or []
        total_deaths = sum(1 for member in team if int(member.get("hp") or 0) <= 0)
        protected_deaths = sum(
            1 for member in team
            if member.get("slot") in protected_slots and int(member.get("hp") or 0) <= 0
        )
        return base[:3] + (-float(total_deaths), -float(protected_deaths)) + base[3:]
    return quality


def _search_future_preserving_line(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    *,
    max_turns: int,
    force_enemy_crits: bool,
    protected_slots: set[int],
    budget: int = 120,
    focused_beam: bool = False,
) -> dict[str, Any]:
    """Search the normal best line and a later-route-preserving variant.

    Keeping the baseline prevents future-value weighting from getting trapped in
    an incomplete local optimum. Both passes are deterministic, and their total
    budget remains bounded.
    """
    baseline_budget = max(40, budget)
    protected_budget = max(30, budget // 2)
    baseline = _search_best_line(
        _clone_calc_team(team), trainer, calculator,
        max_turns=max_turns, force_enemy_crits=force_enemy_crits,
        budget=baseline_budget, focused_beam=focused_beam,
    )
    seed = {
        int(turn): slot
        for turn, slot in ((baseline.get("line_search") or {}).get("overrides") or {}).items()
    } or None
    protected = _search_best_line(
        _clone_calc_team(team), trainer, calculator,
        max_turns=max_turns, force_enemy_crits=force_enemy_crits,
        budget=protected_budget, seed_overrides=seed,
        quality_fn=_future_preserving_quality(protected_slots),
    )
    quality = _future_preserving_quality(protected_slots)
    winner = protected if quality(protected) > quality(baseline) else baseline
    winner["future_answer_protection"] = {
        "protected_slots": sorted(protected_slots),
        "baseline_budget": baseline_budget,
        "protected_budget": protected_budget,
        "policy": "baseline-plus-future-preserving-pass",
    }
    return winner


def _rank_answer_coverage(
    imported: list[PlannedMember],
    enemies: list[PlannedEnemy],
    calculator: DamageCalculator,
) -> list[list[int]]:
    """For each enemy, the imported indices ordered by how cleanly they answer it
    (survive the best hit + outspeed + hit hard). Used only to seed an extra candidate
    roster, never to score existing ones."""
    ranked: list[list[int]] = []
    for enemy in enemies:
        scored: list[tuple[float, int]] = []
        for index, member in enumerate(imported):
            action = _best_player_action(member, enemy, imported, calculator)
            choices = _calc_enemy_choices(enemy, member, imported, calculator)
            choice = choices[0] if choices else None
            incoming = (choice.damage.max_damage / max(1, member.max_hp)) if (choice and choice.damage) else 1.0
            outgoing = 0.0
            if action and getattr(action, "move_name", ""):
                try:
                    dmg = calculator.estimate_move(member.calc_set(), enemy.calc_set(), action.move_name)
                    outgoing = (dmg.max_damage / max(1, enemy.max_hp)) if dmg else 0.0
                except Exception:
                    outgoing = 0.0
            try:
                faster = _speed(member.calc_set(), calculator) > _speed(enemy.calc_set(), calculator)
            except Exception:
                faster = False
            quality = (1.0 - min(1.0, incoming)) * 2.0 + min(1.5, outgoing) + (0.4 if faster else 0.0)
            scored.append((quality, index))
        scored.sort(key=lambda row: row[0], reverse=True)
        ranked.append([index for _, index in scored])
    return ranked


_TYPE_BOOST_ITEM = {
    "Grass": "Miracle Seed", "Water": "Mystic Water", "Fire": "Charcoal",
    "Electric": "Magnet", "Ground": "Soft Sand", "Fighting": "Black Belt",
    "Flying": "Sharp Beak", "Poison": "Poison Barb", "Psychic": "Twisted Spoon",
    "Ice": "Never-Melt Ice", "Dragon": "Dragon Fang", "Rock": "Hard Stone",
    "Normal": "Silk Scarf", "Dark": "Black Glasses", "Ghost": "Spell Tag",
    "Steel": "Metal Coat", "Bug": "Silver Powder", "Fairy": "Fairy Feather",
}


def _unavailable_items_for_trainer(trainer: TrainerBattle) -> set[str]:
    """Progression overrides where a broad split label is too coarse.

    Breeder Corgi is encountered before the player can obtain Sitrus Berries,
    even though the imported trainer record groups the fight in Wattson Split.
    Keep this explicit so a corrected location label cannot silently re-enable it.
    """
    if _normalize(trainer.trainer_name) == "breedercorgi":
        return {"Sitrus Berry"}
    return set()


def _recommend_items_for_trainer(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    calculator: DamageCalculator,
    trainer: TrainerBattle,
) -> list[dict[str, Any]]:
    return recommend_held_items(
        team, enemies, calculator, section=trainer.section,
        unavailable_items=_unavailable_items_for_trainer(trainer),
    )


def _greedy_items(
    roster: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    enemies: list[PlannedEnemy],
    sim_fn,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Greedily adopt recommended held items for a roster against one trainer.

    The held-item recommender tends to swap defensive berries (Oran/Sitrus) for offensive
    items wholesale, which can lose the recovery that keeps a line alive. Instead, start
    from the imported items and adopt each candidate change ONLY if it improves the line
    `sim_fn` produces — so genuine upgrades (e.g. Miracle Seed turning a 2HKO into an OHKO)
    are kept and harmful swaps dropped. `sim_fn(team) -> result` runs one evaluation line
    (single-pass) and must not mutate the team it is given beyond a throwaway clone.

    Returns (best_line, adopted_changes).
    """
    best_line = sim_fn(_clone_calc_team(roster))
    recs = list(_recommend_items_for_trainer(_clone_calc_team(roster), enemies, calculator, trainer))

    # Add each mon's STAB type-boost item and a Sitrus Berry as extra candidates; the
    # singles recommender can miss the strongest type item (e.g. Wise Glasses +10% over
    # Miracle Seed +20% for a Grass attacker).
    seen_recs = {(_normalize(r.get("pokemon", "")), _normalize(str(r.get("suggested_item", "")))) for r in recs}
    for member in roster:
        species_data = calculator._species_data(member.calc_set().species)  # noqa: SLF001
        types = species_data.get("types", []) if species_data else []
        unavailable = {_normalize(item) for item in _unavailable_items_for_trainer(trainer)}
        for item in [_TYPE_BOOST_ITEM[t] for t in types if t in _TYPE_BOOST_ITEM] + ["Sitrus Berry"]:
            if _normalize(item) in unavailable:
                continue
            key = (_normalize(member.name), _normalize(item))
            if key in seen_recs:
                continue
            seen_recs.add(key)
            recs.append({"pokemon": member.name, "suggested_item": item, "old_item": member.item,
                         "reason": f"{item} (type/recovery boost)", "source": "STAB item", "score": 5.0})

    # Track the adopted loadout as an item map; rebuild a pristine team for each trial so
    # simmed (damaged) state never leaks between candidates.
    items_by_idx: dict[int, str] = {}
    by_name = {_normalize(m.name): i for i, m in enumerate(roster)}
    adopted: list[dict[str, Any]] = []
    for rec in sorted(recs, key=lambda r: r.get("score", 0.0), reverse=True):
        idx = by_name.get(_normalize(str(rec.get("pokemon", ""))))
        if idx is None:
            continue
        trial_items = dict(items_by_idx)
        trial_items[idx] = rec["suggested_item"]
        trial = _clone_calc_team([
            replace_member_item(m, trial_items[i]) if i in trial_items else m
            for i, m in enumerate(roster)
        ])
        line = sim_fn(trial)
        if _normalize(rec["suggested_item"]) == "sitrusberry" and not _scarce_sitrus_is_worth_it(best_line, line):
            continue
        if _line_quality_key(line) > _line_quality_key(best_line):
            best_line = line
            items_by_idx = trial_items
            adopted.append(rec)
    return best_line, adopted


def _scarce_sitrus_is_worth_it(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Reserve Sitrus for a line-changing survival swing, not a small polish.

    A result-tier improvement always qualifies.  Within the same tier, require a
    large absolute reliability gain (roughly the user's 40% -> near-certain case).
    """
    if before.get("result") != "win-line" and after.get("result") == "win-line":
        return True
    before_conf = float(before.get("confidence", before.get("success_estimate", 0.0)) or 0.0)
    after_conf = float(after.get("confidence", after.get("success_estimate", 0.0)) or 0.0)
    return after_conf >= 0.95 and after_conf - before_conf >= 0.30


def _doubles_greedy_items(
    roster: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    enemies: list[PlannedEnemy],
    *,
    max_turns: int,
    force_enemy_crits: bool = False,
    forced_leads: tuple[int, int] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Greedy held-item adoption for a doubles roster (see _greedy_items)."""
    def sim_fn(team: list[PlannedMember]) -> dict[str, Any]:
        return _run_text_calc_sim_once_doubles(
            team, trainer, calculator, max_turns=max_turns, force_enemy_crits=force_enemy_crits,
            forced_leads=forced_leads,
        )
    return _greedy_items(roster, trainer, calculator, enemies, sim_fn)


def replace_member_item(member: PlannedMember, item: str | None) -> PlannedMember:
    clone = _clone_calc_team([member])[0]
    clone.item = item
    return clone


def _doubles_team_select(
    imported: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    *,
    max_turns: int,
    force_enemy_crits: bool = False,
    forced_leads: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Select the best 4-mon team for a doubles battle by running multiple candidate sims.

    Doubles battles use 2 leads + 2 bench mons (4 total). We score each box member against
    the first two enemy mons, then generate candidate 4-mon rosters and pick the best sim.
    """
    enemies = _planned_enemies_for_trainer(trainer, calculator)
    _progress_reset([("selecting doubles team", max(4, len(imported)))])
    _progress_stage("selecting doubles team", max(4, len(imported)))

    # Score each box member against the pair of leads: offense minus incoming pressure,
    # so Discharge-immune / resistant mons rank above glass cannons that die turn 1.
    scores: dict[int, float] = {}
    for idx, member in enumerate(imported):
        s = 0.0
        for enemy in enemies[:2]:
            action = _best_player_action(member, enemy, imported, calculator)
            s += action.score
            choices = _ai_move_choices(enemy, member, imported, calculator)
            choice = choices[0] if choices else None
            damage = choice.damage.max_damage if choice and choice.damage else 0
            if damage >= member.hp:
                s -= 120.0
            s -= (damage / max(1, member.max_hp)) * 60.0
        scores[idx] = s

    ordered = sorted(scores, key=lambda i: scores[i], reverse=True)

    best_combo: list[int] | None = None
    best_equipped: dict[str, Any] | None = None
    from itertools import combinations
    # Doubles fields 2 mons but the whole party (up to 6) is available to switch in,
    # so select 6-mon rosters (2 leads + 4 bench), not 4.
    team_size = min(6, len(ordered))
    top_n = min(11, len(ordered))
    tested = 0
    for combo in combinations(ordered[:top_n], team_size):
        if forced_leads is not None and not set(forced_leads).issubset(combo):
            continue
        local_leads = (
            (combo.index(forced_leads[0]), combo.index(forced_leads[1]))
            if forced_leads is not None else None
        )
        # Rank with the items the save/import actually has. An advisory ideal-item
        # line is computed only after choosing the roster; impossible Charcoal/Sitrus
        # suggestions must never decide which legal doubles team enters the fight.
        base = [imported[i] for i in combo]
        result = _run_text_calc_sim_once_doubles(
            _clone_calc_team(base), trainer, calculator,
            max_turns=max_turns, force_enemy_crits=force_enemy_crits,
            forced_leads=local_leads,
        )
        result["team_selection"] = {"chosen": [imported[i].name for i in combo], "indices": list(combo)}
        tested += 1
        # `result` is the item-equipped line; remember its roster as the best.
        if best_combo is None or _line_quality_key(result) > _line_quality_key(best_equipped):
            best_combo = list(combo)
            best_equipped = result
        if tested >= 30:
            break

    if best_combo is None:
        best_combo = list(range(min(6, len(imported))))

    # For the chosen roster, present the current-items line as the main line and the
    # recommended-items line as the "optimized" alternate (only if it's actually better).
    chosen_roster = [imported[i] for i in best_combo]
    chosen_leads = (
        (best_combo.index(forced_leads[0]), best_combo.index(forced_leads[1]))
        if forced_leads is not None and set(forced_leads).issubset(best_combo) else None
    )
    _progress_stage("searching the doubles line", 120)
    best_result = _search_best_line_doubles(
        chosen_roster, trainer, calculator, max_turns=max_turns, force_enemy_crits=force_enemy_crits,
        forced_leads=chosen_leads,
    )
    best_result["team_selection"] = {
        "chosen": [imported[i].name for i in best_combo], "indices": best_combo,
        "policy": "minimum-risk-nuzlocke",
        "range_safe": bool(
            best_result.get("confidence", 0) >= 0.999
            and all(member.get("hp", 0) > 0 for member in best_result.get("team", []))
        ),
        "note": "Doubles roster ranked by deathless, range-safe, crit-aware results before damage or speed.",
    }
    best_result["team_selection"]["fragile_answer_analysis"] = _fragile_answer_analysis(best_result)

    optimized, adopted = _doubles_greedy_items(
        chosen_roster, trainer, calculator, enemies, max_turns=max_turns, force_enemy_crits=force_enemy_crits,
        forced_leads=chosen_leads,
    )
    if adopted and _line_quality_key(optimized) > _line_quality_key(best_result):
        optimized["item_changes"] = [
            {"pokemon": r["pokemon"], "old_item": r.get("old_item"), "new_item": r["suggested_item"],
             "reason": r.get("reason", ""), "source": r.get("source", "")}
            for r in adopted
        ]
        best_result["optimized_item_line"] = optimized

    best_result["threat_answers"] = _threat_answers(imported, enemies, calculator)
    best_result["strategy_report"] = _build_strategy_report(best_result)
    _progress_finish()
    return best_result


def _run_text_calc_sim_with_team_select(
    imported: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    *,
    max_turns: int,
    force_enemy_crits: bool = False,
    forced_doubles_leads: tuple[int, int] | None = None,
) -> dict[str, Any]:
    if trainer.is_double and len(imported) > 4:
        # Doubles: run multiple 4-mon teams (enough for 2 leads + 2 bench), pick best.
        return _doubles_team_select(
            imported, trainer, calculator, max_turns=max_turns,
            force_enemy_crits=force_enemy_crits, forced_leads=forced_doubles_leads,
        )
    if len(imported) <= 6:
        return _run_text_calc_sim(
            imported[:6],
            trainer,
            calculator,
            max_turns=max_turns,
            force_enemy_crits=force_enemy_crits,
            forced_doubles_leads=forced_doubles_leads,
        )
    enemies_preview = _planned_enemies_for_trainer(trainer, calculator)
    candidates = _candidate_team_indices(imported, trainer, calculator, force_enemy_crits=force_enemy_crits)
    first_enemy_sleeps = bool(enemies_preview) and any(
        _normalize(move) in SLEEP_MOVES for move in enemies_preview[0].moves
    )
    lead_mult = 2 if first_enemy_sleeps else 1
    _progress_reset([
        ("scoring candidate rosters", len(candidates) * lead_mult),
        ("hill-climbing roster swaps", 90),
        ("scoring pivot-roster variants", 12 * lead_mult),
        ("refining top rosters with line search", 6 * 60),
        ("searching the final line", 120),
        ("optimizing held items", 24),
        ("trying pre-battle prep", 14),
        ("mapping every contingency path", _CONTINGENCY_FULL_NODE_BUDGET),
    ])

    _sim_key_cache: dict[tuple[int, ...], tuple[float, ...]] = {}

    def _sim_key(indices: tuple[int, ...]) -> tuple[float, ...]:
        cached = _sim_key_cache.get(indices)
        if cached is not None:
            return cached
        leads: list[int | None] = [None]
        if first_enemy_sleeps:
            # Also evaluate sleep-immune leads: blanking the first enemy's sleep
            # strategy can beat the raw best-damage lead.
            for slot, index in enumerate(indices):
                ability = _normalize(imported[index].ability or "")
                if ability in {"vitalspirit", "insomnia", "sweetveil"}:
                    leads.append(slot)
        best: tuple[float, ...] | None = None
        for lead in leads:
            team = _clone_calc_team([imported[index] for index in indices])
            result = _run_text_calc_sim_once(
                team, trainer, calculator, max_turns=max_turns,
                force_enemy_crits=force_enemy_crits, forced_lead=lead,
                compute_item_recs=False,
            )
            key = _line_quality_key(result)
            if best is None or key > best:
                best = key
        assert best is not None
        _sim_key_cache[indices] = best
        return best

    best_key: tuple[float, ...] | None = None
    best_indices: tuple[int, ...] | None = None
    for indices in candidates:
        key = _sim_key(indices)
        if best_key is None or key > best_key:
            best_key = key
            best_indices = indices
    assert best_indices is not None

    # Hill-climb: try swapping each roster slot for each unused box member and keep
    # any swap that sims a strictly better line. This lets the selector escape the
    # seeded candidates when one box mon (e.g. the only clean answer to a late boss
    # mon) never made it into a seed roster.
    _progress_stage("hill-climbing roster swaps", 90)
    sims_budget = 90
    while sims_budget > 0:
        improved = False
        outsiders = [i for i in range(len(imported)) if i not in best_indices]
        for slot in range(len(best_indices)):
            for outsider in outsiders:
                if sims_budget <= 0:
                    break
                trial = tuple(sorted(set(best_indices) - {best_indices[slot]} | {outsider}))
                if trial in candidates:
                    continue
                candidates.append(trial)
                sims_budget -= 1
                key = _sim_key(trial)
                if best_key is None or key > best_key:
                    best_key = key
                    best_indices = trial
                    improved = True
                    break
            if improved or sims_budget <= 0:
                break
        if not improved or sims_budget <= 0:
            break

    # Refine the top rosters with the full line search: a roster whose greedy line
    # is mediocre can still hold the best searched line. Also explore leads here —
    # a sleep-immune or sacrificial lead can dodge the first enemy's strategy.
    ranked_candidates = sorted(candidates, key=_sim_key, reverse=True)[:3]
    if best_indices not in ranked_candidates:
        ranked_candidates.append(best_indices)
    # Utility-pivot variants: an Intimidate mon on the bench can unlock bait-pivot
    # lines that only the full line search can see, so make sure rosters carrying
    # one reach the searched refinement even if their greedy line looks mediocre.
    _progress_stage("scoring pivot-roster variants", 12 * lead_mult)
    pivot_outsiders = [
        index for index in range(len(imported))
        if index not in best_indices and _normalize(imported[index].ability or "") == "intimidate"
    ]
    pivot_trials: list[tuple[int, ...]] = []
    for outsider in pivot_outsiders:
        for slot in range(len(best_indices)):
            trial = tuple(sorted(set(best_indices) - {best_indices[slot]} | {outsider}))
            if trial not in ranked_candidates and trial not in pivot_trials:
                pivot_trials.append(trial)
    pivot_trials.sort(key=_sim_key, reverse=True)
    ranked_candidates.extend(pivot_trials[:2])
    _progress_stage("refining top rosters with line search", max(1, len(ranked_candidates)) * lead_mult * 60)
    best_lead: int | None = None
    best_searched_key: tuple[float, ...] | None = None
    for indices in ranked_candidates:
        roster = [imported[index] for index in indices]
        leads: list[int | None] = [None]
        if first_enemy_sleeps:
            for slot, index in enumerate(indices):
                if _normalize(imported[index].ability or "") in {"vitalspirit", "insomnia", "sweetveil"}:
                    leads.append(slot)
        for lead in leads:
            searched = _search_best_line(
                roster, trainer, calculator, max_turns=max_turns,
                force_enemy_crits=force_enemy_crits, forced_lead=lead, budget=60,
            )
            key = _line_quality_key(searched)
            if best_searched_key is None or key > best_searched_key:
                best_searched_key = key
                best_indices = indices
                best_lead = lead

    chosen = [imported[index] for index in best_indices]
    final = _run_text_calc_sim(
        chosen,
        trainer,
        calculator,
        max_turns=max_turns,
        force_enemy_crits=force_enemy_crits,
        forced_lead=best_lead,
    )
    final["team_selection"] = {
        "box_size": len(imported),
        "chosen": [member.name for member in chosen],
        "candidates_tested": len(candidates),
        "policy": "minimum-risk-nuzlocke",
        "range_safe": bool(final.get("confidence", 0) >= 0.999 and all(member.get("hp", 0) > 0 for member in final.get("team", []))),
        "note": "Best 6 chosen by deathless, range-safe, crit-aware results first; confidence and remaining HP only break ties.",
    }
    final["team_selection"]["fragile_answer_analysis"] = _fragile_answer_analysis(final)
    return final


def _alternate_answer_names(result: dict[str, Any]) -> list[str]:
    """Return the actual first-board answers, in field-slot order."""
    turns = list(result.get("turns") or [])
    if not turns:
        return []
    first = turns[0]
    # In doubles use the board after turn-one switches: an immediate pivot's
    # incoming Pokemon, rather than the cosmetic lead, was the actual answer.
    board = (first.get("board_after") or {}).get("player") or []
    names = [str(slot.get("name") or "").strip() for slot in board]
    if not any(names):
        names = [part.strip() for part in str(first.get("answer") or "").split(" + ")]
    unique: list[str] = []
    normalized: set[str] = set()
    for name in names:
        key = _normalize(name)
        if name and key not in normalized:
            unique.append(name)
            normalized.add(key)
    return unique


def _alternate_answer_lines(
    available: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    primary: dict[str, Any],
    *,
    max_turns: int,
    force_enemy_crits: bool,
    lightweight: bool = False,
) -> list[dict[str, Any]]:
    """Find backups by banning each primary answer for the entire rerun."""
    if primary.get("result") != "win-line":
        return []
    candidates = _alternate_answer_names(primary)[: (2 if trainer.is_double else 1)]
    alternates: list[dict[str, Any]] = []
    for forbidden in candidates:
        pool = [member for member in available if _normalize(member.name) != _normalize(forbidden)]
        if len(pool) < (2 if trainer.is_double else 1):
            continue
        alternate = (
            _search_best_line(
                pool[:6], trainer, calculator, max_turns=max_turns,
                force_enemy_crits=force_enemy_crits, budget=70,
            )
            if lightweight and len(pool) <= 6 else
            _run_text_calc_sim_with_team_select(
                pool, trainer, calculator, max_turns=max_turns,
                force_enemy_crits=force_enemy_crits,
            )
        )
        ending_team = list(alternate.get("team") or [])
        selected = [str(member.get("name") or "") for member in ending_team]
        deathless = bool(ending_team) and all(int(member.get("hp") or 0) > 0 for member in ending_team)
        cleared = alternate.get("result") == "win-line"
        status = (
            "winning-alternate" if cleared and deathless
            else "unsafe-alternate" if cleared
            else "no-complete-alternate"
        )
        alternate.pop("contingency_flowchart", None)
        alternate.pop("optimized_item_line", None)
        alternates.append({
            "forbidden_answer": forbidden,
            "status": status,
            "result": alternate.get("result"),
            "deathless": deathless,
            "confidence": alternate.get("confidence", 0.0),
            "team": selected,
            "turns": alternate.get("turns") or [],
            "line_search": alternate.get("line_search") or {},
            "is_doubles": trainer.is_double,
            "rule": f"{forbidden} is unavailable for this entire backup run.",
        })
    return alternates


def _search_best_line(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    *,
    max_turns: int,
    force_enemy_crits: bool = False,
    forced_lead: int | None = None,
    budget: int = 120,
    seed_overrides: dict[int, int | None] | None = None,
    quality_fn: Any | None = None,
    focused_beam: bool = False,
) -> dict[str, Any]:
    """Local search over lines: replay the sim while forcing different switch
    decisions at the lowest-confidence turns and keep the best line found."""

    # Per-lead decision caches: state signatures do not encode species, so alternate
    # opening leads must never share cached tactical decisions.
    decision_caches: dict[int | None, dict] = {}
    quality = quality_fn or _line_quality_key

    def run(
        overrides: dict[int, int | None] | None,
        lead: int | None,
        move_overrides: dict[int, str] | None = None,
    ) -> dict[str, Any]:
        return _run_text_calc_sim_once(
            _clone_calc_team(team),
            trainer,
            calculator,
            max_turns=max_turns,
            force_enemy_crits=force_enemy_crits,
            forced_lead=lead,
            switch_overrides=overrides,
            player_move_overrides=move_overrides,
            compute_item_recs=False,
            decision_cache=decision_caches.setdefault(lead, {}),
        )

    best_lead = forced_lead
    best = run(None, best_lead)
    best_overrides: dict[int, int | None] = {}
    best_move_overrides: dict[int, str] = {}
    best_key = quality(best)
    # A lead is a full-battle strategy, not a cosmetic choice. Try every living team
    # member when the caller did not pin one, spending only one simulation per opening.
    if forced_lead is None:
        for lead, member in enumerate(team):
            if member.hp <= 0:
                continue
            candidate = run(None, lead)
            budget -= 1
            key = quality(candidate)
            if key > best_key:
                best, best_key, best_lead = candidate, key, lead
            if budget <= 0:
                break
    if seed_overrides:
        seeded = run(seed_overrides, best_lead)
        seeded_key = quality(seeded)
        if seeded_key > best_key:
            best, best_key, best_overrides = seeded, seeded_key, dict(seed_overrides)
    improved = True
    while improved and budget > 0:
        improved = False
        prev = 1.0
        sinks: list[tuple[float, int]] = []
        for row in best.get("turns") or []:
            conf = float(row.get("confidence") or 0.0)
            ratio = conf / prev if prev > 0 else 1.0
            if ratio < 0.96:
                sinks.append((ratio, int(row.get("turn") or 0)))
            prev = conf
        sinks.sort()
        # Map each turn to the slot that was active going into it, so compound
        # bait-pivot variants can switch a mon in and the original right back.
        active_slot_by_turn: dict[int, int] = {}
        slot_by_name = {member.name: slot for slot, member in enumerate(team)}
        prev_answer: str | None = None
        for row in best.get("turns") or []:
            if prev_answer in slot_by_name:
                active_slot_by_turn[int(row.get("turn") or 0)] = slot_by_name[prev_answer]
            prev_answer = row.get("answer")
        answer_slot_by_turn: dict[int, int] = {}
        for row in best.get("turns") or []:
            answer = row.get("answer")
            if answer in slot_by_name:
                answer_slot_by_turn[int(row.get("turn") or 0)] = slot_by_name[answer]
        sites: list[int] = []
        for _, turn_no in sinks[:5]:
            for site in (turn_no, turn_no - 1):
                if site >= 1 and site not in sites:
                    sites.append(site)
        for site in sites:
            variants: list[dict[int, int | None]] = [{**best_overrides, site: None}]
            follow_up = answer_slot_by_turn.get(site)
            for slot in range(len(team)):
                variants.append({**best_overrides, site: slot})
                if follow_up is not None and follow_up != slot:
                    # Bait pivot: bring `slot` in to eat/soften this turn, then put
                    # the line's intended answer straight back in next turn.
                    variants.append({**best_overrides, site: slot, site + 1: follow_up})
            for overrides in variants:
                if budget <= 0:
                    break
                budget -= 1
                candidate = run(overrides, best_lead, best_move_overrides)
                key = quality(candidate)
                if key > best_key:
                    best, best_key, best_overrides = candidate, key, overrides
                    improved = True
                    break
            if improved or budget <= 0:
                break

        if improved or budget <= 0:
            continue
        # Alternate-move search at the same confidence sinks. This is what lets the
        # builder discover recovery/setup/control turns instead of only lead/pivot edits.
        answer_by_turn = {
            int(row.get("turn") or 0): row.get("answer")
            for row in best.get("turns") or []
        }
        member_by_name = {member.name: member for member in team}
        for site in sites:
            member = member_by_name.get(answer_by_turn.get(site))
            if member is None:
                continue
            for move_name in member.known_moves:
                if budget <= 0:
                    break
                candidate_moves = {**best_move_overrides, site: move_name}
                budget -= 1
                candidate = run(best_overrides, best_lead, candidate_moves)
                key = quality(candidate)
                if key > best_key:
                    best, best_key, best_move_overrides = candidate, key, candidate_moves
                    improved = True
                    break
            if improved or budget <= 0:
                break
    # A greedy hill-climb cannot cross a temporarily worse pivot to reach a safer
    # two-pivot line. On final user-facing searches, explore a small deterministic
    # beam around the turns where a sacrifice actually occurred.
    focused_variants = 0
    if focused_beam and any(int(member.get("hp") or 0) <= 0 for member in best.get("team") or []):
        beam: list[tuple[dict[int, int | None], dict[str, Any]]] = [(dict(best_overrides), best)]
        seen_overrides = {tuple(sorted(best_overrides.items()))}

        def sacrifice_sites(result: dict[str, Any]) -> list[int]:
            sites: list[int] = []
            for row in result.get("turns") or []:
                action = str(row.get("action") or "")
                try:
                    hp = int(str(row.get("your_hp") or "0/1").split("/", 1)[0])
                except ValueError:
                    hp = 0
                if "Tactical sac" not in action and hp > 0:
                    continue
                turn_no = int(row.get("turn") or 0)
                for site in (turn_no, turn_no - 1, turn_no - 2):
                    if site >= 1 and site not in sites:
                        sites.append(site)
            return sites[:8]

        def outcome_signature(result: dict[str, Any]) -> tuple[Any, ...]:
            return (
                result.get("result"),
                tuple(int(member.get("hp") or 0) for member in result.get("team") or []),
                tuple(int(enemy.get("hp") or 0) for enemy in result.get("enemies") or []),
                tuple(str(row.get("action") or "") for row in result.get("turns") or []),
            )

        for _depth in range(2):
            expanded = list(beam)
            for overrides, result in beam:
                for site in sacrifice_sites(result)[:5]:
                    for slot in (None, *range(len(team))):
                        candidate_overrides = {**overrides, site: slot}
                        override_key = tuple(sorted(candidate_overrides.items()))
                        if override_key in seen_overrides:
                            continue
                        seen_overrides.add(override_key)
                        candidate = run(candidate_overrides, best_lead, best_move_overrides)
                        focused_variants += 1
                        expanded.append((candidate_overrides, candidate))
            expanded.sort(key=lambda item: quality(item[1]), reverse=True)
            next_beam: list[tuple[dict[int, int | None], dict[str, Any]]] = []
            seen_outcomes: set[tuple[Any, ...]] = set()
            for result_kind in ("win-line", "partial-line"):
                quota = 8
                for item in expanded:
                    if item[1].get("result") != result_kind:
                        continue
                    signature = outcome_signature(item[1])
                    if signature in seen_outcomes:
                        continue
                    seen_outcomes.add(signature)
                    next_beam.append(item)
                    quota -= 1
                    if quota <= 0:
                        break
            beam = next_beam[:16]
            if not beam:
                break
            candidate_overrides, candidate = max(beam, key=lambda item: quality(item[1]))
            candidate_key = quality(candidate)
            if candidate_key > best_key:
                best, best_key, best_overrides = candidate, candidate_key, candidate_overrides
    if best_overrides or best_move_overrides or best_lead != forced_lead:
        best["line_search"] = {
            "overrides": {str(turn): slot for turn, slot in best_overrides.items()},
            "move_overrides": {str(turn): move for turn, move in best_move_overrides.items()},
            "lead": best_lead,
            "note": (
                (f"Line search chose {team[best_lead].name} as the opening lead. " if best_lead is not None else "")
                + ("It also forced non-default switch decisions to stabilize the line." if best_overrides else "")
                + (" It selected non-default tactical moves at the weakest turns." if best_move_overrides else "")
            ).strip(),
        }
    if focused_variants:
        best.setdefault("line_search", {})["focused_switch_beam"] = {
            "depth": 2,
            "width": 16,
            "variants_tested": focused_variants,
            "policy": "deterministic-sacrifice-site-beam",
        }
    return best


def _doubles_lead_candidates(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    calculator: DamageCalculator,
    *,
    limit: int = 6,
) -> list[tuple[int, int]]:
    """Alternate lead pairs for the doubles line search to try.

    The default opening (`_doubles_lead_indices`) is the best-damage / least-pressure
    pair, but niche openings (a sleep-immune lead, an Intimidate body, a bait mon) can
    beat it. We rank the live members with the same lead heuristic and emit distinct
    pairs from the top few — excluding the default, which the search already runs.
    """
    alive = [i for i, m in enumerate(team) if m.alive]
    if len(alive) < 2:
        return []
    enemy_a = enemies[0] if enemies else None
    enemy_b = enemies[1] if len(enemies) > 1 else None

    def _lead_score(idx: int) -> float:
        member = team[idx]
        foes = [e for e in (enemy_a, enemy_b) if e is not None]
        if not foes:
            return 0.0
        offense = max(_best_player_action(member, e, team, calculator).score for e in foes)
        incoming = 0
        for e in foes:
            choices = _ai_move_choices(e, member, team, calculator)
            choice = choices[0] if choices else None
            incoming += choice.damage.max_damage if choice and choice.damage else 0
        score = offense - (incoming / max(1, member.max_hp)) * 70.0
        if incoming >= member.hp:
            score -= 500.0
        return score

    ranked = sorted(alive, key=_lead_score, reverse=True)
    default = _doubles_lead_indices(team, enemies, calculator)
    default_key = tuple(sorted(default))
    from itertools import combinations
    pairs: list[tuple[int, int]] = []
    for a, b in combinations(ranked[: min(5, len(ranked))], 2):
        if tuple(sorted((a, b))) == default_key:
            continue
        pairs.append((a, b))
        if len(pairs) >= limit:
            break
    return pairs


def _doubles_sink_turns(result: dict[str, Any], top: int = 5) -> list[int]:
    """Turns where line confidence dropped the most — the spots a forced pivot is
    most likely to repair. Mirrors the sink detection in singles `_search_best_line`."""
    sinks: list[tuple[float, int]] = []
    prev = 1.0
    for row in result.get("turns") or []:
        conf = float(row.get("confidence") or 0.0)
        ratio = conf / prev if prev > 0 else 1.0
        if ratio < 0.96:
            sinks.append((ratio, int(row.get("turn") or 0)))
        prev = conf
    sinks.sort()
    return [turn for _, turn in sinks[:top] if turn >= 1]


def _search_best_line_doubles(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    *,
    max_turns: int,
    force_enemy_crits: bool = False,
    budget: int = 120,
    forced_leads: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Local search over doubles lines, the 2v2 analog of `_search_best_line`.

    Greedy doubles sims commit to one opening and never reconsider a pivot, so they
    miss niche plays (bait pivots, sacrifices, alternate leads). This explores them:
    first try alternate lead pairs, then hill-climb forced/suppressed voluntary pivots
    at the lowest-confidence turns, keeping the best line by `_line_quality_key`.
    """
    enemies = _planned_enemies_for_trainer(trainer, calculator)

    def run(
        leads: tuple[int, int] | None,
        overrides: dict[int, dict[int, int | None]] | None,
    ) -> dict[str, Any]:
        return _run_text_calc_sim_once_doubles(
            _clone_calc_team(team),
            trainer,
            calculator,
            max_turns=max_turns,
            force_enemy_crits=force_enemy_crits,
            forced_leads=leads,
            switch_overrides=overrides,
        )

    best = run(forced_leads, None)
    best_key = _line_quality_key(best)
    best_leads: tuple[int, int] | None = forced_leads
    best_overrides: dict[int, dict[int, int | None]] = {}

    # --- Stage 1: alternate openings -------------------------------------------------
    for leads in ([] if forced_leads is not None else _doubles_lead_candidates(team, enemies, calculator)):
        if budget <= 0:
            break
        budget -= 1
        candidate = run(leads, None)
        key = _line_quality_key(candidate)
        if key > best_key:
            best, best_key, best_leads = candidate, key, leads

    # --- Stage 2: hill-climb voluntary pivots at the worst turns ---------------------
    improved = True
    while improved and budget > 0:
        improved = False
        for turn_no in _doubles_sink_turns(best):
            existing = best_overrides.get(turn_no, {})
            variants: list[dict[int, dict[int, int | None]]] = []
            for slot in (0, 1):
                # Suppress the automatic pivot in this slot...
                variants.append({**best_overrides, turn_no: {**existing, slot: None}})
                # ...or force each bench mon into it.
                for idx in range(len(team)):
                    variants.append({**best_overrides, turn_no: {**existing, slot: idx}})
            for overrides in variants:
                if budget <= 0:
                    break
                budget -= 1
                candidate = run(best_leads, overrides)
                key = _line_quality_key(candidate)
                if key > best_key:
                    best, best_key, best_overrides = candidate, key, overrides
                    improved = True
                    break
            if improved or budget <= 0:
                break

    if best_leads is not None or best_overrides:
        note_parts: list[str] = []
        if best_leads is not None:
            note_parts.append(f"opened with {team[best_leads[0]].name} + {team[best_leads[1]].name}")
        if best_overrides:
            note_parts.append("forced non-default pivots to stabilize the line")
        best["line_search"] = {
            "leads": list(best_leads) if best_leads is not None else None,
            "overrides": {
                str(turn): {str(slot): idx for slot, idx in slots.items()}
                for turn, slots in best_overrides.items()
            },
            "note": "Doubles line search " + " and ".join(note_parts) + ".",
        }
    return best


_SPEED_CONTROL_MOVES = {
    "thunderwave", "stunspore", "glare", "nuzzle", "rocktomb", "icywind", "electroweb",
    "cottonspore", "scaryface", "stringshot", "bulldoze", "lowsweep", "tailwind",
    "stickyweb", "agility", "rockpolish", "dragondance", "shellsmash", "flamecharge",
}
_HAZARD_MOVES = {"stealthrock", "spikes", "toxicspikes", "stickyweb", "rapidspin", "defog"}

_PRIORITY_FINISHERS = {
    "fakeout", "machpunch", "aquajet", "iceshard", "quickattack", "bulletpunch",
    "suckerpunch", "extremespeed", "vacuumwave", "shadowsneak", "accelerock",
}
_PROTECT_SCOUT_MOVES = {"protect", "detect"}
_RECOVERY_MOVES = {"recover", "roost", "synthesis", "softboiled", "slackoff", "morningsun", "moonlight", "wish", "shoreup", "milkdrink", "healorder", "strengthsap", "rest"}
_SCREEN_MOVES = {"reflect", "lightscreen", "auroraveil"}
_WEATHER_MOVES = {"sunnyday", "raindance", "sandstorm", "hail", "snowscape"}
_MOVE_DENIAL_MOVES = {"encore", "disable", "taunt", "torment", "imprison"}
_MOMENTUM_MOVES = {"uturn", "voltswitch", "flipturn", "batonpass", "partingshot"}
_RECOIL_MOVES = {"wildcharge", "bravebird", "doubleedge", "flareblitz", "headsmash", "woodhammer", "submission", "takedown"}
_SELF_KO_MOVES = {"explosion", "selfdestruct", "memento", "destinybond", "mistyexplosion"}
_ADVISORY_STRATEGY_IDS = {
    "pp-stall", "hazard-mgmt", "resource-conserve", "win-preservation",
    "screen-support", "weather-control", "move-lock", "momentum-move",
}

# Every strategy the line finder can pull from. The battle-plan PDF renders this
# catalog at the end, bolding the entries the current plan actually uses (with the
# turns where they are used) so missing tools are easy to spot.
_STRATEGY_CATALOG: list[dict[str, str]] = [
    {"id": "answer-pivot", "name": "Answer switching", "scope": "single-turn",
     "description": "Switch to the box member that best answers the active enemy: survives its best hit, ideally outspeeds, and hits hard."},
    {"id": "bait-pivot", "name": "Bait & pivot", "scope": "multi-turn",
     "description": "Switch one mon in to bait/absorb the predicted move, then bring the real answer in safely behind it next turn."},
    {"id": "intimidate-pivot", "name": "Intimidate weaken pivot", "scope": "multi-turn",
     "description": "Pivot an Intimidate user through a physical attacker so its Attack drops for the rest of the fight (defuses crit-KO thresholds too)."},
    {"id": "lead-select", "name": "Lead selection", "scope": "full-battle",
     "description": "Pick the lead that blanks the first enemy's plan (e.g. a sleep-immune lead into a Sing user) or preserves a key mon for the endgame."},
    {"id": "move-refine", "name": "Tactical move refinement", "scope": "multi-turn",
     "description": "Re-test every legal move at the line's weakest turns, keeping a non-default attack, recovery, status, or setup move only when the full remaining battle improves.",
     "when": "The greedy highest-damage move leaves a low-confidence follow-up, misses a safer finisher, or overlooks a recovery/control turn.",
     "viable": "The alternate move must improve the complete projected line, not just this turn's score.",
     "example": "Use a guaranteed-hit neutral move to finish a 14 HP target instead of risking a stronger 95%-accurate super-effective move."},
    {"id": "tactical-sac", "name": "Tactical sacrifice", "scope": "single-turn",
     "description": "Deliberately give up a low-value or already-chipped mon to buy a free, damage-less send-in for the real answer."},
    {"id": "planned-trade", "name": "Planned trade", "scope": "single-turn",
     "description": "Keep a chipped mon in to land one more attack, accepting that it faints to the counter-attack."},
    {"id": "free-send", "name": "Forced send after your faint", "scope": "single-turn",
     "description": "When your active Pokémon faints, choose its replacement without paying an extra entry hit. This never applies after you KO the foe: Set mode sends their replacement directly into your current active Pokémon."},
    {"id": "enemy-status", "name": "Statusing the enemy", "scope": "multi-turn",
     "description": "Land sleep/poison/paralysis/confusion on a dangerous enemy to shut it down or out-stall it."},
    {"id": "stat-drop", "name": "Stat-drop stalling", "scope": "multi-turn",
     "description": "Stack Growl/Smokescreen-style drops so the enemy stops threatening KOs while you chip it."},
    {"id": "setup", "name": "Setup sweeping", "scope": "multi-turn",
     "description": "Use a boosting move behind a weakened or blanked enemy, then sweep."},
    {"id": "priority-finish", "name": "Priority finisher", "scope": "single-turn",
     "description": "Pick off a weakened, faster threat with a priority move (Fake Out, Mach Punch, Aqua Jet, ...) before it can act."},
    {"id": "item-opt", "name": "Held-item optimization", "scope": "full-battle",
     "description": "Re-equip obtainable held items so key damage thresholds and survival checks flip in your favor."},
    {"id": "berry-sustain", "name": "Berry sustain", "scope": "multi-turn",
     "description": "Hold Oran/Sitrus so a mon heals mid-fight and wins a damage race it would otherwise lose."},
    {"id": "pre-status", "name": "Pre-statusing", "scope": "full-battle",
     "description": "Give your own mon a status BEFORE the fight (e.g. already poisoned) so enemy status moves like Sing or Will-O-Wisp cannot land a worse one."},
    {"id": "pre-damage", "name": "Pre-damaging", "scope": "full-battle",
     "description": "Enter the fight at reduced HP on purpose to bait a specific AI move or to power Endeavor/Flail-style plays."},
    {"id": "win-preservation", "name": "Win condition preservation", "scope": "full-battle",
     "description": "Identify the mon that is uniquely required to beat a remaining enemy and keep it healthy, even when a riskier play would deal more damage right now.",
     "when": "Whenever the threat-answer table shows exactly one clean answer to an enemy that is still alive.",
     "viable": "The key mon must stay out of chip range: it needs enough HP to pay its eventual entry hit and still win its target matchup.",
     "example": "Seadra is the only reliable answer to Rhydon - so Seadra never pivots into stray hits before Rhydon is removed, even if it could chip something."},
    {"id": "ai-bait", "name": "AI move baiting", "scope": "multi-turn",
     "description": "Present a weakness or board state that makes the AI's scored choice predictable, then blank that move with an immunity, resist, or free setup turn.",
     "when": "The AI document scores one move clearly highest against your visible mon and you hold a switch-in that takes nothing from it.",
     "viable": "Needs a confident AI branch (one move at high probability) and a back mon immune or strongly resistant to exactly that move.",
     "example": "Leave a Water-type in to bait Thunderbolt, then switch the Ground-type in as it is chosen - the predicted move does zero."},
    {"id": "speed-control", "name": "Speed control", "scope": "multi-turn",
     "description": "Change the speed order instead of the damage race: paralysis, Icy Wind / Rock Tomb / Sticky Web drops, Tailwind, or your own speed boosts.",
     "when": "A matchup is lost only because the enemy moves first (it KOs you before you act, or out-paces your 2HKO).",
     "viable": "The speed move must land before the enemy KOs you, and the post-drop order must actually flip - check the stat math, not just the move.",
     "example": "Rock Tomb the faster enemy on the safe turn; from next turn you outspeed and the same calc that lost on speed now wins."},
    {"id": "setup-window", "name": "Setup window creation", "scope": "multi-turn",
     "description": "Do not just find setup turns - manufacture them: weaken the enemy's output first, then boost behind the blank turns you created.",
     "when": "You carry a boosting move but every enemy currently threatens too much damage to boost safely.",
     "viable": "Needs a way to defang the enemy first (Intimidate, stat drops, status, or its weak coverage move) so two-plus safe turns exist.",
     "example": "Intimidate on entry, Growl again, and once its hits stop threatening KOs, click Swords Dance twice and sweep."},
    {"id": "pp-stall", "name": "PP stalling", "scope": "full-battle",
     "description": "Exhaust a dangerous low-PP move by cycling resists and immunities until the AI literally cannot click it any more.",
     "when": "One enemy move is the only thing that breaks your line and it has 5-10 PP (Fire Blast, Hydro Pump, Stone Edge class moves).",
     "viable": "Needs two-plus mons that take little or nothing from the move, enough combined HP to cycle, and no enemy setup that punishes the stall turns.",
     "example": "Alternate the two Fire resists as Fire Blast targets; after five clicks it is gone and the enemy is stuck on weaker coverage."},
    {"id": "hazard-mgmt", "name": "Hazard management", "scope": "full-battle",
     "description": "Treat hazards as a resource on both sides: set Stealth Rock to flip future KO thresholds, avoid switch-spam that bleeds you through your own entry costs, and clear hazards before a switch-heavy endgame.",
     "when": "You or the enemy carries hazard moves, or the plan involves three-plus switches after hazards are up.",
     "viable": "Setting needs a free turn against a defanged enemy; clearing needs Rapid Spin/Defog and a turn where using it does not lose tempo.",
     "example": "Stealth Rock turns the enemy's 2HKO-survivor into a clean OHKO on every later send-in - set it on the blank turn, then stop switching more than the plan needs."},
    {"id": "resource-conserve", "name": "Resource conservation", "scope": "full-battle",
     "description": "Spend the cheap resource, keep the scarce one: HP, berries, PP on key moves, and one-time setup chances are budget items, not per-turn maximization.",
     "when": "Two plays look equal this turn but one burns a finite resource (last Hydro Pump PP, the only Sitrus, a full-HP wall) the endgame still needs.",
     "viable": "Needs a known future matchup that depends on the resource - conserving for nothing is just playing worse.",
     "example": "Click the weaker, high-PP move to finish a chipped enemy and keep the limited nuke's PP for the boss mon behind it."},
    {"id": "death-fodder", "name": "Death fodder routing", "scope": "single-turn",
     "description": "When a sack is required, send the LEAST valuable mon: weigh remaining matchups, current HP, leftover utility, and future pivot value - not just who is already chipped.",
     "when": "A sacrifice is unavoidable (no safe pivot survives the predicted hit) and more than one mon could be the body.",
     "viable": "The fodder must have no unique remaining job: it answers nothing the survivors cannot, and its HP is too low to bait or pivot later.",
     "example": "Both Drednaw (10 HP, Vespiquen answer used up) and Seadra (41 HP, still the Slowbro answer) could eat the hit - feed Drednaw, keep Seadra."},
    {"id": "sack-order", "name": "Sack order optimization", "scope": "full-battle",
     "description": "When multiple sacrifices are coming, choose the order: who must survive to the end, who goes first, and what each death gives the next switch-in for free.",
     "when": "The line projects two-plus deaths, or every remaining matchup forces trades.",
     "viable": "Needs a clear endgame mon to protect and sacks whose free send-ins actually line up with the enemies they enable.",
     "example": "Sack the spent pivot into Delcatty first so the breaker enters free; save the second sack for Slowbro so the cleaner comes in untouched for the last enemy."},
    {"id": "pivot-chain", "name": "Pivot chain construction", "scope": "multi-turn",
     "description": "Evaluate whole switch sequences, not single swaps: a chain of entries that each look mediocre can hand the final mon a winning board.",
     "when": "No single switch is safe, but a sequence of absorbed hits, ability triggers, and one planned death sets the real answer up cleanly.",
     "viable": "Each link must survive its predicted entry hit (except a planned final sack), and the chain must end with the answer entering free or against a defanged enemy.",
     "example": "Staravia enters and Intimidates; Seadra absorbs the predicted attack; Seadra is sacked; Drednaw enters free and cleans - the sequence wins even though every single turn looked like a loss."},
    {"id": "protect-scout", "name": "Protect scouting", "scope": "multi-turn",
     "description": "Use Protect/Detect to reveal the AI's preferred move, collect residual damage, and turn the next pivot from a guess into a response.",
     "when": "The enemy has two materially different move branches or poison/burn/weather damage is already ticking.",
     "viable": "The enemy must not gain more from the free turn than you do; avoid scouting in front of dangerous setup.",
     "example": "Protect once to confirm Wild Charge, then send the Ground immunity on the next predicted click."},
    {"id": "recovery-cycle", "name": "Recovery cycling", "scope": "multi-turn",
     "description": "Use reliable healing on turns where incoming damage is below recovery, converting a narrow survival line into a repeatable loop.",
     "when": "Recover/Roost/Synthesis/Wish restores more than the enemy's likely non-crit damage and PP is sufficient.",
     "viable": "The enemy cannot boost through the loop, inflict an unrecoverable status, or win with a high-probability crit sequence.",
     "example": "Roost above the next-hit threshold, attack once, then Roost again instead of gambling from crit range."},
    {"id": "screen-support", "name": "Screen support", "scope": "multi-turn",
     "description": "Set Reflect, Light Screen, or Aurora Veil before the damage race so several later survival thresholds improve at once.",
     "when": "Multiple remaining enemies attack from the same side or the planned setup mon needs one extra turn.",
     "viable": "The setter must survive the setup turn and the protected turns must cover the important matchups.",
     "example": "Reflect on the weak physical lead, then preserve the remaining screen turns for the cleaner's setup and sweep."},
    {"id": "weather-control", "name": "Weather control", "scope": "full-battle",
     "description": "Change or deny weather to alter damage, speed abilities, recovery, accuracy, and residual chip across the whole line.",
     "when": "Rain/sun/sand/hail changes a KO threshold, activates Swift Swim/Chlorophyll, or weakens the enemy's primary STAB.",
     "viable": "The weather turn must pay back before it expires and must not activate a stronger enemy benefit.",
     "example": "Set Rain before the Water cleaner enters so it outspeeds under Swift Swim and turns two rolls into guaranteed KOs."},
    {"id": "move-lock", "name": "Move denial and locking", "scope": "multi-turn",
     "description": "Use Encore, Disable, Taunt, Torment, or Imprison to remove the branch that breaks the plan and force predictable turns.",
     "when": "One setup, recovery, status, or coverage move is responsible for most losing branches.",
     "viable": "The denial move must act before the target and its duration must cover the planned response.",
     "example": "Encore the enemy's setup move, then bring the breaker in during the forced repeat."},
    {"id": "momentum-move", "name": "Damage-and-pivot momentum", "scope": "single-turn",
     "description": "Use U-turn, Volt Switch, Flip Turn, Baton Pass, or Parting Shot to make progress while handing the next matchup to the correct answer.",
     "when": "A direct switch costs too much HP but a pivot move can act first or soften the incoming threat.",
     "viable": "The user must survive until the pivot resolves and the destination must handle the enemy's response.",
     "example": "U-turn for chip into the resist rather than hard-switching the resist into a full-power hit."},
    {"id": "recoil-budget", "name": "Recoil budgeting", "scope": "full-battle",
     "description": "Treat recoil HP as a finite resource: reserve recoil moves for thresholds where their extra power changes the outcome.",
     "when": "Wild Charge, Brave Bird, Double-Edge, or another recoil move appears more than once in the planned line.",
     "viable": "The user must remain above later entry-hit and priority ranges after recoil is paid.",
     "example": "Use the safe coverage move on the chipped target and save Wild Charge's recoil payment for the bulky Water behind it."},
    {"id": "roll-proof", "name": "Damage-roll proofing", "scope": "full-battle",
     "description": "Prefer lines whose key KOs and survival checks hold across every damage roll, not merely on average.",
     "when": "A turn has a non-guaranteed KO, a low-roll survivor branch, or leaves a key mon inside the enemy's roll range.",
     "viable": "A small item, chip, level, nature, IV, screen, or alternate move must convert the roll into a guarantee.",
     "example": "Add Stealth Rock chip so the 13/16 KO becomes 16/16 and the flowchart loses its dangerous survivor fork."},
    {"id": "self-ko-trade", "name": "Self-KO trade", "scope": "single-turn",
     "description": "Use Explosion, Self-Destruct, Memento, or Destiny Bond when trading the user creates a cleaner and safer free send than preserving it.",
     "when": "The user has finished its unique job and removing or disabling the active enemy unlocks the endgame.",
     "viable": "The trade must remove more remaining threat value than it spends and must not sacrifice the only answer to a later enemy.",
     "example": "Explosion removes the wall, then the preserved priority cleaner enters for free and finishes the last two enemies."},
]


def _build_strategy_report(result: dict[str, Any]) -> list[dict[str, Any]]:
    turns = result.get("turns") or []
    used: dict[str, list[str]] = {}

    def mark(strategy_id: str, where: str) -> None:
        used.setdefault(strategy_id, []).append(where)

    status_moves = SLEEP_MOVES | TOXIC_MOVES | POISON_MOVES | PARALYSIS_MOVES | BURN_MOVES | CONFUSION_MOVES
    drop_moves = set(ATTACK_DROP_MOVES) | set(SPECIAL_ATTACK_DROP_MOVES) | set(SPEED_DROP_MOVES)
    prev_row: dict[str, Any] | None = None
    for row in turns:
        turn_no = row.get("turn")
        action = str(row.get("action") or "")
        calc = str(row.get("calc") or "")
        kind = str(row.get("consistency") or "")
        label = f"Turn {turn_no} ({row.get('answer')} vs {row.get('enemy')})"
        if kind == "switch-cost":
            mark("answer-pivot", label)
        if kind == "forced-send":
            mark("free-send", label)
        if kind == "tactical-sac":
            mark("tactical-sac", label)
        if "Intimidate drops" in calc:
            mark("intimidate-pivot", label)
        if (
            prev_row is not None
            and str(prev_row.get("consistency") or "") == "switch-cost"
            and kind == "switch-cost"
            and prev_row.get("enemy") == row.get("enemy")
        ):
            mark("bait-pivot", f"Turns {prev_row.get('turn')}-{turn_no} (pivot pair into {row.get('enemy')})")
        match = re.search(r"click ([A-Za-z' .\-]+?)\.?$", action)
        move_id = _normalize(match.group(1)) if match else ""
        if move_id:
            if move_id in status_moves:
                mark("enemy-status", label)
            if move_id in drop_moves:
                mark("stat-drop", label)
            if move_id in SETUP_MOVE_BOOSTS:
                mark("setup", label)
            if move_id in _PRIORITY_FINISHERS:
                mark("priority-finish", label)
            if move_id in _PROTECT_SCOUT_MOVES:
                mark("protect-scout", label)
            if move_id in _RECOVERY_MOVES:
                mark("recovery-cycle", label)
            if move_id in _SCREEN_MOVES:
                mark("screen-support", label)
            if move_id in _WEATHER_MOVES:
                mark("weather-control", label)
            if move_id in _MOVE_DENIAL_MOVES:
                mark("move-lock", label)
            if move_id in _MOMENTUM_MOVES:
                mark("momentum-move", label)
            if move_id in _RECOIL_MOVES or " recoil" in calc.lower():
                mark("recoil-budget", label)
            if move_id in _SELF_KO_MOVES:
                mark("self-ko-trade", label)
        risk_text = " ".join(str(risk) for risk in (row.get("risks") or [])).lower()
        if any(marker in risk_text or marker in calc.lower() for marker in ("damage roll", "low roll", "ko roll", "survives")):
            mark("roll-proof", label)
        if kind == "stateful-calc" and f"{row.get('answer')} faints" in calc:
            mark("planned-trade", label)
        if "recovers with" in calc:
            mark("berry-sustain", label)
        if move_id in _SPEED_CONTROL_MOVES:
            mark("speed-control", label)
        if move_id in _HAZARD_MOVES:
            mark("hazard-mgmt", label)
        if kind == "switch-cost" and "takes 0 and ends" in calc:
            mark("ai-bait", f"{label} - predicted move absorbed for zero")
        prev_row = row
    # Multi-turn / whole-line detections.
    switch_run: list[Any] = []
    for row in turns:
        if str(row.get("consistency") or "") in {"switch-cost", "tactical-sac"}:
            switch_run.append(row.get("turn"))
        else:
            if len(switch_run) >= 2:
                mark("pivot-chain", f"Turns {switch_run[0]}-{switch_run[-1]} (chained pivots)")
            switch_run = []
    if len(switch_run) >= 2:
        mark("pivot-chain", f"Turns {switch_run[0]}-{switch_run[-1]} (chained pivots)")
    if "stat-drop" in used and "setup" in used:
        mark("setup-window", "Stat drops landed before boosting - see the stat-drop and setup turns")
    if "intimidate-pivot" in used and "setup" in used:
        mark("setup-window", "Intimidate entry created the boost window")
    deaths = [str(row.get("answer")) for row in turns if str(row.get("consistency") or "") in {"tactical-sac"}]
    trade_deaths = [
        str(row.get("answer")) for row in turns
        if str(row.get("consistency") or "") == "stateful-calc" and f"{row.get('answer')} faints" in str(row.get("calc") or "")
    ]
    if "tactical-sac" in used:
        mark("death-fodder", "The sac target is chosen as the lowest-value body (HP, remaining matchups, pivot value)")
    if len(deaths) + len(trade_deaths) >= 2:
        mark("sack-order", f"Multiple planned deaths sequenced: {', '.join(deaths + trade_deaths)}")
    # Win condition preservation: an enemy with exactly one clean answer whose
    # answer mon was kept alive until that enemy was fought.
    for threat_row in result.get("threat_answers") or []:
        best_mon = ((threat_row.get("best") or {}).get("mon")) or ""
        if not threat_row.get("clean") or not best_mon:
            continue
        fought = [row for row in turns if row.get("enemy") == threat_row.get("enemy") and row.get("answer") == best_mon]
        if fought:
            mark("win-preservation", f"{best_mon} kept healthy for {threat_row.get('enemy')} (turn {fought[0].get('turn')})")
            break
    if (result.get("optimized_item_line") or {}).get("item_changes"):
        mark("item-opt", "Line 2 - recommended held items (see its item changes)")
    if result.get("lead_strategy"):
        mark("lead-select", str(result["lead_strategy"]))
    elif (result.get("line_search") or {}).get("lead") is not None:
        mark("lead-select", str((result.get("line_search") or {}).get("note") or "Line search selected a non-default lead."))
    if (result.get("line_search") or {}).get("move_overrides"):
        for turn, move in (result["line_search"]["move_overrides"] or {}).items():
            mark("move-refine", f"Turn {turn}: {move}")
    for prep in result.get("prep_strategy") or []:
        mark(str(prep.get("id")), str(prep.get("where")))
    return [
        {
            **entry,
            "modeled": entry["id"] not in _ADVISORY_STRATEGY_IDS,
            "used": entry["id"] in used,
            "where": used.get(entry["id"], []),
        }
        for entry in _STRATEGY_CATALOG
    ]


def _try_prep_strategies(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    result: dict[str, Any],
    *,
    max_turns: int,
    force_enemy_crits: bool = False,
    forced_lead: int | None = None,
) -> None:
    """Pre-battle prep search: try entering the fight pre-statused (blocks enemy
    status strategies) or pre-damaged (Endeavor/Flail-style baits). Adopted only
    when the resulting line is strictly better; attached as an alternate line."""
    base_key = _line_quality_key(result)
    seed = {
        int(turn): slot
        for turn, slot in ((result.get("line_search") or {}).get("overrides") or {}).items()
    } or None

    def run(variant: list[PlannedMember]) -> dict[str, Any]:
        return _run_text_calc_sim_once(
            variant, trainer, calculator, max_turns=max_turns,
            force_enemy_crits=force_enemy_crits, forced_lead=forced_lead,
            switch_overrides=seed,
            compute_item_recs=False,
        )

    enemies = _planned_enemies_for_trainer(trainer, calculator)
    status_threat = any(
        _normalize(move) in SLEEP_MOVES | BURN_MOVES | TOXIC_MOVES | PARALYSIS_MOVES
        for enemy in enemies
        for move in enemy.moves
    )
    best: dict[str, Any] | None = None
    best_key = base_key
    adopted: dict[str, Any] | None = None
    if status_threat:
        for slot, member in enumerate(team):
            if member.status:
                continue
            variant = _clone_calc_team(team)
            variant[slot].status = "poison"
            candidate = run(variant)
            key = _line_quality_key(candidate)
            if key > best_key:
                best, best_key = candidate, key
                adopted = {
                    "id": "pre-status",
                    "pokemon": member.name,
                    "detail": "poison",
                    "where": f"Prep: bring {member.name} in already poisoned so enemy status cannot land - see the prep line.",
                }
    for slot, member in enumerate(team):
        if not any(_normalize(move) in {"endeavor", "flail", "reversal", "counter", "mirrorcoat"} for move in member.moves):
            continue
        for fraction in (0.25, 0.05):
            variant = _clone_calc_team(team)
            variant[slot].hp = max(1, int(variant[slot].max_hp * fraction))
            candidate = run(variant)
            key = _line_quality_key(candidate)
            if key > best_key:
                best, best_key = candidate, key
                adopted = {
                    "id": "pre-damage",
                    "pokemon": member.name,
                    "detail": f"enter at {int(fraction * 100)}% HP",
                    "where": f"Prep: pre-damage {member.name} to about {int(fraction * 100)}% HP before the fight - see the prep line.",
                }
    if best is not None and adopted is not None:
        best["prep_note"] = adopted["where"]
        result["prep_line"] = best
        result["prep_strategy"] = [adopted]


def _doubles_lead_indices(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    calculator: DamageCalculator,
) -> tuple[int, int]:
    """Pick two player lead indices for a doubles battle.

    Slot A = best answer to the first enemy; slot B = best answer to the second enemy
    (excluding slot A).  Falls back to first two alive members.
    """
    alive = [i for i, m in enumerate(team) if m.alive]
    if len(alive) == 0:
        return 0, 1
    if len(alive) == 1:
        return alive[0], alive[0]

    enemy_a = enemies[0] if enemies else None
    enemy_b = enemies[1] if len(enemies) > 1 else None

    if enemy_a is None:
        return alive[0], alive[1]

    # Doubles-aware lead score: best offense vs either enemy minus combined
    # incoming damage from both enemy leads (both can target the same mon turn 1).
    def _lead_score(idx: int) -> float:
        member = team[idx]
        offense = max(
            _best_player_action(member, e, team, calculator).score
            for e in [enemy_a] + ([enemy_b] if enemy_b else [])
        )
        incoming = 0
        for e in [enemy_a] + ([enemy_b] if enemy_b else []):
            choices = _ai_move_choices(e, member, team, calculator)
            choice = choices[0] if choices else None
            incoming += choice.damage.max_damage if choice and choice.damage else 0
        score = offense - (incoming / max(1, member.max_hp)) * 70.0
        if incoming >= member.hp:
            score -= 500.0
        return score

    ranked = sorted(alive, key=_lead_score, reverse=True)
    slot_a = ranked[0]
    slot_b = ranked[1] if len(ranked) > 1 else slot_a
    return slot_a, slot_b


def _doubles_bench(team: list[PlannedMember], active_slots: tuple[int, int]) -> list[int]:
    """Return live bench indices (not currently active)."""
    return [i for i, m in enumerate(team) if m.alive and i not in active_slots]


def _doubles_player_action_for_target(
    attacker: PlannedMember,
    target: PlannedEnemy | PlannedMember,
    action: PlayerAction,
    calculator: DamageCalculator,
) -> PlayerAction:
    if action.kind != "move" or not action.move_name:
        return action
    move = calculator.moves.get(_normalize(action.move_name), {})
    if move.get("category") == "Status":
        return action
    field = replace(calculator.default_field, is_doubles=True, targets=2)
    damage = calculator.estimate_move(
        attacker.calc_set(), target.calc_set(), action.move_name,
        DamageContext(field=field, spread=_is_spread_in_doubles(action.move_name, calculator)),
    )
    return PlayerAction(action.kind, action.move_name, action.target_slot, action.score, damage, action.reason)


def _doubles_enemy_choice_for_target(
    attacker: PlannedEnemy,
    target: PlannedMember | PlannedEnemy,
    choice: MoveChoice | None,
    calculator: DamageCalculator,
    *,
    force_crit: bool,
) -> MoveChoice | None:
    if choice is None:
        return None
    field = replace(calculator.default_field, is_doubles=True, targets=2)
    damage = calculator.estimate_move(
        attacker.calc_set(), target.calc_set(), choice.move_name,
        DamageContext(
            field=field,
            critical=force_crit,
            spread=_is_spread_in_doubles(choice.move_name, calculator),
        ),
    )
    return MoveChoice(choice.move_name, choice.score, choice.probability, damage, choice.reason)


def _doubles_enemy_options(
    enemy: PlannedEnemy,
    player_a: PlannedMember,
    player_b: PlannedMember | None,
    partner: PlannedEnemy | None,
    calculator: DamageCalculator,
    *,
    force_crit: bool = False,
) -> list[tuple[MoveChoice, int, float]]:
    """Return every top-scoring (move, target) decision the doubles AI can make.

    Target selection is part of the decision.  Two identical moves aimed at opposite
    field slots therefore remain separate branches in the route explorer.
    """
    players = [(player_a, 0)] + ([(player_b, 1)] if player_b and player_b.alive else [])
    candidates: dict[tuple[str, int], MoveChoice] = {}
    for target, target_slot in players:
        for choice in _ai_move_choices(
            enemy, target, [p for p, _ in players], calculator,
            force_crit=force_crit, partner=partner,
        ):
            if choice.probability <= 0:
                continue
            move_data = calculator.moves.get(_normalize(choice.move_name), {})
            move_target = move_data.get("target", "normal")
            if move_target in {"adjacentAlly", "adjacentAllyOrSelf", "allies", "allySide", "allyTeam", "self"}:
                continue
            resolved_slot = -1 if _is_spread_in_doubles(choice.move_name, calculator) else target_slot
            score = choice.score
            if resolved_slot == -1:
                score += 1.0 + _doubles_spread_penalty(partner, choice.move_name, calculator)
            candidate = MoveChoice(choice.move_name, score, choice.probability, choice.damage, choice.reason)
            key = (_normalize(choice.move_name), resolved_slot)
            if key not in candidates or candidate.score > candidates[key].score:
                candidates[key] = candidate
    if not candidates:
        choice, slot = _doubles_enemy_target(
            enemy, player_a, player_b, partner, calculator, force_crit=force_crit,
        )
        return [(choice, slot, 1.0)] if choice is not None else []
    top_score = max(choice.score for choice in candidates.values())
    winners = [
        (choice, slot)
        for (_move, slot), choice in candidates.items()
        if abs(choice.score - top_score) < 1e-9
    ]
    total_weight = sum(max(0.0, choice.probability) for choice, _ in winners)
    if total_weight <= 0:
        total_weight = float(len(winners))
        return [(choice, slot, 1.0 / total_weight) for choice, slot in winners]
    return sorted(
        [(choice, slot, max(0.0, choice.probability) / total_weight) for choice, slot in winners],
        key=lambda item: (item[2], item[0].score, item[0].move_name),
        reverse=True,
    )


def _doubles_reliable_action(
    member: PlannedMember,
    enemy: PlannedEnemy,
    action: Any,
    calculator: DamageCalculator,
) -> Any:
    """Swap a low-accuracy status pick (e.g. Sing, 55%) for the member's best
    damaging move. In doubles a missed status turn is pure confidence loss."""
    if action is None or not action.move_name:
        return action
    move = calculator.moves.get(_normalize(action.move_name), {})
    if move.get("category") != "Status":
        return action
    if _move_accuracy(action.move_name, calculator, member) >= 0.75:
        return action
    best = None
    for move_name in member.moves:
        dmg = calculator.estimate_move(member.calc_set(), enemy.calc_set(), move_name)
        if dmg is None or dmg.max_damage <= 0:
            continue
        if best is None or dmg.min_damage > best.min_damage:
            best = dmg
    if best is None:
        return action
    return PlayerAction(
        "move", best.move_name,
        score=best.min_percent * 100.0 + best.ko_chance * 95.0,
        damage=best,
        reason="reliable damage over low-accuracy status",
    )


def _doubles_best_bench(
    bench_indices: list[int],
    team: list[PlannedMember],
    enemy: PlannedEnemy,
    calculator: DamageCalculator,
) -> int:
    """Return the bench index with the best matchup against `enemy`.

    Uses the same pressure + player-action scoring as _rank_calc_answers but
    restricted to the provided bench_indices.  Falls back to bench_indices[0]
    if nothing can be scored.
    """
    if len(bench_indices) == 1:
        return bench_indices[0]
    best_score = float("-inf")
    best_idx = bench_indices[0]
    for idx in bench_indices:
        member = team[idx]
        action = _best_player_action(member, enemy, team, calculator)
        choices = _ai_move_choices(enemy, member, team, calculator)
        choice = choices[0] if choices else None
        pressure = _choice_pressure(choice, member, calculator)
        damage = choice.damage.max_damage if choice and choice.damage else 0
        dies = bool(choice and choice.damage and damage >= member.hp)
        hp_after = max(0, member.hp - damage) / max(1, member.max_hp)
        score = action.score - pressure * 90 + hp_after * 25
        if dies:
            score -= 10000
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx


def _doubles_best_bench_enemy(
    enemies: list[PlannedEnemy],
    exclude_indices: set[int],
    player: PlannedMember | None,
    calculator: DamageCalculator,
    team: list[PlannedMember],
) -> int | None:
    """Pick best bench enemy to send into `player`'s slot after a KO.

    Per the post-KO switch-in AI rule (doubles): the fainted enemy slot only
    evaluates replacements against the *matching* player slot.  Mirrors the
    scoring in _choose_next_enemy from turn_planner.
    """
    scored: list[tuple[float, int]] = []
    for i, e in enumerate(enemies):
        if not e.alive or i in exclude_indices:
            continue
        score = 0.0
        if player and player.alive:
            choices = _ai_move_choices(e, player, team, calculator)
            best = choices[0] if choices else None
            score += (best.damage.max_percent * 100.0 if best and best.damage else 0.0)
            score += (best.probability * 20.0 if best else 0.0)
            pa = _best_player_action(player, e, team, calculator)
            if pa.damage and pa.damage.ko_chance >= 1.0:
                score -= 55.0
            if _speed(e.calc_set(), calculator) >= _speed(player.calc_set(), calculator):
                score += 12.0
        score += e.hp / max(1, e.max_hp) * 10.0
        scored.append((score, i))
    if not scored:
        return None
    return max(scored, key=lambda x: x[0])[1]


def _run_text_calc_sim_once_doubles(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    *,
    max_turns: int,
    force_enemy_crits: bool = False,
    forced_leads: tuple[int, int] | None = None,
    switch_overrides: dict[int, dict[int, int | None]] | None = None,
    enemy_action_overrides: dict[int, dict[int, str]] | None = None,
    damage_overrides: dict[int, dict[str, int]] | None = None,
    compute_item_recs: bool = True,
) -> dict[str, Any]:
    """Run a single doubles (2v2) calc sim pass and return a result dict.

    `forced_leads` overrides the auto-selected lead pair (used by the doubles line
    search to explore alternate openings). `switch_overrides` forces voluntary-pivot
    decisions on specific turns: it maps turn number -> {slot: bench team-index} to
    force that slot to pivot to a bench mon, or {slot: None} to suppress the
    automatic pivot. Invalid targets (dead/active mons) fall back to the default.
    """
    _progress_tick()
    enemies = _planned_enemies_for_trainer(trainer, calculator)
    item_recommendations = (
        _recommend_items_for_trainer(team, enemies, calculator, trainer)
        if compute_item_recs else []
    )

    # Enemy field slots: indices into `enemies`. First two are the leads.
    p_ea: int = 0                                      # enemy slot a index into enemies[]
    p_eb: int | None = 1 if len(enemies) > 1 else None # enemy slot b index

    # Player field slots: indices into `team`.
    p_a, p_b = _doubles_lead_indices(team, enemies, calculator)
    if forced_leads is not None:
        fa, fb = forced_leads
        # Honor forced leads only when they are valid, alive, and distinct; otherwise
        # keep the auto-selected pair so a bad override can never crash the sim.
        if (
            0 <= fa < len(team) and 0 <= fb < len(team) and fa != fb
            and team[fa].alive and team[fb].alive
        ):
            p_a, p_b = fa, fb

    opening_player_positions = [team[p_a].name, team[p_b].name if p_b != p_a else None]
    opening_enemy_positions = [
        enemies[p_ea].name if enemies else None,
        enemies[p_eb].name if p_eb is not None else None,
    ]

    # Entry abilities for leads
    if enemies:
        _apply_entry_ability(enemies[0], team[p_a], calculator)
        _apply_entry_ability(team[p_a], enemies[0], calculator)
    if p_eb is not None and p_b < len(team):
        _apply_entry_ability(enemies[p_eb], team[p_b], calculator)

    turns: list[dict[str, Any]] = []
    confidence = 1.0
    best_confidence = 1.0

    def _spd(m: PlannedMember | PlannedEnemy) -> float:
        return float(_speed(m.calc_set(), calculator))

    for turn in range(1, max_turns + 1):
        # Refresh live enemy set from current field pointers
        en_a = enemies[p_ea]
        en_b = enemies[p_eb] if p_eb is not None else None

        if not en_a.alive and (en_b is None or not en_b.alive):
            break  # all enemies on field are down (promotions happen below)

        # Snapshot start-of-turn combatant labels before any damage or promotions
        turn_enemy_str = en_a.name + (f" + {en_b.name}" if en_b and en_b.alive else "")

        active_a = team[p_a] if p_a < len(team) else None
        active_b = team[p_b] if p_b < len(team) and p_b != p_a else None

        # --- Fill fainted player slots from bench ---
        # Pick best matchup vs the enemy in the matching slot (slot A vs en_a, slot B vs en_b).
        if active_a is not None and not active_a.alive:
            bench = _doubles_bench(team, (p_b,))
            if bench:
                p_a = _doubles_best_bench(bench, team, en_a, calculator)
                active_a = team[p_a]
                active_a.turns_out = 0
                if en_a.alive:
                    _apply_entry_ability(active_a, en_a, calculator)
            else:
                active_a = None
        if active_b is not None and not active_b.alive:
            bench = _doubles_bench(team, (p_a,))
            if bench:
                enemy_for_slot_b = en_b if en_b is not None and en_b.alive else en_a
                p_b = _doubles_best_bench(bench, team, enemy_for_slot_b, calculator)
                active_b = team[p_b]
                active_b.turns_out = 0
                if enemy_for_slot_b.alive:
                    _apply_entry_ability(active_b, enemy_for_slot_b, calculator)
            else:
                active_b = None

        if active_a is None and active_b is None:
            break  # no live player mons left

        # Collapse into single-slot if one player slot is empty
        if active_a is None:
            active_a, active_b = active_b, None
            p_a, p_b = p_b, p_a  # type: ignore[assignment]

        # Snapshot player names after bench fills (these are who actually fight this turn)
        turn_player_str = active_a.name + (f" + {active_b.name}" if active_b and active_b.alive else "")
        board_before = {
            "player": [
                {"field_slot": 0, "party_index": p_a, "name": active_a.name, "hp": active_a.hp, "max_hp": active_a.max_hp},
                ({"field_slot": 1, "party_index": p_b, "name": active_b.name, "hp": active_b.hp, "max_hp": active_b.max_hp}
                 if active_b and active_b.alive else None),
            ],
            "enemy": [
                {"field_slot": 0, "party_index": p_ea, "name": en_a.name, "hp": en_a.hp, "max_hp": en_a.max_hp},
                ({"field_slot": 1, "party_index": p_eb, "name": en_b.name, "hp": en_b.hp, "max_hp": en_b.max_hp}
                 if en_b and en_b.alive else None),
            ],
        }

        # --- Enemy AI picks (move, target_slot) ---
        ea_options = (
            _doubles_enemy_options(en_a, active_a, active_b, en_b, calculator, force_crit=force_enemy_crits)
            if en_a.alive else []
        )
        eb_options = (
            _doubles_enemy_options(en_b, active_a, active_b, en_a, calculator, force_crit=force_enemy_crits)
            if en_b is not None and en_b.alive else []
        )

        def _pinned_enemy_action(
            field_slot: int,
            options: list[tuple[MoveChoice, int, float]],
        ) -> tuple[MoveChoice | None, int]:
            if not options:
                return None, 0
            pin = (enemy_action_overrides or {}).get(turn, {}).get(field_slot)
            if pin:
                for choice, target_slot, _probability in options:
                    key = f"{_normalize(choice.move_name)}@{target_slot}"
                    if pin == key:
                        return choice, target_slot
            return options[0][0], options[0][1]

        ea_choice, ea_target_slot = _pinned_enemy_action(0, ea_options)
        eb_choice, eb_target_slot = _pinned_enemy_action(1, eb_options)

        # --- Voluntary pivots: a slot facing lethal combined damage switches to a
        # bench mon that takes the incoming hits far better (e.g. ground vs Discharge).
        switch_events: list[str] = []
        pre_pivot_a, pre_pivot_b = active_a, active_b

        def _slot_incoming(slot: int, member: PlannedMember | None) -> int:
            if member is None or not member.alive:
                return 0
            total = 0
            for ch, ts in [(ea_choice, ea_target_slot), (eb_choice, eb_target_slot)]:
                if ch is None or (ts != slot and ts != -1):
                    continue
                ret = _doubles_enemy_choice_for_target(
                    en_a if ch is ea_choice else en_b, member, ch, calculator,
                    force_crit=force_enemy_crits,
                )
                total += ret.damage.max_damage if ret and ret.damage else 0
            return total

        def _try_pivot(slot: int, member: PlannedMember, occupied_idx: int) -> int | None:
            incoming = _slot_incoming(slot, member)
            if incoming < member.hp:
                return None
            bench = _doubles_bench(team, (p_a, p_b))
            best_idx, best_frac = None, 0.35
            for idx in bench:
                cand = team[idx]
                cand_in = _slot_incoming(slot, cand)
                frac = cand_in / max(1, cand.hp)
                if frac < best_frac:
                    best_frac, best_idx = frac, idx
            return best_idx

        # Line-search overrides for this turn: force or suppress voluntary pivots so
        # the doubles line search can explore bait/sac pivots the greedy pass skips.
        turn_overrides = (switch_overrides or {}).get(turn, {})

        def _resolve_pivot(slot: int, member: PlannedMember | None, auto: int | None) -> int | None:
            if member is None or not member.alive:
                return None
            if slot not in turn_overrides:
                return auto
            forced = turn_overrides[slot]
            if forced is None:
                return None  # suppress the automatic pivot for this slot
            # Only honor a forced target that is a live bench mon (not already on field).
            if forced in _doubles_bench(team, (p_a, p_b)):
                return forced
            return None

        pivot_a = _resolve_pivot(0, active_a, _try_pivot(0, active_a, p_b))
        if pivot_a is not None:
            switch_events.append(f"Switch {active_a.name} -> {team[pivot_a].name} (dodging lethal damage).")
            p_a = pivot_a
            active_a = team[p_a]
            active_a.turns_out = 0
            if en_a.alive:
                _apply_entry_ability(active_a, en_a, calculator)
            confidence *= 0.95
        pivot_b = _resolve_pivot(
            1, active_b, _try_pivot(1, active_b, p_a) if active_b and active_b.alive else None
        )
        if pivot_b is not None:
            switch_events.append(f"Switch {active_b.name} -> {team[pivot_b].name} (dodging lethal damage).")
            p_b = pivot_b
            active_b = team[p_b]
            active_b.turns_out = 0
            if (en_b or en_a).alive:
                _apply_entry_ability(active_b, en_b or en_a, calculator)
            confidence *= 0.95
        if switch_events:
            turn_player_str = active_a.name + (f" + {active_b.name}" if active_b and active_b.alive else "")

        # --- Player picks best action per slot ---
        target_a_idx, target_b_idx = _doubles_player_targets(
            active_a, active_b, en_a, en_b, team, calculator
        )
        enemy_target_a: PlannedEnemy = (en_b if target_a_idx == 1 and en_b and en_b.alive else en_a)
        enemy_target_b: PlannedEnemy | None = (en_b if target_b_idx == 1 and en_b and en_b.alive else en_a) if active_b and active_b.alive else None

        action_a = _best_player_action(active_a, enemy_target_a, team, calculator) if pivot_a is None else None
        action_a = _doubles_reliable_action(active_a, enemy_target_a, action_a, calculator)
        action_b = (
            _best_player_action(active_b, enemy_target_b, team, calculator)
            if active_b and active_b.alive and enemy_target_b is not None and pivot_b is None
            else None
        )
        if action_b is not None and enemy_target_b is not None:
            action_b = _doubles_reliable_action(active_b, enemy_target_b, action_b, calculator)

        # --- Confidence: accumulate per-slot threat branches ---
        # Re-estimate each choice's damage against the slot's CURRENT occupant —
        # after a pivot the original estimate (vs the mon that left) is stale.
        choices_vs_a: list[MoveChoice] = []
        choices_vs_b: list[MoveChoice] = []
        for ch, ts, user in [(ea_choice, ea_target_slot, en_a), (eb_choice, eb_target_slot, en_b)]:
            if ch is None or user is None:
                continue
            if ts in (-1, 0):
                ret = _doubles_enemy_choice_for_target(user, active_a, ch, calculator, force_crit=force_enemy_crits)
                if ret is not None:
                    choices_vs_a.append(ret)
            if ts in (-1, 1) and active_b and active_b.alive:
                ret = _doubles_enemy_choice_for_target(user, active_b, ch, calculator, force_crit=force_enemy_crits)
                if ret is not None:
                    choices_vs_b.append(ret)

        def _doubles_branch(usable: list[MoveChoice], member: PlannedMember, en: PlannedEnemy) -> tuple[float, float]:
            # Same reporting treatment as singles: soften secondary disruption for the
            # reported value; best case ignores secondaries entirely.
            set_soften_reporting(True)
            try:
                real = _ai_branch_confidence(usable, member, en, calculator)
            finally:
                set_soften_reporting(False)
            set_ignore_secondary(True)
            try:
                ceiling = _ai_branch_confidence(usable, member, en, calculator)
            finally:
                set_ignore_secondary(False)
            return real, ceiling

        conf_a, best_a = _doubles_branch(choices_vs_a, active_a, en_a) if choices_vs_a else (1.0, 1.0)
        conf_b, best_b = (
            _doubles_branch(choices_vs_b, active_b, en_b or en_a)
            if (choices_vs_b and active_b and active_b.alive)
            else (1.0, 1.0)
        )
        # conf_a / conf_b applied after execution: if the slot's mon actually dies this
        # turn, the death pays a flat trade cost instead — its zeroed branch confidence
        # would double-count the death (same fix Push 6 made for singles sacks).

        # Player move accuracy (singles multiplies this in; doubles must too)
        if action_a is not None:
            confidence *= _player_action_confidence(action_a, calculator, active_a)
        if action_b is not None and active_b is not None:
            confidence *= _player_action_confidence(action_b, calculator, active_b)

        players_alive_before = sum(1 for m in team if m.alive)
        slot_a_member, slot_b_member = active_a, active_b

        # --- Build and sort priority-then-speed actor queue ---
        # Tuple: (move_priority, speed, role_id, actor, primary_target, action_or_choice)
        # Sorted descending so higher-priority moves (e.g. Fake Out +3) always fire first.
        actor_queue: list[tuple[int, float, str, Any, Any, Any]] = []
        if action_a is not None:
            pa_prio = _move_priority_for(action_a.move_name or "", active_a, enemy_target_a, calculator) if action_a.move_name else 0
            actor_queue.append((pa_prio, _spd(active_a), "pa", active_a, enemy_target_a, action_a))
        if active_b and active_b.alive and action_b:
            pb_prio = _move_priority_for(action_b.move_name or "", active_b, enemy_target_b, calculator) if action_b.move_name else 0
            actor_queue.append((pb_prio, _spd(active_b), "pb", active_b, enemy_target_b, action_b))
        if en_a.alive and ea_choice:
            t_ea: PlannedMember = active_b if ea_target_slot == 1 and active_b and active_b.alive else active_a  # type: ignore[assignment]
            ea_prio = _move_priority_for(ea_choice.move_name, en_a, t_ea, calculator)
            actor_queue.append((ea_prio, _spd(en_a), "ea", en_a, t_ea, ea_choice))
        if en_b and en_b.alive and eb_choice:
            t_eb: PlannedMember = active_b if eb_target_slot == 1 and active_b and active_b.alive else active_a  # type: ignore[assignment]
            eb_prio = _move_priority_for(eb_choice.move_name, en_b, t_eb, calculator)
            actor_queue.append((eb_prio, _spd(en_b), "eb", en_b, t_eb, eb_choice))

        actor_queue.sort(key=lambda x: (x[0], x[1]), reverse=True)

        # Every distinct applied-damage state becomes a navigable flowchart axis.
        # Axis ids are based on actor/target field slots, so they remain stable when
        # the same species appears twice or a party index changes after a switch.
        doubles_damage_options: list[dict[str, Any]] = []

        def _hits_partner(move_name: str) -> bool:
            return calculator.moves.get(_normalize(move_name), {}).get("target") == "allAdjacent"

        def _record_damage_axis(axis: str, actor: Any, target: Any, move_name: str, damage: DamageRange | None) -> None:
            outcomes = _damage_outcomes(damage, target.hp)
            if len(outcomes) < 2:
                return
            doubles_damage_options.append({
                "axis": axis,
                "actor": actor.name,
                "target": target.name,
                "move": move_name,
                "options": outcomes,
            })

        for _priority, _actor_speed, role, actor, tgt, act in actor_queue:
            if role in {"pa", "pb"}:
                refreshed = _refresh_player_action(actor, tgt, act, calculator)
                if _is_spread_in_doubles(refreshed.move_name, calculator):
                    for enemy_field_slot, spread_target in enumerate((en_a, en_b)):
                        if spread_target is None or not spread_target.alive:
                            continue
                        spread_action = _doubles_player_action_for_target(actor, spread_target, refreshed, calculator)
                        _record_damage_axis(f"{role}:e{enemy_field_slot}", actor, spread_target, refreshed.move_name, spread_action.damage)
                    if _hits_partner(refreshed.move_name):
                        partner_target = active_b if role == "pa" else active_a
                        partner_slot = 1 if role == "pa" else 0
                        if partner_target is not None and partner_target.alive and partner_target is not actor:
                            partner_action = _doubles_player_action_for_target(actor, partner_target, refreshed, calculator)
                            _record_damage_axis(f"{role}:p{partner_slot}", actor, partner_target, refreshed.move_name, partner_action.damage)
                else:
                    enemy_field_slot = 1 if en_b is not None and tgt is en_b else 0
                    _record_damage_axis(f"{role}:e{enemy_field_slot}", actor, tgt, refreshed.move_name, refreshed.damage)
            else:
                if _is_spread_in_doubles(act.move_name, calculator):
                    for player_field_slot, spread_target in enumerate((active_a, active_b)):
                        if spread_target is None or not spread_target.alive:
                            continue
                        spread_choice = _doubles_enemy_choice_for_target(
                            actor, spread_target, act, calculator, force_crit=force_enemy_crits,
                        )
                        _record_damage_axis(f"{role}:p{player_field_slot}", actor, spread_target, act.move_name, spread_choice.damage if spread_choice else None)
                    if _hits_partner(act.move_name):
                        partner_target = en_b if role == "ea" else en_a
                        partner_slot = 1 if role == "ea" else 0
                        if partner_target is not None and partner_target.alive and partner_target is not actor:
                            partner_choice = _doubles_enemy_choice_for_target(
                                actor, partner_target, act, calculator, force_crit=force_enemy_crits,
                            )
                            _record_damage_axis(f"{role}:e{partner_slot}", actor, partner_target, act.move_name, partner_choice.damage if partner_choice else None)
                else:
                    player_field_slot = 1 if active_b is not None and tgt is active_b else 0
                    targeted_choice = _doubles_enemy_choice_for_target(
                        actor, tgt, act, calculator, force_crit=force_enemy_crits,
                    )
                    _record_damage_axis(f"{role}:p{player_field_slot}", actor, tgt, act.move_name, targeted_choice.damage if targeted_choice else None)

        # --- Execute in priority+speed order ---
        events: list[str] = list(switch_events)

        def _pin_applied_damage(axis: str, target: PlannedMember | PlannedEnemy, before_hp: int, actual: int) -> int:
            pin = (damage_overrides or {}).get(turn, {}).get(axis)
            if pin is None:
                return actual
            applied = max(0, min(before_hp, int(pin)))
            target.hp = max(0, before_hp - applied)
            return applied

        for _, _, role, actor, tgt, act in actor_queue:
            if not actor.alive:
                continue
            # Flinch / sleep skip — mirrors single-battle _skip_turn logic
            was_flinched = getattr(actor, "flinched", False)
            if _skip_turn(actor):
                events.append(f"{actor.name} flinched and couldn't move!" if was_flinched else f"{actor.name} cannot move this turn.")
                continue
            # Retarget if primary target already fainted
            if not tgt.alive:
                if role in ("pa", "pb"):
                    live_foes = [e for e in (en_a, en_b) if e is not None and e.alive]
                    if not live_foes:
                        continue
                    tgt = live_foes[0]
                else:
                    live_friends = [p for p in (active_a, active_b) if p is not None and p.alive]
                    if not live_friends:
                        continue
                    tgt = live_friends[0]

            if role in ("pa", "pb"):
                pact = _refresh_player_action(actor, tgt, act, calculator)
                if not pact.move_name:
                    continue
                spread_tag = " (spread)" if _is_spread_in_doubles(pact.move_name, calculator) else ""
                player_targets: list[tuple[str, PlannedEnemy | PlannedMember]] = []
                if spread_tag:
                    player_targets.extend(
                        (f"{role}:e{slot}", target)
                        for slot, target in enumerate((en_a, en_b))
                        if target is not None and target.alive
                    )
                    if _hits_partner(pact.move_name):
                        partner_target = active_b if role == "pa" else active_a
                        partner_slot = 1 if role == "pa" else 0
                        if partner_target is not None and partner_target.alive and partner_target is not actor:
                            player_targets.append((f"{role}:p{partner_slot}", partner_target))
                else:
                    enemy_field_slot = 1 if en_b is not None and tgt is en_b else 0
                    player_targets.append((f"{role}:e{enemy_field_slot}", tgt))
                for damage_axis, spread_target in player_targets:
                    refreshed = _doubles_player_action_for_target(actor, spread_target, pact, calculator)
                    snap = _action_snapshot(actor, spread_target)
                    before_hp = spread_target.hp
                    dmg = _apply_player_action(actor, spread_target, refreshed, calculator)
                    dmg = _pin_applied_damage(damage_axis, spread_target, before_hp, dmg)
                    side = _action_side_events(actor, spread_target, snap)
                    events.append(f"{actor.name} uses {pact.move_name}{spread_tag} → {spread_target.name} for {dmg}; {spread_target.name} ends {spread_target.hp}/{spread_target.max_hp}.")
                    events.extend(side)
                    if not spread_target.alive:
                        events.append(f"{spread_target.name} faints!")
            else:
                eact = act
                if _is_spread_in_doubles(eact.move_name, calculator):
                    enemy_targets: list[tuple[str, PlannedMember | PlannedEnemy]] = [
                        (f"{role}:p{slot}", target)
                        for slot, target in enumerate((active_a, active_b))
                        if target is not None and target.alive
                    ]
                    if _hits_partner(eact.move_name):
                        partner_target = en_b if role == "ea" else en_a
                        partner_slot = 1 if role == "ea" else 0
                        if partner_target is not None and partner_target.alive and partner_target is not actor:
                            enemy_targets.append((f"{role}:e{partner_slot}", partner_target))
                    for damage_axis, ptgt in enemy_targets:
                        if ptgt is None or not ptgt.alive:
                            continue
                        ret = _doubles_enemy_choice_for_target(actor, ptgt, eact, calculator, force_crit=force_enemy_crits)
                        snap = _action_snapshot(actor, ptgt)
                        before_hp = ptgt.hp
                        dmg = _apply_enemy_action(actor, ptgt, ret, calculator)
                        dmg = _pin_applied_damage(damage_axis, ptgt, before_hp, dmg)
                        side = _action_side_events(actor, ptgt, snap)
                        events.append(f"{actor.name} uses {eact.move_name} (spread) → {ptgt.name} for {dmg}; {ptgt.name} ends {ptgt.hp}/{ptgt.max_hp}.")
                        events.extend(side)
                        if not ptgt.alive:
                            events.append(f"{ptgt.name} faints!")
                else:
                    ret = _doubles_enemy_choice_for_target(actor, tgt, eact, calculator, force_crit=force_enemy_crits)
                    snap = _action_snapshot(actor, tgt)
                    before_hp = tgt.hp
                    dmg = _apply_enemy_action(actor, tgt, ret, calculator)
                    player_field_slot = 1 if active_b is not None and tgt is active_b else 0
                    dmg = _pin_applied_damage(f"{role}:p{player_field_slot}", tgt, before_hp, dmg)
                    side = _action_side_events(actor, tgt, snap)
                    events.append(f"{actor.name} uses {eact.move_name} → {tgt.name} for {dmg}; {tgt.name} ends {tgt.hp}/{tgt.max_hp}.")
                    events.extend(side)
                    if not tgt.alive:
                        events.append(f"{tgt.name} faints!")

        # Apply per-slot threat confidence. A slot whose mon died pays a flat trade
        # cost (0.60) instead of its branch confidence — the per-choice KO model can't
        # see combined two-attacker damage, and zeroed branch confidence would
        # double-count the realized death.
        slot_a_died = slot_a_member is not None and not slot_a_member.alive
        slot_b_died = slot_b_member is not None and not slot_b_member.alive

        # Doubles board redundancy: when a slot's mon survives the turn and its partner
        # is also still alive, a non-lethal disruption (paralysis/burn/flinch) on that
        # mon is far less likely to flip a 2v2 you're winning — the partner keeps acting.
        # Discount the surviving-slot disruption gap toward 1.0 to reflect that. (A slot
        # that actually died is handled by the flat trade cost below, not discounted.)
        _REDUNDANCY = 0.5
        both_alive_after = (
            slot_a_member is not None and slot_a_member.alive
            and slot_b_member is not None and slot_b_member.alive
        )
        if both_alive_after:
            conf_a = 1.0 - (1.0 - conf_a) * _REDUNDANCY
            conf_b = 1.0 - (1.0 - conf_b) * _REDUNDANCY

        eff_a = 0.60 if slot_a_died else conf_a
        eff_b = 0.60 if slot_b_died else conf_b
        confidence *= eff_a * eff_b
        best_confidence *= (0.85 if slot_a_died else best_a) * (0.85 if slot_b_died else best_b)

        # --- End-of-turn effects ---
        for player_m, enemy_m in [(active_a, en_a), (active_b, en_b)]:
            if player_m is None or not player_m.alive:
                continue
            player_m.turns_out += 1
            eot_enemy = enemy_m if enemy_m is not None and enemy_m.alive else en_a
            events.extend(_end_of_turn_events(player_m, eot_enemy, calculator))
        for enemy_m in (en_a, en_b):
            if enemy_m is not None and enemy_m.alive:
                enemy_m.turns_out += 1

        # --- Mark fainted enemy partners ---
        occupied = {p_ea}
        if p_eb is not None:
            occupied.add(p_eb)
        for e_idx in list(occupied):
            if not enemies[e_idx].alive:
                _mark_enemy_allies_fainted(enemies, e_idx)

        # --- Promote bench enemies into fainted slots ---
        # Per the post-KO switch-in AI rule (doubles): enemy slot A evaluates
        # replacements only against player slot A; slot B against slot B.
        occupied = {p_ea, p_eb} - {None}

        if not en_a.alive:
            next_e = _doubles_best_bench_enemy(
                enemies, occupied, active_a, calculator, team  # type: ignore[arg-type]
            )
            if next_e is not None:
                events.append(f"Trainer sends {enemies[next_e].name}!")
                if active_a and active_a.alive:
                    events.extend(_apply_entry_ability(enemies[next_e], active_a, calculator))
                occupied = (occupied - {p_ea}) | {next_e}
                p_ea = next_e

        if en_b is not None and not en_b.alive:
            next_e = _doubles_best_bench_enemy(
                enemies, occupied, active_b if active_b and active_b.alive else active_a, calculator, team
            )
            if next_e is not None:
                events.append(f"Trainer sends {enemies[next_e].name}!")
                if active_a and active_a.alive:
                    events.extend(_apply_entry_ability(enemies[next_e], active_a, calculator))
                p_eb = next_e
            else:
                p_eb = None  # bench exhausted → collapse to 1 enemy

        # --- Emit turn record ---
        en_a_now = enemies[p_ea]
        en_b_now = enemies[p_eb] if p_eb is not None else None
        action_label = (action_a.move_name if action_a and action_a.move_name else "switch") + (f" / {action_b.move_name}" if action_b and action_b.move_name else (" / switch" if pivot_b is not None else ""))
        enemy_label = (ea_choice.move_name if ea_choice else "?") + (f" / {eb_choice.move_name}" if eb_choice else "")
        slot_actions: list[dict[str, Any]] = []
        for field_slot, actor, action, pivot, target in (
            (0, pre_pivot_a, action_a, pivot_a, enemy_target_a),
            (1, pre_pivot_b, action_b, pivot_b, enemy_target_b),
        ):
            if actor is None:
                continue
            if pivot is not None:
                slot_actions.append({
                    "side": "player", "field_slot": field_slot, "actor": actor.name,
                    "kind": "switch", "switch_to": team[pivot].name,
                })
            elif action is not None:
                slot_actions.append({
                    "side": "player", "field_slot": field_slot, "actor": actor.name,
                    "kind": "move", "move": action.move_name,
                    "target": target.name if target is not None else None,
                })
        for field_slot, actor, choice, target_slot in (
            (0, en_a, ea_choice, ea_target_slot),
            (1, en_b, eb_choice, eb_target_slot),
        ):
            if actor is None or choice is None:
                continue
            target_name = "both player slots" if target_slot == -1 else (
                active_b.name if target_slot == 1 and active_b is not None else active_a.name
            )
            slot_actions.append({
                "side": "enemy", "field_slot": field_slot, "actor": actor.name,
                "kind": "move", "move": choice.move_name, "target": target_name,
            })
        row: dict[str, Any] = {
            "turn": turn,
            "enemy": turn_enemy_str,
            "answer": turn_player_str,
            "action": f"Player: {action_label} | Enemy: {enemy_label}",
            "calc": " ".join(events),
            "risks": [],
            "consistency": "doubles-turn",
            "confidence": round(max(0.0, min(1.0, confidence)), 3),
            "best_confidence": round(max(0.0, min(1.0, best_confidence)), 3),
            "your_hp": f"{active_a.hp}/{active_a.max_hp}" + (f" | {active_b.hp}/{active_b.max_hp}" if active_b else ""),
            "enemy_hp": f"{en_a_now.hp}/{en_a_now.max_hp}" + (f" | {en_b_now.hp}/{en_b_now.max_hp}" if en_b_now and en_b_now.alive else ""),
            "is_doubles": True,
            "board_before": board_before,
            "board_after": {
                "player": [
                    {"field_slot": 0, "party_index": p_a, "name": active_a.name, "hp": active_a.hp, "max_hp": active_a.max_hp},
                    ({"field_slot": 1, "party_index": p_b, "name": active_b.name, "hp": active_b.hp, "max_hp": active_b.max_hp}
                     if active_b and active_b.alive else None),
                ],
                "enemy": [
                    {"field_slot": 0, "party_index": p_ea, "name": en_a_now.name, "hp": en_a_now.hp, "max_hp": en_a_now.max_hp},
                    ({"field_slot": 1, "party_index": p_eb, "name": en_b_now.name, "hp": en_b_now.hp, "max_hp": en_b_now.max_hp}
                     if en_b_now and en_b_now.alive else None),
                ],
            },
            "slot_actions": slot_actions,
            "state_sig": _calc_state_signature_doubles(team, enemies, p_a, p_b, p_ea, p_eb),
            "fork": {
                "doubles_enemy_options": [
                    {
                        "enemy_slot": field_slot,
                        "actor": actor.name,
                        "options": [
                            {
                                "move": choice.move_name,
                                "target_slot": target_slot,
                                "target": (
                                    "both player slots" if target_slot == -1
                                    else (active_b.name if target_slot == 1 and active_b is not None else active_a.name)
                                ),
                                "probability": round(probability, 6),
                                "pin": f"{_normalize(choice.move_name)}@{target_slot}",
                                "damage": (
                                    {"min": choice.damage.min_damage, "max": choice.damage.max_damage}
                                    if choice.damage is not None else None
                                ),
                            }
                            for choice, target_slot, probability in options
                        ],
                    }
                    for field_slot, actor, options in (
                        (0, en_a, ea_options),
                        (1, en_b, eb_options),
                    )
                    if actor is not None and options
                ],
                "doubles_damage_options": doubles_damage_options,
            },
        }
        turns.append(row)

    all_enemies_down = all(not e.alive for e in enemies)
    any_player_alive = any(m.alive for m in team)
    success = all_enemies_down and any_player_alive

    return {
        "result": "win-line" if success else "partial-line",
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "best_confidence": round(max(0.0, min(1.0, best_confidence)), 3),
        "turns": turns,
        "team": [_member_payload(m) for m in team],
        "item_recommendations": item_recommendations,
        "is_doubles": True,
        "lead_positions": {"player": opening_player_positions, "enemy": opening_enemy_positions},
        "enemies": [_enemy_payload(enemy) for enemy in enemies],
    }


def _run_text_calc_sim(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    *,
    max_turns: int,
    force_enemy_crits: bool = False,
    forced_lead: int | None = None,
    forced_doubles_leads: tuple[int, int] | None = None,
) -> dict[str, Any]:
    if trainer.is_double:
        if not _CALC_PROGRESS["running"]:
            _progress_reset([("searching the doubles line", 120)])
        _progress_stage("searching the doubles line", 120)
        result = _search_best_line_doubles(
            team, trainer, calculator, max_turns=max_turns, force_enemy_crits=force_enemy_crits,
            forced_leads=forced_doubles_leads,
        )
        result["threat_answers"] = _threat_answers(team, _planned_enemies_for_trainer(trainer, calculator), calculator)
        result["strategy_report"] = _build_strategy_report(result)
        _progress_finish()
        return result

    if not _CALC_PROGRESS["running"]:
        # Standalone run (6 or fewer imported): no team-select stages.
        _progress_reset([
            ("searching the final line", 120),
            ("optimizing held items", 24),
            ("trying pre-battle prep", 14),
            ("mapping every contingency path", _CONTINGENCY_FULL_NODE_BUDGET),
        ])
    _progress_stage("searching the final line", 120)
    result = _search_best_line(
        team,
        trainer,
        calculator,
        max_turns=max_turns,
        force_enemy_crits=force_enemy_crits,
        forced_lead=forced_lead,
    )
    _progress_stage("optimizing held items", 24)
    # Greedily adopt recommended/STAB items, keeping each only if it improves the line.
    # Candidates are ranked with a cheap single-pass sim; the winning loadout then gets
    # the full line search so the reported optimized line is fully searched.
    enemies_for_items = _planned_enemies_for_trainer(trainer, calculator)
    result["item_recommendations"] = _recommend_items_for_trainer(
        team,
        enemies_for_items,
        calculator,
        trainer,
    )

    def _singles_eval(t: list[PlannedMember]) -> dict[str, Any]:
        return _run_text_calc_sim_once(
            _clone_calc_team(t), trainer, calculator,
            max_turns=max_turns,
            force_enemy_crits=force_enemy_crits,
            forced_lead=forced_lead,
            compute_item_recs=False,
        )

    _, adopted = _greedy_items(team, trainer, calculator, enemies_for_items, _singles_eval)
    base_overrides = {
        int(turn): slot
        for turn, slot in ((result.get("line_search") or {}).get("overrides") or {}).items()
    } or None

    # Build candidate item loadouts: the greedy subset (each change proven on a single-pass
    # line) and the recommender's full set (offensive items the full line search may exploit
    # better than the cheap eval can see). Full-search BOTH and keep whichever is best, so
    # the optimized line never falls below the all-or-nothing recommendation.
    by_name = {_normalize(m.name): i for i, m in enumerate(team)}
    candidate_loadouts: list[tuple[list[PlannedMember], list[dict[str, Any]]]] = []
    if adopted:
        items_by_idx: dict[int, str] = {}
        for rec in adopted:
            idx = by_name.get(_normalize(str(rec.get("pokemon", ""))))
            if idx is not None:
                items_by_idx[idx] = rec["suggested_item"]
        greedy_team = _clone_calc_team([
            replace_member_item(m, items_by_idx[i]) if i in items_by_idx else m
            for i, m in enumerate(team)
        ])
        greedy_changes = [
            {"pokemon": r["pokemon"], "old_item": r.get("old_item"), "new_item": r["suggested_item"],
             "reason": r.get("reason", ""), "source": r.get("source", "")}
            for r in adopted
        ]
        candidate_loadouts.append((greedy_team, greedy_changes))
    full_team, full_changes = _team_with_recommended_items(team, result.get("item_recommendations") or [])
    if full_changes:
        candidate_loadouts.append((full_team, full_changes))

    best_opt: dict[str, Any] | None = None
    best_opt_team: list[PlannedMember] | None = None
    for cand_team, cand_changes in candidate_loadouts:
        cand = _search_best_line(
            cand_team, trainer, calculator, max_turns=max_turns,
            force_enemy_crits=force_enemy_crits, forced_lead=forced_lead,
            budget=24, seed_overrides=base_overrides,
        )
        cand["hax_outlook"] = _hax_outlook(cand.get("turns") or [], _planned_enemies_for_trainer(trainer, calculator), cand_team, calculator)
        if not force_enemy_crits:
            _report_plan_confidence(cand)
        cand["item_changes"] = cand_changes
        cand["item_retry_policy"] = "Recommended-item line uses the planner's suggested held items; current import remains the source of truth for the first line."
        if best_opt is None or _line_quality_key(cand) > _line_quality_key(best_opt):
            best_opt = cand
            best_opt_team = cand_team
    if best_opt is not None:
        result["optimized_item_line"] = best_opt

    # Best-case ceiling: the SAME planned line, but reported as if no enemy crits and
    # no secondary effects fire. Built from the parallel best_confidence tracked during
    # the single run, so it is always >= the realistic line and never diverges.
    result["best_case_line"] = _best_case_view(result)
    # Statistical flinch/crit outlook over the realistic line.
    fresh_enemies = _planned_enemies_for_trainer(trainer, calculator)
    result["hax_outlook"] = _hax_outlook(result.get("turns") or [], fresh_enemies, team, calculator)
    if not force_enemy_crits:
        _report_plan_confidence(result)
    # Advisory: best answer per enemy + which enemies the box has no clean answer to.
    result["threat_answers"] = _threat_answers(team, _planned_enemies_for_trainer(trainer, calculator), calculator)
    if forced_lead is not None and 0 <= forced_lead < len(team):
        result["lead_strategy"] = f"Lead {team[forced_lead].name} chosen by the planner instead of the default best-damage lead."
    # Pre-battle prep strategies (pre-status / pre-damage) + the strategy toolbox
    # used by the battle-plan PDF appendix.
    _progress_stage("trying pre-battle prep", 14)
    _try_prep_strategies(
        team, trainer, calculator, result,
        max_turns=max_turns, force_enemy_crits=force_enemy_crits, forced_lead=forced_lead,
    )
    result["strategy_report"] = _build_strategy_report(result)
    # Contingency flowchart: branch the line on every plausible enemy move, outcome-flipping
    # crits, and non-guaranteed KOs, each fork replayed to its end. Built on the same lead
    # the line search settled on, and on the item loadout of the line the user will actually
    # follow — the optimized-items line when it beats the imported one.
    flowchart_team = team
    flowchart_plan = result
    flowchart_note = "Built for Line 1 (your imported held items)."
    if best_opt is not None and best_opt_team is not None and _line_quality_key(best_opt) > _line_quality_key(result):
        flowchart_team = best_opt_team
        flowchart_plan = best_opt
        flowchart_note = "Built for the recommended-items line — equip the suggested held items before the fight to follow this chart."
    flowchart_search = flowchart_plan.get("line_search") or {}
    flowchart_lead = flowchart_search.get("lead", forced_lead)
    flowchart_moves = {
        int(turn): str(move)
        for turn, move in (flowchart_search.get("move_overrides") or {}).items()
    }
    _progress_stage("mapping every contingency path", _CONTINGENCY_FULL_NODE_BUDGET)
    result["contingency_flowchart"] = _contingency_flowchart(
        flowchart_team, trainer, calculator,
        max_turns=max_turns,
        force_enemy_crits=force_enemy_crits,
        forced_lead=flowchart_lead,
        player_move_overrides=flowchart_moves,
        exhaustive=True,
        time_budget_s=_CONTINGENCY_TIME_BUDGET_S,
    )
    result["contingency_flowchart_note"] = flowchart_note
    _progress_finish()
    return result


_FLINCH_RE = re.compile(r"(\d+)%\s*flinch", re.IGNORECASE)
_AI_MOVE_RE = re.compile(r"AI likely ([A-Za-z0-9 '\-!]+?)\s*\(")


def _hax_outlook(
    turns: list[dict[str, Any]],
    enemies: list[PlannedEnemy],
    team: list[PlannedMember],
    calculator: DamageCalculator,
) -> dict[str, Any]:
    """Expected flinches/crits across the line + the turns where one would break it.

    Probabilities are read from the turn risk notes and the crit-rate model, then folded
    into expectations (sum) and 'at least one' odds (1 - product of not-happening)."""
    enemy_by_name = {e.name: e for e in enemies}
    mon_by_name = {m.name: m for m in team}
    flinch_events: list[dict[str, Any]] = []
    crit_ko_events: list[dict[str, Any]] = []
    for t in turns:
        risks = t.get("risks") or []
        text = " ".join(risks)
        enemy = enemy_by_name.get(t.get("enemy"))
        mon = mon_by_name.get(t.get("answer"))
        move_match = _AI_MOVE_RE.search(text)
        move_name = move_match.group(1).strip() if move_match else ""
        flinch_match = _FLINCH_RE.search(text)
        p_flinch = int(flinch_match.group(1)) / 100.0 if flinch_match else 0.0
        crit_breaks = any("crit" in r.lower() and "can ko" in r.lower() for r in risks)
        p_crit = crit_rate(enemy, mon, move_name, calculator) if (enemy and mon and move_name) else 0.0
        if p_flinch > 0:
            flinch_events.append({"turn": t.get("turn"), "enemy": t.get("enemy"), "mon": t.get("answer"), "p": round(p_flinch, 3), "move": move_name})
        if crit_breaks and p_crit > 0:
            crit_ko_events.append({"turn": t.get("turn"), "enemy": t.get("enemy"), "mon": t.get("answer"), "p": round(p_crit, 3), "move": move_name})

    def _prod_not(events: list[dict[str, Any]]) -> float:
        out = 1.0
        for ev in events:
            out *= (1.0 - ev["p"])
        return out

    expected_flinches = round(sum(ev["p"] for ev in flinch_events), 2)
    expected_crit_kos = round(sum(ev["p"] for ev in crit_ko_events), 2)
    return {
        "expected_flinches": expected_flinches,
        "p_any_flinch": round(1.0 - _prod_not(flinch_events), 3),
        "p_no_flinch": round(_prod_not(flinch_events), 3),
        "flinch_turns": flinch_events,
        "expected_crit_kos": expected_crit_kos,
        "p_any_crit_ko": round(1.0 - _prod_not(crit_ko_events), 3),
        "crit_ko_turns": crit_ko_events,
    }


def _report_plan_confidence(result: dict[str, Any]) -> None:
    """Expose plan stability as the headline confidence and keep hax separately.

    The turn loop tracks two useful numbers: `confidence` includes cumulative crit and
    secondary-effect disruption, while `best_confidence` is the same plan assuming those
    luck branches do not fire. The UI now has `hax_outlook` for the luck audit, so the
    headline confidence should answer "is this line structurally sound?" instead of
    repeatedly multiplying every possible unlucky branch into one scary number.
    """
    realistic = result.get("confidence")
    plan = result.get("best_confidence", realistic)
    if plan is None:
        return
    result["realistic_confidence"] = realistic
    result["confidence"] = plan
    result["confidence_model"] = (
        "Plan confidence excludes enemy crit/secondary hax; see realistic_confidence "
        "and hax_outlook for the luck-adjusted audit."
    )
    result["confidence_range"] = {
        "floor": round(float(realistic), 3) if realistic is not None else None,
        "ceiling": round(float(plan), 3),
        "meaning": "Luck-adjusted floor to structural-plan ceiling; this is a model range, not a measured win-rate interval.",
    }


# Risk notes that only matter because of luck (crits / secondary effects); dropped
# from the best-case line, which assumes none of them happen.
_LUCK_RISK_MARKERS = (
    "crit", "flinch", "paralysis", "paralyze", "burn", "freeze", "frozen",
    "confus", "stat-drop", "% accurate", "accuracy", "chance", "miss",
)


def _best_case_view(result: dict[str, Any]) -> dict[str, Any]:
    def _clean_turn(turn: dict[str, Any]) -> dict[str, Any]:
        kept = [r for r in (turn.get("risks") or []) if not any(m in r.lower() for m in _LUCK_RISK_MARKERS)]
        return {**turn, "confidence": turn.get("best_confidence", turn.get("confidence", 1.0)), "risks": kept}

    return {
        "trainer": result.get("trainer"),
        "location": result.get("location"),
        "result": result.get("result"),
        "confidence": result.get("best_confidence", result.get("confidence", 1.0)),
        "crit_safe": False,
        "team": result.get("team"),
        "enemies": result.get("enemies"),
        "turns": [_clean_turn(t) for t in (result.get("turns") or [])],
        "mode": "best-case",
        "mode_note": "Best case is the exact same line as Line 1, but assuming no enemy crits and no secondary effects fire (no flinch, paralysis, burn, stat drops, etc.) and your own moves land. It is the optimistic ceiling, not what to expect every attempt.",
    }


def _run_text_calc_sim_once(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    *,
    max_turns: int,
    force_enemy_crits: bool = False,
    forced_lead: int | None = None,
    switch_overrides: dict[int, int | None] | None = None,
    player_move_overrides: dict[int, str] | None = None,
    enemy_move_overrides: dict[int, str] | None = None,
    ai_switch_overrides: dict[int, bool] | None = None,
    kill_overrides: dict[int, bool] | None = None,
    enemy_crit_overrides: dict[int, bool] | None = None,
    player_crit_overrides: dict[int, bool] | None = None,
    player_damage_overrides: dict[int, int] | None = None,
    enemy_damage_overrides: dict[int, int] | None = None,
    branch_actual_rng: bool = False,
    compute_item_recs: bool = True,
    decision_cache: dict | None = None,
) -> dict[str, Any]:
    """Single deterministic line.

    `enemy_move_overrides` (turn -> move name) pins the enemy's chosen move on that turn,
    `player_damage_overrides` and `enemy_damage_overrides` pin an exact applied damage
    outcome (0 means a miss) so range branches preserve the real resulting HP state,
    `kill_overrides` (turn -> bool) pins whether the player's attack KOs the enemy that
    turn (True forces the KO, False forces the no-KO outcome: a low-roll survivor, or a
    clean miss when even the min roll would have KO'd), and `enemy_crit_overrides`
    (turn -> bool) pins whether the enemy's attack that turn lands a critical hit. These let
    the contingency flowchart re-run the exact same line with one outcome pinned, producing
    alternate timelines. Each normal attack turn also records a `fork` block (the player's KO
    odds, the enemy's plausible alternate moves, and the enemy's crit odds + whether a crit
    would flip the active mon from surviving to fainting) so the tree builder knows where it
    can branch.

    `decision_cache` (optional; only share across replays of the SAME team/trainer/lead/crit
    mode) memoizes per-turn decisions by the battle-state signature entering the turn. The
    contingency tree and line search replay this sim hundreds of times with identical
    prefixes, so cached decisions make replayed turns nearly free without changing them.
    """
    _progress_tick()
    # A crit-aware line chooses our lead, switches, and moves conservatively. The
    # flowchart still needs the battle's real RNG, so its AI move/damage branches use
    # normal damage and then fork crit vs non-crit explicitly.
    ai_force_enemy_crits = force_enemy_crits and not branch_actual_rng

    def _decide(kind: str, key: Any, compute):
        if decision_cache is None:
            return compute()
        full_key = (kind, key)
        hit = decision_cache.get(full_key, _DECIDE_MISS)
        if hit is not _DECIDE_MISS:
            return hit
        value = compute()
        decision_cache[full_key] = value
        return value
    enemies = _planned_enemies_for_trainer(trainer, calculator)
    # The contingency tree re-runs this sim once per node and never reads item recs, so it
    # opts out — recomputing the held-item table per node was the dominant build cost.
    item_recommendations = (
        _recommend_items_for_trainer(team, enemies, calculator, trainer)
        if compute_item_recs
        else []
    )
    active_index = forced_lead if forced_lead is not None else (
        _best_calc_answer(
            team,
            enemies[0],
            calculator,
            force_enemy_crits=force_enemy_crits,
            allow_sac=True,
            enemy_will_intimidate=True,
        )
        if enemies
        else None
    )
    enemy_index = 0
    turns: list[dict[str, Any]] = []
    confidence = 1.0
    # Parallel "best case" confidence for the SAME line: the planner still decides
    # moves/switches using real risk, but this accumulator assumes no enemy crits and
    # no secondary effects fire, so it is always the optimistic ceiling for this line.
    best_confidence = 1.0

    def _branch_both(usable: list[MoveChoice], member: PlannedMember, en: PlannedEnemy, *, entry: bool = False) -> tuple[float, float]:
        # Reported value softens secondary disruption; best case ignores it entirely.
        # The planner's own decisions are made elsewhere with both flags off (strict).
        # On switch-entry turns the incoming mon does not act, so flinch branches
        # cannot disrupt the line and are excluded from the threat model.
        set_entry_turn(entry)
        set_soften_reporting(True)
        try:
            real = _ai_branch_confidence(usable, member, en, calculator)
        finally:
            set_soften_reporting(False)
        set_ignore_secondary(True)
        try:
            best = _ai_branch_confidence(usable, member, en, calculator)
        finally:
            set_ignore_secondary(False)
            set_entry_turn(False)
        return real, best

    def _player_ko_chance(member: PlannedMember, en: PlannedEnemy, move_name: str) -> float:
        # Probability this move KOs the enemy outright (rolls x accuracy). If we move
        # first and this is high, the enemy never gets to act, so its threat shouldn't
        # count against the line.
        if not move_name or not getattr(en, "alive", True):
            return 0.0
        try:
            dmg = calculator.estimate_move(member.calc_set(), en.calc_set(), move_name)
        except Exception:
            return 0.0
        if dmg is None:
            return 0.0
        return max(0.0, min(1.0, dmg.ko_chance))

    def _maybe_crit(en: PlannedEnemy, target: PlannedMember, ch: MoveChoice | None, this_turn: int) -> MoveChoice | None:
        # When the contingency tree pinned a crit on this turn, swap in a critical-hit
        # damage roll for the enemy's chosen move; otherwise leave the choice untouched.
        if not (enemy_crit_overrides or {}).get(this_turn):
            return ch
        if ch is None or ch.damage is None or ch.damage.max_damage <= 0:
            return ch
        crit = calculator.estimate_move(en.calc_set(), target.calc_set(), ch.move_name, DamageContext(critical=True))
        if crit is None:
            return ch
        return MoveChoice(ch.move_name, ch.score, ch.probability, crit, f"{ch.reason}; crit (branch)")

    def _maybe_player_crit(
        member: PlannedMember,
        target: PlannedEnemy,
        action: PlayerAction,
        this_turn: int,
    ) -> PlayerAction:
        if not (player_crit_overrides or {}).get(this_turn):
            return action
        if action.damage is None or action.damage.max_damage <= 0:
            return action
        critical = calculator.estimate_move(
            member.calc_set(), target.calc_set(), action.move_name,
            DamageContext(critical=True),
        )
        return replace(action, damage=critical, reason=f"{action.reason}; player crit branch") if critical else action

    def _pinned_player_action(action: PlayerAction, this_turn: int) -> PlayerAction:
        value = (player_damage_overrides or {}).get(this_turn)
        if value is None or action.damage is None:
            return action
        return replace(action, damage=_damage_range_at_roll(action.damage, value, enemy.hp))

    def _pinned_enemy_choice(
        choice: MoveChoice | None,
        target: PlannedMember,
        this_turn: int,
    ) -> MoveChoice | None:
        value = (enemy_damage_overrides or {}).get(this_turn)
        if value is None or choice is None or choice.damage is None:
            return choice
        return MoveChoice(
            choice.move_name, choice.score, choice.probability,
            _damage_range_at_roll(choice.damage, value, target.hp),
            f"{choice.reason}; exact damage branch",
        )

    previous_switch: tuple[int, int, int] | None = None
    switch_streak = 0
    last_switch_enemy_index: int | None = None
    last_switch_enemy_hp = 0
    switches_without_progress = 0
    pending_events: list[str] = []
    last_control_action: tuple[int, int, str] | None = None
    if active_index is not None and enemies:
        pending_events += _apply_entry_ability(enemies[0], team[active_index], calculator)
        pending_events += _apply_entry_ability(team[active_index], enemies[0], calculator)

    current_entry_sig: str | None = None

    def _emit(row: dict[str, Any]) -> None:
        nonlocal pending_events
        if pending_events:
            row["calc"] = " ".join(pending_events + ([row["calc"]] if row.get("calc") else []))
            pending_events = []
        row["best_confidence"] = round(max(0.0, min(1.0, best_confidence)), 3)
        row.setdefault("state_sig", current_entry_sig)
        turns.append(row)

    for turn in range(1, max_turns + 1):
        if active_index is None or not any(enemy.alive for enemy in enemies):
            break
        enemy = enemies[enemy_index]
        active = team[active_index]
        if enemy.status == "sleep" and enemy.sleep_turns <= 0:
            enemy.status = None
        if active.status == "sleep" and active.sleep_turns <= 0:
            active.status = None
        # State entering this turn — the memo key the contingency tree dedupes on.
        current_entry_sig = _calc_state_signature(team, enemies, active_index, enemy_index)
        if not active.alive:
            # A faint never ends a battle while another party member is alive. The
            # replacement is mandatory even when every remaining answer is unsafe;
            # refusing to pick one truncated failed lines and let the search hide the
            # actual blackout (or miss a last-ditch clear).
            active_index = _best_calc_answer(
                team, enemy, calculator,
                force_enemy_crits=force_enemy_crits,
                allow_sac=True,
            )
            if active_index is None:
                break
            active = team[active_index]
            active.turns_out = 0
            send_events = _apply_entry_ability(active, enemy, calculator)
            _emit(
                _calc_turn(
                    turn,
                    enemy,
                    active,
                    f"Forced send {_member_label(active)}.",
                    " ".join(["Free send after a faint."] + send_events),
                    [],
                    "forced-send",
                    confidence,
                )
            )
            previous_switch = None
            switch_streak = 0
            continue
        switch_is_stagnant = (
            last_switch_enemy_index == enemy_index
            and enemy.hp >= last_switch_enemy_hp
            and switches_without_progress >= 2
        )
        forced_switch = (switch_overrides or {}).get(turn, "auto")
        if forced_switch != "auto":
            # Line-search override: force this turn's switch decision (None forbids
            # switching; an index forces that slot in if it is a legal switch).
            if (
                isinstance(forced_switch, int)
                and 0 <= forced_switch < len(team)
                and forced_switch != active_index
                and team[forced_switch].alive
            ):
                switch_index = forced_switch
            else:
                switch_index = None
        else:
            switch_index = (
                None
                if switch_is_stagnant
                else _decide(
                    "switch",
                    (current_entry_sig, previous_switch, switch_streak, force_enemy_crits),
                    lambda: _calc_switch_target(
                        team,
                        active_index,
                        enemy,
                        calculator,
                        force_enemy_crits=force_enemy_crits,
                        previous_switch=previous_switch,
                        enemy_index=enemy_index,
                        switch_streak=switch_streak,
                    ),
                )
            )
        if switch_index is not None:
            outgoing = active
            incoming = team[switch_index]
            choices = _decide(
                "enemy_choices",
                (current_entry_sig, ai_force_enemy_crits),
                lambda: _calc_enemy_choices(enemy, outgoing, team, calculator, force_enemy_crits=ai_force_enemy_crits),
            )
            choice = choices[0] if choices else None
            forced_enemy_move = (enemy_move_overrides or {}).get(turn)
            if forced_enemy_move is not None:
                pinned = next((c for c in choices if _normalize(c.move_name) == _normalize(forced_enemy_move)), None)
                if pinned is not None:
                    choice = pinned
            _apply_switch_out_effects(outgoing)
            entry_events = _apply_entry_ability(incoming, enemy, calculator)
            retargeted = _retarget_choice_for_calc(enemy, incoming, choice, calculator, force_enemy_crits=ai_force_enemy_crits)
            retargeted_choices = [_retarget_choice_for_calc(enemy, incoming, item, calculator, force_enemy_crits=ai_force_enemy_crits) for item in choices]
            usable_retargeted_choices = [item for item in retargeted_choices if item is not None]
            retargeted = _maybe_crit(enemy, incoming, retargeted, turn)
            retargeted = _pinned_enemy_choice(retargeted, incoming, turn)
            switch_crit_chance = round(crit_rate(enemy, incoming, retargeted.move_name, calculator), 3) if retargeted else 0.0
            switch_crit_changes = False
            if (
                not force_enemy_crits and retargeted and retargeted.damage
                and retargeted.damage.max_damage > 0 and retargeted.damage.ko_chance < 0.5
                and 0.0 < switch_crit_chance < 1.0
            ):
                switch_crit_damage = calculator.estimate_move(
                    enemy.calc_set(), incoming.calc_set(), retargeted.move_name,
                    DamageContext(critical=True, defender_is_switching=True),
                )
                switch_crit_changes = bool(switch_crit_damage and switch_crit_damage.ko_chance >= 0.9)
            switch_fork_meta = {
                "enemy_move": retargeted.move_name if retargeted else None,
                "enemy_crit_chance": switch_crit_chance,
                "enemy_crit_changes": switch_crit_changes,
                "enemy_crits_forced": ai_force_enemy_crits,
                "enemy_alternatives": [
                    {"move": c.move_name, "score": round(c.score, 2), "probability": round(c.probability, 6)}
                    for c in choices if c.probability > 0.0
                ],
                "player_damage_outcomes": [],
                "enemy_damage_outcomes": _damage_outcomes(retargeted.damage if retargeted else None, incoming.hp),
                "damage_order": ["enemy_damage"],
            }
            risks = _crit_mode_notes(force_enemy_crits) + _choice_risks(retargeted, incoming, enemy, calculator) + _branch_risk_notes(
                usable_retargeted_choices,
                incoming,
                enemy,
                calculator,
            )
            snapshot = _action_snapshot(enemy, incoming)
            # Branch confidence must be judged from the HP the switch-in actually has
            # when the entry hit lands, not after it is applied (otherwise the entry
            # damage is double-counted into crit/KO threats).
            _real, _best = _decide(
                "branch_both_entry",
                (current_entry_sig, switch_index, force_enemy_crits),
                lambda: _branch_both(usable_retargeted_choices, incoming, enemy, entry=True),
            )
            pinned_enemy_damage = (enemy_damage_overrides or {}).get(turn)
            if pinned_enemy_damage == 0 and retargeted and retargeted.damage is not None:
                damage_taken = 0
                attack_text = f"{enemy.name}'s {retargeted.move_name} misses the switch-in; {incoming.name} ends {incoming.hp}/{incoming.max_hp}."
            else:
                damage_taken = _apply_enemy_action(enemy, incoming, retargeted, calculator)
                attack_text = f"{enemy.name} chooses {choice.move_name if choice else 'unknown'} into the outgoing slot; {incoming.name} takes {damage_taken} and ends {incoming.hp}/{incoming.max_hp}."
            side_events = _action_side_events(enemy, incoming, snapshot)
            eot_events = _end_of_turn_events(incoming, enemy, calculator)
            previous_switch = (active_index, switch_index, enemy_index)
            switch_streak += 1
            if last_switch_enemy_index == enemy_index and enemy.hp >= last_switch_enemy_hp:
                switches_without_progress += 1
            else:
                switches_without_progress = 1
            last_switch_enemy_index = enemy_index
            last_switch_enemy_hp = enemy.hp
            active_index = switch_index
            incoming.turns_out = 0
            if enemy.alive:
                enemy.turns_out += 1
            confidence *= _real
            best_confidence *= _best
            switch_row = _calc_turn(
                    turn,
                    enemy,
                    incoming,
                    f"Switch {_member_label(outgoing)} -> {_member_label(incoming)}.",
                    " ".join(
                        entry_events
                        + [attack_text]
                        + side_events
                        + eot_events
                    ),
                    risks,
                    "switch-cost",
                    confidence,
                )
            switch_row["fork"] = switch_fork_meta
            _emit(switch_row)
            continue
        previous_switch = None
        switch_streak = 0
        switches_without_progress = 0
        forced_player_move = (player_move_overrides or {}).get(turn)
        if forced_player_move is not None and any(
            _normalize(move) == _normalize(forced_player_move) for move in active.known_moves
        ):
            forced_damage = calculator.estimate_move(active.calc_set(), enemy.calc_set(), forced_player_move)
            action = PlayerAction(
                "move", forced_player_move, score=0.0, damage=forced_damage,
                reason="line search move override",
            )
        else:
            action = _decide(
                "player_action",
                (current_entry_sig,),
                lambda: _best_player_action(active, enemy, team, calculator),
            )
        # Do not let a one-Pokémon fight loop forever on Growl/Mud-Slap while the
        # opponent immediately restores the same stage with Howl. A repeated
        # zero-damage control click against the same target yields to the best real
        # damaging move on the next turn. Switching or changing targets resets it.
        control_key = (active_index, enemy_index, _normalize(action.move_name))
        if action.damage is None or action.damage.max_damage <= 0:
            if last_control_action == control_key:
                damaging = _ranked_known_damage(
                    calculator, active.calc_set(), enemy.calc_set(), active.known_moves
                )
                if damaging:
                    damage = damaging[0]
                    action = PlayerAction(
                        "move", damage.move_name,
                        score=damage.min_percent * 100.0,
                        damage=damage,
                        reason="make progress after a repeated control turn",
                    )
                    last_control_action = None
                else:
                    last_control_action = control_key
            else:
                last_control_action = control_key
        else:
            last_control_action = None
        if not action.move_name:
            _emit(_calc_turn(turn, enemy, active, "Planner blocked.", "No reliable imported move for this matchup.", [], "blocked", confidence * 0.35))
            break
        ai_switch = _decide(
            "ai_hard_switch",
            (current_entry_sig,),
            lambda: _ai_hard_switch_target(enemies, enemy_index, active, calculator),
        )
        forced_ai_switch = (ai_switch_overrides or {}).get(turn)
        if ai_switch is not None and forced_ai_switch is False:
            ai_switch = None
        if ai_switch is not None:
            old_enemy = enemy
            incoming_enemy = enemies[ai_switch]
            enemy_index = ai_switch
            incoming_enemy.turns_out = 0
            active.turns_out += 1
            entry_events = _apply_entry_ability(incoming_enemy, active, calculator)
            switched_action = _retarget_player_action(active, incoming_enemy, action, calculator)
            snapshot = _action_snapshot(active, incoming_enemy)
            damage = _apply_player_action(active, incoming_enemy, switched_action, calculator)
            side_events = _action_side_events(active, incoming_enemy, snapshot)
            eot_events = _end_of_turn_events(active, incoming_enemy, calculator)
            confidence *= 0.5
            best_confidence *= 0.5
            events = (
                [f"{old_enemy.name} has no useful scored move, so AI may switch."]
                + entry_events
                + [f"{active.name} hits {incoming_enemy.name} for {damage}; {incoming_enemy.name} ends {incoming_enemy.hp}/{incoming_enemy.max_hp}."]
                + side_events
                + eot_events
            )
            if not incoming_enemy.alive:
                events.append(f"{incoming_enemy.name} faints.")
                _mark_enemy_allies_fainted(enemies, enemy_index)
                next_enemy = _next_calc_enemy(enemies, team, active_index, calculator, force_enemy_crits=ai_force_enemy_crits)
                if next_enemy is not None:
                    enemy_index = next_enemy
                    events.append(f"Trainer sends {enemies[enemy_index].name}.")
                    events.extend(_apply_entry_ability(enemies[enemy_index], active, calculator))
            switch_row = _calc_turn(
                    turn,
                    incoming_enemy,
                    active,
                    f"{_member_label(active)} clicked {action.move_name}; AI hard-switch branch to {incoming_enemy.name}.",
                    " ".join(events),
                    ["AI hard switch is a 50% branch when the active enemy has only ineffective moves and a legal safer back mon."],
                    "ai-switch-branch",
                    confidence,
                )
            switch_row["fork"] = {
                "ai_switch_chance": 0.5,
                "ai_switch_target": incoming_enemy.name,
            }
            _emit(switch_row)
            continue
        choices = _decide(
            "enemy_choices",
            (current_entry_sig, ai_force_enemy_crits),
            lambda: _calc_enemy_choices(enemy, active, team, calculator, force_enemy_crits=ai_force_enemy_crits),
        )
        choice = choices[0] if choices else None
        # Pin the enemy's move this turn if the contingency tree asked for it.
        forced_enemy_move = (enemy_move_overrides or {}).get(turn)
        if forced_enemy_move is not None:
            pinned = next((c for c in choices if _normalize(c.move_name) == _normalize(forced_enemy_move)), None)
            if pinned is not None:
                choice = pinned
        choice = _maybe_crit(enemy, active, choice, turn)
        choice = _pinned_enemy_choice(choice, active, turn)
        action = _maybe_player_crit(active, enemy, action, turn)
        action = _pinned_player_action(action, turn)
        # Record what could branch here: the player's KO odds, the enemy's plausible alternate
        # moves (the full distribution down to a small floor, so the long-tail "what does it do
        # the other X%" move is kept, not just the near-tied top moves), and the enemy's crit
        # odds plus whether a crit would flip the active mon from surviving to fainting.
        # Consumed by the contingency tree builder.
        enemy_crit_chance = round(crit_rate(enemy, active, choice.move_name, calculator), 3) if choice else 0.0
        enemy_crit_changes = False
        if (
            not force_enemy_crits
            and choice is not None
            and choice.damage is not None
            and choice.damage.max_damage > 0
            and choice.damage.ko_chance < 0.5
            and 0.0 < enemy_crit_chance < 1.0
        ):
            crit_dmg = calculator.estimate_move(
                enemy.calc_set(), active.calc_set(), choice.move_name, DamageContext(critical=True)
            )
            if crit_dmg is not None and crit_dmg.ko_chance >= 0.9:
                enemy_crit_changes = True
        fork_meta: dict[str, Any] = {
            "player_move": action.move_name,
            "player_ko_chance": round(_player_ko_chance(active, enemy, action.move_name), 3),
            # When even the min roll KOs, the only no-KO outcome is a miss (ko_chance is
            # rolls x accuracy), so the tree can label that branch honestly.
            "player_no_ko_means_miss": bool(
                action.damage is not None
                and action.damage.max_damage > 0
                and action.damage.min_damage >= max(1, enemy.hp)
            ),
            "player_crit_chance": round(crit_rate(active, enemy, action.move_name, calculator), 3) if action.damage else 0.0,
            "enemy_move": choice.move_name if choice else None,
            "enemy_crit_chance": enemy_crit_chance,
            "enemy_crit_changes": enemy_crit_changes,
            "enemy_crits_forced": ai_force_enemy_crits,
            "enemy_alternatives": [
                {"move": c.move_name, "score": round(c.score, 2), "probability": round(c.probability, 6)}
                for c in choices
                if c.probability > 0.0
            ],
            "player_damage_outcomes": _damage_outcomes(action.damage, enemy.hp),
            "enemy_damage_outcomes": _damage_outcomes(choice.damage if choice else None, active.hp),
        }
        risks = _crit_mode_notes(force_enemy_crits) + _choice_risks(choice, active, enemy, calculator) + _branch_risk_notes(choices, active, enemy, calculator)
        enemy_first = _enemy_moves_before_player(enemy, active, choice.move_name if choice else "", action.move_name, calculator)
        if enemy_first and not _will_skip_turn(enemy) and _choice_kills_current(choice, active):
            sac_index = _decide(
                "sac_target",
                (current_entry_sig, choice.move_name if choice else None, force_enemy_crits),
                lambda: _tactical_sac_target(team, active_index, enemy, calculator, choice, force_enemy_crits=force_enemy_crits),
            )
            if sac_index is not None and sac_index != active_index:
                outgoing = active
                incoming = team[sac_index]
                _apply_switch_out_effects(outgoing)
                entry_events = _apply_entry_ability(incoming, enemy, calculator)
                retargeted = _retarget_choice_for_calc(enemy, incoming, choice, calculator, force_enemy_crits=ai_force_enemy_crits)
                retargeted_choices = [
                    _retarget_choice_for_calc(enemy, incoming, item, calculator, force_enemy_crits=ai_force_enemy_crits)
                    for item in choices
                ]
                usable_retargeted_choices = [item for item in retargeted_choices if item is not None]
                snapshot = _action_snapshot(enemy, incoming)
                damage = _apply_enemy_action(enemy, incoming, _maybe_crit(enemy, incoming, retargeted, turn), calculator)
                side_events = _action_side_events(enemy, incoming, snapshot)
                eot_events = _end_of_turn_events(incoming, enemy, calculator)
                previous_switch = (active_index, sac_index, enemy_index)
                switch_streak += 1
                active_index = sac_index
                incoming.turns_out = 0
                if enemy.alive:
                    enemy.turns_out += 1
                # Deliberate sacrifice: the sac mon dying is the plan, so its own
                # KO branches are not line instability. Pay a flat strategic cost;
                # the death itself is already counted against line quality.
                confidence *= 0.95
                best_confidence *= 0.97
                events = (
                    entry_events
                    + [f"{enemy.name} uses {retargeted.move_name if retargeted else choice.move_name} for {damage}; {incoming.name} ends {incoming.hp}/{incoming.max_hp}."]
                    + side_events
                )
                if not incoming.alive:
                    events.append(f"{incoming.name} faints.")
                    active_index = _best_calc_answer(team, enemy, calculator, force_enemy_crits=force_enemy_crits, allow_sac=True)
                    if active_index is not None:
                        replacement = team[active_index]
                        events.append(f"Send {_member_label(replacement)}.")
                        events.extend(_apply_entry_ability(replacement, enemy, calculator))
                else:
                    events.extend(eot_events)
                tactical_risks = risks + [_tactical_sac_note(enemy, choice)]
                _emit(
                    _calc_turn(
                        turn,
                        enemy,
                        incoming,
                        f"Tactical sac: switch {_member_label(outgoing)} -> {_member_label(incoming)}.",
                        " ".join(events),
                        tactical_risks,
                        "tactical-sac",
                        confidence,
                    )
                )
                continue
            snapshot = _action_snapshot(enemy, active)
            damage = _apply_enemy_action(enemy, active, _maybe_crit(enemy, active, choice, turn), calculator)
            side_events = _action_side_events(enemy, active, snapshot)
            eot_events = _end_of_turn_events(active, enemy, calculator)
            if active.alive:
                active.turns_out += 1
            if enemy.alive:
                enemy.turns_out += 1
            # Stay-in sacrifice: same accounting as a switch sac — the active mon's
            # KO branches are the plan, not instability. Flat strategic cost only.
            confidence *= 0.95
            best_confidence *= 0.97
            events = [f"{enemy.name} uses {choice.move_name if choice else 'its best move'} for {damage}; {active.name} ends {active.hp}/{active.max_hp}."] + side_events
            if not active.alive:
                events.append(f"{active.name} faints.")
                active_index = _best_calc_answer(team, enemy, calculator, force_enemy_crits=force_enemy_crits, allow_sac=True)
                previous_switch = None
                switch_streak = 0
                if active_index is not None:
                    replacement = team[active_index]
                    events.append(f"Send {_member_label(replacement)}.")
                    events.extend(_apply_entry_ability(replacement, enemy, calculator))
            else:
                events.extend(eot_events)
            _emit(
                _calc_turn(
                    turn,
                    enemy,
                    active,
                    f"Tactical sac: keep {_member_label(active)} in against {enemy.name}.",
                    " ".join(events),
                    risks + [_tactical_sac_note(enemy, choice)],
                    "tactical-sac",
                    confidence,
                )
            )
            continue
        _real, _best = _decide(
            "branch_both_stay",
            (current_entry_sig, force_enemy_crits),
            lambda: _branch_both(choices, active, enemy),
        )
        # If we move first and KO the enemy, it never gets to threaten us this turn, so
        # blend the enemy-branch penalty toward "safe" by how likely our KO is.
        if not enemy_first:
            pko = _player_ko_chance(active, enemy, action.move_name)
            _real = _real + (1.0 - _real) * pko
            _best = _best + (1.0 - _best) * pko
        if (
            choice is not None
            and not enemy_first
            and not _will_skip_turn(enemy)
            and _choice_kills_current(choice, active)
        ):
            # Planned trade: the printed line already has the active mon acting and
            # then fainting, so its own KO branch is the plan, not a deviation.
            # Charge the same flat strategic cost as a stay-in sacrifice instead of
            # double-counting the death as line instability.
            _real = max(_real, 0.95)
            _best = max(_best, 0.97)
        confidence *= _player_action_confidence(action, calculator, active) * _real
        # Best case assumes your own move lands too, so player accuracy is not applied.
        best_confidence *= _best
        events: list[str] = []
        enemy_struck = False
        if enemy_first and choice:
            if _skip_turn(enemy):
                events.append(f"{enemy.name} cannot move.")
            else:
                snapshot = _action_snapshot(enemy, active)
                pinned_enemy_damage = (enemy_damage_overrides or {}).get(turn)
                if pinned_enemy_damage == 0 and choice.damage is not None:
                    damage = 0
                    events.append(f"{enemy.name}'s {choice.move_name} misses.")
                else:
                    damage = _apply_enemy_action(enemy, active, choice, calculator)
                    events.append(f"{enemy.name} uses {choice.move_name} for {damage}; {active.name} ends {active.hp}/{active.max_hp}.")
                events.extend(_action_side_events(enemy, active, snapshot))
                enemy_struck = True
        player_struck = False
        player_target_hp_before: int | None = None
        miss_rollback: tuple[PlannedMember, PlannedEnemy] | None = None
        forced_kill = (kill_overrides or {}).get(turn)
        if active.alive:
            if not enemy.alive:
                events.append(f"{enemy.name} already fainted; {active.name} does not need to act.")
            elif _skip_turn(active):
                events.append(f"{active.name} cannot move.")
            else:
                action = _refresh_player_action(active, enemy, action, calculator)
                action = _maybe_player_crit(active, enemy, action, turn)
                action = _pinned_player_action(action, turn)
                # Enemy-first recoil/contact damage changes the HP range your move
                # actually attacks.  Refresh every flowchart field from this exact
                # mid-turn position instead of the pre-turn enemy HP snapshot.
                player_target_hp_before = enemy.hp
                fork_meta["player_move"] = action.move_name
                fork_meta["player_ko_chance"] = round(
                    _damage_ko_chance_at_hp(action.damage, player_target_hp_before), 3
                )
                fork_meta["player_no_ko_means_miss"] = bool(
                    action.damage is not None
                    and action.damage.max_damage > 0
                    and action.damage.min_damage >= max(1, player_target_hp_before)
                )
                fork_meta["player_crit_chance"] = round(
                    crit_rate(active, enemy, action.move_name, calculator), 3
                ) if action.damage else 0.0
                fork_meta["player_damage_outcomes"] = _damage_outcomes(
                    action.damage, player_target_hp_before
                )
                snapshot = _action_snapshot(active, enemy)
                if forced_kill is False and action.damage is not None and action.damage.min_damage >= enemy.hp:
                    # Even the min roll KOs, so the pinned "no KO" timeline is a miss:
                    # snapshot both battlers so the whole attack can be undone below.
                    miss_rollback = (_battler_copy(active), _battler_copy(enemy))
                pinned_player_damage = (player_damage_overrides or {}).get(turn)
                if pinned_player_damage == 0 and action.damage is not None:
                    damage = 0
                    events.append(f"{active.name}'s {action.move_name} misses; {enemy.name} stays {enemy.hp}/{enemy.max_hp}.")
                else:
                    damage = _apply_player_action(active, enemy, action, calculator)
                    events.append(f"{active.name} uses {action.move_name} for {damage}; {enemy.name} ends {enemy.hp}/{enemy.max_hp}.")
                    events.extend(_action_side_events(active, enemy, snapshot))
                player_struck = True
        # Pin the attack's kill outcome if the contingency tree asked for it (only when the
        # player actually landed a damaging move this turn).
        if player_struck and forced_kill is not None and action.damage is not None and action.damage.max_damage > 0:
            if forced_kill and enemy.alive:
                enemy.hp = 0
                events.append(f"[branch] {enemy.name} is KO'd by {action.move_name}.")
            elif not forced_kill and not enemy.alive:
                if miss_rollback is not None:
                    # Surviving here means the move missed — restore both battlers to
                    # their pre-attack state instead of inventing a 1 HP survivor.
                    _battler_restore(active, miss_rollback[0])
                    _battler_restore(enemy, miss_rollback[1])
                    events.append(f"[branch] {action.move_name} misses; {enemy.name} stays at {enemy.hp}/{enemy.max_hp}.")
                else:
                    enemy.hp = 1
                    events.append(f"[branch] {enemy.name} survives {action.move_name} on a low roll (1 HP left).")
        if not enemy_first and enemy.alive and choice:
            if _skip_turn(enemy):
                events.append(f"{enemy.name} cannot move.")
            else:
                snapshot = _action_snapshot(enemy, active)
                pinned_enemy_damage = (enemy_damage_overrides or {}).get(turn)
                if pinned_enemy_damage == 0 and choice.damage is not None:
                    damage = 0
                    events.append(f"{enemy.name}'s {choice.move_name} misses.")
                else:
                    damage = _apply_enemy_action(enemy, active, choice, calculator)
                    events.append(f"{enemy.name} uses {choice.move_name} for {damage}; {active.name} ends {active.hp}/{active.max_hp}.")
                    events.extend(_action_side_events(enemy, active, snapshot))
                enemy_struck = True
        events.extend(_end_of_turn_events(active, enemy, calculator))
        if active.alive:
            active.turns_out += 1
        if enemy.alive:
            enemy.turns_out += 1
        if not enemy.alive:
            _mark_enemy_allies_fainted(enemies, enemy_index)
            next_enemy = _next_calc_enemy(enemies, team, active_index, calculator, force_enemy_crits=ai_force_enemy_crits)
            events.append(f"{enemy.name} faints.")
            if next_enemy is not None:
                enemy_index = next_enemy
                enemies[enemy_index].turns_out = 0
                events.append(f"Trainer sends {enemies[enemy_index].name}.")
                events.extend(_apply_entry_ability(enemies[enemy_index], active, calculator))
        if not active.alive:
            events.append(f"{active.name} faints.")
            active_index = _best_calc_answer(team, enemies[enemy_index], calculator, force_enemy_crits=force_enemy_crits, allow_sac=True) if any(enemy.alive for enemy in enemies) else None
            if active_index is not None:
                replacement = team[active_index]
                replacement.turns_out = 0
                events.append(f"Send {_member_label(replacement)}.")
                events.extend(_apply_entry_ability(replacement, enemies[enemy_index], calculator))
        normal_row = _calc_turn(turn, enemy, active, f"{_member_label(active)} vs {enemy.name}: click {action.move_name}.", " ".join(events), risks, "stateful-calc", confidence)
        if not player_struck:
            fork_meta["player_damage_outcomes"] = []
            fork_meta["player_crit_chance"] = 0.0
        if not enemy_struck:
            fork_meta["enemy_damage_outcomes"] = []
        fork_meta["damage_order"] = (
            ["enemy_damage", "player_damage"] if enemy_first
            else ["player_damage", "enemy_damage"]
        )
        fork_meta["uncertainty_order"] = (
            ["enemy_crit", "enemy_damage", "player_crit", "player_damage"] if enemy_first
            else ["player_crit", "player_damage", "enemy_crit", "enemy_damage"]
        )
        normal_row["fork"] = fork_meta
        _emit(normal_row)
    return {
        "trainer": trainer.trainer_name,
        "location": trainer.location,
        "result": "win-line" if not any(enemy.alive for enemy in enemies) and any(member.alive for member in team) else "partial-line",
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "best_confidence": round(max(0.0, min(1.0, best_confidence)), 3),
        "crit_safe": force_enemy_crits,
        "battle_mode": "set",
        "risk_policy": "Enemy crit-aware mode: enemy damage, AI choices, switch checks, and line survival are calculated as if enemy damaging moves crit." if force_enemy_crits else "Normal mode: enemy high rolls are used; crits are reported as risks when they can break the line.",
        "team": [_member_payload(member) for member in team],
        "item_recommendations": item_recommendations,
        "enemies": [_enemy_payload(enemy) for enemy in enemies],
        "turns": turns,
        "matchups": _calc_matchup_table(team, enemies, calculator),
    }


def _calc_member_sig(member: PlannedMember | PlannedEnemy) -> tuple:
    """Everything about a battler that changes how the rest of the fight plays out."""
    return (
        max(0, round(member.hp)),
        member.status,
        tuple(sorted(member.boosts.items())),
        member.sleep_turns,
        member.toxic_counter,
        member.leech_seeded,
        member.confused_turns,
        member.protected,
        member.flinched,
        member.trapped,
        member.salt_cured,
        member.syrup_bomb_turns,
        member.heal_blocked_turns,
        member.sound_blocked_turns,
        # The engine only ever reads turns_out as "first turn out or not" (Fake Out,
        # hazard/Protect AI timing), so the raw counter would just block merges between
        # states with identical futures.
        min(1, member.turns_out),
        member.consumed_item,
        member.ability_on,
    )


def _calc_state_signature(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    active_index: int | None,
    enemy_index: int,
) -> str:
    """Faithful battle-state key so the contingency tree can memoize converged lines.

    Two states with the same signature have identical futures, so the subtree below
    them is built once and shared — this is what lets every enemy move branch at every
    turn (the lines reconverge hard once a mon faints) without exploding exponentially.
    """
    parts = (
        active_index,
        enemy_index,
        tuple(_calc_member_sig(member) for member in team),
        tuple(
            (_calc_member_sig(enemy), getattr(enemy.pokemon, "allies_fainted", 0))
            for enemy in enemies
        ),
    )
    return repr(parts)


def _calc_state_signature_doubles(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    player_a: int | None,
    player_b: int | None,
    enemy_a: int | None,
    enemy_b: int | None,
) -> str:
    """Doubles memo key: board position plus every stateful battler attribute."""
    parts = (
        (player_a, player_b),
        (enemy_a, enemy_b),
        tuple(_calc_member_sig(member) for member in team),
        tuple(
            (_calc_member_sig(enemy), getattr(enemy.pokemon, "allies_fainted", 0))
            for enemy in enemies
        ),
    )
    return repr(parts)


def _battler_copy(battler):
    """Field-level snapshot of a battler (boosts copied) so an attack can be undone."""
    return replace(battler, boosts=dict(battler.boosts))


def _battler_restore(dst, src) -> None:
    for f in dataclass_fields(src):
        setattr(dst, f.name, getattr(src, f.name))


def _clone_calc_team(team: list[PlannedMember]) -> list[PlannedMember]:
    return [
        replace(
            member,
            boosts=dict(member.boosts),
            evs=dict(member.evs) if member.evs else None,
            ivs=dict(member.ivs) if member.ivs else None,
        )
        for member in team
    ]


def _clear_between_battle_effects(
    member: PlannedMember, *, heal: bool, revive_fainted: bool = False
) -> PlannedMember:
    """Apply the game's between-battle rules without restoring consumed held items."""
    clone = _clone_calc_team([member])[0]
    if heal and (clone.hp > 0 or revive_fainted):
        clone.hp = clone.max_hp
        clone.status = None
        clone.sleep_turns = 0
        clone.toxic_counter = 0
    clone.boosts = {}
    clone.leech_seeded = False
    clone.confused_turns = 0
    clone.protected = False
    clone.flinched = False
    clone.trapped = False
    clone.salt_cured = False
    clone.syrup_bomb_turns = 0
    clone.heal_blocked_turns = 0
    clone.sound_blocked_turns = 0
    clone.turns_out = 0
    clone.ability_on = True
    return clone


_EMERALD_LEVEL_EVOLUTIONS: dict[str, tuple[int, str]] = {
    "whismur": (20, "loudred"),
    "loudred": (40, "exploud"),
    "slugma": (38, "magcargo"),
    "numel": (33, "camerupt"),
    "tentacool": (30, "tentacruel"),
    "oddish": (21, "gloom"),
    "vibrava": (45, "flygon"),
}

_EMERALD_RARE_CANDY_FAMILY: dict[str, str] = {
    "loudred": "whismur",
    "exploud": "whismur",
    "magcargo": "slugma",
    "camerupt": "numel",
    "tentacruel": "tentacool",
    "gloom": "oddish",
    "flygon": "vibrava",
}

# Full four-move choices at the relevant level prompts. These are limited to moves
# the captured Pokémon would learn while consuming Rare Candies; TMs, tutors, and
# Move Reminder access are deliberately not invented.
_EMERALD_RARE_CANDY_MOVESETS: dict[str, tuple[tuple[int, tuple[str, ...]], ...]] = {
    "swampert": (
        (46, ("Protect", "Ice Beam", "Mud-Slap", "Surf")),
        (52, ("Protect", "Ice Beam", "Earthquake", "Surf")),
    ),
    "banette": ((48, ("Feint Attack", "Night Shade", "Will-O-Wisp", "Shadow Ball")),),
    "linoone": (
        (41, ("Strength", "Slash", "Rock Smash", "Headbutt")),
        (47, ("Strength", "Slash", "Rest", "Headbutt")),
        (53, ("Strength", "Slash", "Rest", "Belly Drum")),
    ),
    "dustox": (
        (34, ("Protect", "Moonlight", "Psybeam", "Silver Wind")),
        (38, ("Protect", "Moonlight", "Silver Wind", "Toxic")),
    ),
    "nuzleaf": (
        (31, ("Fake Out", "Feint Attack", "Growth", "Nature Power")),
        (49, ("Fake Out", "Feint Attack", "Nature Power", "Extrasensory")),
    ),
    "tentacool": (
        (25, ("Acid", "Supersonic", "Waterfall", "Bubble Beam")),
        (38, ("Acid", "Barrier", "Waterfall", "Bubble Beam")),
        (47, ("Acid", "Screech", "Waterfall", "Bubble Beam")),
        (55, ("Acid", "Screech", "Waterfall", "Hydro Pump")),
    ),
    "golbat": (
        (35, ("Air Cutter", "Wing Attack", "Astonish", "Bite")),
        (49, ("Air Cutter", "Wing Attack", "Poison Fang", "Bite")),
    ),
    "oddish": (
        (35, ("Poison Powder", "Stun Spore", "Sleep Powder", "Moonlight")),
        (44, ("Stun Spore", "Sleep Powder", "Moonlight", "Petal Dance")),
    ),
    "castform": ((30, ("Weather Ball", "Powder Snow", "Rain Dance", "Sunny Day")),),
    "whismur": (
        (29, ("Uproar", "Astonish", "Howl", "Stomp")),
        (37, ("Uproar", "Astonish", "Stomp", "Screech")),
        (40, ("Uproar", "Stomp", "Screech", "Hyper Beam")),
    ),
    "slugma": (
        (36, ("Harden", "Amnesia", "Flamethrower", "Rock Throw")),
        (48, ("Harden", "Amnesia", "Flamethrower", "Rock Slide")),
    ),
    "numel": (
        (33, ("Take Down", "Amnesia", "Ember", "Rock Slide")),
        (37, ("Take Down", "Amnesia", "Earthquake", "Rock Slide")),
        (45, ("Eruption", "Amnesia", "Earthquake", "Rock Slide")),
    ),
    "hariyama": (
        (44, ("Endure", "Knock Off", "Smelling Salts", "Belly Drum")),
        (51, ("Endure", "Seismic Toss", "Smelling Salts", "Belly Drum")),
        (55, ("Endure", "Seismic Toss", "Reversal", "Belly Drum")),
    ),
}


def _emerald_rare_candy_raise(
    member: PlannedMember, target_level: int, calculator: DamageCalculator
) -> PlannedMember:
    """Apply legal level gains, including automatic Gen-III level evolutions."""
    clone = _clone_calc_team([member])[0]
    if target_level <= clone.level:
        return clone
    species = _normalize(clone.species)
    while species in _EMERALD_LEVEL_EVOLUTIONS:
        evolution_level, evolved_species = _EMERALD_LEVEL_EVOLUTIONS[species]
        if target_level < evolution_level:
            break
        species = evolved_species
    species_data = calculator._species_data(species)  # noqa: SLF001
    if not species_data:
        return clone
    max_hp = calculator._stat(  # noqa: SLF001
        species_data, "hp", target_level, clone.nature, clone.evs, clone.ivs
    )
    moves = clone.known_moves
    move_family = _EMERALD_RARE_CANDY_FAMILY.get(
        _normalize(member.species), _normalize(member.species)
    )
    for learn_level, learned_moves in _EMERALD_RARE_CANDY_MOVESETS.get(move_family, ()):
        if member.level < learn_level <= target_level:
            moves = learned_moves
    was_fainted = clone.hp <= 0
    return replace(
        clone,
        species=species,
        level=target_level,
        max_hp=max_hp,
        hp=0 if was_fainted else max_hp,
        moves=tuple(moves),
    )


def _team_from_calc_result(result: dict[str, Any], source: list[PlannedMember]) -> list[PlannedMember]:
    """Rebuild the exact final party state emitted by a searched line."""
    by_slot = {member.slot: member for member in source}
    remaining = list(source)
    rebuilt: list[PlannedMember] = []
    for index, payload in enumerate(result.get("team") or []):
        slot = payload.get("slot")
        member = by_slot.get(slot) if isinstance(slot, int) else None
        if member is None:
            member = next(
                (candidate for candidate in remaining if _normalize(candidate.name) == _normalize(str(payload.get("name") or ""))),
                remaining[index] if index < len(remaining) else None,
            )
        if member is None:
            continue
        if member in remaining:
            remaining.remove(member)
        clone = _clone_calc_team([member])[0]
        clone.hp = max(0, min(clone.max_hp, int(payload.get("hp", clone.hp))))
        clone.status = payload.get("status") or None
        clone.consumed_item = bool(payload.get("consumed_item", clone.consumed_item))
        rebuilt.append(clone)
    return rebuilt


def _merge_gauntlet_roster(
    roster: list[PlannedMember], ending_party: list[PlannedMember]
) -> list[PlannedMember]:
    """Put the selected party's post-battle state back into the full imported box."""
    by_slot = {member.slot: member for member in ending_party}
    by_name = {_normalize(member.name): member for member in ending_party}
    merged: list[PlannedMember] = []
    for member in roster:
        replacement = by_slot.get(member.slot)
        if replacement is None:
            replacement = by_name.get(_normalize(member.name))
        merged.append(_clone_calc_team([replacement or member])[0])
    return merged


def _planned_box_from_pc_state(
    rom: str, pc_state: str, calculator: DamageCalculator,
    ability_by_species: dict[str, str] | None = None,
) -> list[PlannedMember]:
    """Decode the real party + PC storage into the same roster the calc solver uses."""
    _validate_paths(rom, pc_state)
    # Executable playbooks intentionally start at the exact battle command.
    # The encrypted save blocks still contain the authoritative party/PC/bag;
    # allow that snapshot here so one-click runs never depend on pasted sets.
    scan = scan_pc_boxes(
        rom, pc_state, calculator=calculator, allow_midbattle_snapshot=True
    )
    decoded = list(scan.party) + [
        mon for mon in scan.roster
        if (
            (calculator.game_mode != "pokemon-emerald" or mon.box < 13)
            and (not config.NUZLOCKE_GRAVEYARD_BOX or mon.box != config.NUZLOCKE_GRAVEYARD_BOX)
        )
    ]
    planned: list[PlannedMember] = []
    ability_by_species = {
        "mightyena": "Intimidate",
        "flaaffy": "Static",
        "dwebble": "Sturdy",
        "musharna": "Synchronize",
        **(ability_by_species or {}),
    }
    for index, mon in enumerate(decoded):
        species_data = calculator._species_data(mon.species)  # noqa: SLF001
        if not species_data or not mon.moves:
            continue
        max_hp = mon.max_hp or calculator._stat(  # noqa: SLF001
            species_data, "hp", mon.level, mon.nature, mon.evs, mon.ivs
        )
        source = (
            f"party:{mon.slot}"
            if mon.source == "party" or mon.box == 0
            else f"box:{mon.box}:{mon.slot}"
        )
        planned.append(PlannedMember(
            name=mon.display_name,
            species=mon.species,
            level=mon.level,
            max_hp=max_hp,
            hp=max_hp,
            moves=tuple(mon.moves[:4]),
            # The save is authoritative for inventory. Text-import items are
            # useful for a hypothetical calculator team, but applying them to
            # a live PC scan invents berries the Nuzlocke may not own.
            item=mon.held_item,
            ability=ability_by_species.get(_normalize(mon.species)),
            nature=mon.nature,
            evs=mon.evs,
            ivs=mon.ivs,
            source=source,
            slot=index,
        ))
    return planned


def _selected_gauntlet_party(
    result: dict[str, Any], roster: list[PlannedMember]
) -> list[PlannedMember]:
    selection = (result.get("team_selection") or {}).get("indices")
    if isinstance(selection, list) and selection:
        valid = [int(index) for index in selection if isinstance(index, int) and 0 <= index < len(roster)]
        if valid:
            return _clone_calc_team([roster[index] for index in valid[:6]])
    chosen = {_normalize(str(name)) for name in (result.get("team_selection") or {}).get("chosen", [])}
    if chosen:
        selected = [member for member in roster if _normalize(member.name) in chosen]
        if selected:
            return _clone_calc_team(selected[:6])
    return _clone_calc_team(roster[:6])


def _apply_owned_item_changes(
    roster: list[PlannedMember],
    selected_party: list[PlannedMember],
    changes: list[dict[str, Any]],
) -> tuple[list[PlannedMember], list[PlannedMember], list[dict[str, Any]], str | None]:
    """Move real held items between roster records without cloning or deleting them."""
    updated = _clone_calc_team(roster)
    by_slot = {member.slot: member for member in updated}
    selected_slots = {member.slot for member in selected_party}
    applied: list[dict[str, Any]] = []
    for change in changes:
        target_name = _normalize(str(change.get("pokemon") or ""))
        wanted = str(change.get("new_item") or "").strip()
        target = next(
            (member for member in updated if member.slot in selected_slots and _normalize(member.name) == target_name),
            None,
        )
        if target is None or not wanted:
            continue
        if _normalize(target.item) == _normalize(wanted) and not target.consumed_item:
            continue
        donor = next(
            (
                member for member in updated
                if member.slot != target.slot and not member.consumed_item
                and _normalize(member.item) == _normalize(wanted)
            ),
            None,
        )
        if donor is None:
            return roster, selected_party, [], f"No owned, unconsumed {wanted} is available."
        old_item, old_consumed = target.item, target.consumed_item
        target.item, target.consumed_item = donor.item, False
        donor.item, donor.consumed_item = old_item, old_consumed
        applied.append({
            **change,
            "from": donor.name,
            "to": target.name,
            "old_item": old_item,
            "new_item": target.item,
        })
    equipped = _clone_calc_team([by_slot[member.slot] for member in selected_party])
    return updated, equipped, applied, None


def _run_calc_gauntlet(
    imported: list[PlannedMember],
    trainers: list[TrainerBattle],
    calculator: DamageCalculator,
    *,
    max_turns: int,
    force_enemy_crits: bool,
    heal_between: bool,
    optimize_between_fights: bool = True,
    leveling_policy: str = "none",
    deathless_required: bool = False,
    max_total_faints: int = 0,
    allow_revives: bool = False,
) -> dict[str, Any]:
    """Run the route in order, reselecting from the box on legal Center visits."""
    _GAUNTLET_PROGRESS.update(
        running=True, completed=0, total=len(trainers), current=1,
        trainer=trainers[0].trainer_name if trainers else "", phase="starting first fight", pct=0.0,
        stage="planning", completed_replays=0, total_replays=2, video_ready=False,
    )
    roster = _clone_calc_team(imported)
    working = _clone_calc_team(imported)
    route_team_selection: dict[str, Any] | None = None
    # Bag-healing gauntlets (notably the Emerald League) must carry one party for
    # the whole route. Selecting six solely for fight one produces a misleading
    # Sidney-specialist roster that may have no answer to Phoebe or Wallace. Seed
    # the fixed party from candidates proposed for every trainer, then score each
    # candidate over the complete route with between-fight healing and legal level
    # caps. The expensive per-fight line search still runs below for the winner.
    if (
        heal_between
        and not optimize_between_fights
        and len(roster) > 6
        and trainers
    ):
        coverage_candidates, coverage_report = _route_coverage_candidates(
            roster, trainers, calculator,
            force_enemy_crits=force_enemy_crits,
            leveling_policy=leveling_policy,
        )
        route_candidates: list[tuple[int, ...]] = list(coverage_candidates)
        for route_trainer in trainers:
            candidate_roster = roster
            if leveling_policy == "boss-cap":
                preview_level = max((mon.level or 1 for mon in route_trainer.party), default=1)
                candidate_roster = [
                    _emerald_rare_candy_raise(member, preview_level, calculator)
                    for member in roster
                ]
            for candidate in _candidate_team_indices(
                candidate_roster, route_trainer, calculator,
                force_enemy_crits=force_enemy_crits,
            ):
                if candidate not in route_candidates:
                    route_candidates.append(candidate)

        route_score_cache: dict[tuple[int, ...], tuple[float, ...]] = {}

        def route_candidate_key(indices: tuple[int, ...]) -> tuple[float, ...]:
            cached = route_score_cache.get(indices)
            if cached is not None:
                return cached
            team = _clone_calc_team([roster[index] for index in indices])
            deathless_wins = 0
            wins = 0
            total_faints = 0
            progress = 0.0
            remaining = 0.0
            for route_index, route_trainer in enumerate(trainers):
                enemy_ace = max((mon.level or 1 for mon in route_trainer.party), default=1)
                party_high = max((member.level for member in team), default=1)
                target_level = (
                    enemy_ace if leveling_policy == "boss-cap"
                    else party_high if leveling_policy == "party-max"
                    else None
                )
                if target_level is not None:
                    team = [
                        _emerald_rare_candy_raise(member, target_level, calculator)
                        for member in team
                    ]
                quick = _run_text_calc_sim_once(
                    _clone_calc_team(team), route_trainer, calculator,
                    max_turns=max_turns, force_enemy_crits=force_enemy_crits,
                    compute_item_recs=False,
                )
                alive = all(int(member.get("hp") or 0) > 0 for member in quick.get("team") or [])
                won = quick.get("result") == "win-line"
                alive_before = {
                    member.slot for member in team if member.hp > 0
                }
                new_faints = sum(
                    1 for member in quick.get("team") or []
                    if member.get("slot") in alive_before and int(member.get("hp") or 0) <= 0
                )
                total_faints += new_faints
                wins += int(won)
                deathless_wins += int(won and alive)
                enemies = quick.get("enemies") or []
                progress += sum(
                    1.0 - int(enemy.get("hp") or 0) / max(1, int(enemy.get("max_hp") or 1))
                    for enemy in enemies
                )
                remaining += sum(
                    int(member.get("hp") or 0) / max(1, int(member.get("max_hp") or 1))
                    for member in quick.get("team") or []
                )
                team = [
                    _clear_between_battle_effects(member, heal=True)
                    for member in _team_from_calc_result(quick, team)
                ]
                if not won or total_faints > max_total_faints or not any(member.alive for member in team):
                    break
            route_complete = int(wins == len(trainers) and total_faints <= max_total_faints)
            score = (
                float(route_complete), float(wins), -float(total_faints),
                float(deathless_wins), progress, remaining,
            )
            route_score_cache[indices] = score
            return score

        best_route_indices = max(route_candidates, key=route_candidate_key)
        # The per-trainer seeds can omit a route specialist. Hill-climb swaps against
        # the aggregate route score so, for example, a Phoebe answer is not discarded
        # merely because it contributes less damage against Sidney.
        route_swap_budget = 120
        while route_swap_budget > 0:
            current_key = route_candidate_key(best_route_indices)
            best_trial = best_route_indices
            for slot in range(len(best_route_indices)):
                for outsider in range(len(roster)):
                    if outsider in best_route_indices:
                        continue
                    trial = tuple(sorted(
                        set(best_route_indices) - {best_route_indices[slot]} | {outsider}
                    ))
                    trial_key = route_candidate_key(trial)
                    route_swap_budget -= 1
                    if trial_key > route_candidate_key(best_trial):
                        best_trial = trial
                    if route_swap_budget <= 0:
                        break
                if route_swap_budget <= 0:
                    break
            if route_candidate_key(best_trial) <= current_key:
                break
            best_route_indices = best_trial
            if best_route_indices not in route_candidates:
                route_candidates.append(best_route_indices)

        # Refine only the strongest cheap candidates with the real line search.
        # This keeps selection fast while preventing a greedy Drake line from
        # sacrificing the Manectric that is the best answer to Wallace.
        refinement_pool = sorted(
            route_score_cache,
            key=lambda indices: (route_candidate_key(indices), tuple(-i for i in indices)),
            reverse=True,
        )[:12]
        refinement_cache: dict[tuple[int, ...], tuple[float, ...]] = {}

        def refined_route_key(indices: tuple[int, ...]) -> tuple[float, ...]:
            cached = refinement_cache.get(indices)
            if cached is not None:
                return cached
            team = _clone_calc_team([roster[index] for index in indices])
            wins = 0
            total_deaths = 0
            deathless_wins = 0
            progress = 0.0
            remaining = 0.0
            for route_index, route_trainer in enumerate(trainers):
                enemy_ace = max((mon.level or 1 for mon in route_trainer.party), default=1)
                party_high = max((member.level for member in team if member.hp > 0), default=1)
                target_level = (
                    enemy_ace if leveling_policy == "boss-cap"
                    else party_high if leveling_policy == "party-max"
                    else None
                )
                if target_level is not None:
                    team = [
                        _emerald_rare_candy_raise(member, target_level, calculator)
                        for member in team
                    ]
                alive_slots = {member.slot for member in team if member.hp > 0}
                protected_slots = _future_route_protected_slots(
                    team, trainers[route_index + 1:], calculator,
                    force_enemy_crits=force_enemy_crits,
                )
                searched = _search_future_preserving_line(
                    _clone_calc_team(team), route_trainer, calculator,
                    max_turns=max_turns, force_enemy_crits=force_enemy_crits,
                    budget=70, protected_slots=protected_slots,
                )
                won = searched.get("result") == "win-line"
                ending = _team_from_calc_result(searched, team)
                new_deaths = sum(
                    1 for member in ending
                    if member.slot in alive_slots and member.hp <= 0
                )
                total_deaths += new_deaths
                wins += int(won)
                deathless_wins += int(won and new_deaths == 0)
                enemies = searched.get("enemies") or []
                progress += sum(
                    1.0 - int(enemy.get("hp") or 0) / max(1, int(enemy.get("max_hp") or 1))
                    for enemy in enemies
                )
                remaining += sum(
                    member.hp / max(1, member.max_hp) for member in ending
                )
                team = [
                    _clear_between_battle_effects(member, heal=True)
                    for member in ending
                ]
                if not won or total_deaths > max_total_faints or not any(member.alive for member in team):
                    break
            complete = int(wins == len(trainers) and total_deaths <= max_total_faints)
            key = (
                float(complete), float(wins), -float(total_deaths),
                float(deathless_wins), progress, remaining,
            )
            refinement_cache[indices] = key
            return key

        if refinement_pool:
            refined_best = max(
                refinement_pool,
                key=lambda indices: (refined_route_key(indices), tuple(-i for i in indices)),
            )
            if refined_route_key(refined_best) > refined_route_key(best_route_indices):
                best_route_indices = refined_best
        working = _clone_calc_team([roster[index] for index in best_route_indices])
        reproducibility_payload = {
            "algorithm": "paired-route-coverage-v1",
            "trainers": [trainer.trainer_name for trainer in trainers],
            "roster": [
                [member.name, member.species, member.level, list(member.known_moves)]
                for member in roster
            ],
            "leveling_policy": leveling_policy,
            "max_total_faints": max_total_faints,
            "chosen_indices": list(best_route_indices),
        }
        reproducibility_key = hashlib.sha256(
            json.dumps(reproducibility_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        route_team_selection = {
            "policy": "paired-route-coverage-v1",
            "box_size": len(roster),
            "candidates_tested": len(route_score_cache),
            "indices": list(best_route_indices),
            "chosen": [member.name for member in working],
            "coverage": coverage_report,
            "selection_score": list(route_candidate_key(best_route_indices)),
            "refinement_candidates_tested": len(refinement_cache),
            "refinement_score": list(refined_route_key(best_route_indices)),
            "reproducibility_key": reproducibility_key,
            "note": "One legal six-Pokémon party was selected by deterministic paired coverage across every queued trainer; no PC reselection occurs between fights.",
        }
    fights: list[dict[str, Any]] = []
    confidence = 1.0
    total_faints = 0
    stopped_reason: str | None = None
    for index, trainer in enumerate(trainers):
        _GAUNTLET_PROGRESS.update(
            current=index + 1, trainer=trainer.trainer_name,
            phase="preparing battle state", completed=index,
            pct=round(55 * index / max(1, len(trainers)), 1),
        )
        if index and heal_between:
            # Heal the carried party as well as the roster. In bag-healing League
            # routes there is no PC reselection, so `working` is the authoritative
            # party; healing only `roster` silently carried damage/status forward.
            roster = [
                _clear_between_battle_effects(member, heal=True, revive_fainted=allow_revives)
                for member in roster
            ]
            working = [
                _clear_between_battle_effects(member, heal=True, revive_fainted=allow_revives)
                for member in working
            ]
        elif index:
            working = [_clear_between_battle_effects(member, heal=False) for member in working]

        may_reselect = (
            (index == 0 and route_team_selection is None)
            or (heal_between and optimize_between_fights)
        )
        available = roster if may_reselect else working
        protected_slots = _future_route_protected_slots(
            available[:6], trainers[index + 1:], calculator,
            force_enemy_crits=force_enemy_crits,
        ) if not may_reselect else set()
        if may_reselect:
            result = _run_text_calc_sim_with_team_select(
                available,
                trainer,
                calculator,
                max_turns=max_turns,
                force_enemy_crits=force_enemy_crits,
            )
            selected_party = _selected_gauntlet_party(result, available)
            optimized = result.get("optimized_item_line") or {}
            item_error = None
            if (
                optimized.get("item_changes")
                and _line_quality_key(optimized) > _line_quality_key(result)
            ):
                next_roster, equipped_party, applied, item_error = _apply_owned_item_changes(
                    roster, selected_party, list(optimized.get("item_changes") or [])
                )
                if applied and item_error is None:
                    selection = copy.deepcopy(result.get("team_selection") or {})
                    result = copy.deepcopy(optimized)
                    result["team_selection"] = selection
                    result["item_changes"] = applied
                    roster = next_roster
                    selected_party = equipped_party
            if item_error:
                result["item_optimization_error"] = item_error
        else:
            result = _search_future_preserving_line(
                working[:6],
                trainer,
                calculator,
                max_turns=max_turns,
                force_enemy_crits=force_enemy_crits,
                budget=120,
                protected_slots=protected_slots,
            )
            selected_party = _clone_calc_team(working[:6])
        leveling_changes: list[dict[str, Any]] = []
        alive_slots_before = {member.slot for member in selected_party if member.hp > 0}
        projected_new_faints = [
            str(member.get("name") or "unknown")
            for member in result.get("team") or []
            if member.get("slot") in alive_slots_before and int(member.get("hp") or 0) <= 0
        ]
        unsafe_nuzlocke_win = bool(
            deathless_required
            and result.get("result") == "win-line"
            and projected_new_faints
        )
        if (
            calculator.game_mode == "pokemon-emerald"
            and (result.get("result") != "win-line" or unsafe_nuzlocke_win)
            and leveling_policy in {"party-max", "boss-cap"}
        ):
            party_high = max((member.level for member in selected_party), default=1)
            enemy_ace = max((mon.level or 1 for mon in trainer.party), default=1)
            target_level = party_high if leveling_policy == "party-max" else enemy_ace
            leveled_available: list[PlannedMember] = []
            for member in available:
                if member.level >= target_level:
                    leveled_available.append(_clone_calc_team([member])[0])
                    continue
                raised = _emerald_rare_candy_raise(member, target_level, calculator)
                leveled_available.append(raised)
                leveling_changes.append({
                    "pokemon": member.name, "from": member.level, "to": target_level,
                    "from_species": member.species, "to_species": raised.species,
                    "moves_before": list(member.known_moves),
                    "moves_after": list(raised.known_moves),
                    "method": "Rare Candy", "reason": "last-resort retry after the original line failed",
                })
            if leveling_changes:
                retry = (
                    _run_text_calc_sim_with_team_select(
                        leveled_available, trainer, calculator, max_turns=max_turns,
                        force_enemy_crits=force_enemy_crits,
                    )
                    if may_reselect else
                    _search_future_preserving_line(
                        leveled_available[:6], trainer, calculator, max_turns=max_turns,
                        force_enemy_crits=force_enemy_crits, budget=120,
                        protected_slots=protected_slots,
                    )
                )
                if retry.get("result") == "win-line" or _line_quality_key(retry) > _line_quality_key(result):
                    result = retry
                    available = leveled_available
                    selected_party = (
                        _selected_gauntlet_party(result, available)
                        if may_reselect else _clone_calc_team(available[:6])
                    )
                    if may_reselect:
                        roster = leveled_available
                    else:
                        working = leveled_available
                    result["last_resort_leveling"] = leveling_changes
        result["alternate_answer_lines"] = _alternate_answer_lines(
            available,
            trainer,
            calculator,
            result,
            max_turns=max_turns,
            force_enemy_crits=force_enemy_crits,
            lightweight=True,
        )
        emerald_guidance = None
        emerald_diagnosis = None
        if calculator.game_mode == "pokemon-emerald":
            emerald_guidance = _emerald_level_guidance(
                selected_party, trainer, result, None, calculator
            )
            emerald_diagnosis = _emerald_failure_diagnosis(
                selected_party, trainer, result, emerald_guidance, calculator
            )
        alive_slots_before = {member.slot for member in selected_party if member.hp > 0}
        starting_team = [_member_payload(member) for member in selected_party]
        working = _team_from_calc_result(result, selected_party)
        new_fainted = [
            member.name for member in working
            if member.slot in alive_slots_before and member.hp <= 0
        ]
        total_faints += len(new_fainted)
        if deathless_required and total_faints > max_total_faints:
            result["result"] = "nuzlocke-failed"
            result["nuzlocke_failure"] = {
                "fainted": new_fainted,
                "total_faints": total_faints,
                "max_total_faints": max_total_faints,
                "note": "The opponent was defeated, but this line exceeds the configured Gauntlet faint budget.",
            }
        elif new_fainted:
            result["accepted_sacrifices"] = {
                "fainted": new_fainted,
                "total_faints": total_faints,
                "max_total_faints": max_total_faints,
                "note": "The solver preferred zero faints first; this sacrifice is within the explicitly configured fallback budget.",
            }
        if may_reselect:
            roster = _merge_gauntlet_roster(roster, working)
        confidence *= float(result.get("confidence") or 0.0)
        fight = {
            "position": index + 1,
            "trainer": trainer.trainer_name,
            "location": trainer.location,
            "result": result.get("result", "partial-line"),
            "confidence": result.get("confidence", 0.0),
            "starting_team": starting_team,
            "ending_team": [_member_payload(member) for member in working],
            "turns": result.get("turns") or [],
            "line_search": result.get("line_search") or {},
            "alternate_answer_lines": result.get("alternate_answer_lines") or [],
            "level_guidance": emerald_guidance,
            "failure_diagnosis": emerald_diagnosis,
            "nuzlocke_failure": result.get("nuzlocke_failure"),
            "accepted_sacrifices": result.get("accepted_sacrifices"),
            "is_doubles": trainer.is_double,
            "preparation": {
                "box_visit": bool(index and heal_between and optimize_between_fights),
                "chosen": [member.name for member in selected_party],
                "held_items": {member.name: member.item for member in selected_party if member.item},
                "item_changes": result.get("item_changes") or [],
                "item_error": result.get("item_optimization_error"),
                "leveling": result.get("last_resort_leveling") or [],
            },
        }
        fights.append(fight)
        _GAUNTLET_PROGRESS.update(
            completed=index + 1,
            pct=round(55 * (index + 1) / max(1, len(trainers)), 1),
        )
        if result.get("result") != "win-line":
            stopped_reason = f"The route stopped at {trainer.trainer_name}; no complete winning line was found."
            break
        if not any(member.alive for member in working):
            stopped_reason = f"The route stopped at {trainer.trainer_name}; the party blacked out."
            break
    payload = {
        "result": "route-complete" if len(fights) == len(trainers) and stopped_reason is None else "route-stopped",
        "mode": "healing-between" if heal_between else "back-to-back",
        "heal_between": heal_between,
        "route_confidence": round(max(0.0, min(1.0, confidence)), 4),
        "fights": fights,
        "queued": len(trainers),
        "completed": len(fights),
        "stopped_reason": stopped_reason,
        "total_faints": total_faints,
        "max_total_faints": max_total_faints,
        "ruleset_label": "Hardcore Nuzlocke with bounded sacrifice fallback" if deathless_required and max_total_faints else "Hardcore Nuzlocke" if deathless_required else "Standard rules",
        "final_team": [_member_payload(member) for member in working],
        "final_box": [_member_payload(member) for member in roster],
    }
    if route_team_selection is not None:
        payload["route_team_selection"] = route_team_selection
    _GAUNTLET_PROGRESS.update(
        running=True, stage="cartridge-proof",
        phase="Planner complete; starting cartridge proof" if stopped_reason is None else "Planner stopped; no game proof",
        pct=55.0 if stopped_reason is None else 99.0,
    )
    return payload


# ---- Contingency flowchart -------------------------------------------------------------
# The line finder normally commits to one expected timeline. The flowchart instead forks on
# everything genuinely uncertain in a real battle and replays each branch to its own end:
#   1. which move the enemy AI picks (the full move distribution, including the low-odds
#      "what does it do the other X%" move, plus a catch-all so branches sum to ~100%),
#   2. whether the enemy's attack lands a critical hit when a crit would flip the active mon
#      from surviving to fainting (crits that don't change anything are not branched),
#   3. whether a non-guaranteed player attack actually KOs.
# Each fork re-runs the SAME deterministic sim with that one outcome pinned, so every branch
# is a faithful alternate line — including the planner's real response after a crit KO.
# EVERY enemy move (any nonzero model probability in exhaustive mode) branches at EVERY turn.
# Three things keep that finite and fast enough to always return:
#   (1) state memoization — lines reconverge hard once a mon faints (the dying mon's move
#       choice usually stops mattering), so subtrees are keyed by battle state and built once,
#       and repeat arrivals link back via a compact merge-leaf;
#   (2) best-first expansion — pending positions are expanded in order of cumulative path
#       probability, so the node/time budget is always spent on the likeliest lines first;
#   (3) hard node + wall-clock budgets — when they run out, only the least likely tails are
#       left, and each is closed with an explicit "rare line" truncation leaf (never a silent
#       dead end); the Co-pilot tab can play any truncated position out live.
_CONTINGENCY_MAX_DEPTH = 180    # enough for move/crit/range axes across a full 60-turn line
_CONTINGENCY_MIN_PROB = 0.05    # non-exhaustive: crit/KO axes must be at least this likely to fork
_CONTINGENCY_MOVE_FLOOR = 0.02  # non-exhaustive: enemy-move branch floor (below folds to catch-all)
_CONTINGENCY_ALT_FLOOR = 0.02   # leftover tail mass below the move floor folded into a catch-all
_CONTINGENCY_NODE_BUDGET = 400  # default expanded-node cap (each node re-runs the sim once)
_CONTINGENCY_FULL_NODE_BUDGET = 700  # fast first chart; explicit "Explore every branch" is uncapped
_CONTINGENCY_TIME_BUDGET_S = 25.0     # wall-clock cap so the calc endpoint always returns
_DECIDE_MISS = object()  # sentinel: None is a legitimate cached decision


def _damage_range_at_roll(damage: DamageRange, amount: int, target_hp: int) -> DamageRange:
    """Clone a calc range as one exact hit outcome for deterministic replay."""
    amount = max(0, int(amount))
    return replace(
        damage,
        min_damage=amount,
        max_damage=amount,
        rolls=(amount,),
        min_percent=damage.min_percent if amount else 0.0,
        max_percent=damage.max_percent if amount else 0.0,
        average_percent=damage.average_percent if amount else 0.0,
        ko_chance=1.0 if amount > 0 and amount >= max(1, target_hp) else 0.0,
        expected_damage=float(amount),
    )


def _damage_outcomes(damage: DamageRange | None, target_hp: int) -> list[dict[str, Any]]:
    """Group the real calc rolls by distinct resulting HP state.

    Accuracy contributes an explicit zero-damage miss. Rolls that all produce the same
    state (most importantly a guaranteed KO) collapse into one outcome; otherwise every
    distinct applied damage value remains independently navigable.
    """
    if damage is None or damage.max_damage <= 0:
        return []
    hp = max(0, int(target_hp))
    rolls = tuple(int(value) for value in (damage.rolls or (damage.min_damage,)))
    if not rolls:
        return []
    accuracy = max(0.0, min(1.0, float(damage.accuracy)))
    probabilities: dict[int, float] = {}
    if accuracy < 1.0:
        probabilities[0] = 1.0 - accuracy
    per_roll = accuracy / len(rolls)
    for roll in rolls:
        applied = min(hp, max(0, roll))
        probabilities[applied] = probabilities.get(applied, 0.0) + per_roll
    return [
        {
            "damage": amount,
            "remaining_hp": max(0, hp - amount),
            "probability": round(probability, 8),
            "miss": amount == 0 and accuracy < 1.0,
        }
        for amount, probability in sorted(probabilities.items())
        if probability > 0.0
    ]


def _damage_ko_chance_at_hp(damage: DamageRange | None, target_hp: int) -> float:
    """KO probability for the HP that exists at the instant the move lands.

    DamageRange.ko_chance belongs to the HP used when the range was created.  A
    faster foe can change its own HP through recoil/contact damage before the
    player's queued move lands, so flowchart branching must recompute the roll
    threshold from the real mid-turn HP.
    """
    if damage is None or damage.max_damage <= 0 or target_hp <= 0:
        return 0.0
    rolls = tuple(int(value) for value in (damage.rolls or (damage.min_damage,)))
    if not rolls:
        return 0.0
    accuracy = max(0.0, min(1.0, float(damage.accuracy)))
    return accuracy * sum(1 for roll in rolls if roll >= target_hp) / len(rolls)


def _contingency_step(row: dict[str, Any]) -> dict[str, Any]:
    """Compact per-turn step shown along a flowchart branch's trunk."""
    return {
        "turn": row.get("turn"),
        "answer": row.get("answer"),
        "enemy": row.get("enemy"),
        "action": row.get("action"),
        "detail": row.get("calc"),
        "your_hp": row.get("your_hp"),
        "enemy_hp": row.get("enemy_hp"),
        "consistency": row.get("consistency"),
    }


def _contingency_state_label(row: dict[str, Any] | None) -> str:
    """Human label for a battle position, used to point merge-leaves at the line they rejoin."""
    if not row:
        return ""
    return (
        f"Turn {row.get('turn')}: {row.get('answer') or 'your mon'} ({row.get('your_hp') or '?'})"
        f" vs {row.get('enemy') or 'foe'} ({row.get('enemy_hp') or '?'})"
    )


def _contingency_merge_leaf(cached: dict[str, Any]) -> dict[str, Any]:
    """A compact pointer node emitted when a branch reconverges to an already-drawn position.

    Keeps the serialized flowchart finite: each distinct battle state is fully drawn once;
    every other path that reaches it links back instead of re-inlining the whole subtree.
    """
    return {
        "steps": [],
        "outcome": "merge",
        "merge_label": cached.get("state_label", ""),
        "merge_state_id": cached.get("state_id"),
        "confidence": cached.get("confidence"),
    }


def _contingency_truncated_leaf(path_prob: float) -> dict[str, Any]:
    """Leaf closing a line the node/time budget did not reach.

    Best-first expansion spends the budget on the likeliest lines first, so anything
    truncated is the improbable tail — and it is marked, never a silent dead end.
    """
    pct = round(path_prob * 100, 1)
    return {
        "steps": [],
        "outcome": "truncated",
        "note": (
            f"Rare line (~{pct if pct >= 0.1 else '<0.1'}% of attempts) not expanded — "
            "open the Co-pilot tab from this position to play it out live."
        ),
        "confidence": None,
    }


def _contingency_pct(probability: float) -> str:
    pct = max(0.0, probability * 100)
    if 0 < pct < 0.1:
        return "<0.1%"
    if pct < 1:
        return f"{pct:.1f}%"
    return f"{round(pct)}%"


def _expand_contingency_node(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    task: dict[str, Any],
    *,
    memo: dict[tuple, dict[str, Any]],
    push: Any,
    max_turns: int,
    force_enemy_crits: bool,
    forced_lead: int | None,
    player_move_overrides: dict[int, str] | None,
    exhaustive: bool,
    decision_cache: dict | None = None,
) -> dict[str, Any]:
    """Expand one battle position into a flowchart node (trunk + at most one fork).

    Re-runs the deterministic sim with this line's pinned outcomes, emits the trunk up to
    the first unpinned uncertainty, and queues one child task per branch via `push`.
    """
    move_ovr: dict[int, str] = task["move_ovr"]
    kill_ovr: dict[int, bool] = task["kill_ovr"]
    crit_ovr: dict[int, bool] = task["crit_ovr"]
    player_crit_ovr: dict[int, bool] = task["player_crit_ovr"]
    ai_switch_ovr: dict[int, bool] = task["ai_switch_ovr"]
    player_damage_ovr: dict[int, int] = task["player_damage_ovr"]
    enemy_damage_ovr: dict[int, int] = task["enemy_damage_ovr"]
    start_turn: int = task["start_turn"]
    path_prob: float = task["prob"]
    line = _run_text_calc_sim_once(
        _clone_calc_team(team),
        trainer,
        calculator,
        max_turns=max_turns,
        force_enemy_crits=force_enemy_crits,
        forced_lead=forced_lead,
        player_move_overrides=player_move_overrides,
        enemy_move_overrides=move_ovr,
        ai_switch_overrides=ai_switch_ovr,
        kill_overrides=kill_ovr,
        enemy_crit_overrides=crit_ovr,
        player_crit_overrides=player_crit_ovr,
        player_damage_overrides=player_damage_ovr,
        enemy_damage_overrides=enemy_damage_ovr,
        branch_actual_rng=True,
        compute_item_recs=False,
        decision_cache=decision_cache,
    )
    rows = line.get("turns") or []
    # Memoize by the battle state entering start_turn: lines that reconverge to the same
    # state share one subtree, so every enemy move can branch at every turn without the
    # tree exploding exponentially. Different paths to the same state are identical futures.
    start_row = next(
        (r for r in rows if r.get("turn") is not None and r.get("turn") >= start_turn),
        None,
    )
    entry_sig = start_row.get("state_sig") if start_row else None
    # Pins for turns BEFORE start_turn already shaped entry_sig, so the key only needs the
    # entry state plus pins still PENDING at/after start_turn — those are what make sibling
    # branches (which share the same entering state) diverge. Without them every branch of a
    # fork would collapse onto its parent, since pinning a move happens *during* the turn.
    pending = tuple(sorted(
        [("m", t, v) for t, v in move_ovr.items() if t >= start_turn]
        + [("k", t, v) for t, v in kill_ovr.items() if t >= start_turn]
        + [("c", t, v) for t, v in crit_ovr.items() if t >= start_turn]
        + [("C", t, v) for t, v in player_crit_ovr.items() if t >= start_turn]
        + [("s", t, v) for t, v in ai_switch_ovr.items() if t >= start_turn]
        + [("p", t, v) for t, v in player_damage_ovr.items() if t >= start_turn]
        + [("e", t, v) for t, v in enemy_damage_ovr.items() if t >= start_turn]
    ))
    memo_key = (start_turn, entry_sig, pending) if entry_sig is not None else None
    if memo_key is not None and memo_key in memo:
        # Already drawn elsewhere: link back instead of re-inlining, so each distinct
        # battle state is expanded once and the serialized tree stays finite.
        return _contingency_merge_leaf(memo[memo_key])
    node: dict[str, Any] = {"steps": [], "state_label": _contingency_state_label(start_row)}
    if memo_key is not None:
        # Register before queuing children so a state that loops back to itself merges.
        node["state_id"] = f"state-{len(memo) + 1}"
        memo[memo_key] = node
    trunk = node["steps"]
    fork: tuple[str, int, dict[str, Any], Any, dict[str, Any]] | None = None
    for row in rows:
        turn = row.get("turn")
        if turn is None or turn < start_turn:
            continue
        meta = row.get("fork")
        if meta and task["depth"] < _CONTINGENCY_MAX_DEPTH:
            all_alts = meta.get("enemy_alternatives") or []
            branchable = [
                a for a in all_alts
                if float(a.get("probability") or 0.0) > 0.0
                and (exhaustive or float(a.get("probability") or 0.0) >= _CONTINGENCY_MOVE_FLOOR)
            ]
            ko_chance = float(meta.get("player_ko_chance") or 0.0)
            crit_chance = float(meta.get("enemy_crit_chance") or 0.0)
            player_crit_chance = float(meta.get("player_crit_chance") or 0.0)
            ai_switch_chance = float(meta.get("ai_switch_chance") or 0.0)
            crit_changes = bool(meta.get("enemy_crit_changes"))
            player_damage_outcomes = meta.get("player_damage_outcomes") or []
            enemy_damage_outcomes = meta.get("enemy_damage_outcomes") or []
            # Each axis only forks once per turn; a turn already pinned on an axis falls
            # through to the next axis (so a pinned-move turn can still fork on a crit).
            if turn not in ai_switch_ovr and 0.0 < ai_switch_chance < 1.0:
                fork = ("ai_switch", turn, meta, ai_switch_chance, row)
                break
            if turn not in move_ovr and len(branchable) >= 2:
                fork = ("enemy_move", turn, meta, all_alts, row)
                break
            damage_forked = False
            for axis in (meta.get("uncertainty_order") or ["enemy_crit", "player_damage", "enemy_damage"]):
                if axis == "enemy_crit":
                    if (
                        turn not in crit_ovr
                        and not meta.get("enemy_crits_forced")
                        and crit_chance > 0.0
                        and (
                            (exhaustive and bool(meta.get("enemy_damage_outcomes")))
                            or (crit_changes and crit_chance >= _CONTINGENCY_MIN_PROB)
                        )
                    ):
                        fork = ("crit", turn, meta, crit_chance, row)
                        damage_forked = True
                        break
                    continue
                if axis == "player_crit":
                    if (
                        turn not in player_crit_ovr
                        and player_crit_chance > 0.0
                        and exhaustive
                        and bool(player_damage_outcomes)
                    ):
                        fork = ("player_crit", turn, meta, player_crit_chance, row)
                        damage_forked = True
                        break
                    continue
                outcomes = player_damage_outcomes if axis == "player_damage" else enemy_damage_outcomes
                overrides = player_damage_ovr if axis == "player_damage" else enemy_damage_ovr
                if turn not in overrides and len(outcomes) >= 2:
                    fork = (axis, turn, meta, outcomes, row)
                    damage_forked = True
                    break
            if damage_forked:
                break
            if (
                not player_damage_outcomes
                and turn not in player_damage_ovr
                and turn not in kill_ovr
                and 0.0 < ko_chance < 1.0
                and (exhaustive or 0.05 < ko_chance < 0.95)
            ):
                fork = ("kill", turn, meta, ko_chance, row)
                break
        trunk.append(_contingency_step(row))

    if fork is None:
        node["outcome"] = line.get("result")
        node["confidence"] = line.get("confidence")
        return node

    kind, turn, meta, info, row = fork
    enemy_name = row.get("enemy") or "the foe"
    branches: list[dict[str, Any]] = []

    def queue_branch(
        label: str,
        prob: float,
        *,
        move_pin: str | None = None,
        kill_pin: bool | None = None,
        crit_pin: bool | None = None,
        player_crit_pin: bool | None = None,
        ai_switch_pin: bool | None = None,
        player_damage_pin: int | None = None,
        enemy_damage_pin: int | None = None,
    ) -> None:
        branch = {"label": label, "probability": round(prob, 6), "node": None}
        branches.append(branch)
        push(
            branch,
            move_ovr={**move_ovr, turn: move_pin} if move_pin is not None else move_ovr,
            kill_ovr={**kill_ovr, turn: kill_pin} if kill_pin is not None else kill_ovr,
            crit_ovr={**crit_ovr, turn: crit_pin} if crit_pin is not None else crit_ovr,
            player_crit_ovr={**player_crit_ovr, turn: player_crit_pin} if player_crit_pin is not None else player_crit_ovr,
            ai_switch_ovr={**ai_switch_ovr, turn: ai_switch_pin} if ai_switch_pin is not None else ai_switch_ovr,
            player_damage_ovr={**player_damage_ovr, turn: player_damage_pin} if player_damage_pin is not None else player_damage_ovr,
            enemy_damage_ovr={**enemy_damage_ovr, turn: enemy_damage_pin} if enemy_damage_pin is not None else enemy_damage_ovr,
            start_turn=turn,
            depth=task["depth"] + 1,
            prob=path_prob * max(0.0, min(1.0, prob)),
        )

    if kind == "ai_switch":
        switch_chance = float(info)
        target = meta.get("ai_switch_target") or "a safer back Pokemon"
        queue_branch(f"{enemy_name} hard-switches to {target} (50%)", switch_chance, ai_switch_pin=True)
        queue_branch(f"{enemy_name} stays in (50%)", 1 - switch_chance, ai_switch_pin=False)
        question = f"Turn {turn}: does {enemy_name} take its 50% hard-switch check?"
    elif kind in {"player_damage", "enemy_damage"}:
        actor = row.get("answer") if kind == "player_damage" else enemy_name
        target = enemy_name if kind == "player_damage" else (row.get("answer") or "your Pokemon")
        move = meta.get("player_move") if kind == "player_damage" else meta.get("enemy_move")
        for outcome in info:
            amount = int(outcome.get("damage") or 0)
            remaining = int(outcome.get("remaining_hp") or 0)
            probability = float(outcome.get("probability") or 0.0)
            if outcome.get("miss"):
                label = f"{actor}'s {move} misses ({_contingency_pct(probability)})"
            elif remaining <= 0:
                label = f"{actor}'s {move} deals {amount} and KOs {target} ({_contingency_pct(probability)})"
            else:
                label = f"{actor}'s {move} deals {amount}; {target} has {remaining} HP ({_contingency_pct(probability)})"
            queue_branch(
                label,
                probability,
                player_damage_pin=amount if kind == "player_damage" else None,
                enemy_damage_pin=amount if kind == "enemy_damage" else None,
            )
        question = f"Turn {turn}: what damage did {actor}'s {move} do?"
    elif kind == "kill":
        ko_chance = float(info)
        # When even the minimum roll KOs, the only way the foe survives is a miss —
        # branch it as one, instead of pretending a phantom low roll left it at 1 HP.
        no_ko_is_miss = bool(meta.get("player_no_ko_means_miss"))
        survive_label = (
            f"{meta['player_move']} misses ({_contingency_pct(1 - ko_chance)})"
            if no_ko_is_miss
            else f"{enemy_name} survives ({_contingency_pct(1 - ko_chance)})"
        )
        specs = [
            (f"{meta['player_move']} KOs {enemy_name} ({_contingency_pct(ko_chance)})", True, ko_chance),
            (survive_label, False, 1 - ko_chance),
        ]
        for label, pin, prob in specs:
            if not exhaustive and prob < _CONTINGENCY_MIN_PROB:
                continue
            queue_branch(label, prob, kill_pin=pin)
        question = f"Turn {turn}: does {meta['player_move']} KO {enemy_name}? ({_contingency_pct(ko_chance)} to KO)"
    elif kind in {"crit", "player_crit"}:
        crit_chance = float(info)
        player_axis = kind == "player_crit"
        actor = (row.get("answer") or "your Pokemon") if player_axis else enemy_name
        move = (meta.get("player_move") if player_axis else meta.get("enemy_move")) or "its attack"
        specs = [
            (f"{actor} crits with {move} ({_contingency_pct(crit_chance)})", True, crit_chance),
            (f"No crit ({_contingency_pct(1 - crit_chance)})", False, 1 - crit_chance),
        ]
        for label, pin, prob in specs:
            if not exhaustive and prob < _CONTINGENCY_MIN_PROB:
                continue
            queue_branch(label, prob, player_crit_pin=pin if player_axis else None, crit_pin=pin if not player_axis else None)
        question = f"Turn {turn}: does {actor}'s {move} crit? ({_contingency_pct(crit_chance)})"
    else:  # enemy_move
        branchable = [
            a for a in info
            if float(a.get("probability") or 0.0) > 0.0
            and (exhaustive or float(a.get("probability") or 0.0) >= _CONTINGENCY_MOVE_FLOOR)
        ]
        for alt in branchable:
            prob = float(alt.get("probability") or 0.0)
            queue_branch(f"{enemy_name} uses {alt['move']} ({_contingency_pct(prob)})", prob, move_pin=alt["move"])
        # Non-exhaustive only: fold the tail below the move floor into one catch-all branch
        # so the percentages sum to ~100%. Exhaustive mode branches every nonzero move, so
        # there is no tail to fold there.
        if not exhaustive:
            residual = [a for a in info if float(a.get("probability") or 0.0) < _CONTINGENCY_MOVE_FLOOR]
            residual_prob = max(0.0, 1.0 - sum(float(a.get("probability") or 0.0) for a in branchable))
            if residual and residual_prob >= _CONTINGENCY_ALT_FLOOR:
                top_other = residual[0]["move"]
                queue_branch(
                    f"Other moves — mostly {top_other} ({_contingency_pct(residual_prob)})",
                    residual_prob,
                    move_pin=top_other,
                )
        question = f"Turn {turn}: which move does {enemy_name} pick?"

    node["fork"] = {
        "turn": turn,
        "type": kind,
        "question": question,
        "instruction": _contingency_step(row),
        "branches": branches,
    }
    return node


def _doubles_contingency_flowchart(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    *,
    max_turns: int,
    force_enemy_crits: bool = False,
    forced_leads: tuple[int, int] | None = None,
    node_budget: int | None = None,
    time_budget_s: float | None = None,
    exhaustive: bool = False,
    progress_callback: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Replay every modeled doubles AI move+target branch from a 2v2 board.

    Each enemy field slot is a separate uncertainty axis.  The heap expands the
    most likely joint routes first, while state signatures merge lines that arrive
    at the same two active pairs with the same HP/status/boost/item state.
    """
    budget = node_budget if node_budget is not None else (
        _CONTINGENCY_FULL_NODE_BUDGET if exhaustive else _CONTINGENCY_NODE_BUDGET
    )
    unbounded = budget < 0
    deadline = time.monotonic() + time_budget_s if time_budget_s else None
    heap: list[tuple[float, int, dict[str, Any]]] = []
    seq = itertools.count()
    memo: dict[tuple[Any, ...], dict[str, Any]] = {}
    root_slot: dict[str, Any] = {"node": None}

    def push(
        slot: dict[str, Any], *, pins: dict[int, dict[int, str]],
        damage_pins: dict[int, dict[str, int]], start_turn: int, depth: int, prob: float,
    ) -> None:
        heapq.heappush(heap, (-prob, next(seq), {
            "slot": slot, "pins": pins, "damage_pins": damage_pins,
            "start_turn": start_turn, "depth": depth, "prob": prob,
        }))

    push(root_slot, pins={}, damage_pins={}, start_turn=1, depth=0, prob=1.0)
    expanded = 0
    truncated_count = 0
    truncated_probability = 0.0
    cancelled = False

    while heap:
        _neg_prob, _sequence, task = heapq.heappop(heap)
        cancelled = cancelled or bool(cancel_event and cancel_event.is_set())
        out_of_budget = (
            cancelled
            or (not unbounded and expanded >= budget)
            or (deadline is not None and time.monotonic() > deadline)
        )
        if out_of_budget and task["depth"] > 0:
            task["slot"]["node"] = _contingency_truncated_leaf(task["prob"])
            truncated_count += 1
            truncated_probability += float(task["prob"])
            continue

        line = _run_text_calc_sim_once_doubles(
            _clone_calc_team(team),
            trainer,
            calculator,
            max_turns=max_turns,
            force_enemy_crits=force_enemy_crits,
            forced_leads=forced_leads,
            enemy_action_overrides=task["pins"],
            damage_overrides=task["damage_pins"],
            compute_item_recs=False,
        )
        rows = line.get("turns") or []
        start_row = next((row for row in rows if int(row.get("turn") or 0) >= task["start_turn"]), None)
        pending = tuple(sorted(
                [("action", turn, field_slot, pin)
                for turn, slots in task["pins"].items()
                if turn >= task["start_turn"]
                for field_slot, pin in slots.items()]
                + [("damage", turn, axis, amount)
                   for turn, axes in task["damage_pins"].items()
                   if turn >= task["start_turn"]
                   for axis, amount in axes.items()]
        ))
        memo_key = (
            task["start_turn"], start_row.get("state_sig"), pending
        ) if start_row and start_row.get("state_sig") is not None else None
        if memo_key is not None and memo_key in memo:
            task["slot"]["node"] = _contingency_merge_leaf(memo[memo_key])
            expanded += 1
            continue

        node: dict[str, Any] = {
            "steps": [],
            "state_label": _contingency_state_label(start_row),
            "board": start_row.get("board_before") if start_row else None,
        }
        if memo_key is not None:
            node["state_id"] = f"doubles-state-{len(memo) + 1}"
            memo[memo_key] = node

        fork_row: dict[str, Any] | None = None
        fork_axis: dict[str, Any] | None = None
        for row in rows:
            turn = int(row.get("turn") or 0)
            if turn < task["start_turn"]:
                continue
            for axis in (row.get("fork") or {}).get("doubles_enemy_options") or []:
                field_slot = int(axis.get("enemy_slot") or 0)
                options = [option for option in axis.get("options") or [] if float(option.get("probability") or 0.0) > 0]
                if task["depth"] >= _CONTINGENCY_MAX_DEPTH:
                    continue
                if field_slot not in task["pins"].get(turn, {}) and len(options) >= 2:
                    fork_row, fork_axis = row, {**axis, "options": options, "_kind": "enemy_action"}
                    break
            if fork_axis is None:
                for axis in (row.get("fork") or {}).get("doubles_damage_options") or []:
                    axis_id = str(axis.get("axis") or "")
                    options = [option for option in axis.get("options") or [] if float(option.get("probability") or 0.0) > 0]
                    if (
                        task["depth"] < _CONTINGENCY_MAX_DEPTH
                        and axis_id not in task["damage_pins"].get(turn, {})
                        and len(options) >= 2
                    ):
                        fork_row, fork_axis = row, {**axis, "options": options, "_kind": "damage"}
                        break
            if fork_axis is not None:
                break
            node["steps"].append(_contingency_step(row))

        if fork_row is None or fork_axis is None:
            node["outcome"] = line.get("result")
            node["confidence"] = line.get("confidence")
        else:
            turn = int(fork_row.get("turn") or 0)
            kind = str(fork_axis.get("_kind") or "enemy_action")
            field_slot = int(fork_axis.get("enemy_slot") or 0)
            actor = str(fork_axis.get("actor") or f"Enemy slot {field_slot + 1}")
            branches: list[dict[str, Any]] = []
            for option in fork_axis["options"]:
                probability = float(option.get("probability") or 0.0)
                if not exhaustive and probability < _CONTINGENCY_ALT_FLOOR:
                    continue
                if kind == "damage":
                    amount = int(option.get("damage") or 0)
                    remaining = int(option.get("remaining_hp") or 0)
                    label = (
                        f"{actor}'s {fork_axis.get('move')} misses ({_contingency_pct(probability)})"
                        if option.get("miss") else
                        f"{actor}'s {fork_axis.get('move')} deals {amount} to {fork_axis.get('target')}; "
                        f"{remaining} HP remains ({_contingency_pct(probability)})"
                    )
                else:
                    damage = option.get("damage") or {}
                    damage_text = (
                        f" · {damage.get('min')}-{damage.get('max')} damage"
                        if damage.get("min") is not None else ""
                    )
                    label = (
                        f"{actor} uses {option.get('move')} on {option.get('target')}"
                        f"{damage_text} ({_contingency_pct(probability)})"
                    )
                branch = {"label": label, "probability": round(probability, 6), "node": None}
                branches.append(branch)
                next_pins = {turn_no: dict(slots) for turn_no, slots in task["pins"].items()}
                next_damage_pins = {turn_no: dict(axes) for turn_no, axes in task["damage_pins"].items()}
                if kind == "damage":
                    next_damage_pins.setdefault(turn, {})[str(fork_axis.get("axis"))] = int(option.get("damage") or 0)
                else:
                    next_pins.setdefault(turn, {})[field_slot] = str(option.get("pin"))
                push(
                    branch,
                    pins=next_pins,
                    damage_pins=next_damage_pins,
                    start_turn=turn,
                    depth=task["depth"] + 1,
                    prob=task["prob"] * probability,
                )
            node["fork"] = {
                "turn": turn,
                "type": "doubles_damage" if kind == "damage" else "doubles_enemy_action",
                "question": (
                    f"Turn {turn}: how much damage does {actor}'s {fork_axis.get('move')} deal to {fork_axis.get('target')}?"
                    if kind == "damage" else
                    f"Turn {turn}: what does {actor} target from field slot {field_slot + 1}?"
                ),
                "instruction": _contingency_step(fork_row),
                "branches": branches,
            }

        task["slot"]["node"] = node
        expanded += 1
        if progress_callback and (expanded == 1 or expanded % 10 == 0):
            progress_callback(expanded, len(heap))

    root = root_slot["node"] or {"steps": [], "outcome": "empty"}
    root["_meta"] = {
        "expanded_nodes": expanded,
        "node_budget": None if unbounded else budget,
        "truncated_branches": truncated_count,
        "truncated_probability_upper_bound": round(min(1.0, truncated_probability), 4),
        "complete": truncated_count == 0 and not cancelled,
        "cancelled": cancelled,
        "battle_mode": "doubles",
        "branch_axes": ["enemy field slot", "move", "target slot", "accuracy", "damage roll", "remaining HP"],
    }
    if progress_callback and not cancelled:
        progress_callback(expanded, 0, complete=True)
    return root


def _contingency_flowchart(
    team: list[PlannedMember],
    trainer: TrainerBattle,
    calculator: DamageCalculator,
    *,
    max_turns: int,
    force_enemy_crits: bool = False,
    forced_lead: int | None = None,
    forced_doubles_leads: tuple[int, int] | None = None,
    player_move_overrides: dict[int, str] | None = None,
    node_budget: int | None = None,
    time_budget_s: float | None = None,
    exhaustive: bool = False,
    progress_callback: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Top-level contingency tree for singles or doubles.

    Positions are expanded best-first by cumulative path probability, so the node and
    wall-clock budgets always go to the likeliest lines; lines the budget never reaches
    are closed with an explicit truncation leaf instead of dangling.
    """
    if trainer.is_double:
        return _doubles_contingency_flowchart(
            team,
            trainer,
            calculator,
            max_turns=max_turns,
            force_enemy_crits=force_enemy_crits,
            forced_leads=forced_doubles_leads,
            node_budget=node_budget,
            time_budget_s=time_budget_s,
            exhaustive=exhaustive,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
    budget = node_budget if node_budget is not None else (
        _CONTINGENCY_FULL_NODE_BUDGET if exhaustive else _CONTINGENCY_NODE_BUDGET
    )
    # Every replay starts from the same untouched roster. Resolve the opening lead once;
    # otherwise a large exhaustive tree repeats the full matchup ranking at every node.
    resolved_lead = forced_lead
    if resolved_lead is None:
        opening_enemies = _planned_enemies_for_trainer(trainer, calculator)
        if opening_enemies:
            resolved_lead = _best_calc_answer(
                team, opening_enemies[0], calculator,
                force_enemy_crits=force_enemy_crits,
                allow_sac=True,
                enemy_will_intimidate=True,
            )
    unbounded = budget < 0
    deadline = (time.monotonic() + time_budget_s) if time_budget_s else None
    memo: dict[tuple, dict[str, Any]] = {}
    # Per-build decision cache: state signatures don't encode species, so this must not
    # outlive one (team, trainer, lead, crit-mode) build — which is exactly this scope.
    decision_cache: dict = {}
    heap: list[tuple[float, int, dict[str, Any]]] = []
    seq = itertools.count()
    root_slot: dict[str, Any] = {"node": None}

    def push(slot: dict[str, Any], *, move_ovr: dict[int, str], kill_ovr: dict[int, bool],
             crit_ovr: dict[int, bool], player_crit_ovr: dict[int, bool], ai_switch_ovr: dict[int, bool],
             player_damage_ovr: dict[int, int], enemy_damage_ovr: dict[int, int],
             start_turn: int, depth: int, prob: float) -> None:
        heapq.heappush(heap, (-prob, next(seq), {
            "slot": slot, "move_ovr": move_ovr, "kill_ovr": kill_ovr, "crit_ovr": crit_ovr,
            "player_crit_ovr": player_crit_ovr,
            "ai_switch_ovr": ai_switch_ovr,
            "player_damage_ovr": player_damage_ovr,
            "enemy_damage_ovr": enemy_damage_ovr,
            "start_turn": start_turn, "depth": depth, "prob": prob,
        }))

    push(
        root_slot,
        move_ovr={}, kill_ovr={}, crit_ovr={}, player_crit_ovr={}, ai_switch_ovr={},
        player_damage_ovr={}, enemy_damage_ovr={},
        start_turn=1, depth=0, prob=1.0,
    )
    expanded = 0
    truncated_count = 0
    truncated_probability = 0.0
    cancelled = False
    while heap:
        _, _, task = heapq.heappop(heap)
        cancelled = cancelled or bool(cancel_event and cancel_event.is_set())
        out_of_budget = cancelled or (not unbounded and expanded >= budget) or (deadline is not None and time.monotonic() > deadline)
        if out_of_budget and task["depth"] > 0:
            task["slot"]["node"] = _contingency_truncated_leaf(task["prob"])
            truncated_count += 1
            truncated_probability += float(task["prob"])
            continue
        expanded += 1
        task["slot"]["node"] = _expand_contingency_node(
            team, trainer, calculator, task, memo=memo, push=push, max_turns=max_turns,
            force_enemy_crits=force_enemy_crits, forced_lead=resolved_lead, exhaustive=exhaustive,
            player_move_overrides=player_move_overrides, decision_cache=decision_cache,
        )
        if progress_callback and (expanded == 1 or expanded % 10 == 0):
            progress_callback(expanded, len(heap))
    root = root_slot["node"] or {"steps": [], "outcome": "empty"}
    root["_meta"] = {
        "expanded_nodes": expanded,
        "node_budget": None if unbounded else budget,
        "truncated_branches": truncated_count,
        # Branch probabilities overlap after reconvergence, so this is explicitly an
        # upper bound rather than pretending to be exact covered probability mass.
        "truncated_probability_upper_bound": round(min(1.0, truncated_probability), 4),
        "complete": truncated_count == 0 and not cancelled,
        "cancelled": cancelled,
    }
    if progress_callback and not cancelled:
        progress_callback(expanded, 0, complete=True)
    return root


def _team_with_recommended_items(
    team: list[PlannedMember],
    recommendations: list[dict[str, Any]],
) -> tuple[list[PlannedMember], list[dict[str, Any]]]:
    optimized = _clone_calc_team(team)
    by_name = {_normalize(member.name): member for member in optimized}
    changes: list[dict[str, Any]] = []
    for recommendation in recommendations:
        member = by_name.get(_normalize(str(recommendation.get("pokemon") or "")))
        item = recommendation.get("suggested_item")
        if member is None or not item or _normalize(member.item) == _normalize(str(item)):
            continue
        old_item = member.item
        member.item = str(item)
        member.consumed_item = False
        changes.append(
            {
                "pokemon": member.name,
                "old_item": old_item,
                "new_item": member.item,
                "reason": recommendation.get("reason", ""),
                "source": recommendation.get("source", ""),
            }
        )
    return optimized, changes


def _best_calc_answer(
    team: list[PlannedMember],
    enemy: PlannedEnemy,
    calculator: DamageCalculator,
    *,
    force_enemy_crits: bool = False,
    allow_sac: bool = False,
    exclude_index: int | None = None,
    enemy_will_intimidate: bool = False,
) -> int | None:
    ranked = _rank_calc_answers(
        team,
        enemy,
        calculator,
        force_enemy_crits=force_enemy_crits,
        exclude_index=exclude_index,
        enemy_will_intimidate=enemy_will_intimidate,
    )
    for _, index, _, _, dies in ranked:
        if not dies:
            return index
    if allow_sac:
        return _tactical_sac_target(team, exclude_index, enemy, calculator, None, force_enemy_crits=force_enemy_crits)
    return None


def _rank_calc_answers(
    team: list[PlannedMember],
    enemy: PlannedEnemy,
    calculator: DamageCalculator,
    *,
    force_enemy_crits: bool = False,
    exclude_index: int | None = None,
    enemy_will_intimidate: bool = False,
) -> list[tuple[float, int, Any, MoveChoice | None, bool]]:
    scored = []
    for index, member in enumerate(team):
        if index == exclude_index or not member.alive:
            continue
        eval_enemy = _entry_adjusted_enemy(enemy, member)
        eval_member = _entry_adjusted_member(member, enemy) if enemy_will_intimidate else member
        action = _best_player_action(eval_member, eval_enemy, team, calculator)
        choices = _calc_enemy_choices(eval_enemy, eval_member, team, calculator, force_enemy_crits=force_enemy_crits)
        choice = choices[0] if choices else None
        faster_guaranteed_ko = _is_faster_guaranteed_ko(eval_member, eval_enemy, action, choice, calculator)
        pressure = _choice_pressure(choice, member, calculator)
        damage = choice.damage.max_damage if choice and choice.damage else 0
        raw_dies = bool(choice and choice.damage and damage >= member.hp)
        dies = raw_dies and not faster_guaranteed_ko
        hp_after = max(0, member.hp - damage) / max(1, member.max_hp)
        score = action.score - pressure * 90 + hp_after * 25 + _ai_branch_confidence(choices, eval_member, eval_enemy, calculator) * 10
        if faster_guaranteed_ko:
            score += 250
        if dies:
            score -= 10000
        scored.append((score, index, action, choice, dies))
    return sorted(scored, key=lambda row: row[0], reverse=True)


def _threat_answers(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    calculator: DamageCalculator,
) -> list[dict[str, Any]]:
    """Advisory only (does NOT steer planning): for each enemy, rank your team by how
    cleanly they answer it — tanks the best hit (resist/bulk), outspeeds, hits hard.
    Used to explain matchups and to flag enemies the box has no clean answer to."""
    rows: list[dict[str, Any]] = []
    for enemy in enemies:
        best = None
        for member in team:
            action = _best_player_action(member, enemy, team, calculator)
            direct_damage = _ranked_known_damage(
                calculator, member.calc_set(), enemy.calc_set(), member.known_moves
            )
            advisory_damage = direct_damage[0] if direct_damage else None
            choices = _calc_enemy_choices(enemy, member, team, calculator)
            choice = choices[0] if choices else None
            incoming = (choice.damage.max_damage / max(1, member.max_hp)) if (choice and choice.damage) else 1.0
            outgoing = 0.0
            if advisory_damage is not None:
                try:
                    dmg = calculator.estimate_move(
                        member.calc_set(), enemy.calc_set(), advisory_damage.move_name
                    )
                    outgoing = (dmg.max_damage / max(1, enemy.max_hp)) if dmg else 0.0
                except Exception:
                    outgoing = 0.0
            try:
                faster = _speed(member.calc_set(), calculator) > _speed(enemy.calc_set(), calculator)
            except Exception:
                faster = False
            # Higher is a cleaner answer: survives the hit + hits hard + outspeeds.
            quality = (1.0 - min(1.0, incoming)) * 2.0 + min(1.5, outgoing) + (0.4 if faster else 0.0)
            survives = incoming < 1.0
            cand = {"mon": member.name, "quality": round(quality, 2), "incoming": round(incoming, 2), "outgoing": round(outgoing, 2), "faster": faster, "survives": survives, "move": advisory_damage.move_name if advisory_damage else getattr(action, "move_name", "")}
            if best is None or cand["quality"] > best["quality"]:
                best = cand
        clean = bool(best and best["survives"] and (best["outgoing"] >= 0.34 or best["faster"]))
        rows.append({"enemy": enemy.name, "best": best, "clean": clean})
    return rows


def _rank_calc_switches(
    team: list[PlannedMember],
    active_index: int,
    enemy: PlannedEnemy,
    calculator: DamageCalculator,
    *,
    force_enemy_crits: bool = False,
) -> list[tuple[float, int, Any, MoveChoice | None, MoveChoice | None, bool, bool, bool, float]]:
    active = team[active_index]
    outgoing_choices = _calc_enemy_choices(enemy, active, team, calculator, force_enemy_crits=force_enemy_crits)
    outgoing_choice = outgoing_choices[0] if outgoing_choices else None
    scored = []
    for index, member in enumerate(team):
        if index == active_index or not member.alive:
            continue
        eval_enemy = _entry_adjusted_enemy(enemy, member)
        action = _best_player_action(member, eval_enemy, team, calculator)
        direct_choices = _calc_enemy_choices(eval_enemy, member, team, calculator, force_enemy_crits=force_enemy_crits)
        direct_choice = direct_choices[0] if direct_choices else None
        retargeted = _retarget_choice_for_calc(eval_enemy, member, outgoing_choice, calculator, force_enemy_crits=force_enemy_crits)
        pursuit_trap = _pursuit_catches_switch(enemy, active, calculator, force_enemy_crits=force_enemy_crits)
        immediate_damage = retargeted.damage.max_damage if retargeted and retargeted.damage else 0
        direct_damage = direct_choice.damage.max_damage if direct_choice and direct_choice.damage else 0
        immediate_dies = pursuit_trap or bool(retargeted and retargeted.damage and immediate_damage >= member.hp)
        # Follow-up survival must be judged from the HP left AFTER paying the entry
        # hit, otherwise a pivot that eats entry + next-turn damage looks "stable".
        direct_dies_raw = bool(direct_choice and direct_choice.damage and immediate_damage + direct_damage >= member.hp)
        followup_faster_ko = _is_faster_guaranteed_ko(member, eval_enemy, action, direct_choice, calculator)
        direct_dies = direct_dies_raw and not followup_faster_ko
        immediate_pressure = _choice_pressure(retargeted, member, calculator)
        direct_pressure = _choice_pressure(direct_choice, member, calculator)
        hp_after = max(0, member.hp - immediate_damage) / max(1, member.max_hp)
        bait_score = _bait_pivot_score(
            team,
            active_index,
            index,
            enemy,
            calculator,
            direct_choice,
            force_enemy_crits=force_enemy_crits,
        )
        score = action.score - immediate_pressure * 110 - direct_pressure * 55 + hp_after * 35
        score += bait_score
        score += _intimidate_pivot_bonus(member, eval_enemy, immediate_dies, calculator)
        if followup_faster_ko:
            score += 250
        if direct_dies:
            score -= 350
        if pursuit_trap:
            score -= 10000
        if immediate_dies:
            score -= 10000
        scored.append((score, index, action, retargeted, direct_choice, immediate_dies, direct_dies, followup_faster_ko, bait_score))
    return sorted(scored, key=lambda row: row[0], reverse=True)


def _calc_switch_target(
    team: list[PlannedMember],
    active_index: int,
    enemy: PlannedEnemy,
    calculator: DamageCalculator,
    *,
    force_enemy_crits: bool = False,
    previous_switch: tuple[int, int, int] | None = None,
    enemy_index: int | None = None,
    switch_streak: int = 0,
) -> int | None:
    active = team[active_index]
    choices = _calc_enemy_choices(enemy, active, team, calculator, force_enemy_crits=force_enemy_crits)
    active_action = _best_player_action(active, enemy, team, calculator)
    choice = choices[0] if choices else None
    active_dies = _choice_kills_current(choice, active)
    if _is_faster_guaranteed_ko(active, enemy, active_action, choice, calculator):
        return None
    if not active_dies and _active_can_stay_and_progress(active, enemy, active_action, choice, calculator):
        return None
    ranked = _rank_calc_switches(team, active_index, enemy, calculator, force_enemy_crits=force_enemy_crits)
    immediate_safe = [row for row in ranked if not row[5]]
    if active_dies:
        stable_safe = sorted(
            [row for row in immediate_safe if not row[6]],
            key=lambda row: _emergency_switch_score(row, team, enemy, calculator),
            reverse=True,
        )
        for row in stable_safe:
            if _switch_allowed_in_streak(row, enemy, active_index, previous_switch, enemy_index, switch_streak):
                return row[1]
        if switch_streak > 0:
            return None
        for row in immediate_safe:
            if _switch_row_has_payoff(row, enemy) and _switch_allowed_in_streak(row, enemy, active_index, previous_switch, enemy_index, switch_streak):
                return row[1]
        # Emergency pivot: the active mon dies if it stays. A switch-in that keeps
        # most of its HP after eating the entry hit beats trading the active away,
        # even without an immediate payoff.
        emergency = sorted(immediate_safe, key=lambda row: _emergency_switch_score(row, team, enemy, calculator), reverse=True)
        for row in emergency:
            _, incoming_index, in_action, retargeted, _, _, _, _, _ = row
            if not _switch_allowed_in_streak(row, enemy, active_index, previous_switch, enemy_index, switch_streak):
                continue
            if previous_switch is not None and previous_switch == (incoming_index, active_index, enemy_index):
                continue
            incoming = team[incoming_index]
            entry_damage = retargeted.damage.max_damage if retargeted and retargeted.damage else 0
            hp_after_entry = (incoming.hp - entry_damage) / max(1, incoming.max_hp)
            if hp_after_entry >= 0.4 and in_action.score > 0:
                return incoming_index
        return None
    long_term_safe = [row for row in immediate_safe if not row[6]]
    if not long_term_safe:
        return None
    enemy_is_setting_up = _choice_is_setup(choice)
    _, best, best_action, _, _, _, _, followup_faster_ko, bait_score = long_term_safe[0]
    if not _switch_allowed_in_streak(long_term_safe[0], enemy, active_index, previous_switch, enemy_index, switch_streak):
        return None
    if followup_faster_ko and best_action.score > active_action.score + 10:
        return best
    if enemy_is_setting_up:
        return None
    if best_action.score > active_action.score + 85:
        return best
    return None


def _choice_is_setup(choice: MoveChoice | None) -> bool:
    return bool(choice and _normalize(choice.move_name) in SETUP_MOVE_BOOSTS)


def _switch_allowed_in_streak(
    row: tuple[float, int, Any, MoveChoice | None, MoveChoice | None, bool, bool, bool, float],
    enemy: PlannedEnemy,
    active_index: int,
    previous_switch: tuple[int, int, int] | None,
    enemy_index: int | None,
    switch_streak: int,
) -> bool:
    _, incoming_index, action, _, _, _, direct_dies, followup_faster_ko, bait_score = row
    has_payoff = _switch_row_has_payoff(row, enemy)
    if previous_switch is not None and previous_switch == (incoming_index, active_index, enemy_index) and not has_payoff:
        return False
    if switch_streak >= 1 and not has_payoff:
        return False
    return True


def _switch_row_has_payoff(
    row: tuple[float, int, Any, MoveChoice | None, MoveChoice | None, bool, bool, bool, float],
    enemy: PlannedEnemy,
) -> bool:
    _, _, action, _, _, _, direct_dies, followup_faster_ko, bait_score = row
    guaranteed_ko_after_entry = bool(action.damage and action.damage.min_damage >= enemy.hp and not direct_dies)
    return followup_faster_ko or bait_score >= 120 or guaranteed_ko_after_entry


def _intimidate_pivot_bonus(
    member: PlannedMember,
    enemy: PlannedEnemy,
    immediate_dies: bool,
    calculator: DamageCalculator,
) -> float:
    """Bait/weaken pivot: an Intimidate switch-in drops the enemy's Attack stage,
    softening every later physical hit (and defusing crit-KO threats) for the rest
    of the team. Only credited when the pivot survives its entry hit and the enemy
    actually leans on physical damage."""
    if immediate_dies or _normalize(member.ability or "") != "intimidate":
        return 0.0
    if enemy.boosts.get("atk", 0) <= -2:
        return 0.0
    if _enemy_has_damaging_category(enemy, member, calculator, "Physical"):
        return 130.0
    return 0.0


def _emergency_switch_score(
    row: tuple[float, int, Any, MoveChoice | None, MoveChoice | None, bool, bool, bool, float],
    team: list[PlannedMember],
    enemy: PlannedEnemy,
    calculator: DamageCalculator | None = None,
) -> float:
    _, incoming_index, action, retargeted, direct_choice, _, direct_dies, followup_faster_ko, _ = row
    incoming = team[incoming_index]
    entry_damage = retargeted.damage.max_damage if retargeted and retargeted.damage else 0
    followup_damage = direct_choice.damage.max_damage if direct_choice and direct_choice.damage else 0
    hp_after_entry = max(0, incoming.hp - entry_damage) / max(1, incoming.max_hp)
    hp_after_followup = max(0, incoming.hp - entry_damage - followup_damage) / max(1, incoming.max_hp)
    progress = action.damage.min_damage / max(1, enemy.hp) if action.damage else 0.0
    score = progress * 120 + hp_after_entry * 80 + hp_after_followup * 60
    if calculator is not None:
        score += _intimidate_pivot_bonus(incoming, enemy, False, calculator)
    if action.damage and action.damage.min_damage >= enemy.hp:
        score += 100
    if followup_faster_ko:
        score += 80
    if direct_dies:
        score -= 300
    return score


def _apply_switch_out_effects(member: PlannedMember) -> None:
    if _normalize(member.calc_set().ability) == "regenerator" and member.hp > 0:
        member.hp = min(member.max_hp, member.hp + max(1, member.max_hp // 3))


def _pursuit_catches_switch(
    enemy: PlannedEnemy,
    outgoing: PlannedMember,
    calculator: DamageCalculator,
    *,
    force_enemy_crits: bool = False,
) -> bool:
    if not any(_normalize(move) == "pursuit" for move in enemy.moves):
        return False
    context = DamageContext(critical=force_enemy_crits, defender_is_switching=True)
    damage = calculator.estimate_move(enemy.calc_set(), outgoing.calc_set(), "Pursuit", context)
    return bool(damage and damage.max_damage >= outgoing.hp)


def _is_faster_guaranteed_ko(
    member: PlannedMember,
    enemy: PlannedEnemy,
    action: Any,
    choice: MoveChoice | None,
    calculator: DamageCalculator,
) -> bool:
    if not action.damage or action.damage.min_damage < enemy.hp:
        return False
    return not _enemy_moves_before_player(enemy, member, choice.move_name if choice else "", action.move_name, calculator)


def _bait_pivot_score(
    team: list[PlannedMember],
    active_index: int,
    bait_index: int,
    enemy: PlannedEnemy,
    calculator: DamageCalculator,
    bait_choice: MoveChoice | None,
    *,
    force_enemy_crits: bool = False,
) -> float:
    if bait_choice is None:
        return 0.0
    best_score = 0.0
    for index, partner in enumerate(team):
        if index in {active_index, bait_index} or not partner.alive:
            continue
        retargeted = _retarget_choice_for_calc(enemy, partner, bait_choice, calculator, force_enemy_crits=force_enemy_crits)
        if retargeted and retargeted.damage and retargeted.damage.max_damage >= partner.hp:
            continue
        action = _best_player_action(partner, enemy, team, calculator)
        direct_choices = _calc_enemy_choices(enemy, partner, team, calculator, force_enemy_crits=force_enemy_crits)
        direct_choice = direct_choices[0] if direct_choices else None
        faster_ko = _is_faster_guaranteed_ko(partner, enemy, action, direct_choice, calculator)
        direct_safe = not _choice_kills_current(direct_choice, partner)
        if faster_ko:
            score = 30.0
        elif direct_safe and action.score > 80:
            score = 15.0
        else:
            continue
        best_score = max(best_score, score)
    return best_score


def _tactical_sac_target(
    team: list[PlannedMember],
    active_index: int | None,
    enemy: PlannedEnemy,
    calculator: DamageCalculator,
    outgoing_choice: MoveChoice | None,
    *,
    force_enemy_crits: bool = False,
) -> int | None:
    scored = []
    for index, member in enumerate(team):
        if not member.alive:
            continue
        eval_enemy = _entry_adjusted_enemy(enemy, member) if active_index is None or index != active_index else enemy
        action = _best_player_action(member, eval_enemy, team, calculator)
        choices = _calc_enemy_choices(eval_enemy, member, team, calculator, force_enemy_crits=force_enemy_crits)
        direct_choice = choices[0] if choices else None
        incoming_choice = direct_choice
        if active_index is not None and index != active_index and outgoing_choice is not None:
            incoming_choice = _retarget_choice_for_calc(eval_enemy, member, outgoing_choice, calculator, force_enemy_crits=force_enemy_crits)
        incoming_damage = incoming_choice.damage.max_damage if incoming_choice and incoming_choice.damage else 0
        direct_damage = direct_choice.damage.max_damage if direct_choice and direct_choice.damage else 0
        incoming_dies = bool(incoming_choice and incoming_choice.damage and incoming_damage >= member.hp)
        direct_dies = bool(direct_choice and direct_choice.damage and direct_damage >= member.hp)
        hp_fraction = member.hp / max(1, member.max_hp)
        keep_value = max(action.score, 0.0) + hp_fraction * 75
        if not incoming_dies:
            keep_value += 500
        if not direct_dies:
            keep_value += 150
        if active_index is not None and index != active_index:
            keep_value += 20
        scored.append((keep_value, index))
    return min(scored, default=(0.0, None))[1]


def _tactical_sac_note(enemy: PlannedEnemy, choice: MoveChoice | None) -> str:
    move = choice.move_name if choice else "its likely move"
    return f"No safe pivot survives {enemy.name}'s likely {move}; the sim is reporting a tactical sacrifice instead of pretending this is a clean line."


def _mark_enemy_allies_fainted(enemies: list[PlannedEnemy], fainted_index: int) -> None:
    for index, enemy in enumerate(enemies):
        if index != fainted_index and enemy.alive:
            enemy.pokemon = replace(enemy.pokemon, allies_fainted=enemy.pokemon.allies_fainted + 1)


def _next_calc_enemy(
    enemies: list[PlannedEnemy],
    team: list[PlannedMember],
    active_index: int,
    calculator: DamageCalculator,
    *,
    force_enemy_crits: bool = False,
) -> int | None:
    alive = [index for index, enemy in enumerate(enemies) if enemy.alive]
    if not alive:
        return None
    active = team[active_index]
    return max(
        alive,
        key=lambda index: (
            _calc_enemy_choices(enemies[index], active, team, calculator, force_enemy_crits=force_enemy_crits)[0].score
            if _calc_enemy_choices(enemies[index], active, team, calculator, force_enemy_crits=force_enemy_crits)
            else 0
        ),
    )


def _retarget_choice_for_calc(
    enemy: PlannedEnemy,
    incoming: PlannedMember,
    choice: MoveChoice | None,
    calculator: DamageCalculator,
    *,
    force_enemy_crits: bool = False,
) -> MoveChoice | None:
    if choice is None:
        return None
    context = DamageContext(critical=True) if force_enemy_crits else None
    damage = calculator.estimate_move(enemy.calc_set(), incoming.calc_set(), choice.move_name, context)
    return MoveChoice(choice.move_name, choice.score, choice.probability, damage, choice.reason)


def _calc_enemy_choices(
    enemy: PlannedEnemy,
    player: PlannedMember,
    team: list[PlannedMember],
    calculator: DamageCalculator,
    *,
    force_enemy_crits: bool = False,
) -> list[MoveChoice]:
    choices = _ai_move_choices(enemy, player, team, calculator, force_crit=force_enemy_crits)
    if not force_enemy_crits:
        return choices
    return [
        MoveChoice(choice.move_name, choice.score, choice.probability, choice.damage, f"{choice.reason}; crit-aware")
        for choice in choices
    ]


def _crit_mode_notes(force_enemy_crits: bool) -> list[str]:
    if not force_enemy_crits:
        return []
    return ["Crit-aware mode is ON: this line is being tested with enemy attacks treated as critical hits."]


_STATUS_WORDS = {
    "burn": "burned",
    "paralysis": "paralyzed",
    "poison": "poisoned",
    "toxic": "badly poisoned",
    "sleep": "asleep",
    "freeze": "frozen",
}


def _member_held_item(member: PlannedMember | PlannedEnemy) -> str | None:
    item = getattr(member, "item", None)
    if item is None:
        pokemon = getattr(member, "pokemon", None)
        item = getattr(pokemon, "held_item", None) if pokemon is not None else None
    return item


def _action_side_events(
    attacker: PlannedMember | PlannedEnemy,
    target: PlannedMember | PlannedEnemy,
    snapshot: tuple[int, str | None, int, int, bool, bool],
) -> list[str]:
    (
        attacker_hp_before, target_status_before, target_confused_before,
        target_boost_count_before, attacker_consumed_before, target_consumed_before,
    ) = snapshot
    events: list[str] = []
    if target.status != target_status_before and target.status:
        events.append(f"{target.name} is {_STATUS_WORDS.get(target.status, target.status)}.")
    if target.confused_turns > target_confused_before:
        events.append(f"{target.name} becomes confused.")
    if sum(target.boosts.values()) < target_boost_count_before:
        events.append(f"{target.name}'s stats are lowered.")
    if attacker.hp < attacker_hp_before:
        events.append(f"{attacker.name} takes {attacker_hp_before - attacker.hp} recoil and ends {attacker.hp}/{attacker.max_hp}.")
        if not attacker.alive:
            events.append(f"{attacker.name} faints from recoil.")
    elif attacker.hp > attacker_hp_before:
        events.append(f"{attacker.name} recovers to {attacker.hp}/{attacker.max_hp}.")
    if not target_consumed_before and getattr(target, "consumed_item", False):
        item = _member_held_item(target)
        if item:
            events.append(f"{target.name}'s {item} activates; {target.name} is now {target.hp}/{target.max_hp}.")
    if not attacker_consumed_before and getattr(attacker, "consumed_item", False):
        item = _member_held_item(attacker)
        if item:
            events.append(f"{attacker.name}'s {item} activates; {attacker.name} is now {attacker.hp}/{attacker.max_hp}.")
    return events


def _action_snapshot(attacker: PlannedMember | PlannedEnemy, target: PlannedMember | PlannedEnemy) -> tuple[int, str | None, int, int, bool, bool]:
    return (
        attacker.hp, target.status, target.confused_turns, sum(target.boosts.values()),
        bool(getattr(attacker, "consumed_item", False)),
        bool(getattr(target, "consumed_item", False)),
    )


def _end_of_turn_events(player: PlannedMember, enemy: PlannedEnemy, calculator: DamageCalculator) -> list[str]:
    snapshots = [
        (member, member.hp, _member_held_item(member), getattr(member, "consumed_item", False))
        for member in (player, enemy)
    ]
    _end_of_turn(player, enemy, calculator)
    events: list[str] = []
    for member, hp_before, item, consumed_before in snapshots:
        newly_consumed = not consumed_before and getattr(member, "consumed_item", False)
        if member.hp > hp_before:
            source = f" with {item}" if newly_consumed and item else ""
            events.append(f"End of turn: {member.name} recovers{source} to {member.hp}/{member.max_hp}.")
        elif member.hp < hp_before:
            events.append(f"End of turn: {member.name} drops to {member.hp}/{member.max_hp}.")
        elif newly_consumed and item:
            events.append(f"End of turn: {member.name}'s {item} triggers.")
    return events


def _choice_pressure(choice: MoveChoice | None, member: PlannedMember, calculator: DamageCalculator) -> float:
    """Pressure of the enemy's likely choice as a fraction of the member's max HP.

    Damaging moves use their max-roll percent; status moves count their
    disruption risk (burn on a physical attacker, sleep, paralysis, ...) so
    a Will-O-Wisp target does not look like a free pivot.
    """
    if choice is None:
        return 0.0
    damage_pressure = choice.damage.max_percent if choice.damage else 0.0
    status_pressure = _status_disruption_risk(choice.move_name, member, calculator)
    return max(damage_pressure, status_pressure)


def _entry_adjusted_member(member: PlannedMember, enemy: PlannedEnemy) -> PlannedMember:
    """Return an evaluation copy of the member as it would look after the enemy's Intimidate."""
    if _normalize(enemy.calc_set().ability) != "intimidate":
        return member
    member_ability = _normalize(member.calc_set().ability)
    if member_ability in INTIMIDATE_IMMUNE_ABILITIES:
        return member
    if member_ability in {"clearbody", "whitesmoke", "fullmetalbody", "mirrorarmor", "hypercutter"}:
        return member
    boosts = dict(member.boosts)
    if member_ability == "contrary":
        boosts["atk"] = min(6, boosts.get("atk", 0) + 1)
    elif member_ability == "simple":
        boosts["atk"] = max(-6, boosts.get("atk", 0) - 2)
    else:
        boosts["atk"] = max(-6, boosts.get("atk", 0) - 1)
    return replace(member, boosts=boosts)


def _entry_adjusted_enemy(enemy: PlannedEnemy, incoming: PlannedMember) -> PlannedEnemy:
    """Return an evaluation copy of the enemy as it would look after the incoming mon's Intimidate."""
    if _normalize(incoming.calc_set().ability) != "intimidate":
        return enemy
    enemy_ability = _normalize(enemy.calc_set().ability)
    if enemy_ability in INTIMIDATE_IMMUNE_ABILITIES:
        return enemy
    if enemy_ability in {"clearbody", "whitesmoke", "fullmetalbody", "mirrorarmor", "hypercutter"}:
        return enemy
    boosts = dict(enemy.boosts)
    if enemy_ability == "contrary":
        boosts["atk"] = min(6, boosts.get("atk", 0) + 1)
    elif enemy_ability == "simple":
        boosts["atk"] = max(-6, boosts.get("atk", 0) - 2)
    else:
        boosts["atk"] = max(-6, boosts.get("atk", 0) - 1)
    return replace(enemy, boosts=boosts)


def _calc_turn(turn: int, enemy: PlannedEnemy, active: PlannedMember, action: str, calc: str, risks: list[str], consistency: str, confidence: float) -> dict[str, Any]:
    return {
        "turn": turn,
        "enemy": enemy.name,
        "answer": active.name,
        "action": action,
        "calc": calc,
        "risks": risks[:6],
        "consistency": consistency,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "your_hp": f"{active.hp}/{active.max_hp}",
        "enemy_hp": f"{enemy.hp}/{enemy.max_hp}",
    }


def _member_payload(member: PlannedMember) -> dict[str, Any]:
    return {
        "name": member.name,
        "species": member.species,
        "slot": member.slot,
        "source": member.source,
        "level": member.level,
        "item": member.item,
        "ability": member.ability,
        "moves": list(member.moves),
        "hp": member.hp,
        "max_hp": member.max_hp,
        "status": member.status,
        "consumed_item": member.consumed_item,
    }


def _enemy_payload(enemy: PlannedEnemy) -> dict[str, Any]:
    return {
        "name": enemy.name,
        "level": enemy.pokemon.level,
        "item": enemy.pokemon.held_item,
        "ability": enemy.pokemon.ability,
        "moves": list(enemy.moves),
        "hp": enemy.hp,
        "max_hp": enemy.max_hp,
    }


def _calc_matchup_table(team: list[PlannedMember], enemies: list[PlannedEnemy], calculator: DamageCalculator) -> list[dict[str, Any]]:
    rows = []
    for member in team:
        for enemy in enemies:
            player_moves = calculator.rank_move_names(member.calc_set(), enemy.calc_set(), member.moves)
            enemy_moves = calculator.rank_move_names(enemy.calc_set(), member.calc_set(), enemy.moves)
            rows.append(
                {
                    "player": member.name,
                    "enemy": enemy.name,
                    "best_move": player_moves[0].move_name if player_moves else "",
                    "damage": _damage_text(player_moves[0]) if player_moves else "0-0%",
                    "enemy_best": enemy_moves[0].move_name if enemy_moves else "",
                    "enemy_damage": _damage_text(enemy_moves[0]) if enemy_moves else "0-0%",
                }
            )
    return rows


def _damage_text(damage: Any) -> str:
    return f"{damage.min_damage}-{damage.max_damage} ({damage.min_percent * 100:.1f}-{damage.max_percent * 100:.1f}%)"


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        await websocket.send_json({"type": "status", "data": await api_status()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


def _run_solve_thread(request: SolveRequest) -> None:
    thread = threading.Thread(target=_solve_worker, args=(request,), daemon=True)
    thread.start()


def _run_prepare_thread(request: PrepareRequest) -> None:
    thread = threading.Thread(target=_prepare_worker, args=(request,), daemon=True)
    thread.start()


def _solve_worker(request: SolveRequest) -> None:
    global display_state, last_result
    cancel_event = _new_cancel_event()
    mcts: MCTS | None = None
    try:
        _sim_proof_update(
            running=True, phase="Exploring candidate lines", pct=1.0,
            completed_replays=0,
            total_replays=max(2, request.final_line_trials) * max(1, request.final_line_candidates),
            video_ready=False, verified=False,
        )
        _set_status(True, "solve", "Running MCTS")
        _validate_paths(request.rom, request.state)
        if request.nuzlocke:
            config.NUZLOCKE_WEIGHT = 8.0
        manager.broadcast_sync({"type": "started", "message": "Solver started"})
        display_state = _broadcast_initial_state(request.rom, request.state)
        mcts = MCTS(
            request.rom,
            request.state,
            pool_size=request.instances,
            max_turns=request.turns,
            trials_per_node=request.trials_per_node,
            final_line_trials=request.final_line_trials,
            final_line_candidates=request.final_line_candidates,
            on_node_visited=_broadcast_mcts_event,
            cancel_event=cancel_event,
            game_mode=request.game_mode,
        )
        _register_mcts(mcts)
        try:
            result = mcts.search(request.iterations)
        finally:
            mcts.shutdown()
            _unregister_mcts(mcts)
        last_result = result
        _broadcast_flowchart(result, display_state)
        _set_status(True, "recording", "Recording recommended line and saving run log")
        manager.broadcast_sync({"type": "video_recording", "message": "Recording the recommended line…"})
        run_log = _record_completed_simulator(request, result, display_state)
        manager.broadcast_sync({"type": "sim_run_log", "data": _sim_run_summary(run_log)})
        manager.broadcast_sync(
            {
                "type": "complete" if run_log.get("proof_complete") else "proof_rejected",
                "data": {
                    "has_deathless_line": result.has_deathless_line,
                    "recommended_line": result.to_dict()["recommended_line"],
                    "battle_plan": _battle_plan(result, display_state),
                    "win_probability": result.win_probability,
                    "faint_probability": result.faint_probability,
                    "total_trials": result.total_trials_run,
                    "search_time": result.search_time_seconds,
                    "validated_lines": result.validated_lines,
                    "run_log": _sim_run_summary(run_log),
                    "proof_complete": bool(run_log.get("proof_complete")),
                },
            }
        )
        _set_status(False, "idle", "")
    except SearchCancelled as exc:
        manager.broadcast_sync({"type": "killed", "message": str(exc)})
        _set_status(False, "idle", "Killed")
    except Exception as exc:
        manager.broadcast_sync({"type": "error", "message": str(exc)})
        _set_status(False, "idle", str(exc))
    finally:
        _clear_cancel_event(cancel_event)


def _prepare_worker(request: PrepareRequest) -> None:
    global display_state, last_result
    cancel_event = _new_cancel_event()
    try:
        _set_status(True, "prepare", "Running box optimizer")
        _validate_paths(request.rom, request.battle_state)
        if not Path(request.pc_state).is_file():
            raise FileNotFoundError(
                f"PC state not found: {request.pc_state}. "
                "Prepare needs battle_state for the fight and pc_state for Pokemon Storage."
            )
        display_state = _read_state(request.rom, request.battle_state)
        result = run_prepare(
            request.rom,
            request.battle_state,
            request.pc_state,
            instances=request.instances,
            iterations=request.iterations,
            nuzlocke=request.nuzlocke,
            on_event=manager.broadcast_sync,
            cancel_event=cancel_event,
            on_mcts_start=_register_mcts,
        )
        with job_lock:
            active_mcts.clear()
        last_result = result
        _broadcast_flowchart(result.baseline, display_state)
        _set_status(False, "idle", "")
    except SearchCancelled as exc:
        manager.broadcast_sync({"type": "killed", "message": str(exc)})
        _set_status(False, "idle", "Killed")
    except Exception as exc:
        manager.broadcast_sync({"type": "error", "message": str(exc)})
        _set_status(False, "idle", str(exc))
    finally:
        with job_lock:
            active_mcts.clear()
        _clear_cancel_event(cancel_event)


def _broadcast_mcts_event(data: dict[str, Any]) -> None:
    if "node" in data:
        manager.broadcast_sync({"type": "node", "data": _humanize_node(data["node"], display_state)})
    if "simulators" in data:
        manager.broadcast_sync(
            {
                "type": "simulators",
                "data": [_humanize_simulator(item, display_state) for item in data["simulators"]],
            }
        )
    if "progress" in data:
        manager.broadcast_sync({"type": "progress", "data": data["progress"]})
        progress = data["progress"]
        message = str(progress.get("message") or "")
        if message.startswith("Confidence check"):
            finished_lines = max(0, int(progress.get("iterations_done") or 0))
            total_lines = max(1, int(progress.get("iterations_total") or 1))
            completed = min(
                int(_SIM_PROOF_PROGRESS.get("total_replays") or 0),
                finished_lines * max(2, int(_SIM_PROOF_PROGRESS.get("total_replays") or 0) // total_lines),
            )
            _sim_proof_update(
                running=True,
                phase=f"Running candidate line {finished_lines}/{total_lines} in the game",
                pct=35.0 + 50.0 * min(1.0, finished_lines / total_lines),
                completed_replays=completed,
            )
        else:
            done = max(0, int(progress.get("iterations_done") or 0))
            total = max(1, int(progress.get("iterations_total") or 1))
            _sim_proof_update(
                running=True, phase="Exploring candidate lines",
                pct=max(float(_SIM_PROOF_PROGRESS.get("pct") or 0.0), 35.0 * min(1.0, done / total)),
            )
    if "validation" in data:
        manager.broadcast_sync({"type": "validation", "data": data["validation"]})


def _read_state(rom_path: str, state_path: str) -> BattleState:
    instance = MGBAInstance(rom_path, state_path, 68)
    try:
        return StateReader(instance).read()
    finally:
        instance.shutdown()


def _live_battle_payload(state: BattleState, match: Any, screen: dict[str, Any] | None = None) -> dict[str, Any]:
    trainer = None
    if match is not None:
        trainer = {
            "name": match.battle.trainer_name,
            "location": match.battle.location or match.battle.section,
            "is_double": match.battle.is_double,
            "hp_error": match.hp_error,
            "confidence": "exact" if match.hp_error == 0 else "close",
            "party": [
                {
                    "species": mon.species,
                    "level": mon.level,
                    "moves": list(mon.moves),
                    "item": mon.held_item,
                }
                for mon in match.battle.party
            ],
        }
    player_slot = state.player_active_slots[0] if state.player_active_slots else 0
    enemy_slot = state.enemy_active_slots[0] if state.enemy_active_slots else 0
    return {
        "recognized": trainer is not None,
        "trainer": trainer,
        "is_doubles": state.is_doubles,
        "player": {
            "active_slot": player_slot,
            "name": state.player_names[player_slot] if player_slot is not None and player_slot < len(state.player_names) else "Unknown",
            "moves": list(state.player_move_names),
            "move_ids": list(state.player_move_ids),
            "hp": list(state.player_hp),
            "max_hp": list(state.player_max_hp),
            "party": list(state.player_names),
        },
        "enemy": {
            "active_slot": enemy_slot,
            "name": state.enemy_names[enemy_slot] if enemy_slot is not None and enemy_slot < len(state.enemy_names) else "Unknown",
            "moves": list(state.enemy_move_names),
            "move_ids": list(state.enemy_move_ids),
            "hp": list(state.enemy_hp),
            "max_hp": list(state.enemy_max_hp),
            "party": list(state.enemy_names),
        },
        "screen": screen,
    }


def _broadcast_initial_state(rom_path: str, state_path: str) -> BattleState:
    state = _read_state(rom_path, state_path)
    legal_actions = len(ActionEnumerator().legal_actions(state))
    manager.broadcast_sync(
        {
            "type": "battle_state",
            "data": {
                "player_hp": state.player_hp,
                "player_max_hp": state.player_max_hp,
                "enemy_hp": state.enemy_hp,
                "enemy_max_hp": state.enemy_max_hp,
                "player_names": state.player_names,
                "enemy_names": state.enemy_names,
                "player_move_names": state.player_move_names,
                "player_move_names_by_slot": state.player_move_names_by_slot,
                "player_move_ids": state.player_move_ids,
                "enemy_move_names": state.enemy_move_names,
                "enemy_move_ids": state.enemy_move_ids,
                "player_active_slots": state.player_active_slots,
                "enemy_active_slots": state.enemy_active_slots,
                "player_species": state.player_species,
                "enemy_species": state.enemy_species,
                "is_doubles": state.is_doubles,
                "legal_actions": legal_actions,
            },
        }
    )
    return state


def _broadcast_flowchart(result: SearchResult, state: BattleState | None) -> None:
    recommended_ids = set()
    prefix = []
    for turn_action in result.recommended_line:
        prefix.append(turn_action)
        recommended_ids.add(_action_id(prefix, state))
    nodes = _flatten_flowchart(result.flowchart, state=state)
    for node in nodes:
        node["is_recommended"] = node["id"] in recommended_ids
    manager.broadcast_sync({"type": "flowchart", "data": nodes})


def _battle_plan(result: SearchResult, state: BattleState | None) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    active_slot = 0
    snapshots = {snapshot.turn: snapshot for snapshot in result.projected_turns}
    for turn_number, turn_action in enumerate(result.recommended_line, start=1):
        action_text, active_slot = _format_plan_turn(turn_action, state, active_slot)
        snapshot = snapshots.get(turn_number)
        plan.append(
            {
                "turn": turn_number,
                "action": action_text,
                "outcome": _format_turn_outcome(snapshot, state) if snapshot else None,
                "player_hp": list(snapshot.player_hp) if snapshot else None,
                "enemy_hp": list(snapshot.enemy_hp) if snapshot else None,
                "player_fainted": list(snapshot.player_fainted) if snapshot else None,
                "enemy_fainted": list(snapshot.enemy_fainted) if snapshot else None,
                "battle_over": snapshot.battle_over if snapshot else False,
                "player_won": snapshot.player_won if snapshot else False,
                "screen": {
                    "width": snapshot.screen_width,
                    "height": snapshot.screen_height,
                    "rgba_base64": snapshot.screen_rgba_base64,
                }
                if snapshot and snapshot.screen_rgba_base64
                else None,
            }
        )
    return plan


def _format_turn_outcome(snapshot: Any, state: BattleState | None) -> str:
    player = _hp_summary(
        "Your side",
        snapshot.player_hp,
        snapshot.player_max_hp,
        snapshot.player_fainted,
        [_player_name(state, index) for index in range(len(snapshot.player_hp))],
    )
    enemy = _hp_summary(
        "Enemy side",
        snapshot.enemy_hp,
        snapshot.enemy_max_hp,
        snapshot.enemy_fainted,
        [_enemy_name(state, index) for index in range(len(snapshot.enemy_hp))],
    )
    suffix = ""
    if snapshot.battle_over:
        suffix = " · Battle won" if snapshot.player_won else " · Battle lost"
    return f"{player} · {enemy}{suffix}"


def _hp_summary(label: str, hp: list[int], max_hp: list[int], fainted: list[bool], names: list[str]) -> str:
    parts: list[str] = []
    for index, max_value in enumerate(max_hp):
        if max_value <= 0:
            continue
        name = names[index] if index < len(names) else f"slot {index}"
        current = hp[index] if index < len(hp) else 0
        if index < len(fainted) and fainted[index]:
            parts.append(f"{name} fainted")
        else:
            parts.append(f"{name} {current}/{max_value}")
    return f"{label}: " + (", ".join(parts) if parts else "unknown")


def _format_plan_turn(
    turn_action: Any,
    state: BattleState | None,
    active_slot: int,
) -> tuple[str, int]:
    actions = (turn_action,) if isinstance(turn_action, Action) else turn_action
    current_active = active_slot
    labels: list[str] = []
    for action in actions:
        if action.is_move:
            actor_party_slot = _field_actor_party_slot(state, action.actor_slot, current_active)
            target = ""
            if action.target_slot is not None:
                target = f" on {_enemy_name(state, action.target_slot)}"
            labels.append(
                f"Use {_move_name(state, action.move_slot, actor_party_slot)}{target} "
                f"with {_player_name(state, actor_party_slot)}"
            )
        elif action.is_switch:
            labels.append(f"Switch to {_player_name(state, action.switch_target)}")
            if action.switch_target is not None:
                current_active = action.switch_target
        else:
            labels.append(action.kind.replace("_", " "))
    return "; then ".join(labels), current_active


def _flatten_flowchart(
    root: Any,
    parent_id: str | None = None,
    path: list[Any] | None = None,
    state: BattleState | None = None,
) -> list[dict[str, Any]]:
    path = path or []
    active_slot = _active_slot_after(path)
    current_path = path + ([root.action] if root.action else [])
    node_id = _action_id(current_path, state) if root.action else "root"
    data = {
        "id": node_id,
        "parent_id": parent_id,
        "turn": root.turn,
        "action": _format_action(root.action, state, active_slot),
        "win_rate": root.win_rate,
        "faint_rate": root.faint_rate,
        "avg_hp": root.avg_hp,
        "visit_count": root.visit_count,
        "label": root.line_label,
        "branch_condition": _humanize_text(root.branch_condition, state),
        "is_recommended": False,
    }
    items = [data]
    for child in root.children:
        items.extend(_flatten_flowchart(child, node_id, current_path, state))
    return items


def _humanize_node(node: dict[str, Any], state: BattleState | None) -> dict[str, Any]:
    return {
        **node,
        "action": _humanize_text(str(node.get("action", "")), state),
        "branch_condition": _humanize_text(str(node.get("branch_condition", "")), state),
    }


def _humanize_simulator(item: dict[str, Any], state: BattleState | None) -> dict[str, Any]:
    return {
        **item,
        "actions": [_humanize_text(str(action), state) for action in item.get("actions", [])],
        "reason": _humanize_text(str(item.get("reason", "")), state),
    }


def _format_action(action: Any, state: BattleState | None = None, active_slot: int = 0) -> str:
    if action is None:
        return "BATTLE START"
    if isinstance(action, Action):
        if action.is_move:
            actor_slot = _field_actor_party_slot(state, action.actor_slot, active_slot)
            target = ""
            if action.target_slot is not None:
                target = f" on {_enemy_name(state, action.target_slot)}"
            return f"{_player_name(state, actor_slot)} uses {_move_name(state, action.move_slot, actor_slot)}{target}"
        if action.is_switch:
            return f"Switch to {_player_name(state, action.switch_target)}"
        return action.kind
    parts = []
    current_active = active_slot
    is_multi_actor = len(action) > 1
    for index, item in enumerate(action):
        actor_slot = _field_actor_party_slot(
            state,
            item.actor_slot if item.actor_slot is not None else (index if is_multi_actor and item.is_move else None),
            current_active,
        )
        if item.is_move:
            target = ""
            if item.target_slot is not None:
                target = f" on {_enemy_name(state, item.target_slot)}"
            parts.append(f"{_player_name(state, actor_slot)} uses {_move_name(state, item.move_slot, actor_slot)}{target}")
        elif item.is_switch:
            parts.append(f"Switch to {_player_name(state, item.switch_target)}")
            if item.switch_target is not None:
                current_active = item.switch_target
        else:
            parts.append(item.kind)
    return " + ".join(parts)


def _action_id(line: list[Any], state: BattleState | None = None) -> str:
    parts = []
    active_slot = 0
    for turn_action in line:
        if turn_action is None:
            continue
        parts.append(_format_action(turn_action, state, active_slot).replace(" ", "-"))
        actions = (turn_action,) if isinstance(turn_action, Action) else turn_action
        for action in actions:
            if action.is_switch and action.switch_target is not None:
                active_slot = action.switch_target
    return "n-" + "-".join(parts) if parts else "root"


def _humanize_text(text: str, state: BattleState | None) -> str:
    text = re.sub(
        r"Move slot (\d+)(?: -> enemy)?",
        lambda match: f"{_active_name(state)} uses {_move_name(state, int(match.group(1)), 0)}",
        text,
    )
    text = re.sub(
        r"Switch slot (\d+)",
        lambda match: f"Switch to {_player_name(state, int(match.group(1)))}",
        text,
    )
    text = re.sub(
        r"slot (\d+)",
        lambda match: _player_name(state, int(match.group(1))),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"target (\d+)",
        lambda match: _enemy_name(state, int(match.group(1))),
        text,
        flags=re.IGNORECASE,
    )
    return text


def _active_slot_after(path: list[Any]) -> int:
    active_slot = 0
    for turn_action in path:
        if turn_action is None:
            continue
        actions = (turn_action,) if isinstance(turn_action, Action) else turn_action
        for action in actions:
            if action.is_switch and action.switch_target is not None:
                active_slot = action.switch_target
    return active_slot


def _move_name(state: BattleState | None, move_slot: int | None, active_slot: int = 0) -> str:
    if move_slot is None:
        return "unknown move"
    if state and 0 <= active_slot < len(state.player_move_names_by_slot):
        slot_names = state.player_move_names_by_slot[active_slot]
        if 0 <= move_slot < len(slot_names):
            name = slot_names[move_slot].strip()
            if name:
                return name
    if active_slot == 0 and state and 0 <= move_slot < len(state.player_move_names):
        name = state.player_move_names[move_slot].strip()
        if name:
            return name
    return f"unknown move ({_move_position_name(move_slot)})"


def _active_name(state: BattleState | None) -> str:
    return _player_name(state, 0)


def _player_name(state: BattleState | None, party_slot: int | None) -> str:
    if party_slot is None:
        return "your Pokemon"
    if state and 0 <= party_slot < len(state.player_names):
        name = state.player_names[party_slot].strip()
        if name:
            return name
    return f"your {_party_position_name(party_slot)} Pokemon"


def _field_actor_party_slot(
    state: BattleState | None,
    field_slot: int | None,
    fallback: int = 0,
) -> int:
    if (
        state is not None
        and field_slot is not None
        and 0 <= field_slot < len(state.player_active_slots)
        and state.player_active_slots[field_slot] is not None
    ):
        return int(state.player_active_slots[field_slot])
    return fallback


def _enemy_name(state: BattleState | None, enemy_slot: int | None) -> str:
    if enemy_slot is None:
        return "the enemy"
    if (
        state is not None
        and 0 <= enemy_slot < len(state.enemy_active_slots)
        and state.enemy_active_slots[enemy_slot] is not None
    ):
        enemy_slot = int(state.enemy_active_slots[enemy_slot])
    if state and 0 <= enemy_slot < len(state.enemy_names):
        name = state.enemy_names[enemy_slot].strip()
        if name:
            return name
    return f"the {_enemy_position_name(enemy_slot)} enemy"


def _move_position_name(index: int) -> str:
    return ["top move", "second move", "third move", "bottom move"][index] if 0 <= index < 4 else "unknown move"


def _party_position_name(index: int) -> str:
    names = ["lead", "second", "third", "fourth", "fifth", "sixth"]
    return names[index] if 0 <= index < len(names) else "backup"


def _enemy_position_name(index: int) -> str:
    names = ["first", "second", "third", "fourth", "fifth", "sixth"]
    return names[index] if 0 <= index < len(names) else "opposing"


def _new_cancel_event() -> threading.Event:
    global current_cancel_event
    event = threading.Event()
    with job_lock:
        current_cancel_event = event
    return event


def _clear_cancel_event(event: threading.Event) -> None:
    global current_cancel_event
    with job_lock:
        if current_cancel_event is event:
            current_cancel_event = None


def _register_mcts(mcts: MCTS) -> None:
    with job_lock:
        active_mcts.add(mcts)


def _unregister_mcts(mcts: MCTS) -> None:
    with job_lock:
        active_mcts.discard(mcts)


def _kill_current_job() -> bool:
    with job_lock:
        event = current_cancel_event
        mcts_items = list(active_mcts)
    if event is None and not mcts_items:
        return False
    if event is not None:
        event.set()
    for mcts in mcts_items:
        try:
            mcts.cancel()
        except Exception:
            pass
    _set_status(True, "killing", "Killing simulations")
    with status_lock:
        payload = dict(status)
    manager.broadcast_sync({"type": "status", "data": payload})
    return True


def _set_status(running: bool, phase: str, message: str) -> None:
    with status_lock:
        status.update({"running": running, "phase": phase, "message": message})


def _ensure_idle() -> None:
    with status_lock:
        if status["running"]:
            raise HTTPException(status_code=409, detail="Solver already running")


def _validate_paths(rom: str, state: str) -> None:
    if not Path(rom).is_file():
        raise FileNotFoundError(f"ROM not found: {rom}")
    if not Path(state).is_file():
        raise FileNotFoundError(f"State not found: {state}")
