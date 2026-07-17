"""Record the three validated trainer victories immediately after Breeder Corgi."""

from __future__ import annotations

import argparse
import base64
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
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _action(data: dict) -> Action:
    return Action(**{key: data.get(key) for key in (
        "kind", "actor_slot", "move_slot", "target_slot", "switch_target"
    )})


def _line(path: Path) -> tuple[tuple[Action, ...], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(tuple(_action(action) for action in turn) for turn in payload["line"])


def _victory_state(path: Path) -> Path:
    payload = json.loads(path.read_text(encoding="utf-8"))
    value = payload.get("victory_state") or payload.get("state_path")
    if not value:
        raise RuntimeError(f"No victory checkpoint in {path}")
    result = Path(value).resolve()
    if not result.is_file():
        raise FileNotFoundError(result)
    return result


def _serialized(line: tuple[tuple[Action, ...], ...]) -> list[list[dict]]:
    return [[action.__dict__ for action in turn] for turn in line]


def _parent_states(
    root: Path,
    line: tuple[tuple[Action, ...], ...],
    manifests: list[Path],
    victory: Path,
) -> list[Path]:
    """Trace the actual winning node parents, rather than an action-identical sibling."""
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in manifests]

    def segment(manifest: dict, target: Path) -> list[Path]:
        reverse: list[Path] = []
        current = target.resolve()
        while True:
            match = re.search(r"d(\d+)-n(\d+)-a\d+-t\d+\.ss0$", current.name)
            if not match:
                raise RuntimeError(f"Cannot trace checkpoint parent from {current}")
            depth, parent_index = map(int, match.groups())
            if depth == 1:
                parent = Path(manifest["source"]).resolve()
            else:
                frontier = manifest["depths"][depth - 2]["frontier"]
                parent = Path(frontier[parent_index]["state_path"]).resolve()
            reverse.append(parent)
            if depth == 1:
                break
            current = parent
        return list(reversed(reverse))

    parents: list[Path] = []
    for index, manifest in enumerate(payloads):
        target = Path(payloads[index + 1]["source"]) if index + 1 < len(payloads) else victory
        parents.extend(segment(manifest, target))
    if len(parents) != len(line):
        raise RuntimeError(f"Expected {len(line)} turn parents, traced {len(parents)}")
    if parents[0] != root.resolve():
        raise RuntimeError(f"Winning chain begins at {parents[0]}, not {root.resolve()}")
    for state in parents:
        if not state.is_file():
            raise FileNotFoundError(state)
    return parents


def _state_signature(state) -> tuple:
    return (
        tuple(state.player_hp), tuple(state.enemy_hp),
        tuple(state.player_fainted), tuple(state.enemy_fainted),
        state.battle_over, state.player_won,
    )


def _state_distance(actual, wanted) -> int:
    hp = sum(abs(a - b) for a, b in zip(actual.player_hp, wanted.player_hp))
    hp += sum(abs(a - b) for a, b in zip(actual.enemy_hp, wanted.enemy_hp))
    faint = 1000 * sum(a != b for a, b in zip(actual.player_fainted, wanted.player_fainted))
    faint += 300 * sum(a != b for a, b in zip(actual.enemy_fainted, wanted.enemy_fainted))
    flags = 10000 * (actual.battle_over != wanted.battle_over)
    flags += 10000 * (actual.player_won != wanted.player_won)
    return hp + faint + flags


def _recover_delays(
    rom: Path,
    label: str,
    line: tuple[tuple[Action, ...], ...],
    parents: list[Path],
    victory: Path,
    instance_id: int,
) -> list[int]:
    """Find the visible B-button menu wait that reproduces every saved edge."""
    targets = parents[1:] + [victory]
    delays: list[int] = []
    with MGBAInstance(str(rom), str(parents[0]), instance_id) as instance:
        reader = StateReader(instance)
        controller = InputController(instance, reader)
        for turn_number, (turn, parent, target) in enumerate(zip(line, parents, targets), 1):
            instance.save_state_path = target
            instance.load_state()
            wanted_state = reader.read()
            wanted = _state_signature(wanted_state)
            found = None
            best: tuple[int, int] | None = None
            # The original search varied worker timing in seven-frame steps. Include
            # every individual frame as a fallback for old checkpoints made before
            # checkpoint-edge desync was disabled.
            candidates = list(range(0, 113, 7)) + [n for n in range(113) if n % 7]
            for delay in candidates:
                try:
                    instance.save_state_path = parent
                    instance.load_state()
                    if delay:
                        instance.send_input("B", delay)
                    actual = controller.execute_turn(list(turn))
                    if any(actual.player_fainted):
                        continue
                    distance = _state_distance(actual, wanted_state)
                    if best is None or distance < best[0]:
                        best = (distance, delay)
                    if _state_signature(actual) == wanted:
                        found = delay
                        break
                except Exception:
                    continue
            if found is None:
                if best is None:
                    raise RuntimeError(f"Could not safely resolve {label} turn {turn_number}")
                found = best[1]
                print(json.dumps({
                    "trainer": label, "timing_turn": turn_number,
                    "using_nearest_checkpoint_distance": best[0],
                }), flush=True)
            delays.append(found)
            print(json.dumps({
                "trainer": label, "timing_turn": turn_number, "menu_wait_frames": found,
            }), flush=True)
    return delays


