"""Record one uninterrupted manifest-driven cartridge gauntlet.

The emulator is started once.  No savestate is loaded after recording begins:
victory dialogue, walking, Center healing, and every next trainer are visible in
the same A/V stream.  Any divergence, faint, missed heal, or wrong trainer aborts
the artifact instead of publishing partial evidence.
"""

from __future__ import annotations

import argparse
from base64 import b64decode
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from battle.action import Action
from emulator.game_state import GameMode, WholeGameStateReader
from emulator.input_controller import InputController
from emulator.mgba_instance import MGBAInstance
from emulator.overworld import OverworldMover
from emulator.preparation import TeamSlotRequest, prepare_party
from emulator.box_controller import BoxController


def _party_deaths(state) -> list[int]:
    """Party slots with a real Pokemon at zero HP (the ROM faint flags are transient)."""
    return [
        index for index, (hp, max_hp) in enumerate(zip(state.player_hp, state.player_max_hp))
        if max_hp > 0 and hp <= 0
    ]


def _fragile_party_slots(state) -> list[int]:
    """Living answers stranded at exactly 1 HP while the battle is still active."""
    if state.battle_over:
        return []
    return [
        index for index, (hp, max_hp) in enumerate(zip(state.player_hp, state.player_max_hp))
        if max_hp > 0 and hp == 1
    ]
from emulator.state_reader import StateReader


GBA_FPS = "16777216/280896"


def _actions_and_delays(paths: list[str]) -> tuple[list[tuple[Action, ...]], list[int]]:
    actions: list[tuple[Action, ...]] = []
    delays: list[int] = []
    for raw_path in paths:
        payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        raw_line = payload.get("line") or payload.get("result", {}).get("line") or []
        raw_delays = payload.get("delays") or [0] * len(raw_line)
        if len(raw_delays) != len(raw_line):
            raise RuntimeError(f"Delay count does not match line in {raw_path}")
        for turn in raw_line:
            actions.append(tuple(Action(**item) for item in turn))
        delays.extend(int(value) for value in raw_delays)
    return actions, delays


