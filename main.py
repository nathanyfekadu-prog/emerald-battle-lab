from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import config
from battle.action import Action
from emulator.input_controller import InputController
from emulator.mgba_instance import MGBAInstance
from emulator.state_reader import StateReader
from output.renderer import Renderer
from optimizer.box_optimizer import print_prepare_report, run_prepare
from search.action_enumerator import ActionEnumerator
from search.mcts import MCTS

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
except Exception:  # pragma: no cover
    Console = None
    Progress = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Pokemon battle solver")
    subparsers = parser.add_subparsers(dest="command", required=True)

    solve = subparsers.add_parser("solve")
    solve.add_argument("--rom", required=True)
    solve.add_argument("--state", required=True)
    solve.add_argument("--instances", type=int, default=16)
    solve.add_argument("--turns", type=int, default=config.MAX_TURNS, help=argparse.SUPPRESS)
    solve.add_argument("--iterations", type=int, default=200)
    solve.add_argument("--trials-per-node", type=int, default=16)
    solve.add_argument("--nuzlocke", action="store_true")
    solve.add_argument("--output", choices=["flowchart", "json", "both"], default="flowchart")

    probe = subparsers.add_parser("probe")
    probe.add_argument("--rom", required=True)
    probe.add_argument("--state", required=True)

    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--rom", required=True)
    calibrate.add_argument("--state", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--rom", required=True)
    prepare.add_argument("--battle-state", required=True)
    prepare.add_argument("--pc-state", required=True)
    prepare.add_argument("--instances", type=int, default=4)
    prepare.add_argument("--iterations", type=int, default=50)
    prepare.add_argument("--turns", type=int, default=config.MAX_TURNS)
    prepare.add_argument("--trials-per-node", type=int, default=16)
    prepare.add_argument("--nuzlocke", action="store_true")

    web = subparsers.add_parser("web")
    web.add_argument("--port", type=int, default=8000)
    web.add_argument("--open", action="store_true")

    args = parser.parse_args()
    if args.command == "solve":
        solve_command(args)
    elif args.command == "probe":
        probe_command(args)
    elif args.command == "calibrate":
        calibrate_command(args)
    elif args.command == "prepare":
        prepare_command(args)
    elif args.command == "web":
        web_command(args)


def solve_command(args: argparse.Namespace) -> None:
    _validate_paths(args.rom, args.state)
    if args.nuzlocke:
        config.NUZLOCKE_WEIGHT = 8.0

    console = Console() if Console else None
    _print(console, "POKEMON BATTLE SOLVER starting")
    _print(
        console,
        f"instances={args.instances} whole_fight_cap={args.turns} iterations={args.iterations} "
        f"trials_per_node={args.trials_per_node} nuzlocke={args.nuzlocke}",
    )

    instance = MGBAInstance(args.rom, args.state, 88)
    try:
        state = StateReader(instance).read()
    finally:
        instance.shutdown()
    _print(console, f"Initial BattleState: {state}")
    _print(console, f"Detected battle type: {'doubles' if state.is_doubles else 'singles'}")
    legal_actions = ActionEnumerator().legal_actions(state)
    _print(console, f"Legal action count this turn: {len(legal_actions)}")
    _print(console, "Searching for deathless line first...")

    mcts = MCTS(
        args.rom,
        args.state,
        pool_size=args.instances,
        max_turns=args.turns,
        trials_per_node=args.trials_per_node,
    )
    try:
        if Progress:
            with Progress(SpinnerColumn(), TextColumn("{task.description}"), TimeElapsedColumn(), console=console) as progress:
                progress.add_task("Running MCTS", total=None)
                result = mcts.search(args.iterations)
        else:
            result = mcts.search(args.iterations)
    finally:
        mcts.shutdown()

    _print(console, f"Deathless line found: {'YES' if result.has_deathless_line else 'NO'}")
    if args.output in ("flowchart", "both"):
        Renderer(console, state).render(result)
    if args.output in ("json", "both"):
        Path("results.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        _print(console, "Wrote results.json")


def probe_command(args: argparse.Namespace) -> None:
    _validate_paths(args.rom, args.state)
    instance = MGBAInstance(args.rom, args.state, 77)
    try:
        reader = StateReader(instance)
        print(reader.read())
        while True:
            value = input("address> ").strip()
            if value.lower() in {"quit", "exit", "q"}:
                break
            address = int(value, 16)
            print(
                f"u8={instance.read_u8(address)} "
                f"u16={instance.read_u16(address)} "
                f"u32={instance.read_u32(address)}"
            )
    finally:
        instance.shutdown()


def calibrate_command(args: argparse.Namespace) -> None:
    _validate_paths(args.rom, args.state)
    rows: list[tuple[int, int, int, bool]] = []
    for delay in range(1, 21):
        instance = MGBAInstance(args.rom, args.state, delay + 120)
        try:
            reader = StateReader(instance)
            controller = InputController(instance, reader, inter_input_delay_frames=delay)
            before = reader.read().player_hp[0]
            after = controller.execute_turn([Action.move(0)]).player_hp[0]
            rows.append((delay, before, after, after != before and after > 0))
        finally:
            instance.shutdown()
    print("delay | before | after | valid")
    for row in rows:
        print(f"{row[0]:>5} | {row[1]:>6} | {row[2]:>5} | {row[3]}")
    valid = [delay for delay, _before, _after, ok in rows if ok]
    if valid:
        recommended = min(valid)
        print(f"Recommended minimum reliable delay: {recommended}")
        _update_config_delay(recommended)
        print("Updated config.py")
    else:
        print("No reliable delay found")


def prepare_command(args: argparse.Namespace) -> None:
    _validate_paths(args.rom, args.battle_state, state_label="battle state")
    if not Path(args.pc_state).is_file():
        raise SystemExit(
            f"PC state not found: {args.pc_state}\n"
            "--pc-state is the overworld save state used to access Pokemon Storage; "
            "--battle-state is the in-battle save state used to solve the target fight."
        )
    console = Console() if Console else None
    _print(console, "Preparing team: solving current party first...")
    result = run_prepare(
        args.rom,
        args.battle_state,
        args.pc_state,
        instances=args.instances,
        iterations=args.iterations,
        turns=args.turns,
        trials_per_node=args.trials_per_node,
        nuzlocke=args.nuzlocke,
    )
    print_prepare_report(result)
    _print(console, "\nBaseline solve result:")
    instance = MGBAInstance(args.rom, args.battle_state, 91)
    try:
        battle_state = StateReader(instance).read()
    finally:
        instance.shutdown()
    Renderer(console, battle_state).render(result.baseline)


def web_command(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install requirements first: pip install -r requirements.txt") from exc

    url = f"http://localhost:{args.port}"
    print(f"Solver UI at {url}")
    if args.open:
        subprocess.Popen(["open", url])
    uvicorn.run("web.server:app", host="127.0.0.1", port=args.port, reload=False)


def _validate_paths(rom: str, state: str, state_label: str = "state") -> None:
    if not Path(rom).is_file():
        raise SystemExit(f"ROM not found: {rom}")
    if not Path(state).is_file():
        raise SystemExit(f"{state_label.title()} not found: {state}")


def _print(console: Console | None, message: str) -> None:
    if console:
        console.print(message)
    else:
        print(message)


def _update_config_delay(delay: int) -> None:
    path = Path("config.py")
    text = path.read_text(encoding="utf-8")
    lines = []
    updated = False
    for line in text.splitlines():
        if line.startswith("INTER_INPUT_DELAY_FRAMES ="):
            lines.append(f"INTER_INPUT_DELAY_FRAMES = {delay}")
            updated = True
        else:
            lines.append(line)
    if not updated:
        lines.append(f"INTER_INPUT_DELAY_FRAMES = {delay}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
