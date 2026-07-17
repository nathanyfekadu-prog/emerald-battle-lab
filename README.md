# Pokemon Emerald Battle Solver

This project reads a live Pokemon battle from an mGBA checkpoint, searches possible moves and switches, and plays a chosen line back through the emulator. The emulator is the final judge: a plan only counts as verified after the real game reaches a win.

The current prototype targets **Pokemon Run & Bun**, an Emerald-based ROM hack. It is not a general solver for every Pokemon game, and it does not ship a ROM.

## What works now

- Reads party HP, enemy HP, battle type, active Pokemon, moves, and other state from a running Emerald-based game.
- Drives several headless mGBA instances for search and replay.
- Handles moves, switches, forced replacements, singles, doubles, status, healing, PC visits, and multi-trainer routes in the current Run & Bun setup.
- Replays saved cartridge policies from the original checkpoint and records MP4 proof, event logs, and final save states.
- Provides a FastAPI web interface with a one-fight simulator, gauntlet mode, damage calculator, line finder, and replay results.
- Includes 150 automated tests covering state reading, damage calculations, planning, doubles support, emulator control, preparation, and output generation.

There is one important limitation: the abstract planner can still disagree with the cartridge, and some verified fights currently fall back to a saved cartridge playbook after the planner misses the winning line. Closing that search-to-replay gap is active work. A recorded win proves that the controller can execute and verify the policy; it does not, by itself, prove that the general search discovered the policy without help.

## Supported setup

The tested development setup is:

- macOS on Apple Silicon
- Python 3.12 or newer
- mGBA 0.10.x installed through Homebrew, or the locally built mGBA source tree expected by `emulator/mgba_instance.py`
- FFmpeg for MP4 recording
- A user-supplied Pokemon Run & Bun ROM and mGBA checkpoint

Other operating systems and unmodified Emerald may need different library paths, memory addresses, menu timing, and game data. They are not validated yet.

## Files the user provides

The program treats these as separate inputs:

- `.gba`: the game ROM. The user supplies this locally.
- `.sav`: the battery save containing the player's normal saved run.
- `.ss0`, `.ss1`, `.ss2`, `.ss3`, or similar: an mGBA save state containing an exact emulator checkpoint.

A ROM does not contain the player's current run. For the most repeatable battle search, start from a save state taken at the battle command menu or immediately before the trainer fight.

## Installation

Install the system dependencies on macOS:

```bash
brew install python@3.12 mgba ffmpeg
```

Create a virtual environment and install the Python packages:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The native helper in `emulator/mgba_bridge.c` compiles locally against `libmgba` when needed. The generated `emulator/mgba_bridge` executable should not be committed.

## Run the web app

```bash
source .venv/bin/activate
python main.py web --port 8000 --open
```

In the Simulator tab:

1. Choose a local mGBA checkpoint.
2. Select **Check this battle** and confirm the detected trainer, party, moves, and HP.
3. Set the search and replay effort.
4. Start the search.
5. Review the proposed line and the cartridge replay evidence.

### Judge Demo (no ROM required)

After starting the web app, open:

```text
http://localhost:8000/?view=demo
```

Judge Demo reads the committed `demo/judge-demo.json` sample. It shows a sanitized eight-fight verification record and battle snapshot without including a ROM, save state, extracted game asset, or game audio. Live emulator search still requires the user's local game and checkpoint.

Submission copy, the timed video script, and the publication checklist live under `submission/`.

Large searches may run several emulator copies and take a while. Start with four instances and a small replay count before increasing either value.

## Command-line examples

Inspect a checkpoint:

```bash
python main.py probe \
  --rom "/absolute/path/to/game.gba" \
  --state "/absolute/path/to/prebattle.ss0"
```

Run the emulator-backed battle search:

```bash
python main.py solve \
  --rom "/absolute/path/to/game.gba" \
  --state "/absolute/path/to/battle.ss0" \
  --instances 4 \
  --iterations 60 \
  --output both
```

Run the tests:

```bash
python -m pytest -q
```

## How it fits together

```text
Web UI / CLI
     |
     v
State reader ----> battle snapshot ----> planner / search
     ^                                      |
     |                                      v
libmgba bridge <---- input controller <---- selected policy
     |
     v
real cartridge replay ----> logs, final state, and MP4 proof
```

The main pieces are:

- `emulator/`: mGBA process control, RAM reads and writes, menu input, overworld movement, screenshots, and recording.
- `search/`: action enumeration, MCTS, and checkpoint beam search.
- `battle/`: battle state and damage calculations.
- `optimizer/`: turn planning, held-item advice, and box/team selection.
- `web/`: FastAPI endpoints and the browser interface.
- `tests/`: regression coverage for mechanics and controller behavior.
- `docs/`: architecture, state format, confidence model, and known AI assumptions.

The ROM remains the mechanics source of truth. The internal calculator helps rank lines, but Run & Bun changes enough mechanics and AI behavior that calculator output can be wrong. Replay logs should record those disagreements instead of hiding them.

## How Codex was used

Codex was used throughout development rather than added as a submission wrapper. Work done with Codex includes the native mGBA bridge, memory-address investigation, state decoding, emulator input control, search and planner code, replay repair, web UI work, and regression tests.

The OpenAI Build Week submission includes the `/feedback` session ID for the main development session. The project description should also call out specific failures Codex helped diagnose, especially cases where a predicted line diverged from the real cartridge and required a controller or mechanics fix.

## ROMs, saves, and game assets

This repository does not include Pokemon Emerald, Pokemon Run & Bun, patched ROMs, extracted ROM assets, or download links for unauthorized game copies. You must provide your own lawfully obtained game file and any patch required by the ROM hack.

Do not commit `.gba`, `.sav`, or mGBA save-state files. The included `.gitignore` blocks the common extensions. Public demos should use recorded video, screenshots, decoded JSON fixtures, and replay logs; judges who want to run the emulator-backed path must supply their own compatible ROM locally.

A patch is not automatically safe to redistribute merely because it excludes the base ROM. Only include a patch when its author has granted permission or published it under terms that allow redistribution. Otherwise, link to the author's official release page.

## Known limitations

- Memory addresses and timing are calibrated for the tested Run & Bun build.
- Run & Bun AI, switching, secondary effects, and several mechanics remain approximations in the internal planner.
- A planner confidence score is not yet a calibrated probability of winning on the cartridge.
- Some recorded policies were repaired or saved after earlier searches; the UI must distinguish those from newly discovered lines.
- The native mGBA path is currently macOS-oriented.
- Replays can diverge when RNG, menu state, timing, or an unmodeled mechanic changes.

## License and attribution

Original project code is available under the MIT License; see `LICENSE`.

The project also uses separately licensed software and data, including mGBA and the Smogon/Run & Bun damage calculator family. Those components keep their original licenses and copyright notices. See `THIRD_PARTY_NOTICES.md` before redistributing the repository or compiled binaries.

Pokemon, Pokemon Emerald, Game Boy Advance, and related names and assets belong to their respective owners. This is an unofficial fan-made research and automation project. It is not affiliated with Nintendo, The Pokemon Company, Game Freak, the Pokemon Run & Bun developers, Smogon, or the mGBA project.