def _directions(path: str) -> list[str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [str(value).upper() for value in payload["directions"]]


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _load_playbook(path: str) -> dict:
    payload = json.loads(_project_path(path).read_text(encoding="utf-8"))
    trainers = payload.get("trainers") or []
    transitions = payload.get("transitions") or []
    if len(trainers) < 1 or len(transitions) != len(trainers) - 1:
        raise RuntimeError("Playbook must contain one transition between every trainer")
    return payload


def _team_requests(payload: dict) -> list[TeamSlotRequest]:
    requests: list[TeamSlotRequest] = []
    for member in payload.get("party") or []:
        if member.get("source") == "party":
            requests.append(TeamSlotRequest.party(int(member["slot"])))
        elif member.get("source") == "box":
            box = int(member["box"])
            if box == config.NUZLOCKE_GRAVEYARD_BOX:
                raise RuntimeError("Playbook attempted to use the Nuzlocke graveyard")
            requests.append(TeamSlotRequest.box_mon(box, int(member["slot"])))
        else:
            raise RuntimeError(f"Unknown preparation source: {member.get('source')!r}")
    return requests


def _reach_battle(
    instance: MGBAInstance,
    reader: WholeGameStateReader,
    trainer: str,
) -> None:
    for index in range(240):
        snapshot = reader.read()
        if snapshot.mode == GameMode.BATTLE_COMMAND:
            actual = snapshot.trainer_name or ""
            if trainer.casefold() not in actual.casefold():
                raise RuntimeError(f"Expected {trainer!r}, reached {actual!r}")
            return
        instance.send_input("A" if index % 3 == 0 else "B", 1)
        instance.advance_frames(18)
    raise RuntimeError(f"Dialogue never reached {trainer}'s battle command")


def _settle_overworld(instance: MGBAInstance, reader: WholeGameStateReader) -> None:
    stable = 0
    for _ in range(500):
        instance.send_input("B", 1)
        instance.advance_frames(12)
        stable = stable + 1 if reader.read().mode == GameMode.OVERWORLD else 0
        if stable >= 12:
            return
    raise RuntimeError("Victory dialogue never settled into the overworld")


def _walk(
    instance: MGBAInstance,
    mover: OverworldMover,
    directions: list[str],
) -> None:
    for direction in directions:
        mover.step(direction)
        instance.advance_frames(30)


def _heal(
    instance: MGBAInstance,
    mover: OverworldMover,
    reader: WholeGameStateReader,
) -> None:
    arrived = reader.read()
    if arrived.map_id != (6, 4) or (arrived.x, arrived.y) != (14, 15):
        raise RuntimeError(
            f"Center route ended at {arrived.map_id} {(arrived.x, arrived.y)}"
        )
    mover.walk("UP", 4)
    for _ in range(10):
        mover.tap("A", settle_frames=100)
    instance.advance_frames(180)
    healed = reader.read()
    if tuple(healed.player_hp) != tuple(healed.player_max_hp):
        raise RuntimeError(f"Nurse did not heal the party: {healed.player_hp}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--post-state", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument(
        "--playbook",
        default="config/gauntlet_playbooks/corgi_to_chelle.json",
    )
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    root = ROOT / "output" / "gauntlet_runs"
    playbook = _load_playbook(args.playbook)
    battles = [
        (str(entry["name"]), [_project_path(path) for path in entry.get("lines") or []])
        for entry in playbook["trainers"]
    ]
    routes = list(playbook["transitions"])
    if args.validate_only:
        missing = [
            str(path) for _trainer, paths in battles for path in paths if not path.is_file()
        ]
        missing.extend(
            str(_project_path(transition[key]))
            for transition in routes for key in ("to_center", "to_trainer")
            if not _project_path(transition[key]).is_file()
        )
        if missing:
            raise RuntimeError(f"Playbook references missing files: {missing}")
        print(json.dumps({
            "playbook": playbook.get("id"), "trainers": len(battles),
            "transitions": len(routes), "valid": True,
        }, indent=2))
        return

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="rnb-full-gauntlet-") as temp_dir:
        temp = Path(temp_dir)
        video_fifo = temp / "video.rgba"
        audio_raw = temp / "audio.s16le"
        video_only = temp / "video.mp4"
        os.mkfifo(video_fifo)
        encoder = subprocess.Popen([
            "/opt/homebrew/bin/ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", "240x160",
            "-framerate", GBA_FPS, "-i", str(video_fifo),
            "-vf", "scale=960:640:flags=neighbor,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-movflags", "+faststart", str(video_only),
        ])
        recording_info = None
        try:
            with MGBAInstance(args.rom, args.state, 96) as instance:
                game_reader = WholeGameStateReader(instance)
                battle_reader = StateReader(instance)
                controller = InputController(instance, battle_reader, stop_on_player_faint=True)
                mover = OverworldMover(instance)
                instance.start_recording(video_fifo, audio_raw)
                try:
                    for battle_index, (trainer, manifest_paths) in enumerate(battles):
                        _reach_battle(instance, game_reader, trainer)
                        # Diagnostic checkpoints are writes only.  The uninterrupted
                        # proof run never loads them, but a rejected legacy segment can
                        # be re-solved from the exact connected state it actually reached.
                        checkpoint_name = "".join(
                            character.lower() if character.isalnum() else "-"
                            for character in trainer
                        ).strip("-")
                        instance.save_state(root / f"{checkpoint_name}-live-prebattle.ss0")
                        line, delays = _actions_and_delays([str(path) for path in manifest_paths])
                        events.append({
                            "event": "battle_start", "trainer": trainer,
                            "party_hp": list(battle_reader.read().player_hp),
                            "turns_planned": len(line),
                        })
                        for turn_number, (turn, delay) in enumerate(zip(line, delays), 1):
                            before = battle_reader.read()
                            if before.battle_over:
                                break
                            if delay:
                                instance.send_input("B", delay)
                            after = controller.execute_turn(list(turn))
                            deaths = _party_deaths(after)
                            if deaths:
                                raise RuntimeError(
                                    f"{trainer} turn {turn_number} caused a Nuzlocke death in party slot(s) {deaths}"
                                )
                            fragile = _fragile_party_slots(after)
                            if fragile:
                                raise RuntimeError(
                                    f"{trainer} turn {turn_number} repeatedly left an answer at exactly 1 HP "
                                    f"in party slot(s) {fragile}; search a safer box answer"
                                )
                            events.append({
                                "event": "turn", "trainer": trainer,
                                "turn": turn_number, "delay": delay,
                                "player_hp": list(after.player_hp),
                                "enemy_hp": list(after.enemy_hp),
                            })
                        final = battle_reader.read()
                        if not final.battle_over or not final.player_won:
                            raise RuntimeError(
                                f"{trainer} did not reproduce its victory after {len(line)} actions"
                            )
                        if _party_deaths(final):
                            raise RuntimeError(f"{trainer} victory included a Nuzlocke death")
                        events.append({
                            "event": "battle_won", "trainer": trainer,
                            "party_hp": list(final.player_hp),
                        })
                        _settle_overworld(instance, game_reader)
                        if battle_index == len(battles) - 1:
                            instance.save_state(args.post_state)
                            break

                        transition = routes[battle_index]
                        _walk(instance, mover, _directions(str(_project_path(transition["to_center"]))))
                        _heal(instance, mover, game_reader)
                        healed = game_reader.read()
                        instance.save_state(root / f"after-{checkpoint_name}-live-healed.ss0")
                        events.append({
                            "event": "center_heal", "after": trainer,
                            "party_hp": list(healed.player_hp),
                            "map": list(healed.map_id or ()),
                            "position": [healed.x, healed.y],
                        })
                        preparation = transition.get("preparation")
                        if preparation:
                            before_party_hp = list(game_reader.read().player_hp)
                            # The nurse leaves the player at (14, 11). Walk to the actual PC,
                            # show the live Storage UI in the uncut recording, then return to
                            # the same route origin after the verified atomic swap.
                            pc_out = ["DOWN", "DOWN"] + ["RIGHT"] * 6
                            pc_back = ["LEFT"] * 6 + ["UP", "UP"]
                            _walk(instance, mover, pc_out)
                            BoxController(instance).show_storage_visit()
                            report = prepare_party(
                                instance,
                                _team_requests(preparation),
                                allow_item_donor_boxes={
                                    int(box) for box in preparation.get("item_donor_boxes") or []
                                    if int(box) != config.NUZLOCKE_GRAVEYARD_BOX
                                },
                            )
                            events.append({
                                "event": "center_party_preparation",
                                "after": trainer,
                                "visible_pc_visit": True,
                                "party_hp_before": before_party_hp,
                                "party": [member.display_name for member in report.party],
                                "box_swaps": [list(value) for value in report.moved_from_boxes],
                                "item_changes": list(report.item_changes),
                                "inventory_before": report.inventory_before,
                                "inventory_after": report.inventory_after,
                                "graveyard_box_used": False,
                            })
                            # Show the newly prepared roster in the real party menu before
                            # continuing, so the video and JSON log prove the same team.
                            instance.send_input("START", 3)
                            instance.advance_frames(45)
                            instance.send_input("DOWN", 3)
                            instance.advance_frames(12)
                            instance.send_input("A", 3)
                            instance.advance_frames(120)
                            instance.send_input("B", 3)
                            instance.advance_frames(35)
                            instance.send_input("B", 3)
                            instance.advance_frames(35)
                            _walk(instance, mover, pc_back)
                        _walk(instance, mover, _directions(str(_project_path(transition["to_trainer"]))))
                        final_direction = transition.get("final_direction")
                        if final_direction:
                            mover.step(final_direction)
                            instance.advance_frames(30)
                finally:
                    recording_info = instance.stop_recording()
        except Exception:
            if encoder.poll() is None:
                encoder.terminate()
            raise

        if encoder.wait(timeout=180) != 0:
            raise RuntimeError("Video encoder failed")
        if not recording_info or recording_info["audio_rate"] <= 0:
            raise RuntimeError("Emulator did not report a valid audio rate")
        subprocess.run([
            "/opt/homebrew/bin/ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video_only),
            "-f", "s16le", "-ar", str(recording_info["audio_rate"]), "-ac", "2",
            "-i", str(audio_raw), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", str(output),
        ], check=True)

    log_payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "cartridge-verified",
        "proof_complete": True,
        "uncut": True,
        "savestate_loads_after_start": 0,
        "graveyard_box": 14,
        "graveyard_used": False,
        "trainers": [name for name, _ in battles],
        "playbook": playbook.get("id"),
        "video": str(output),
        "post_state": str(Path(args.post_state).expanduser().resolve()),
        "recording": recording_info,
        "events": events,
    }
    log_path.write_text(json.dumps(log_payload, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(output), "log": str(log_path),
        "battles": len(battles), "proof_complete": True,
        **recording_info,
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
