from __future__ import annotations

import config
from battle.action import Action
from battle.battle_state import BattleState
from outcome import TurnAction
from search.mcts import FlowchartNode, SearchResult

try:
    from rich.console import Console
    from rich.text import Text
except Exception:  # pragma: no cover
    Console = None
    Text = None


class Renderer:
    def __init__(self, console: Console | None = None, state: BattleState | None = None):
        self.console = console or (Console() if Console else None)
        self.state = state

    def render(self, result: SearchResult) -> None:
        if self.console is None:
            print(render_plain(result, self.state))
            return
        self._simple_plan(result)
        self.console.print("FULL FLOWCHART", style="bold cyan")
        self._header(result)
        if config.NUZLOCKE_WEIGHT > 0:
            self.console.print("☠ NUZLOCKE MODE ACTIVE - deathless lines prioritized", style="bold red on white")
        if result.has_deathless_line:
            self.console.print("\n✓ RECOMMENDED LINE - DEATHLESS", style="bold green")
        else:
            self.console.print("\n☠ NO DEATHLESS LINE FOUND", style="bold red on white")
            self.console.print("⚔ RECOMMENDED LINE - PLANNED SACRIFICE", style="bold yellow")
            if config.NUZLOCKE_WEIGHT > 0:
                self.console.print("☠ WARNING: NO DEATHLESS LINE ACHIEVES > 40% WIN RATE", style="bold red on white")
                self.console.print("☠ NUZLOCKE UNSAFE LINE SHOWN AS LAST RESORT", style="bold red on white")

        for child in result.flowchart.children:
            self._render_node(child, "")

        self.console.print("\nALL LINES (ranked by score):", style="bold cyan")
        for index, line in enumerate(result.ranked_lines, start=1):
            deaths = 0 if line.faint_rate < 0.10 else 1
            label = _label_text(line.line_label)
            self.console.print(
                f"#{index}  {self._format_turn_action(line.action)} - "
                f"Win: {line.win_rate:.0%} | Deaths: {deaths} | "
                f"Score: {line.score:.1f}  {label}",
                style=_style_for_label(line.line_label, line.win_rate),
            )

    def _simple_plan(self, result: SearchResult) -> None:
        self.console.print("══════════════════════════════════════════", style="bold cyan")
        self.console.print(" YOUR BATTLE PLAN", style="bold cyan")
        self.console.print("══════════════════════════════════════════", style="bold cyan")
        if not result.recommended_line:
            self.console.print("No playable line was found.", style="bold red")
        else:
            for turn_number, action in enumerate(result.recommended_line, start=1):
                self.console.print(f"TURN {turn_number}: {self._format_plan_action(action)}", style="bold white")
            branches = self._simple_branches(result)
            for branch in branches:
                self.console.print(f"        -> {branch}", style="yellow")
        deathless = "YES" if result.has_deathless_line else "NO"
        self.console.print(
            f"\nWIN RATE: {result.win_probability:.0%} | "
            f"DEATHLESS: {deathless} | FAINT RISK: {result.faint_probability:.0%}\n",
            style="bold green" if result.has_deathless_line else "bold yellow",
        )

    def _simple_branches(self, result: SearchResult) -> list[str]:
        recommended_actions = [_turn_key(action) for action in result.recommended_line]
        current = result.flowchart
        for action_key in recommended_actions:
            match = next((child for child in current.children if _turn_key(child.action) == action_key), None)
            if match is None:
                break
            current = match

        branches: list[str] = []
        for child in current.children[:3]:
            condition = self._human_branch(child.branch_condition)
            if condition.upper() == "EXPECTED":
                continue
            branches.append(f"IF {condition.lower()}: {self._format_plan_action(child.action)}")
        return branches

    def _format_plan_action(self, action: TurnAction | None) -> str:
        if action is None:
            return "Start the fight"
        pieces = [self._format_action(item, for_plan=True) for item in action]
        return "; then ".join(pieces)

    def _header(self, result: SearchResult) -> None:
        self.console.print("╔═══════════════════════════════════════════╗", style="bold cyan")
        self.console.print("║        POKEMON BATTLE SOLVER             ║", style="bold cyan")
        self.console.print(
            f"║  Trials: {result.total_trials_run:<5} |  Search: {result.search_time_seconds:>5.1f}s          ║",
            style="bold cyan",
        )
        self.console.print(
            f"║  Win rate: {result.win_probability:>4.0%}  |  Faint risk: {result.faint_probability:>4.0%}       ║",
            style="bold cyan",
        )
        found = "YES" if result.has_deathless_line else "NO "
        self.console.print(f"║  Deathless line found: {found:<3}              ║", style="bold cyan")
        self.console.print("╚═══════════════════════════════════════════╝", style="bold cyan")

    def _render_node(self, node: FlowchartNode, prefix: str) -> None:
        planned_sack = node.line_label == "SACK LINE"
        line = (
            f"{prefix}Turn {node.turn}: {self._format_turn_action(node.action)}\n"
            f"{prefix}        Win rate: {node.win_rate:.0%} | "
            f"Faint rate: {node.faint_rate:.0%} | Avg HP: {node.avg_hp:.0%} | "
            f"Visits: {node.visit_count} | Score: {node.score:.1f}  {_label_text(node.line_label)}"
        )
        self.console.print(line, style=_style_for_label(node.line_label, node.win_rate))
        if planned_sack:
            self.console.print(
                f"{prefix}        [PLANNED SACK: {self._active_name()} expected to faint]",
                style="bold yellow",
            )
        for index, child in enumerate(node.children):
            branch = "└──" if index == len(node.children) - 1 else "├──"
            self.console.print(f"{prefix}  {branch} {self._human_branch(child.branch_condition)}:", style="cyan")
            self._render_node(child, prefix + "  │   ")

    def _format_turn_action(self, action: TurnAction | None) -> str:
        if action is None:
            return "Battle start"
        return " + ".join(self._format_action(item) for item in action)

    def _format_action(self, action: Action, for_plan: bool = False) -> str:
        if action.is_move:
            move_name = self._move_name(action.move_slot)
            target = ""
            if action.target_slot is not None:
                target = f" on {self._enemy_name(action.target_slot)}"
            if for_plan:
                return f"Use {move_name}{target} with {self._active_name()}"
            return f"{self._active_name()} uses {move_name}{target}"
        if action.is_switch:
            return f"Switch to {self._player_name(action.switch_target)}"
        return action.kind.replace("_", " ")

    def _move_name(self, move_slot: int | None) -> str:
        if move_slot is None:
            return "an unknown move"
        if self.state and 0 <= move_slot < len(self.state.player_move_names):
            name = self.state.player_move_names[move_slot].strip()
            if name:
                return name
        return f"unknown move ({_move_position_name(move_slot)})"

    def _player_name(self, party_slot: int | None) -> str:
        if party_slot is None:
            return "your Pokemon"
        if self.state and 0 <= party_slot < len(self.state.player_names):
            name = self.state.player_names[party_slot].strip()
            if name:
                return name
        return f"your {_party_position_name(party_slot)} Pokemon"

    def _enemy_name(self, enemy_slot: int | None) -> str:
        if enemy_slot is None:
            return "the enemy"
        if self.state and 0 <= enemy_slot < len(self.state.enemy_names):
            name = self.state.enemy_names[enemy_slot].strip()
            if name:
                return name
        return f"the {_enemy_position_name(enemy_slot)} enemy"

    def _active_name(self) -> str:
        return self._player_name(0)

    def _human_branch(self, branch_condition: str) -> str:
        text = branch_condition.replace("SACK PAYS OFF", "sacrifice pays off")
        text = text.replace("SACK FAILS", "sacrifice fails")
        for index in range(6):
            text = text.replace(f"slot {index}", self._player_name(index))
            text = text.replace(f"Slot {index}", self._player_name(index))
            text = text.replace(f"target {index}", self._enemy_name(index))
            text = text.replace(f"Target {index}", self._enemy_name(index))
        if text.startswith("IF "):
            text = text[3:]
        return text


