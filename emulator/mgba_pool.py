from __future__ import annotations

import atexit
import concurrent.futures
import logging
import multiprocessing
import os
import traceback
from pathlib import Path

import config
from battle.action import Action
from battle.battle_state import BattleState
from emulator.input_controller import InputController
from emulator.mgba_instance import MGBAInstance
from emulator.state_reader import StateReader
from outcome import Outcome, PolicyAction, TrialSpec, TurnSnapshot
from search.action_enumerator import ActionEnumerator

logger = logging.getLogger(__name__)

# One mGBA process is booted per worker process and reused across every trial that worker
# runs — booting a fresh emulator (ROM load + bridge compile/connect) per trial was the
# dominant cost in the old code. Each trial just reloads the savestate to reset RAM to the
# battle start, which is exactly what booting fresh achieved, only ~100x cheaper.
_WORKER_INSTANCE: MGBAInstance | None = None
_WORKER_READER: StateReader | None = None
_WORKER_READER_STATE: str | None = None


def _worker_instance(rom_path: str, save_state_path: str, instance_id: int) -> MGBAInstance:
    global _WORKER_INSTANCE
    if _WORKER_INSTANCE is None:
        _WORKER_INSTANCE = MGBAInstance(rom_path, save_state_path, instance_id)
        atexit.register(_shutdown_worker_instance)
    return _WORKER_INSTANCE


def _shutdown_worker_instance() -> None:
    global _WORKER_INSTANCE, _WORKER_READER, _WORKER_READER_STATE
    if _WORKER_INSTANCE is not None:
        try:
            _WORKER_INSTANCE.shutdown()
        finally:
            _WORKER_INSTANCE = None
            _WORKER_READER = None
            _WORKER_READER_STATE = None


def _worker_reader(instance: MGBAInstance, state_path: str) -> StateReader:
    """Reuse immutable party decoding for trials from the exact same state."""
    global _WORKER_READER, _WORKER_READER_STATE
    resolved = str(Path(state_path).expanduser().resolve())
    if _WORKER_READER is None or _WORKER_READER_STATE != resolved:
        _WORKER_READER = StateReader(instance)
        _WORKER_READER_STATE = resolved
    return _WORKER_READER


def _warm_worker_and_read_state(
    rom_path: str, save_state_path: str, pool_size: int, warmup_id: int
) -> BattleState:
    """Boot one reusable worker and return the exact initial battle state."""
    instance = _worker_instance(rom_path, save_state_path, warmup_id % max(1, pool_size))
    instance.save_state_path = Path(save_state_path).expanduser().resolve()
    instance.load_state()
    return _worker_reader(instance, save_state_path).read()


def _empty_state() -> BattleState:
    return BattleState(
        player_hp=[0] * 6,
        player_max_hp=[0] * 6,
        player_fainted=[False] * 6,
        enemy_hp=[0] * 6,
        enemy_max_hp=[0] * 6,
        enemy_fainted=[False] * 6,
        battle_over=False,
        player_won=False,
        is_doubles=False,
        menu_ready=False,
        player_move_names_by_slot=[],
        player_species=[],
        enemy_species=[],
    )


def _turn_actions(actions: list[object]) -> list[tuple[object, ...]]:
    grouped: list[tuple[object, ...]] = []
    for action in actions:
        if isinstance(action, tuple):
            grouped.append(action)
        else:
            grouped.append((action,))
    return grouped


class PolicyDivergence(RuntimeError):
    pass