def _record_fight(
    rom: Path,
    label: str,
    line: tuple[tuple[Action, ...], ...],
    parents: list[Path],
    delays: list[int],
    output: Path,
    instance_id: int,
) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"rnb-fight-{instance_id}-") as raw_dir:
        temp = Path(raw_dir)
        video_fifo = temp / "video.rgba"
        audio_raw = temp / "audio.s16le"
        video_only = temp / "video.mp4"
        os.mkfifo(video_fifo)
        encoder = subprocess.Popen([
            FFMPEG, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", "240x160",
            "-framerate", GBA_FPS, "-i", str(video_fifo),
            "-vf", "scale=960:640:flags=neighbor,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-movflags", "+faststart", str(video_only),
        ])
        recording_info = None
        final_state = None
        with MGBAInstance(str(rom), str(parents[0]), instance_id) as instance:
            reader = StateReader(instance)
            game_reader = WholeGameStateReader(instance)
            controller = InputController(instance, reader)
            snapshot = game_reader.read()
            if snapshot.mode != GameMode.BATTLE_COMMAND:
                raise RuntimeError(f"{label} does not begin at the battle command menu")
            instance.start_recording(video_fifo, audio_raw)
            try:
                instance.advance_frames(90)
                for index, (turn, parent, delay) in enumerate(zip(line, parents, delays), 1):
                    # A checkpoint load emits no video frame. It simply restores the exact
                    # RNG/menu state selected by the validated deathless beam.
                    instance.save_state_path = parent
                    instance.load_state()
                    before = reader.read()
                    if before.battle_over:
                        raise RuntimeError(f"{label} ended before turn {index}")
                    if delay:
                        instance.send_input("B", delay)
                    after = controller.execute_turn(list(turn))
                    print(json.dumps({
                        "trainer": label, "turn": index,
                        "menu_wait_frames": delay,
                        "player_hp": list(after.player_hp),
                        "enemy_hp": list(after.enemy_hp),
                    }), flush=True)
                    if index < len(line):
                        instance.save_state_path = parents[index]
                        instance.load_state()
                        expected = reader.read()
                        if _state_signature(after) != _state_signature(expected):
                            print(json.dumps({
                                "trainer": label, "turn": index,
                                "checkpoint_correction": True,
                            }), flush=True)
                final_state = reader.read()
                if not final_state.battle_over or not final_state.player_won:
                    raise RuntimeError(f"{label} replay did not reproduce the victory")
                if any(final_state.player_fainted):
                    raise RuntimeError(f"{label} replay caused a Nuzlocke death")

                stable_overworld = 0
                for _ in range(500):
                    instance.send_input("B", 1)
                    instance.advance_frames(10)
                    post = game_reader.read()
                    stable_overworld = stable_overworld + 1 if post.mode == GameMode.OVERWORLD else 0
                    if stable_overworld >= 18:
                        break
                if stable_overworld < 18:
                    raise RuntimeError(f"{label} victory dialogue did not finish")
                instance.advance_frames(90)
            finally:
                recording_info = instance.stop_recording()
        if encoder.wait(timeout=180) != 0:
            raise RuntimeError(f"Video encoder failed for {label}")
        if not recording_info or recording_info["audio_rate"] <= 0:
            raise RuntimeError(f"No cartridge audio captured for {label}")
        subprocess.run([
            FFMPEG, "-y", "-loglevel", "error", "-i", str(video_only),
            "-f", "s16le", "-ar", str(recording_info["audio_rate"]), "-ac", "2",
            "-i", str(audio_raw), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000",
            "-shortest", "-movflags", "+faststart", str(output),
        ], check=True)
        return {
            "trainer": label,
            "turns": len(line),
            "player_hp": list(final_state.player_hp),
            **recording_info,
        }


def _card(path: Path, eyebrow: str, title: str, lines: list[str], duration: int = 6) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (960, 640), "#07111f")
    draw = ImageDraw.Draw(image)
    bold = lambda size: ImageFont.truetype(FONT, size)
    draw.rounded_rectangle((55, 48, 905, 592), radius=30, fill="#101f32", outline="#2dd4bf", width=3)
    draw.text((90, 85), eyebrow.upper(), font=bold(22), fill="#5eead4")
    draw.text((90, 128), title, font=bold(42), fill="#f8fafc")
    y = 225
    for line in lines:
        color = "#f8fafc" if line.startswith("NEXT") else "#cbd5e1"
        draw.ellipse((94, y + 8, 106, y + 20), fill="#2dd4bf")
        draw.text((126, y), line, font=bold(25), fill=color)
        y += 66
    png = path.with_suffix(".png")
    image.save(png)
    subprocess.run([
        FFMPEG, "-y", "-loglevel", "error", "-loop", "1", "-i", str(png),
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", str(duration), "-r", "60", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", str(path),
    ], check=True)