def render_plain(result: SearchResult, state: BattleState | None = None) -> str:
    plan = []
    for turn_number, action in enumerate(result.recommended_line, start=1):
        plan.append(f"TURN {turn_number}: {_format_plan_action(action, state)}")
    deathless = "YES" if result.has_deathless_line else "NO"
    return (
        "══════════════════════════════════════════\n"
        "YOUR BATTLE PLAN\n"
        "══════════════════════════════════════════\n"
        + "\n".join(plan)
        + "\n\n"
        f"WIN RATE: {result.win_probability:.0%} | DEATHLESS: {deathless} | "
        f"FAINT RISK: {result.faint_probability:.0%}\n\n"
        "FULL FLOWCHART\n"
        f"Trials: {result.total_trials_run} Search: {result.search_time_seconds:.1f}s\n"
        f"Win: {result.win_probability:.0%} Faint risk: {result.faint_probability:.0%}"
    )


def _format_turn_action(action: TurnAction | None, state: BattleState | None = None) -> str:
    if action is None:
        return "Battle start"
    return " + ".join(_format_action(item, state) for item in action)


def _format_plan_action(action: TurnAction | None, state: BattleState | None = None) -> str:
    if action is None:
        return "Start the fight"
    return "; then ".join(_format_action(item, state, for_plan=True) for item in action)


