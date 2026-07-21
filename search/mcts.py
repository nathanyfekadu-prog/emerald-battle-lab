from __future__ import annotations

import math
import statistics
import threading
import time
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any, Callable

import config
from battle.action import Action
from battle.battle_state import BattleState
from emulator.mgba_pool import MGBAPool
from outcome import Outcome, TrialSpec, TurnAction, TurnSnapshot
from search.action_enumerator import ActionEnumerator


class SearchCancelled(RuntimeError):
    pass


@dataclass
class LineStats:
    win_rate: float = 0.0
    avg_hp: float = 0.0
    faint_rate: float = 0.0
    sack_rate: float = 0.0
    score: float = 0.0
    avg_faints: float = 0.0
    label: str = "DANGER"
    enemy_hp_pct: float = 0.0


@dataclass
class Node:
    turn_number: int
    action_taken: TurnAction | None
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)
    visit_count: int = 0
    total_score: float = 0.0
    outcomes: list[Outcome] = field(default_factory=list)
    is_sack_node: bool = False
    untried_actions: list[TurnAction] = field(default_factory=list)
    stats: LineStats = field(default_factory=LineStats)

    def action_sequence(self) -> list[TurnAction]:
        actions: list[TurnAction] = []
        node: Node | None = self
        while node is not None and node.action_taken is not None:
            actions.append(node.action_taken)
            node = node.parent
        actions.reverse()
        return actions

    def ucb1(self) -> float:
        if self.visit_count == 0:
            return math.inf
        if self.parent is None or self.parent.visit_count == 0:
            return self.total_score / self.visit_count
        return (self.total_score / self.visit_count) + config.UCB1_C * math.sqrt(
            math.log(self.parent.visit_count) / self.visit_count
        )


@dataclass
class FlowchartNode:
    turn: int
    action: TurnAction | None
    win_rate: float
    avg_hp: float
    faint_rate: float
    sack_rate: float
    visit_count: int
    line_label: str
    children: list["FlowchartNode"] = field(default_factory=list)
    branch_condition: str = "EXPECTED"
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = _serialize_turn_action(self.action)
        data["children"] = [child.to_dict() for child in self.children]
        return data


@dataclass
class SearchResult:
    best_deathless_line: list[TurnAction] | None
    best_sack_line: list[TurnAction] | None
    recommended_line: list[TurnAction]
    projected_turns: list[TurnSnapshot]
    win_probability: float
    faint_probability: float
    avg_hp_remaining: float
    nuzlocke_safe: bool
    has_deathless_line: bool
    best_deathless_win_rate: float | None
    best_sack_win_rate: float | None
    flowchart: FlowchartNode
    total_trials_run: int
    nodes_explored: int
    search_time_seconds: float
    ranked_lines: list[FlowchartNode] = field(default_factory=list)
    validated_lines: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "best_deathless_line": _serialize_line(self.best_deathless_line),
            "best_sack_line": _serialize_line(self.best_sack_line),
            "recommended_line": _serialize_line(self.recommended_line),
            "projected_turns": [_serialize_turn_snapshot(snapshot) for snapshot in self.projected_turns],
            "win_probability": self.win_probability,
            "faint_probability": self.faint_probability,
            "avg_hp_remaining": self.avg_hp_remaining,
            "nuzlocke_safe": self.nuzlocke_safe,
            "has_deathless_line": self.has_deathless_line,
            "best_deathless_win_rate": self.best_deathless_win_rate,
            "best_sack_win_rate": self.best_sack_win_rate,
            "flowchart": self.flowchart.to_dict(),
            "total_trials_run": self.total_trials_run,
            "nodes_explored": self.nodes_explored,
            "search_time_seconds": self.search_time_seconds,
            "ranked_lines": [line.to_dict() for line in self.ranked_lines],
            "validated_lines": self.validated_lines,
        }