def _normalized_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _resolve_policy_action(instruction: object, state: BattleState) -> Action:
    if isinstance(instruction, Action):
        return instruction
    if not isinstance(instruction, PolicyAction):
        raise TypeError(f"Unsupported trial action: {instruction!r}")
    if instruction.kind == "move":
        actor_slot = instruction.actor_slot or 0
        active_party_slot = (
            state.player_active_slots[actor_slot]
            if actor_slot < len(state.player_active_slots)
            else None
        )
        move_names = (
            state.player_move_names_by_slot[active_party_slot]
            if active_party_slot is not None and active_party_slot < len(state.player_move_names_by_slot)
            else state.player_move_names
        )
        if not move_names:
            move_names = state.player_move_names
        wanted = _normalized_name(instruction.move_name or "")
        move_slot = next(
            (index for index, name in enumerate(move_names) if _normalized_name(name) == wanted),
            None,
        )
        if move_slot is None:
            actor_party_slot = active_party_slot
            actor = (
                state.player_names[actor_party_slot]
                if actor_party_slot is not None and actor_party_slot < len(state.player_names)
                else f"field slot {actor_slot + 1}"
            )
            raise PolicyDivergence(
                f"Planned move {instruction.move_name!r} is unavailable for {actor}; "
                f"live moves are {move_names or ['unknown']}."
            )
        return Action.move(move_slot, instruction.target_slot, actor_slot=instruction.actor_slot)
    if instruction.kind == "switch":
        target = instruction.switch_party_slot
        if target is None:
            wanted = _normalized_name(instruction.switch_to or "")
            target = next(
                (index for index, name in enumerate(state.player_names) if _normalized_name(name) == wanted),
                None,
            )
        if target is None:
            raise PolicyDivergence(
                f"Planned switch target {instruction.switch_to!r} is not in the live party "
                f"{state.player_names or ['unknown']}."
            )
        if target < len(state.player_fainted) and state.player_fainted[target]:
            raise PolicyDivergence(f"Planned switch target {instruction.switch_to!r} has fainted.")
        return Action.switch(target, actor_slot=instruction.actor_slot)
    raise PolicyDivergence(f"Unknown policy action kind {instruction.kind!r}.")