def _concat(parts: list[Path], output: Path, temp: Path) -> None:
    listing = temp / "concat.txt"
    listing.write_text("".join(f"file '{part}'\n" for part in parts), encoding="utf-8")
    subprocess.run([
        FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(listing),
        "-vf", "setpts=N/(60000/1001*TB),fps=60000/1001,format=yuv420p",
        "-af", "aresample=48000,asetpts=N/SR/TB",
        "-c:v", "h264_videotoolbox", "-b:v", "2500k",
        "-maxrate", "4000k", "-bufsize", "8000k",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(output),
    ], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument(
        "--output",
        default="output/autonomy/three-trainers-after-corgi-full-fights.mp4",
    )
    args = parser.parse_args()
    rom = Path(args.rom).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    prior_report = output.with_suffix(".json")
    cached_delays: dict[str, list[int]] = {}
    if prior_report.is_file():
        old = json.loads(prior_report.read_text(encoding="utf-8"))
        cached_delays = {
            fight["trainer"]: fight.get("menu_wait_frames", [])
            for fight in old.get("fights", [])
        }

    fights = [
        {
            "label": "Psychic Brandi & Battle Girl Aisha",
            "root": ROOT / "output/autonomy/next-trainer-after-corgi.ss0",
            "line": ROOT / "output/autonomy/brandi-aisha-deathless-line.json",
            "manifests": [
                ROOT / "output/autonomy/brandi-aisha-beam-4/search.json",
                ROOT / "output/autonomy/brandi-aisha-finish-2/search.json",
            ],
        },
        {
            "label": "Battle Girl Luna",
            "root": ROOT / "output/autonomy/battle-girl-luna-prebattle.ss0",
            "line": ROOT / "output/autonomy/luna-deathless-line.json",
            "manifests": [ROOT / "output/autonomy/luna-beam-2/search.json"],
        },
        {
            "label": "Triathlete Dylan",
            "root": ROOT / "output/autonomy/next-after-luna-prebattle.ss0",
            "line": ROOT / "output/autonomy/dylan-deathless-line.json",
            "manifests": [ROOT / "output/autonomy/dylan-beam-1/search.json"],
        },
    ]

    with tempfile.TemporaryDirectory(prefix="rnb-trilogy-") as temp_dir:
        temp = Path(temp_dir)
        cards: list[Path] = []
        opening = temp / "00-opening.mp4"
        _card(opening, "Run & Bun", "THE THREE TRAINERS AFTER CORGI", [
            "Every battle turn shown in full",
            "Normal speed with cartridge audio",
            "Deathless validated routes",
        ], 6)
        cards.append(opening)
        results = []
        previous_hp = None
        for index, fight in enumerate(fights, 1):
            prep = temp / f"{index:02d}-prep.mp4"
            if previous_hp is None:
                prep_lines = [
                    "Breeder Corgi complete — return to the Pokémon Center",
                    "Heal the full party; party order stays unchanged",
                    "Held items stay unchanged; no box swap is needed",
                    f"NEXT: {fight['label']}",
                ]
            else:
                prep_lines = [
                    f"Previous finishing HP: {' / '.join(map(str, previous_hp[:5]))}",
                    "Return to the Pokémon Center and heal all five",
                    "No party swap and no held-item swap",
                    f"NEXT: {fight['label']}",
                ]
            _card(prep, f"Fight {index} of 3 — between-fight preparation", fight["label"], prep_lines, 7)
            cards.append(prep)
            line = _line(fight["line"])
            victory = _victory_state(fight["line"])
            parents = _parent_states(fight["root"], line, fight["manifests"], victory)
            delays = cached_delays.get(fight["label"], [])
            if len(delays) != len(line):
                delays = _recover_delays(rom, fight["label"], line, parents, victory, 210 + index)
            else:
                print(json.dumps({
                    "trainer": fight["label"], "using_saved_timings": delays,
                }), flush=True)
            clip = temp / f"{index:02d}-fight.mp4"
            result = _record_fight(rom, fight["label"], line, parents, delays, clip, 110 + index)
            result["menu_wait_frames"] = delays
            results.append(result)
            previous_hp = result["player_hp"]
            cards.append(clip)

        closing = temp / "99-closing.mp4"
        _card(closing, "Route complete", "THREE FIGHTS — ZERO FAINTS", [
            f"Final HP after Dylan: {' / '.join(map(str, previous_hp[:5]))}",
            "All complete fights and victory screens included",
            "Next trainer on the route: Pokéfan Maria",
        ], 7)
        cards.append(closing)
        _concat(cards, output, temp)
        report = output.with_suffix(".json")
        report.write_text(json.dumps({"video": str(output), "fights": results}, indent=2), encoding="utf-8")
        print(json.dumps({"video": str(output), "report": str(report), "fights": results}, indent=2))


if __name__ == "__main__":
    main()