def _format_action(action: Action, state: BattleState | None = None, for_plan: bool = False) -> str:
    if action.is_move:
        move_slot = action.move_slot if action.move_slot is not None else 0
        if state and 0 <= move_slot < len(state.player_move_names):
            move_name = state.player_move_names[move_slot] or f"unknown move ({_move_position_name(move_slot)})"
        else:
            move_name = f"unknown move ({_move_position_name(move_slot)})"
        active_name = state.player_names[0] if state and state.player_names else "your Pokemon"
        if for_plan:
            return f"Use {move_name} with {active_name}"
        return f"{active_name} uses {move_name}"
    if action.is_switch:
        party_slot = action.switch_target if action.switch_target is not None else 0
        if state and 0 <= party_slot < len(state.player_names):
            name = state.player_names[party_slot] or f"your {_party_position_name(party_slot)} Pokemon"
        else:
            name = f"your {_party_position_name(party_slot)} Pokemon"
        return f"Switch to {name}"
    return action.kind


def _turn_key(action: TurnAction | None) -> tuple[tuple[str, int | None, int | None, int | None], ...]:
    if action is None:
        return ()
    return tuple((item.kind, item.move_slot, item.target_slot, item.switch_target) for item in action)


def _move_position_name(index: int) -> str:
    return ["top move", "second move", "third move", "bottom move"][index] if 0 <= index < 4 else "unknown move"


def _party_position_name(index: int) -> str:
    names = ["lead", "second", "third", "fourth", "fifth", "sixth"]
    return names[index] if 0 <= index < len(names) else "backup"


def _enemy_position_name(index: int) -> str:
    names = ["first", "second", "third", "fourth", "fifth", "sixth"]
    return names[index] if 0 <= index < len(names) else "opposing"


def _label_text(label: str) -> str:
    return {
        "DEATHLESS": "✓ DEATHLESS",
        "SACK LINE": "⚔ SACK LINE",
        "GOOD": "✓ GOOD",
        "RISKY": "⚠ RISKY",
        "DANGER": "⚠ DANGER",
        "AVOID": "✗ AVOID",
        "LOSS": "✗ AVOID",
    }.get(label, label)


def _style_for_label(label: str, win_rate: float) -> str:
    if label in {"DEATHLESS", "GOOD"}:
        return "bold green"
    if label == "SACK LINE":
        return "bold yellow"
    if label in {"DANGER", "AVOID", "LOSS"}:
        return "bold red"
    if win_rate >= 0.80:
        return "green"
    if win_rate >= 0.60:
        return "yellow"
    return "red"