def _run_trial_worker(
    rom_path: str,
    save_state_path: str,
    pool_size: int,
    game_mode: str,
    trial: TrialSpec,
) -> Outcome:
    instance_id = trial.trial_id % max(1, pool_size)
    instance: MGBAInstance | None = None
    actions_taken = []
    turn_snapshots: list[TurnSnapshot] = []
    frames_run = 0
    try:
        # Reuse this worker's emulator; load_state() below resets it to the battle start.
        instance = _worker_instance(rom_path, save_state_path, os.getpid() % 100)
        instance.save_state_path = Path(trial.start_state_path).expanduser().resolve() if trial.start_state_path else Path(save_state_path).expanduser().resolve()
        instance.load_state()
        reader = _worker_reader(instance, str(instance.save_state_path))
        controller = InputController(
            instance, reader, stop_on_player_faint=trial.stop_on_player_faint
        )
        enumerator = ActionEnumerator(game_mode)
        initial_state = reader.read()
        # A checkpoint edge must be the exact continuation of its saved parent.
        # Worker-dependent desync made a beam path a collection of individually
        # valid RNG branches that could not be replayed as one continuous fight.
        # Keep desync only for intentional independent full-battle sampling.
        automatic_desync = 0 if trial.start_state_path else instance_id * config.RNG_DESYNC_FRAMES
        desync_frames = automatic_desync + trial.rng_advance_frames
        if desync_frames:
            instance.send_input("B", desync_frames)
            frames_run += desync_frames

        turns_attempted = 0
        stopped_for_faint = False
        for turn_action in _turn_actions(trial.actions):
            before = reader.read()
            if before.battle_over:
                break
            resolved_actions = [_resolve_policy_action(action, before) for action in turn_action]
            after = controller.execute_turn(resolved_actions)
            actions_taken.extend(resolved_actions)
            turn_snapshots.append(_turn_snapshot(
                turns_attempted + 1,
                resolved_actions,
                before,
                after,
                _capture_screen(instance) if trial.capture_screens else {},
            ))
            turns_attempted += 1
            frames_run += config.TURN_RESOLUTION_MIN_FRAMES
            if trial.stop_on_player_faint and any(
                final and not initial
                for final, initial in zip(after.player_fainted, initial_state.player_fainted)
            ):
                stopped_for_faint = True
                break

        while not stopped_for_faint and turns_attempted < trial.max_turns:
            before = reader.read()
            if before.battle_over:
                break
            legal_actions = enumerator.prioritize(enumerator.legal_actions(before), before)
            if actions_taken and actions_taken[-1].is_switch:
                move_actions = [
                    turn_action
                    for turn_action in legal_actions
                    if all(action.is_move for action in turn_action)
                ]
                if move_actions:
                    legal_actions = move_actions
            if not legal_actions:
                break
            finisher = enumerator.finishing_action(legal_actions, before)
            # Most rollout turns should make visible progress. Every fourth
            # choice still samples the wider action list for setup and pivots.
            turn_action = (
                finisher
                if finisher is not None and (trial.trial_id + turns_attempted) % 4
                else legal_actions[(trial.trial_id + turns_attempted) % len(legal_actions)]
            )
            after = controller.execute_turn(list(turn_action))
            actions_taken.extend(turn_action)
            turn_snapshots.append(_turn_snapshot(
                turns_attempted + 1,
                list(turn_action),
                before,
                after,
                _capture_screen(instance) if trial.capture_screens else {},
            ))
            turns_attempted += 1
            frames_run += config.TURN_RESOLUTION_MIN_FRAMES
            if trial.stop_on_player_faint and any(
                final and not initial
                for final, initial in zip(after.player_fainted, initial_state.player_fainted)
            ):
                stopped_for_faint = True
                break

        final_state = reader.read()
        if trial.output_state_path:
            instance.save_state(trial.output_state_path)
        screen = _capture_screen(instance) if trial.capture_screens else {}
        player_fainted_count = sum(
            final and not initial
            for final, initial in zip(final_state.player_fainted, initial_state.player_fainted)
        )
        enemy_fainted_count = sum(
            final and not initial
            for final, initial in zip(final_state.enemy_fainted, initial_state.enemy_fainted)
        )
        battle_won = final_state.battle_over and final_state.player_won
        return Outcome(
            final_state=final_state,
            actions_taken=actions_taken,
            instance_id=instance_id,
            trial_id=trial.trial_id,
            frames_run=frames_run,
            battle_won=battle_won,
            player_fainted_count=player_fainted_count,
            enemy_fainted_count=enemy_fainted_count,
            final_player_hp=final_state.player_hp,
            final_enemy_hp=final_state.enemy_hp,
            is_sack_line=player_fainted_count > 0 and battle_won,
            turn_snapshots=turn_snapshots,
            error=None,
            screen_width=screen.get("width"),
            screen_height=screen.get("height"),
            screen_rgba_base64=screen.get("rgba_base64"),
        )
    except Exception:
        error = traceback.format_exc(limit=8)
        screen = _capture_screen(instance) if instance is not None and trial.capture_screens else {}
        # A trial that blew up may have left the emulator in a bad state (mid-menu, desynced
        # bridge). Drop it so the next trial in this worker boots a clean one.
        _shutdown_worker_instance()
        return Outcome(
            final_state=_empty_state(),
            actions_taken=actions_taken,
            instance_id=instance_id,
            trial_id=trial.trial_id,
            frames_run=frames_run,
            battle_won=False,
            player_fainted_count=0,
            enemy_fainted_count=0,
            final_player_hp=[0] * 6,
            final_enemy_hp=[0] * 6,
            is_sack_line=False,
            turn_snapshots=turn_snapshots,
            error=error,
            screen_width=screen.get("width"),
            screen_height=screen.get("height"),
            screen_rgba_base64=screen.get("rgba_base64"),
        )
    # The worker's emulator is intentionally kept alive between trials; it is shut down by
    # the atexit hook when the worker process exits (or above, if a trial errored).


