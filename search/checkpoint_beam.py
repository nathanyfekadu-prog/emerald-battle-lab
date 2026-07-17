"""Fast battle search that branches from per-turn savestates instead of replaying prefixes."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from battle.action import Action
from battle.battle_state import BattleState
from emulator.mgba_instance import MGBAInstance
from emulator.mgba_pool import MGBAPool
from emulator.state_reader import StateReader
from outcome import Outcome, TrialSpec, TurnAction
from search.action_enumerator import ActionEnumerator


@dataclass(frozen=True)
class CheckpointNode:
    state_path: str
    state: BattleState
    line: tuple[TurnAction, ...]
    score: float
    trial_id: int


class CheckpointBeamSearch:
    """Deathless-first beam search over real cartridge states.

    Every edge begins from its parent's saved state and writes a child state. A 20-turn
    candidate therefore executes 20 cartridge turns total, rather than replaying the first
    19 turns again for every final-turn alternative.
    """

    def __init__(
        self,
        rom_path: str,
        state_path: str,
        output_dir: str | Path,
        *,
        workers: int = 8,
        beam_width: int = 8,
        actions_per_node: int = 6,
        max_turns: int = 30,
        rng_variants_per_state: int = 8,
        forbidden_answers: dict[int, set[int]] | None = None,
    ):
        self.rom_path = rom_path
        self.state_path = str(Path(state_path).expanduser().resolve())
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.pool = MGBAPool(rom_path, self.state_path, workers)
        self.enumerator = ActionEnumerator()
        self.beam_width = beam_width
        self.actions_per_node = actions_per_node
        self.max_turns = max_turns
        # HP/menu state is not the whole cartridge state. Identical-looking
        # checkpoints can have different RNG and therefore different next-turn
        # outcomes. Keep a small deterministic reservoir instead of merging all
        # of them into one and accidentally deleting the only winning route.
        self.rng_variants_per_state = max(1, rng_variants_per_state)
        self.forbidden_answers = {
            int(enemy): {int(slot) for slot in slots}
            for enemy, slots in (forbidden_answers or {}).items()
        }
        self._trial_id = 0

    def search(
        self,
        progress: Any | None = None,
        initial_frontier: list[CheckpointNode] | None = None,
    ) -> CheckpointNode | None:
        if initial_frontier:
            frontier = initial_frontier
        else:
            root_state = self._read_state(self.state_path)
            root = CheckpointNode(self.state_path, root_state, (), self._score(root_state, 0), 0)
            frontier = [root]
        manifest: dict[str, Any] = {"source": self.state_path, "depths": []}
        try:
            for depth in range(1, self.max_turns + 1):
                trials: list[TrialSpec] = []
                parents: dict[int, tuple[CheckpointNode, TurnAction, str]] = {}
                for node_index, node in enumerate(frontier):
                    raw_actions = self.enumerator.legal_actions(node.state)
                    raw_moves = [turn for turn in raw_actions if all(action.is_move for action in turn)]
                    raw_switches = [turn for turn in raw_actions if any(action.is_switch for action in turn)]
                    # The ordinary enumerator intentionally filters tactically dubious
                    # switches. A complete cartridge beam must still branch them: a switch
                    # that looks bad for one turn can be the only deathless multi-turn route.
                    ranked_actions = self.enumerator.prioritize(raw_moves, node.state) + raw_switches
                    ranked_actions = self._exclude_forbidden_answers(ranked_actions, node.state)
                    actions = self._diverse_actions(ranked_actions, self.actions_per_node)
                    actions = self._switch_guard(actions, node.line)
                    for action_index, turn_action in enumerate(actions):
                        self._trial_id += 1
                        child_path = str(self.output_dir / f"d{depth:02d}-n{node_index:02d}-a{action_index:02d}-t{self._trial_id}.ss0")
                        parents[self._trial_id] = (node, turn_action, child_path)
                        trials.append(TrialSpec(
                            trial_id=self._trial_id,
                            actions=[turn_action],
                            max_turns=1,
                            capture_screens=False,
                            start_state_path=node.state_path,
                            output_state_path=child_path,
                            stop_on_player_faint=True,
                        ))
                if not trials:
                    return None
                outcomes = self.pool.run_trials(trials)
                candidate_buckets: dict[tuple[Any, ...], list[CheckpointNode]] = {}
                for outcome in outcomes:
                    parent, turn_action, child_path = parents[outcome.trial_id]
                    if outcome.error or not Path(child_path).is_file():
                        continue
                    state = outcome.final_state
                    # A Nuzlocke search never keeps a state with a new death.
                    if self._party_deaths(state):
                        continue
                    line = parent.line + (turn_action,)
                    score = self._score(state, len(line))
                    no_progress_move = (
                        all(action.is_move for action in turn_action)
                        and tuple(state.player_hp) == tuple(parent.state.player_hp)
                        and tuple(state.enemy_hp) == tuple(parent.state.enemy_hp)
                    )
                    if no_progress_move:
                        # Preserve pivots that can escape sleep, Disable, immunity, or a
                        # trapped matchup. Otherwise a harmless repeated move can occupy
                        # every beam slot merely because it retains high team HP.
                        score -= 350.0
                    child = CheckpointNode(child_path, state, line, score, outcome.trial_id)
                    if state.battle_over and state.player_won and not self._party_deaths(state):
                        self._write_result(child, manifest, status="won")
                        return child
                    # The decoder cannot yet expose every volatile (sleep turns,
                    # stat stages, Disable, Substitute, etc.). Recent actions are
                    # therefore part of the merge key, while the per-key reservoir
                    # preserves still-hidden RNG/volatile differences.
                    signature = (self._signature(state), self._line_key(line[-3:]))
                    bucket = candidate_buckets.setdefault(signature, [])
                    bucket.append(child)
                    bucket.sort(key=lambda item: (-item.score, self._line_key(item.line), item.trial_id))
                    del bucket[self.rng_variants_per_state:]
                candidates = [
                    node for bucket in candidate_buckets.values() for node in bucket
                ]
                frontier = sorted(
                    candidates,
                    key=lambda node: (-node.score, self._line_key(node.line), node.trial_id),
                )[: self.beam_width]
                manifest["depths"].append({
                    "depth": depth, "trials": len(trials), "survivors": len(candidates),
                    "visible_states": len(candidate_buckets),
                    "rng_variants_per_state": self.rng_variants_per_state,
                    "frontier": [self._node_payload(node) for node in frontier],
                })
                self._write_manifest(manifest)
                if progress is not None:
                    progress(depth, len(trials), frontier)
                self._remove_unselected_states(frontier, depth)
            if frontier:
                self._write_result(frontier[0], manifest, status="depth-limit")
            return None
        finally:
            self.pool.shutdown()

    @staticmethod
    def _switch_guard(actions: list[TurnAction], line: tuple[TurnAction, ...]) -> list[TurnAction]:
        if not line or not all(action.is_switch for action in line[-1]):
            return actions
        moves = [turn for turn in actions if all(action.is_move for action in turn)]
        return moves or actions

    def _exclude_forbidden_answers(
        self, actions: list[TurnAction], state: BattleState,
    ) -> list[TurnAction]:
        forbidden: set[int] = set()
        for enemy_slot, party_slots in self.forbidden_answers.items():
            if enemy_slot < len(state.enemy_fainted) and not state.enemy_fainted[enemy_slot]:
                forbidden.update(party_slots)
        if not forbidden:
            return actions
        active_by_field = {
            field_slot: party_slot
            for field_slot, party_slot in enumerate(state.player_active_slots)
            if party_slot is not None
        }

        def legal(turn: TurnAction) -> bool:
            # A forbidden answer may not be switched in. If it already occupies a
            # field position (common when testing alternate doubles leads), that
            # battler must switch out this turn; do not accidentally delete every
            # legal action and strand the search.
            if any(action.is_switch and action.switch_target in forbidden for action in turn):
                return False
            actions_by_actor = {action.actor_slot: action for action in turn}
            for field_slot, party_slot in active_by_field.items():
                if party_slot not in forbidden:
                    continue
                action = actions_by_actor.get(field_slot)
                if action is None or not action.is_switch or action.switch_target in forbidden:
                    return False
            return True

        return [turn for turn in actions if legal(turn)]

    @staticmethod
    def _diverse_actions(actions: list[TurnAction], limit: int) -> list[TurnAction]:
        """Keep attacks and pivots represented instead of letting four moves crowd out switches."""
        if actions and len(actions[0]) == 2:
            categories = [
                [turn for turn in actions if turn[0].is_move and turn[1].is_move],
                [turn for turn in actions if turn[0].is_move and turn[1].is_switch],
                [turn for turn in actions if turn[0].is_switch and turn[1].is_move],
                [turn for turn in actions if turn[0].is_switch and turn[1].is_switch],
            ]
            # Round-robin is intentional: doubles must preserve both left-slot
            # and right-slot pivots. Taking the first N "contains a switch"
            # combinations only ever switched the right battler because of
            # Cartesian-product enumeration order.
            selected: list[TurnAction] = []
            index = 0
            while len(selected) < limit and any(index < len(group) for group in categories):
                for group in categories:
                    if index < len(group) and group[index] not in selected:
                        selected.append(group[index])
                        if len(selected) >= limit:
                            break
                index += 1
            if len(selected) < limit:
                selected.extend(turn for turn in actions if turn not in selected)
            return selected[:limit]
        moves = [turn for turn in actions if all(action.is_move for action in turn)]
        switches = [turn for turn in actions if any(action.is_switch for action in turn)]
        if not moves or not switches:
            return actions[:limit]
        move_limit = max(1, (limit + 1) // 2)
        switch_limit = max(1, limit - move_limit)
        selected = moves[:move_limit] + switches[:switch_limit]
        if len(selected) < limit:
            selected.extend(action for action in actions if action not in selected)
        return selected[:limit]

    @staticmethod
    def _score(state: BattleState, depth: int) -> float:
        enemy_present = sum(max_hp > 0 for max_hp in state.enemy_max_hp)
        enemy_faints = sum(state.enemy_fainted)
        enemy_remaining = sum(state.enemy_hp) / max(1, sum(state.enemy_max_hp))
        player_remaining = sum(state.player_hp) / max(1, sum(state.player_max_hp))
        living_ratios = [
            hp / max_hp for hp, max_hp in zip(state.player_hp, state.player_max_hp)
            if max_hp > 0 and hp > 0
        ]
        minimum_ratio = min(living_ratios, default=0.0)
        uncontrolled_one_hp = any(
            max_hp > 0 and hp == 1
            for hp, max_hp in zip(state.player_hp, state.player_max_hp)
        )
        fragile_penalty = 900.0 if not state.battle_over and uncontrolled_one_hp else 0.0
        return (
            enemy_faints * 1000.0 - enemy_remaining * 300.0
            + player_remaining * 180.0 + minimum_ratio * 240.0
            - fragile_penalty - depth * 0.1 - enemy_present
        )

    @staticmethod
    def _party_deaths(state: BattleState) -> tuple[int, ...]:
        return tuple(
            index for index, (hp, max_hp) in enumerate(zip(state.player_hp, state.player_max_hp))
            if max_hp > 0 and hp <= 0
        )

    @staticmethod
    def _signature(state: BattleState) -> tuple[Any, ...]:
        return (
            tuple(state.player_hp), tuple(state.enemy_hp), tuple(state.player_fainted),
            tuple(state.enemy_fainted), tuple(state.player_active_slots), tuple(state.enemy_active_slots),
            tuple(state.player_move_names), state.battle_over, state.player_won,
        )

    @staticmethod
    def _line_key(line: tuple[TurnAction, ...]) -> tuple[tuple[tuple[Any, ...], ...], ...]:
        """Stable tie-breaker so parallel completion order cannot change the saved route."""
        return tuple(tuple(
            (
                action.kind, action.actor_slot,
                -1 if action.move_slot is None else action.move_slot,
                -1 if action.target_slot is None else action.target_slot,
                -1 if action.switch_target is None else action.switch_target,
            )
            for action in turn
        ) for turn in line)

    def _read_state(self, path: str) -> BattleState:
        instance = MGBAInstance(self.rom_path, path, 98)
        try:
            return StateReader(instance).read()
        finally:
            instance.shutdown()

    def _write_result(self, node: CheckpointNode, manifest: dict[str, Any], *, status: str) -> None:
        manifest["status"] = status
        manifest["result"] = self._node_payload(node)
        self._write_manifest(manifest)

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        (self.output_dir / "search.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    @staticmethod
    def _node_payload(node: CheckpointNode) -> dict[str, Any]:
        return {
            "state_path": node.state_path, "score": node.score, "depth": len(node.line),
            "line": [[action.__dict__ for action in turn] for turn in node.line],
            "player_hp": list(node.state.player_hp), "enemy_hp": list(node.state.enemy_hp),
        }

    def _remove_unselected_states(self, frontier: list[CheckpointNode], depth: int) -> None:
        keep = {node.state_path for node in frontier}
        for path in self.output_dir.glob(f"d{depth:02d}-*.ss0"):
            if str(path) not in keep:
                path.unlink(missing_ok=True)


__all__ = ["CheckpointBeamSearch", "CheckpointNode"]
