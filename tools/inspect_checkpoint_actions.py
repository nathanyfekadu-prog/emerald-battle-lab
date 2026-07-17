from __future__ import annotations

import argparse
from pathlib import Path
import sys
from base64 import b64decode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from battle.action import Action
from emulator.mgba_pool import MGBAPool, TrialSpec
from emulator.mgba_instance import MGBAInstance
from emulator.input_controller import InputController
from emulator.state_reader import StateReader
from emulator.game_state import WholeGameStateReader


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--direct-switch", type=int)
    parser.add_argument("--screenshot")
    parser.add_argument("--trace-party", action="store_true")
    parser.add_argument("--trace-switch", type=int)
    parser.add_argument("--direct-double", action="store_true")
    args = parser.parse_args()
    if args.direct_double:
        with MGBAInstance(args.rom, args.state, 988) as instance:
            reader = StateReader(instance)
            controller = InputController(instance, reader)
            try:
                state = controller.execute_turn([
                    Action.move(0, 0, actor_slot=0),
                    Action.move(0, 0, actor_slot=1),
                ])
                print({"player_hp": state.player_hp, "enemy_hp": state.enemy_hp, "phase": instance.read_u8(__import__("config").RUN_BUN_BATTLE_COMMAND_PHASE)})
            except Exception as exc:
                print({"error": repr(exc)})
            if args.screenshot:
                screen = instance.screenshot()
                from PIL import Image
                Image.frombytes("RGBA", (screen["width"], screen["height"]), b64decode(screen["rgba_base64"])).save(args.screenshot)
        return
    if (
        args.screenshot
        and args.direct_switch is None
        and not args.trace_party
        and args.trace_switch is None
    ):
        with MGBAInstance(args.rom, args.state, 989) as instance:
            screen = instance.screenshot()
            from PIL import Image
            Image.frombytes(
                "RGBA", (screen["width"], screen["height"]),
                b64decode(screen["rgba_base64"]),
            ).save(args.screenshot)
        return
    if args.trace_party:
        import config
        with MGBAInstance(args.rom, args.state, 990) as instance:
            reader = StateReader(instance)
            controller = InputController(instance, reader)
            controller._prepare_for_battle_menu()
            controller._tap("DOWN")
            controller._tap("A")
            controller._delay(150)
            opened = reader.read()
            print({"open": instance.read_u8(config.RUN_BUN_PARTY_CURSOR), "names": opened.player_names, "hp": opened.player_hp})
            for button in ("LEFT", "UP", "UP", "UP", "UP", "UP", "UP", "LEFT", "RIGHT", "DOWN", "DOWN", "DOWN"):
                controller._party_tap(button)
                print({button: instance.read_u8(config.RUN_BUN_PARTY_CURSOR)})
        return
    if args.trace_switch is not None:
        import config
        from PIL import Image

        with MGBAInstance(args.rom, args.state, 992) as instance:
            reader = StateReader(instance)
            controller = InputController(instance, reader)
            trace_dir = Path(args.screenshot or "/tmp/switch-trace")
            trace_dir.mkdir(parents=True, exist_ok=True)

            def snap(index: int, label: str) -> None:
                screen = instance.screenshot()
                Image.frombytes(
                    "RGBA", (screen["width"], screen["height"]),
                    b64decode(screen["rgba_base64"]),
                ).save(trace_dir / f"{index:02d}-{label}.png")
                state = reader.read()
                print(index, label, {
                    "cursor": instance.read_u8(config.RUN_BUN_PARTY_CURSOR),
                    "active": state.player_active_slots,
                    "names": state.player_names,
                })

            state = reader.read()
            target_name = state.player_names[args.trace_switch]
            snap(0, "command")
            controller._tap("DOWN")
            controller._tap("A")
            controller._delay(150)
            snap(1, "party")
            controller._move_party_cursor(args.trace_switch, target_name=target_name)
            snap(2, "target")
            controller._party_tap("A")
            controller._delay(50)
            snap(3, "submenu")
            for index, button in enumerate(("UP", "UP", "A"), start=4):
                controller._party_tap(button)
                controller._delay(20)
                snap(index, button.lower())
        return
    if args.direct_switch is not None:
        with MGBAInstance(args.rom, args.state, 991) as instance:
            reader = StateReader(instance)
            controller = InputController(instance, reader)
            try:
                controller.execute_turn([Action.switch(args.direct_switch, actor_slot=0)])
            except Exception as exc:
                print({"error": repr(exc)})
            state = reader.read()
            game = WholeGameStateReader(instance).read()
            if args.screenshot:
                screen = instance.screenshot()
                # Raw RGBA is intentionally used only for local diagnosis.
                from PIL import Image
                Image.frombytes(
                    "RGBA", (screen["width"], screen["height"]),
                    b64decode(screen["rgba_base64"]),
                ).save(args.screenshot)
            print({
                "player_hp": state.player_hp, "enemy_hp": state.enemy_hp,
                "active": state.player_active_slots, "menu_ready": state.menu_ready,
                "battle_over": state.battle_over,
                "party_cursor": instance.read_u8(__import__("config").RUN_BUN_PARTY_CURSOR),
                "mode": game.mode.value,
            })
        return
    actions = [Action.move(index, actor_slot=0) for index in range(4)]
    actions += [Action.switch(index, actor_slot=0) for index in range(6)]
    pool = MGBAPool(args.rom, args.state, 4)
    try:
        outcomes = pool.run_trials([
            TrialSpec(index, [action], max_turns=1)
            for index, action in enumerate(actions)
        ])
    finally:
        pool.shutdown()
    for outcome in sorted(outcomes, key=lambda item: item.trial_id):
        action = actions[outcome.trial_id]
        print({
            "action": action.__dict__,
            "error": outcome.error,
            "player_hp": outcome.final_state.player_hp,
            "enemy_hp": outcome.final_state.enemy_hp,
            "active": outcome.final_state.player_active_slots,
            "battle_over": outcome.final_state.battle_over,
            "faints": outcome.player_fainted_count,
        })


if __name__ == "__main__":
    main()
