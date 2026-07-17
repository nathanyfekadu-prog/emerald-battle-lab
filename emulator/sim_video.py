"""Plain, persistent video capture for completed simulator lines."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from battle.action import Action
from emulator.game_state import GameMode, WholeGameStateReader
from emulator.input_controller import InputController
from emulator.mgba_instance import MGBAInstance
from emulator.state_reader import StateReader

GBA_FPS = "16777216/280896"
FFMPEG = "/opt/homebrew/bin/ffmpeg"


def compose_split_screen(left: Path, right: Path, output: Path) -> Path:
    """Place two uninterrupted gameplay captures side by side with no title card."""
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        FFMPEG, "-y", "-loglevel", "error", "-i", str(left), "-i", str(right),
        "-filter_complex",
        "[0:v]scale=960:640:flags=neighbor,setsar=1[l];"
        "[1:v]scale=960:640:flags=neighbor,setsar=1[r];"
        "[l][r]hstack=inputs=2[v]",
        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(output),
    ], check=True)
    return output


def _action_label(action: Action, state: Any) -> str:
    actor_index = action.actor_slot or 0
    party_slot = state.player_active_slots[actor_index] if actor_index < len(state.player_active_slots) else 0
    actor = state.player_names[party_slot] if party_slot is not None and party_slot < len(state.player_names) else "Pokemon"
    if action.is_switch:
        target = action.switch_target
        name = state.player_names[target] if target is not None and target < len(state.player_names) else f"slot {target}"
        return f"{actor} → {name}"
    moves = state.player_move_names_by_slot[party_slot] if party_slot is not None and party_slot < len(state.player_move_names_by_slot) else []
    name = moves[action.move_slot] if action.move_slot is not None and action.move_slot < len(moves) else f"move {action.move_slot}"
    return f"{actor}: {name}"


def record_simulator_line(
    rom: str,
    state_path: str,
    line: list[tuple[Action, ...]],
    output: Path,
    *,
    instance_id: int = 91,
    rng_pre_roll_frames: int = 0,
) -> dict[str, Any]:
    """Record only the actual game, from command menu through the result dialogue."""
    output.parent.mkdir(parents=True, exist_ok=True)
    turns: list[dict[str, Any]] = []
    error: str | None = None
    final = None
    with tempfile.TemporaryDirectory(prefix="rnb-sim-video-") as raw_dir:
        temp = Path(raw_dir)
        video_fifo = temp / "video.rgba"
        audio_raw = temp / "audio.s16le"
        video_only = temp / "video.mp4"
        os.mkfifo(video_fifo)
        encoder = subprocess.Popen([
            FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo",
            "-pixel_format", "rgba", "-video_size", "240x160", "-framerate", GBA_FPS,
            "-i", str(video_fifo), "-vf", "scale=960:640:flags=neighbor,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(video_only),
        ])
        info = None
        with MGBAInstance(rom, state_path, instance_id) as instance:
            reader = StateReader(instance)
            game_reader = WholeGameStateReader(instance)
            controller = InputController(instance, reader)
            # Search may run at unlimited wall-clock speed. This pre-roll restores
            # the selected confidence-pass RNG branch before capture begins; it is
            # not a cut inside the recording.
            if rng_pre_roll_frames:
                instance.send_input("B", rng_pre_roll_frames)
            instance.start_recording(video_fifo, audio_raw)
            try:
                instance.advance_frames(60)
                for number, turn in enumerate(line, 1):
                    before = reader.read()
                    if before.battle_over:
                        break
                    labels = [_action_label(action, before) for action in turn]
                    try:
                        after = controller.execute_turn(list(turn))
                    except Exception as exc:
                        error = str(exc)
                        break
                    turns.append({
                        "turn": number, "actions": labels,
                        "player_hp": list(after.player_hp), "enemy_hp": list(after.enemy_hp),
                    })
                final = reader.read()
                if final.battle_over and final.player_won:
                    stable = 0
                    for _ in range(500):
                        instance.send_input("B", 1)
                        instance.advance_frames(10)
                        stable = stable + 1 if game_reader.read().mode == GameMode.OVERWORLD else 0
                        if stable >= 12:
                            break
                instance.advance_frames(60)
            finally:
                info = instance.stop_recording()
        if encoder.wait(timeout=180) != 0:
            raise RuntimeError("Game video encoder failed")
        if not info or info["audio_rate"] <= 0:
            raise RuntimeError("The emulator did not capture audio")
        subprocess.run([
            FFMPEG, "-y", "-loglevel", "error", "-i", str(video_only),
            "-f", "s16le", "-ar", str(info["audio_rate"]), "-ac", "2", "-i", str(audio_raw),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-shortest", "-movflags", "+faststart", str(output),
        ], check=True)
    won = bool(final and final.battle_over and final.player_won)
    player_faints = sum(final.player_fainted) if final else 0
    return {
        "status": "won" if won else "incomplete" if not error else "recording_error",
        "won": won, "deathless": won and player_faints == 0,
        "player_faints": player_faints, "turns": turns,
        "final_player_hp": list(final.player_hp) if final else [],
        "final_enemy_hp": list(final.enemy_hp) if final else [],
        "error": error, "video_frames": info["video_frames"],
    }