class MCTS:
    def __init__(
        self,
        rom_path: str,
        save_state_path: str,
        pool_size: int = config.POOL_SIZE,
        max_turns: int = config.MAX_TURNS,
        trials_per_node: int = config.TRIALS_PER_NODE,
        final_line_trials: int = config.FINAL_LINE_TRIALS,
        final_line_candidates: int = config.FINAL_LINE_CANDIDATES,
        on_node_visited: Callable[[dict[str, Any]], None] | None = None,
        cancel_event: threading.Event | None = None,
        game_mode: str = "run-and-bun",
    ):
        self.rom_path = rom_path
        self.save_state_path = save_state_path
        self.pool = MGBAPool(rom_path, save_state_path, pool_size, game_mode=game_mode)
        self.max_turns = max_turns
        self.trials_per_node = trials_per_node
        self.final_line_trials = max(1, final_line_trials)
        self.final_line_candidates = max(1, final_line_candidates)
        self.game_mode = game_mode
        self.enumerator = ActionEnumerator(game_mode)
        self.total_trials_run = 0
        self._trial_counter = 0
        self.initial_state = self._read_initial_state()
        self.root_actions = self.enumerator.prioritize(
            self.enumerator.legal_actions(self.initial_state),
            self.initial_state,
        )
        self.on_node_visited = on_node_visited
        self.cancel_event = cancel_event or threading.Event()

    def shutdown(self) -> None:
        try:
            self.pool.shutdown()
        except Exception:
            if not self.cancel_event.is_set():
                raise

    def cancel(self) -> None:
        self.cancel_event.set()
        self.pool.terminate()

    def _raise_if_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise SearchCancelled("Search killed")

    def search(self, iterations: int = config.MCTS_ITERATIONS) -> SearchResult:
        start = time.perf_counter()
        root = Node(0, None, untried_actions=list(self.root_actions))

        for iteration in range(iterations):
            self._raise_if_cancelled()
            node = self._select(root)
            if node.turn_number < self.max_turns:
                node = self._expand(node)
            if self.on_node_visited is not None:
                explored = len([item for item in _walk_nodes(root) if item.action_taken is not None])
                self.on_node_visited(
                    {
                        "progress": {
                            "iterations_done": iteration,
                            "iterations_total": iterations,
                            "trials_run": self.total_trials_run,
                            "nodes_explored": explored,
                            "best_win_rate": max(
                                (
                                    item.stats.win_rate
                                    for item in _walk_nodes(root)
                                    if item.action_taken is not None
                                ),
                                default=0.0,
                            ),
                            "has_deathless": any(
                                item.stats.label == "DEATHLESS"
                                for item in _walk_nodes(root)
                            ),
                            "elapsed_seconds": time.perf_counter() - start,
                            "message": f"Running whole-battle rollout {iteration + 1}/{iterations}",
                        },
                    }
                )
            outcomes = self._simulate(node)
            self._raise_if_cancelled()
            stats = score_outcomes(outcomes, self.initial_state)
            node.stats = stats
            node.is_sack_node = stats.label == "SACK LINE"
            self._backpropagate(node, stats.score, outcomes)
            ranked_live = sorted(
                [item for item in _walk_nodes(root) if item.action_taken is not None and item.visit_count],
                key=lambda item: (item.stats.score, item.stats.win_rate),
                reverse=True,
            )
            current_best_id = _node_id(ranked_live[0]) if ranked_live else None
            if self.on_node_visited is not None:
                self.on_node_visited(
                    {
                        "node": _node_event_data(node, self.initial_state, current_best_id),
                        "simulators": _simulator_event_data(outcomes, self.initial_state),
                        "progress": {
                            "iterations_done": iteration + 1,
                            "iterations_total": iterations,
                            "trials_run": self.total_trials_run,
                            "nodes_explored": len(
                                [
                                    item
                                    for item in _walk_nodes(root)
                                    if item.action_taken is not None
                                ]
                            ),
                            "best_win_rate": max(
                                (
                                    item.stats.win_rate
                                    for item in _walk_nodes(root)
                                    if item.action_taken is not None
                                ),
                                default=0.0,
                            ),
                            "has_deathless": any(
                                item.stats.label == "DEATHLESS"
                                for item in _walk_nodes(root)
                            ),
                            "elapsed_seconds": time.perf_counter() - start,
                        },
                    }
                )

        ranked_nodes = sorted(
            [node for node in _walk_nodes(root) if node.action_taken is not None and node.visit_count],
            key=lambda node: (node.stats.score, node.stats.win_rate),
            reverse=True,
        )
        deathless = [node for node in ranked_nodes if node.stats.label == "DEATHLESS"]
        sacks = [node for node in ranked_nodes if node.stats.label == "SACK LINE"]

        best_deathless = deathless[0] if deathless else None
        best_sack = sacks[0] if sacks else None
        recommended = best_deathless or best_sack or (ranked_nodes[0] if ranked_nodes else root)
        flowchart = _flowchart_from_node(root, recommended)
        ranked_flow_nodes = [_flowchart_from_line(node) for node in ranked_nodes[:10]]
        recommended_outcome = _best_outcome(recommended)
        recommended_line = _best_full_line(recommended, self.initial_state.is_doubles, recommended_outcome)
        validated_lines = self._validate_final_lines(ranked_nodes, recommended_line)
        if validated_lines:
            best_validation = max(
                validated_lines,
                key=lambda item: (
                    bool(item.get("critical_analysis", {}).get("critical_safe")),
                    -float(item.get("critical_analysis", {}).get("critical_failure_rate", 1.0)),
                    item["stats"].win_rate,
                    -item["stats"].faint_rate,
                    item["stats"].avg_hp,
                ),
            )
            recommended_outcome = _best_outcome_from_list(best_validation["outcomes"])
            recommended_line = _trim_line_to_outcome(best_validation["line"], recommended_outcome, self.initial_state.is_doubles)
            recommended_stats = best_validation["stats"]
        else:
            recommended_stats = recommended.stats
        elapsed = time.perf_counter() - start

        return SearchResult(
            best_deathless_line=best_deathless.action_sequence() if best_deathless else None,
            best_sack_line=best_sack.action_sequence() if best_sack else None,
            recommended_line=recommended_line,
            projected_turns=recommended_outcome.turn_snapshots if recommended_outcome else [],
            win_probability=recommended_stats.win_rate,
            faint_probability=recommended_stats.faint_rate,
            avg_hp_remaining=recommended_stats.avg_hp,
            nuzlocke_safe=recommended_stats.faint_rate == 0.0,
            has_deathless_line=best_deathless is not None,
            best_deathless_win_rate=best_deathless.stats.win_rate if best_deathless else None,
            best_sack_win_rate=best_sack.stats.win_rate if best_sack else None,
            flowchart=flowchart,
            total_trials_run=self.total_trials_run,
            nodes_explored=len([node for node in _walk_nodes(root) if node.action_taken is not None]),
            search_time_seconds=elapsed,
            ranked_lines=ranked_flow_nodes,
            validated_lines=[_validation_payload(item, self.initial_state) for item in validated_lines],
        )

    def _validate_final_lines(
        self,
        ranked_nodes: list[Node],
        fallback_line: list[TurnAction],
    ) -> list[dict[str, Any]]:
        """Replay distinct full lines from the exact initial state.

        MCTS visits are for discovery. These trials are the confidence pass: no
        random fallback actions are appended and every candidate receives the
        same sample count.
        """
        lines: list[list[TurnAction]] = []
        seen: set[tuple[tuple[Action, ...], ...]] = set()
        # Always test the obvious fast clear first. If plain attacking survives
        # every replay, the solver should not invent setup turns for a Wurmple.
        fast_action = self.enumerator.finishing_action(self.root_actions, self.initial_state)
        if fast_action is not None:
            fast_line = [fast_action] * self.max_turns
            lines.append(fast_line)
            seen.add(_line_key(fast_line))
        for node in ranked_nodes:
            line = self._pad_validation_line(
                _best_full_line(node, self.initial_state.is_doubles)
            )
            key = _line_key(line)
            if line and key not in seen:
                seen.add(key)
                lines.append(line)
            if len(lines) >= self.final_line_candidates:
                break
        fallback_line = self._pad_validation_line(fallback_line)
        if fallback_line and _line_key(fallback_line) not in seen:
            lines.append(fallback_line)
        lines = lines[: self.final_line_candidates]

        validated: list[dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            self._raise_if_cancelled()
            trials: list[TrialSpec] = []
            rng_frames_by_trial: dict[int, int] = {}
            for sample in range(self.final_line_trials):
                self._trial_counter += 1
                # A long prime-sized frame window exposes far more independent RNG
                # positions than the old 97-frame loop. 256 replays now sample roughly
                # sixteen expected 1/16 crit opportunities per attacking turn.
                rng_frames = (sample * 17 + index * 5) % 997
                rng_frames_by_trial[self._trial_counter] = rng_frames
                trials.append(TrialSpec(
                    trial_id=self._trial_counter,
                    actions=list(line),
                    rng_advance_frames=rng_frames,
                    max_turns=len(line),
                    # One representative replay is enough for the live preview.
                    # Every trial still executes and contributes full HP/actions
                    # to scoring; avoiding duplicate full-frame IPC does not
                    # reduce RNG coverage or validation accuracy.
                    capture_screens=sample == 0,
                ))
            outcomes = self.pool.run_trials(trials)
            self.total_trials_run += len(outcomes)
            stats = score_outcomes(outcomes, self.initial_state)
            critical_analysis = _critical_diversion_analysis(
                outcomes, self.initial_state, rng_frames_by_trial,
            )
            item = {
                "line": line, "outcomes": outcomes, "stats": stats,
                "rng_frames_by_trial": rng_frames_by_trial,
                "critical_analysis": critical_analysis,
            }
            validated.append(item)
            if self.on_node_visited is not None:
                self.on_node_visited({
                    "simulators": _simulator_event_data(outcomes, self.initial_state),
                    "validation": _validation_payload(item, self.initial_state),
                    "progress": {
                        "iterations_done": index,
                        "iterations_total": len(lines),
                        "trials_run": self.total_trials_run,
                        "nodes_explored": len(ranked_nodes),
                        "best_win_rate": max(entry["stats"].win_rate for entry in validated),
                        "has_deathless": any(entry["stats"].faint_rate == 0 for entry in validated),
                        "message": (
                            f"Confidence check {index}/{len(lines)}: "
                            f"replayed this full line {self.final_line_trials} times"
                        ),
                    },
                })
        return validated

    def _pad_validation_line(self, line: list[TurnAction]) -> list[TurnAction]:
        """Keep a discovered win valid across ordinary damage rolls.

        Discovery records only the actions needed by that particular rollout.
        A favorable roll can therefore produce a six-turn line that needs a
        seventh attack on another seed. Final proof must have an instruction
        for that branch. The emulator stops as soon as the battle ends, so a
        deterministic attacking tail is harmless for shorter clears.
        """
        if not line or len(line) >= self.max_turns:
            return list(line)
        finisher = self._finishing_action()
        if finisher is None:
            return list(line)
        return list(line) + [finisher] * (self.max_turns - len(line))

    def _finishing_action(self) -> TurnAction | None:
        """Choose actual damage for the low-roll tail, never a status loop."""
        return self.enumerator.finishing_action(self.root_actions, self.initial_state)

    def _select(self, root: Node) -> Node:
        node = root
        while not node.untried_actions and node.children:
            deathless_candidates = [
                child
                for child in node.children
                if child.visit_count < config.MIN_DEATHLESS_VISITS
                and child.stats.faint_rate < 0.10
            ]
            if deathless_candidates:
                node = max(deathless_candidates, key=lambda child: child.ucb1())
            else:
                node = max(node.children, key=lambda child: child.ucb1())
        return node

    def _expand(self, node: Node) -> Node:
        if not node.untried_actions:
            return node
        action = node.untried_actions.pop(0)
        child = Node(
            turn_number=node.turn_number + 1,
            action_taken=action,
            parent=node,
            untried_actions=[],
        )
        if child.turn_number < self.max_turns:
            child.untried_actions = self._actions_for_node(child)
        node.children.append(child)
        return child

    def _actions_for_node(self, node: Node) -> list[TurnAction]:
        if self.initial_state.is_doubles:
            return list(self.root_actions)

        action_sequence = node.action_sequence()
        active_slot = _active_slot_after(action_sequence)
        blocked_switch_target = _previous_active_slot_if_last_action_was_switch(action_sequence)
        if active_slot == 0:
            return _without_switch_target(self.root_actions, blocked_switch_target)

        move_slots = list(range(4))
        rotation = active_slot % len(move_slots)
        move_slots = move_slots[rotation:] + move_slots[:rotation]
        actions: list[TurnAction] = [(Action.move(slot),) for slot in move_slots]
        for slot, fainted in enumerate(self.initial_state.player_fainted):
            if slot == active_slot or fainted:
                continue
            if blocked_switch_target is not None and slot == blocked_switch_target:
                continue
            actions.append((Action.switch(slot),))
        return self.enumerator.prioritize(actions, self.initial_state)

    def _simulate(self, node: Node) -> list[Outcome]:
        self._raise_if_cancelled()
        actions = node.action_sequence()
        trials = []
        for _ in range(self.trials_per_node):
            self._trial_counter += 1
            trials.append(
                TrialSpec(
                    self._trial_counter,
                    actions,
                    self._trial_counter % 11,
                    max_turns=self.max_turns,
                    capture_screens=False,
                )
            )
        try:
            outcomes = self.pool.run_trials(trials)
        except Exception as exc:
            if self.cancel_event.is_set():
                raise SearchCancelled("Search killed") from exc
            raise
        self.total_trials_run += len(outcomes)
        return outcomes

    def _backpropagate(self, node: Node, score: float, outcomes: list[Outcome]) -> None:
        while node is not None:
            node.visit_count += 1
            node.total_score += score
            node.outcomes.extend(outcomes)
            node = node.parent

    def _read_initial_state(self) -> BattleState:
        return self.pool.warmup_and_read_state()


def score_outcomes(outcomes: list[Outcome], initial_state: BattleState) -> LineStats:
    valid = [outcome for outcome in outcomes if outcome.error is None]
    if not valid:
        return LineStats(label="LOSS")

    total_max_hp = max(1, sum(initial_state.player_max_hp))
    avg_hp = mean(sum(outcome.final_player_hp) / total_max_hp for outcome in valid)
    faint_rate = mean(outcome.player_fainted_count > 0 for outcome in valid)
    sack_rate = mean(outcome.is_sack_line for outcome in valid)
    avg_faints = mean(outcome.player_fainted_count for outcome in valid)
    enemy_hp_pct = mean(
        sum(outcome.final_enemy_hp) / max(1, sum(outcome.final_state.enemy_max_hp))
        for outcome in valid
    )
    terminal = [outcome for outcome in valid if outcome.final_state.battle_over]
    if terminal:
        win_rate = sum(outcome.battle_won for outcome in valid) / len(valid)
    else:
        # Short-horizon searches often stop before the ROM reaches a win/loss
        # screen. Use a conservative survival/HP projection and penalize lines
        # that leave the opposing side healthy.
        win_rate = max(0.0, min(1.0, avg_hp * (1.0 - faint_rate) * (1.0 - enemy_hp_pct * 0.5)))
    nuzlocke_safety = 1.0 if faint_rate == 0.0 else 0.0
    is_deathless = faint_rate < 0.10
    is_sack = sack_rate >= 0.50 and win_rate >= 0.70
    avg_switches = mean(_switch_count(outcome.actions_taken) for outcome in valid)
    avg_consecutive_switches = mean(_consecutive_switch_count(outcome.actions_taken) for outcome in valid)

    base_score = (
        win_rate * config.WIN_WEIGHT
        + avg_hp * config.HP_WEIGHT
        + (1 - faint_rate) * config.SURVIVAL_WEIGHT
        + nuzlocke_safety * config.NUZLOCKE_WEIGHT
        - avg_switches * config.SWITCH_ACTION_PENALTY
        - avg_consecutive_switches * config.CONSECUTIVE_SWITCH_PENALTY
    )

    if is_deathless:
        score = base_score + config.DEATHLESS_BONUS
    elif is_sack:
        extra_faints = max(0.0, avg_faints - 1)
        score = base_score - (extra_faints * config.MULTI_SACK_PENALTY)
    else:
        score = base_score

    label = classify_line(win_rate, faint_rate, sack_rate, score)
    return LineStats(win_rate, avg_hp, faint_rate, sack_rate, score, avg_faints, label, enemy_hp_pct)


def classify_line(win_rate: float, faint_rate: float, sack_rate: float, score: float) -> str:
    if win_rate < 0.20:
        return "LOSS"
    if win_rate < 0.50:
        return "AVOID"
    if faint_rate < 0.10:
        return "DEATHLESS"
    if faint_rate >= 0.50 and win_rate >= 0.70 and sack_rate >= 0.50:
        return "SACK LINE"
    if win_rate >= 0.70 and faint_rate < 0.30:
        return "GOOD"
    if score < 6.0:
        return "DANGER"
    if win_rate >= 0.55:
        return "RISKY"
    return "DANGER"


def _walk_nodes(root: Node) -> list[Node]:
    nodes = [root]
    for child in root.children:
        nodes.extend(_walk_nodes(child))
    return nodes


def _best_outcome(node: Node) -> Outcome | None:
    valid = [outcome for outcome in node.outcomes if outcome.error is None and outcome.actions_taken]
    if not valid:
        return None
    return max(
        valid,
        key=lambda outcome: (
            outcome.battle_won,
            -outcome.player_fainted_count,
            sum(outcome.final_player_hp),
            -sum(outcome.final_enemy_hp),
        ),
    )


def _best_outcome_from_list(outcomes: list[Outcome]) -> Outcome | None:
    valid = [outcome for outcome in outcomes if outcome.error is None and outcome.actions_taken]
    if not valid:
        return None
    return max(
        valid,
        key=lambda outcome: (
            outcome.battle_won,
            -outcome.player_fainted_count,
            sum(outcome.final_player_hp),
            -sum(outcome.final_enemy_hp),
        ),
    )


def _line_key(line: list[TurnAction]) -> tuple[tuple[Action, ...], ...]:
    return tuple(tuple(turn) for turn in line)


def _wilson_interval(wins: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    p = wins / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def _critical_diversion_analysis(
    outcomes: list[Outcome],
    initial_state: BattleState,
    rng_frames_by_trial: dict[int, int],
) -> dict[str, Any]:
    """Find damage spikes and the state/continuation changes they produced.

    The GBA bridge does not expose the battle script's critical-hit flag. A damage
    increase of at least 35% over the turn's median non-zero loss is therefore marked
    ``crit-like`` rather than falsely claimed as a forced crit. Ordinary Gen III damage
    rolls fit well below that threshold. The calc flowchart separately forces the crit
    bit mathematically for every turn.
    """
    valid = [outcome for outcome in outcomes if outcome.error is None and outcome.turn_snapshots]
    if not valid:
        return {
            "critical_safe": False, "critical_failure_rate": 1.0,
            "enemy_crit_like_events": [], "player_crit_like_events": [],
            "material_diversions": [], "note": "No complete emulator turn data was available.",
        }

    def hp_before(outcome: Outcome, turn_index: int, player: bool) -> list[int]:
        if turn_index == 0:
            return list(initial_state.player_hp if player else initial_state.enemy_hp)
        previous = outcome.turn_snapshots[turn_index - 1]
        return list(previous.player_hp if player else previous.enemy_hp)

    def total_loss(outcome: Outcome, turn_index: int, player: bool) -> int:
        snapshot = outcome.turn_snapshots[turn_index]
        after = list(snapshot.player_hp if player else snapshot.enemy_hp)
        before = hp_before(outcome, turn_index, player)
        return sum(max(0, old - (after[index] if index < len(after) else 0)) for index, old in enumerate(before))

    def signature(outcome: Outcome, turn_index: int) -> tuple[Any, ...]:
        snap = outcome.turn_snapshots[turn_index]
        return (
            tuple(snap.player_active_slots), tuple(snap.enemy_active_slots),
            tuple(snap.player_fainted), tuple(snap.enemy_fainted),
        )

    enemy_events: list[dict[str, Any]] = []
    player_events: list[dict[str, Any]] = []
    max_turns = max(len(outcome.turn_snapshots) for outcome in valid)
    for turn_index in range(max_turns):
        present = [outcome for outcome in valid if turn_index < len(outcome.turn_snapshots)]
        if len(present) < 4:
            continue
        signatures = [signature(outcome, turn_index) for outcome in present]
        modal_signature = max(set(signatures), key=signatures.count)
        for player_takes_damage, destination in ((True, enemy_events), (False, player_events)):
            losses = {outcome.trial_id: total_loss(outcome, turn_index, player_takes_damage) for outcome in present}
            nonzero = [loss for loss in losses.values() if loss > 0]
            if len(nonzero) < 2:
                continue
            baseline = float(statistics.median(nonzero))
            threshold = max(baseline * 1.35, baseline + 2)
            for outcome in present:
                loss = losses[outcome.trial_id]
                if loss < threshold:
                    continue
                changed = signature(outcome, turn_index) != modal_signature
                continuation = [
                    " + ".join(snapshot.action_labels)
                    for snapshot in outcome.turn_snapshots[turn_index + 1 : turn_index + 4]
                ]
                destination.append({
                    "turn": turn_index + 1,
                    "trial_id": outcome.trial_id,
                    "rng_frames": rng_frames_by_trial.get(outcome.trial_id, 0),
                    "damage": loss,
                    "baseline_damage": round(baseline),
                    "state_changed": changed,
                    "won": outcome.battle_won,
                    "deathless": outcome.battle_won and outcome.player_fainted_count == 0,
                    "continuation": continuation,
                    "player_hp": list(outcome.turn_snapshots[turn_index].player_hp),
                    "enemy_hp": list(outcome.turn_snapshots[turn_index].enemy_hp),
                })

    # Deduplicate repeated high rolls from the same turn/state while retaining one
    # exact RNG offset that can be replayed and recorded later.
    def compact(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for event in events:
            key = (event["turn"], event["state_changed"], event["won"], event["deathless"], tuple(event["player_hp"]), tuple(event["enemy_hp"]))
            if key not in seen:
                seen.add(key)
                result.append(event)
        return result

    enemy_events = compact(enemy_events)
    player_events = compact(player_events)
    critical_failures = [event for event in enemy_events if not event["deathless"]]
    material = [
        {**event, "side": "enemy"} for event in enemy_events if event["state_changed"]
    ] + [
        {**event, "side": "player"} for event in player_events if event["state_changed"]
    ]
    baseline_outcome = next(
        (outcome for outcome in valid if outcome.battle_won and outcome.player_fainted_count == 0),
        valid[0],
    )
    safe_diversion = next((event for event in material if event["deathless"]), None)
    # A failed high-damage branch is still useful: the recorder can recreate its
    # exact RNG offset, checkpoint immediately after the changed turn, and ask a
    # fresh search to rescue the position. Prefer it whenever the fixed line was
    # not crit-safe; otherwise record a harmless material diversion for comparison.
    rescue_diversion = next((event for event in enemy_events if not event["deathless"]), None)
    selected_diversion = safe_diversion if not critical_failures else rescue_diversion
    return {
        "critical_safe": not critical_failures,
        "critical_failure_rate": round(len(critical_failures) / max(1, len(enemy_events)), 4),
        "enemy_crit_like_events": enemy_events,
        "player_crit_like_events": player_events,
        "material_diversions": material,
        "baseline_rng_frames": rng_frames_by_trial.get(baseline_outcome.trial_id, 0),
        "diversion_rng_frames": selected_diversion.get("rng_frames") if selected_diversion else None,
        "diversion": selected_diversion,
        "rescue_diversion": rescue_diversion,
        "note": (
            "Crit-like means an observed cartridge damage spike at least 35% above the turn median; "
            "the forced-crit calculator remains the deterministic coverage layer."
        ),
    }


def _validation_payload(item: dict[str, Any], state: BattleState) -> dict[str, Any]:
    outcomes: list[Outcome] = item["outcomes"]
    valid = [outcome for outcome in outcomes if outcome.error is None]
    wins = sum(outcome.battle_won for outcome in valid)
    losses = sum(outcome.final_state.battle_over and not outcome.battle_won for outcome in valid)
    incomplete = sum(not outcome.final_state.battle_over for outcome in valid)
    errors = len(outcomes) - len(valid)
    error_messages = sorted({
        outcome.error.splitlines()[-1]
        for outcome in outcomes
        if outcome.error
    })
    low, high = _wilson_interval(wins, len(valid))
    stats: LineStats = item["stats"]
    representative = _best_outcome_from_list(valid) or (valid[0] if valid else None)
    live_labels = (
        [" + ".join(snapshot.action_labels) for snapshot in representative.turn_snapshots]
        if representative is not None
        else []
    )
    fallback_labels = [
        _format_turn_action(turn, state, _active_slot_after(item["line"][:index]))
        for index, turn in enumerate(item["line"])
    ]
    winning_outcome = next(
        (outcome for outcome in outcomes if outcome.battle_won and outcome.player_fainted_count == 0),
        next((outcome for outcome in outcomes if outcome.battle_won), None),
    )
    return {
        "line": live_labels or fallback_labels,
        "trials": len(outcomes),
        "wins": wins,
        "losses": losses,
        "incomplete": incomplete,
        "errors": errors,
        "error_messages": error_messages[:3],
        "clear_rate": wins / len(valid) if valid else 0.0,
        "confidence_low": low,
        "confidence_high": high,
        "faint_rate": stats.faint_rate,
        "average_hp": stats.avg_hp,
        "label": stats.label,
        "actions": _serialize_line(item["line"]),
        "winning_rng_frames": (
            item.get("rng_frames_by_trial", {}).get(winning_outcome.trial_id)
            if winning_outcome is not None else None
        ),
        "deathless_winning_rng_frames": [
            item.get("rng_frames_by_trial", {}).get(outcome.trial_id)
            for outcome in outcomes
            if outcome.battle_won and outcome.player_fainted_count == 0
            and item.get("rng_frames_by_trial", {}).get(outcome.trial_id) is not None
        ],
        "critical_analysis": item.get("critical_analysis", {}),
    }


def _best_full_line(node: Node, is_doubles: bool, best: Outcome | None = None) -> list[TurnAction]:
    fallback = node.action_sequence()
    if best is None:
        best = _best_outcome(node)
    if best is None:
        return fallback
    full_line = _actions_to_turns(best.actions_taken, is_doubles)
    return full_line if len(full_line) > len(fallback) else fallback


def _actions_to_turns(actions: list[Action], is_doubles: bool) -> list[TurnAction]:
    if not is_doubles:
        return [(action,) for action in actions]
    turns: list[TurnAction] = []
    index = 0
    while index < len(actions):
        turns.append(tuple(actions[index : index + 2]))
        index += 2
    return turns


def _trim_line_to_outcome(
    line: list[TurnAction], outcome: Outcome | None, is_doubles: bool
) -> list[TurnAction]:
    """Hide unused safety-tail instructions from the user-facing plan."""
    if outcome is None or not outcome.actions_taken:
        return list(line)
    executed = _actions_to_turns(outcome.actions_taken, is_doubles)
    return list(line[: len(executed)])


def _flowchart_from_node(root: Node, recommended: Node) -> FlowchartNode:
    flow = FlowchartNode(0, None, 0, 0, 0, 0, root.visit_count, "ROOT")
    for child in sorted(root.children, key=lambda node: node.stats.score, reverse=True)[:4]:
        flow.children.append(_flowchart_from_line(child, recommended))
    return flow


def _flowchart_from_line(node: Node, recommended: Node | None = None) -> FlowchartNode:
    branch = "EXPECTED"
    if node.stats.label == "SACK LINE":
        branch = "SACK PAYS OFF"
    elif node.stats.faint_rate > 0:
        branch = "IF slot 0 faints"
    child_nodes = sorted(node.children, key=lambda child: child.stats.score, reverse=True)[:3]
    return FlowchartNode(
        turn=node.turn_number,
        action=node.action_taken,
        win_rate=node.stats.win_rate,
        avg_hp=node.stats.avg_hp,
        faint_rate=node.stats.faint_rate,
        sack_rate=node.stats.sack_rate,
        visit_count=node.visit_count,
        line_label=node.stats.label,
        children=[_flowchart_from_line(child, recommended) for child in child_nodes],
        branch_condition=branch,
        score=node.stats.score,
    )


def _node_event_data(
    node: Node,
    state: BattleState | None = None,
    current_best_id: str | None = None,
) -> dict[str, Any]:
    parent_id = _node_id(node.parent) if node.parent else None
    return {
        "id": _node_id(node),
        "parent_id": parent_id,
        "turn": node.turn_number,
        "action": _format_turn_action(node.action_taken, state, _active_slot_before(node)),
        "win_rate": node.stats.win_rate,
        "faint_rate": node.stats.faint_rate,
        "avg_hp": node.stats.avg_hp,
        "enemy_hp_pct": node.stats.enemy_hp_pct,
        "visit_count": node.visit_count + 1,
        "label": node.stats.label,
        "branch_condition": "SACK PAYS OFF"
        if node.stats.label == "SACK LINE"
        else ("IF slot 0 faints" if node.stats.faint_rate > 0 else "EXPECTED"),
        "is_recommended": _node_id(node) == current_best_id,
    }


def _simulator_event_data(outcomes: list[Outcome], state: BattleState | None = None) -> list[dict[str, Any]]:
    return [_simulator_trial_data(outcome, state) for outcome in outcomes]


def _simulator_trial_data(outcome: Outcome, state: BattleState | None = None) -> dict[str, Any]:
    final_state = outcome.final_state
    screen_width = outcome.screen_width
    screen_height = outcome.screen_height
    screen_rgba_base64 = outcome.screen_rgba_base64
    if not screen_rgba_base64 and outcome.turn_snapshots:
        last_snapshot = outcome.turn_snapshots[-1]
        screen_width = last_snapshot.screen_width
        screen_height = last_snapshot.screen_height
        screen_rgba_base64 = last_snapshot.screen_rgba_base64
    if outcome.error:
        status = "ERROR"
        reason = "worker error"
    elif final_state.battle_over and final_state.player_won:
        status = "WIN"
        reason = "battle ended with a win"
    elif final_state.battle_over:
        status = "BLACKOUT"
        reason = "battle ended with a loss"
    else:
        status = "IN PROGRESS"
        reason = "search depth ended before battle_over"

    return {
        "trial_id": outcome.trial_id,
        "instance_id": outcome.instance_id,
        "status": status,
        "reason": reason,
        "battle_over": final_state.battle_over,
        "player_won": final_state.player_won,
        "frames_run": outcome.frames_run,
        "turns_attempted": len(outcome.actions_taken),
        "actions": [
            label
            for snapshot in outcome.turn_snapshots
            for label in (snapshot.action_labels or [])
        ] or _format_action_sequence(outcome.actions_taken, state),
        "turns": [
            {
                "turn": snapshot.turn,
                "actions": list(snapshot.action_labels),
                "player_hp": list(snapshot.player_hp),
                "enemy_hp": list(snapshot.enemy_hp),
                "player_active_slots": list(snapshot.player_active_slots),
                "enemy_active_slots": list(snapshot.enemy_active_slots),
                "player_moves": list(snapshot.player_move_names),
                "enemy_moves": list(snapshot.enemy_move_names),
                "screen": {
                    "width": snapshot.screen_width,
                    "height": snapshot.screen_height,
                    "rgba_base64": snapshot.screen_rgba_base64,
                },
            }
            for snapshot in outcome.turn_snapshots
        ],
        "player_hp": list(outcome.final_player_hp),
        "player_hp_total": sum(outcome.final_player_hp),
        "player_max_hp_total": sum(final_state.player_max_hp),
        "enemy_hp": list(outcome.final_enemy_hp),
        "enemy_hp_total": sum(outcome.final_enemy_hp),
        "enemy_max_hp_total": sum(final_state.enemy_max_hp),
        "player_faints": outcome.player_fainted_count,
        "enemy_faints": outcome.enemy_fainted_count,
        "error": outcome.error.splitlines()[-1] if outcome.error else None,
        "screen": {
            "width": screen_width,
            "height": screen_height,
            "rgba_base64": screen_rgba_base64,
        }
        if screen_rgba_base64
        else None,
    }


def _format_action_sequence(actions: list[Action], state: BattleState | None = None) -> list[str]:
    labels: list[str] = []
    active_slot = 0
    for action in actions:
        labels.append(_resolve_action_label(action, state, active_slot))
        if action.is_switch and action.switch_target is not None:
            active_slot = action.switch_target
    return labels


def _node_id(node: Node | None) -> str:
    if node is None or node.action_taken is None:
        return "root"
    parts = []
    for turn_action in node.action_sequence():
        parts.append("_".join(_format_action(action).replace(" ", "-") for action in turn_action))
    return "n-" + "-".join(parts)


def _format_turn_action(
    action: TurnAction | None,
    state: BattleState | None = None,
    active_slot: int = 0,
) -> str:
    if action is None:
        return "BATTLE START"
    labels: list[str] = []
    current_active = active_slot
    is_multi_actor = len(action) > 1
    for index, item in enumerate(action):
        actor_slot = index if is_multi_actor and item.is_move else current_active
        labels.append(_resolve_action_label(item, state, actor_slot))
        if item.is_switch and item.switch_target is not None:
            current_active = item.switch_target
    return " + ".join(labels)


def _format_action(action: Action) -> str:
    if action.is_move:
        target = " -> enemy" if action.target_slot is None else f" -> target {action.target_slot}"
        return f"Move slot {action.move_slot}{target}"
    if action.is_switch:
        return f"Switch slot {action.switch_target}"
    return action.kind


def _resolve_action_label(action: Action, state: BattleState | None, active_slot: int = 0) -> str:
    if action.is_move:
        return f"{_player_name(state, active_slot)} uses {_move_name(state, action.move_slot, active_slot)}"
    if action.is_switch:
        return f"Switch to {_player_name(state, action.switch_target)}"
    return action.kind


def _active_slot_before(node: Node) -> int:
    if node.action_taken is None:
        return 0
    previous: list[TurnAction] = []
    parent = node.parent
    while parent is not None and parent.action_taken is not None:
        previous.append(parent.action_taken)
        parent = parent.parent
    previous.reverse()
    return _active_slot_after(previous)


def _active_slot_after(action_sequence: list[TurnAction]) -> int:
    active_slot = 0
    for turn_action in action_sequence:
        for action in turn_action:
            if action.is_switch and action.switch_target is not None:
                active_slot = action.switch_target
    return active_slot


def _previous_active_slot_if_last_action_was_switch(action_sequence: list[TurnAction]) -> int | None:
    active_slot = 0
    previous_active_before_switch: int | None = None
    last_was_switch = False
    for turn_action in action_sequence:
        for action in turn_action:
            last_was_switch = False
            if action.is_switch and action.switch_target is not None:
                previous_active_before_switch = active_slot
                active_slot = action.switch_target
                last_was_switch = True
    return previous_active_before_switch if last_was_switch else None


def _without_switch_target(actions: list[TurnAction], blocked_target: int | None) -> list[TurnAction]:
    if blocked_target is None:
        return list(actions)
    return [
        turn_action
        for turn_action in actions
        if not any(action.is_switch and action.switch_target == blocked_target for action in turn_action)
    ]


def _switch_count(actions: list[Action]) -> int:
    return sum(action.is_switch for action in actions)


def _consecutive_switch_count(actions: list[Action]) -> int:
    count = 0
    previous_was_switch = False
    for action in actions:
        if action.is_switch and previous_was_switch:
            count += 1
        previous_was_switch = action.is_switch
    return count


def _move_name(state: BattleState | None, move_slot: int | None, active_slot: int = 0) -> str:
    if move_slot is None:
        return "Move"
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
    return _move_position_name(move_slot)


def _move_position_name(index: int) -> str:
    return ["top move", "second move", "third move", "bottom move"][index] if 0 <= index < 4 else "unknown move"


def _player_name(state: BattleState | None, party_slot: int | None) -> str:
    if party_slot is None:
        return "your Pokemon"
    if state and 0 <= party_slot < len(state.player_names):
        name = state.player_names[party_slot].strip()
        if name:
            return name
    return f"slot {party_slot}"


def _serialize_turn_action(action: TurnAction | None) -> list[dict[str, Any]] | None:
    if action is None:
        return None
    return [asdict(item) for item in action]


def _serialize_line(line: list[TurnAction] | None) -> list[list[dict[str, Any]]] | None:
    if line is None:
        return None
    return [_serialize_turn_action(action) or [] for action in line]


def _serialize_turn_snapshot(snapshot: TurnSnapshot) -> dict[str, Any]:
    data = asdict(snapshot)
    data["actions"] = [asdict(action) for action in snapshot.actions]
    return data