class MGBAPool:
    def __init__(self, rom_path: str, save_state_path: str, pool_size: int = 16, game_mode: str = "run-and-bun"):
        self.rom_path = rom_path
        self.save_state_path = save_state_path
        self.pool_size = pool_size
        self.game_mode = game_mode
        cpu_count = os.cpu_count()
        if cpu_count is not None and pool_size > cpu_count:
            logger.warning("pool_size %s is greater than cpu_count %s", pool_size, cpu_count)
        # Explicit spawn context: each worker boots its own emulator and must not inherit the
        # parent's open bridge pipes/sockets (which fork would duplicate). Spawn is also the
        # macOS/Py3.14 default, so this just makes the requirement explicit and portable.
        self._executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=pool_size,
            mp_context=multiprocessing.get_context("spawn"),
        )

    def warmup_and_read_state(self) -> BattleState:
        """Boot all emulator workers concurrently and reuse one exact state read.

        Previously MCTS booted a throwaway emulator to inspect the state, then
        booted the real workers serially on their first trials. This overlaps
        worker startup and removes that disposable emulator without changing a
        single search or validation sample.
        """
        futures = [
            self._executor.submit(
                _warm_worker_and_read_state,
                self.rom_path,
                self.save_state_path,
                self.pool_size,
                index,
            )
            for index in range(self.pool_size)
        ]
        states = [future.result() for future in concurrent.futures.as_completed(futures)]
        if not states:
            raise RuntimeError("No emulator worker returned the initial battle state")
        return states[0]

    def run_trials(self, trials: list[TrialSpec]) -> list[Outcome]:
        futures = [
            self._executor.submit(
                _run_trial_worker,
                self.rom_path,
                self.save_state_path,
                self.pool_size,
                self.game_mode,
                trial,
            )
            for trial in trials
        ]
        return [future.result() for future in concurrent.futures.as_completed(futures)]

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def terminate(self) -> None:
        terminate_workers = getattr(self._executor, "terminate_workers", None)
        if terminate_workers is not None:
            terminate_workers()
            return
        kill_workers = getattr(self._executor, "kill_workers", None)
        if kill_workers is not None:
            kill_workers()
            return
        self._executor.shutdown(wait=False, cancel_futures=True)


def _capture_screen(instance: MGBAInstance) -> dict[str, int | str]:
    try:
        return instance.screenshot()
    except Exception:
        return {}


def _turn_snapshot(
    turn: int,
    actions: list[object],
    before: BattleState,
    state: BattleState,
    screen: dict[str, int | str],
) -> TurnSnapshot:
    return TurnSnapshot(
        turn=turn,
        actions=list(actions),
        player_hp=list(state.player_hp),
        player_max_hp=list(state.player_max_hp),
        enemy_hp=list(state.enemy_hp),
        enemy_max_hp=list(state.enemy_max_hp),
        player_fainted=list(state.player_fainted),
        enemy_fainted=list(state.enemy_fainted),
        battle_over=state.battle_over,
        player_won=state.player_won,
        screen_width=screen.get("width"),
        screen_height=screen.get("height"),
        screen_rgba_base64=screen.get("rgba_base64"),
        action_labels=[_live_action_label(action, before) for action in actions],
        player_active_slots=tuple(state.player_active_slots),
        enemy_active_slots=tuple(state.enemy_active_slots),
        player_move_names=list(state.player_move_names),
        enemy_move_names=list(state.enemy_move_names),
    )


def _live_action_label(action: object, state: BattleState) -> str:
    if getattr(action, "is_move", False):
        move_slot = getattr(action, "move_slot", None)
        name = (
            state.player_move_names[move_slot]
            if move_slot is not None and move_slot < len(state.player_move_names)
            else f"move slot {move_slot}"
        )
        party_slot = state.player_active_slots[0] if state.player_active_slots else 0
        actor = (
            state.player_names[party_slot]
            if party_slot is not None and party_slot < len(state.player_names)
            else "Your Pokemon"
        )
        return f"{actor} uses {name}"
    if getattr(action, "is_switch", False):
        target = getattr(action, "switch_target", None)
        name = state.player_names[target] if target is not None and target < len(state.player_names) else target
        return f"Switch to {name}"
    return str(getattr(action, "kind", action))
