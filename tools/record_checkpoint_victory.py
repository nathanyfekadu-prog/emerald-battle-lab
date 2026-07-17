"""Replay a winning checkpoint line and produce a normal-speed MP4 with cartridge audio."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from battle.action import Action
from emulator.game_state import GameMode, WholeGameStateReader
from emulator.input_controller import InputController
from emulator.mgba_instance import MGBAInstance
from emulator.state_reader import StateReader


GBA_FPS = "16777216/280896"


def _action(data: dict) -> Action:
    return Action(**{key: data.get(key) for key in (
        "kind", "actor_slot", "move_slot", "target_slot", "switch_target"
    )})


def _checkpoint_delays(manifest: dict, pool_size: int) -> list[int]:
    """Recreate the visible menu waits used by a sampled-RNG checkpoint beam."""
    result = manifest["result"]
    line = result["line"]
    paths: list[str] = []
    for depth in range(1, len(line)):
        prefix = line[:depth]
        node = next(
            item
            for item in manifest["depths"][depth - 1]["frontier"]
            if item["line"] == prefix
        )
        paths.append(node["state_path"])
    paths.append(result["state_path"])
    delays: list[int] = []
    for path in paths:
        match = re.search(r"-t(\d+)\.ss0$", path)
        if not match:
            raise RuntimeError(f"Cannot recover trial timing from {path}")
        delays.append((int(match.group(1)) % pool_size) * 7)
    return delays


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--intro-state", required=True)
    parser.add_argument("--manifest", required=True, action="append")
    parser.add_argument("--output", required=True)
    parser.add_argument("--post-state", required=True)
    parser.add_argument(
        "--trainer",
        help="Expected trainer name (case-insensitive substring).",
    )
    parser.add_argument("--replay-worker-delays", type=int, metavar="POOL_SIZE")
    parser.add_argument(
        "--delays-json",
        action="append",
        help="JSON list (or object with a delays list) of visible per-turn wait frames.",
    )
    args = parser.parse_args()

    manifests = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.manifest]
    for manifest in manifests:
        if manifest.get("status") not in {"won", "continuous-timed"}:
            raise RuntimeError("Manifest does not contain a verified win")
    raw_line = [
        turn
        for manifest in manifests
        for turn in (manifest.get("line") or manifest.get("result", {}).get("line") or [])
    ]
    if not raw_line:
        raise RuntimeError("Manifests do not contain a replay line")
    line = [tuple(_action(item) for item in turn) for turn in raw_line]
    if args.delays_json:
        delays = []
        for path in args.delays_json:
            delay_payload = json.loads(Path(path).read_text(encoding="utf-8"))
            values = delay_payload.get("delays") if isinstance(delay_payload, dict) else delay_payload
            if not isinstance(values, list):
                raise ValueError("delays JSON must contain a list")
            delays.extend(values)
        if not isinstance(delays, list) or len(delays) != len(line):
            raise ValueError("delays JSON must contain one integer for every turn")
        delays = [int(value) for value in delays]
    else:
        delays = (
            _checkpoint_delays(manifests[0], args.replay_worker_delays)
            if args.replay_worker_delays and len(manifests) == 1
            else [0] * len(line)
        )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="rnb-record-") as temp_dir:
        temp = Path(temp_dir)
        video_fifo = temp / "video.rgba"
        audio_raw = temp / "audio.s16le"
        video_only = temp / "video.mp4"
        os.mkfifo(video_fifo)
        video_encoder = subprocess.Popen([
            "/opt/homebrew/bin/ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", "240x160",
            "-framerate", GBA_FPS, "-i", str(video_fifo),
            "-vf", "scale=960:640:flags=neighbor,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-movflags", "+faststart", str(video_only),
        ])

        recording_info: dict[str, int] | None = None
        with MGBAInstance(args.rom, args.intro_state, 97) as instance:
            game_reader = WholeGameStateReader(instance)
            battle_reader = StateReader(instance)
            controller = InputController(instance, battle_reader)
            instance.start_recording(video_fifo, audio_raw)
            try:
                for _ in range(100):
                    snapshot = game_reader.read()
                    if snapshot.mode == GameMode.BATTLE_COMMAND:
                        break
                    instance.send_input("A", 1)
                    instance.advance_frames(18)
                else:
                    raise RuntimeError("Intro did not reach a battle command")
                if args.trainer and args.trainer.casefold() not in (snapshot.trainer_name or "").casefold():
                    raise RuntimeError(
                        f"Expected trainer containing {args.trainer!r}, "
                        f"got {snapshot.trainer_name!r}"
                    )

                for turn_number, (turn, delay) in enumerate(zip(line, delays), 1):
                    before = battle_reader.read()
                    if before.battle_over:
                        break
                    if delay:
                        instance.send_input("B", delay)
                    after = controller.execute_turn(list(turn))
                    print({
                        "turn": turn_number,
                        "menu_wait_frames": delay,
                        "player_hp": list(after.player_hp),
                        "enemy_hp": list(after.enemy_hp),
                    }, flush=True)

                final_battle = battle_reader.read()
                if not final_battle.battle_over or not final_battle.player_won:
                    raise RuntimeError("Recorded replay did not reproduce the victory")
                if any(final_battle.player_fainted):
                    raise RuntimeError("Recorded replay won with a Nuzlocke death")

                stable_overworld = 0
                for _ in range(400):
                    instance.send_input("B", 1)
                    instance.advance_frames(12)
                    post = game_reader.read()
                    stable_overworld = stable_overworld + 1 if post.mode == GameMode.OVERWORLD else 0
                    if stable_overworld >= 12:
                        break
                if stable_overworld < 12:
                    raise RuntimeError("Victory dialogue did not settle back into the overworld")
                instance.save_state(args.post_state)
            finally:
                recording_info = instance.stop_recording()

        if video_encoder.wait(timeout=120) != 0:
            raise RuntimeError("Video encoder failed")
        if not recording_info or recording_info["audio_rate"] <= 0:
            raise RuntimeError("Emulator did not report a valid audio rate")
        subprocess.run([
            "/opt/homebrew/bin/ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video_only),
            "-f", "s16le", "-ar", str(recording_info["audio_rate"]), "-ac", "2",
            "-i", str(audio_raw),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            "-movflags", "+faststart", str(output),
        ], check=True)
        print({"output": str(output), **recording_info}, flush=True)


if __name__ == "__main__":
    main()
