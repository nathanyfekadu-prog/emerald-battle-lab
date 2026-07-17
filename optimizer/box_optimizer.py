from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import config
from battle.battle_state import BattleState
from battle.damage_calc import DamageCalculator, DamageContext, DamageRange, PokemonCalcSet
from emulator.input_controller import InputController
from emulator.mgba_instance import MGBAInstance
from emulator.state_reader import StateReader
from optimizer.gen3_save import BagItem, DecodedPokemon, RomNameResolver, SavePointers, read_save_snapshot
from optimizer.turn_planner import build_stateful_turn_plan
from search.action_enumerator import ActionEnumerator
from search.mcts import MCTS, SearchCancelled, SearchResult


@dataclass(frozen=True)
class WeaknessReport:
    baseline_deathless_win_rate: float
    baseline_sack_win_rate: float
    most_common_faint_slot: int
    most_common_faint_rate: float
    battle_lost_on_turn: int
    problem_description: str
    needs_box_pull: bool
    pc_accessible: bool
    pc_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BoxCandidateResult:
    name: str
    box: int
    slot: int
    level: int
    deathless_win_rate: float
    improvement: float
    rank: int
    note: str = ""
    species: str = ""
    held_item: str | None = None
    suggested_item: str | None = None
    replace_slot: int = 0
    replace_name: str = ""
    score: float = 0.0
    plan: list[str] = field(default_factory=list)
    matchup_summary: list[dict[str, Any]] = field(default_factory=list)
    team: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BoxPokemon:
    name: str
    box: int
    slot: int
    level: int
    hp: int
    max_hp: int
    species: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BoxScanResult:
    roster: list[DecodedPokemon]
    party: list[DecodedPokemon]
    bag: list[BagItem]
    pointers: SavePointers

    def to_dict(self) -> dict[str, Any]:
        return {
            "roster": [asdict(mon) for mon in self.roster],
            "party": [asdict(mon) for mon in self.party],
            "bag": [asdict(item) for item in self.bag],
            "pointers": asdict(self.pointers),
        }


@dataclass(frozen=True)
class PrepareResult:
    baseline: SearchResult
    weakness_report: WeaknessReport
    candidates: list[BoxCandidateResult]
    message: str
    team_plan: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline.to_dict(),
            "weakness_report": self.weakness_report.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "message": self.message,
            "team_plan": self.team_plan,
        }


@dataclass(frozen=True)
class CandidateEvaluation:
    mon: DecodedPokemon
    estimated_win_rate: float
    improvement: float
    score: float
    suggested_item: str | None
    replace_slot: int
    replace_name: str
    note: str
    plan: list[str]
    matchup_summary: list[dict[str, Any]]


ProgressCallback = Callable[[dict[str, Any]], None]
MCTSCallback = Callable[[MCTS], None]


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SearchCancelled("Prepare killed")


def run_prepare(
    rom_path: str,
    battle_state_path: str,
    pc_state_path: str,
    instances: int = 4,
    iterations: int = 50,
    turns: int = config.MAX_TURNS,
    trials_per_node: int = config.TRIALS_PER_NODE,
    nuzlocke: bool = False,
    on_event: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    on_mcts_start: MCTSCallback | None = None,
) -> PrepareResult:
    _raise_if_cancelled(cancel_event)
    if nuzlocke:
        config.NUZLOCKE_WEIGHT = 8.0

    _raise_if_cancelled(cancel_event)
    if not detect_battle_state(rom_path, battle_state_path):
        message = (
            "ERROR: --battle-state must be saved inside the target battle at the action menu. "
            "Use --pc-state for the Pokemon Center PC save."
        )
        _emit(on_event, {"type": "error", "message": message})
        raise ValueError(message)

    _emit(on_event, {"type": "started", "message": "Solving current party baseline"})
    _emit_initial_state(on_event, rom_path, battle_state_path)
    _raise_if_cancelled(cancel_event)

    mcts = MCTS(
        rom_path,
        battle_state_path,
        pool_size=instances,
        max_turns=turns,
        trials_per_node=trials_per_node,
        on_node_visited=lambda data: _emit_solver_event(on_event, data),
        cancel_event=cancel_event,
    )
    if on_mcts_start is not None:
        on_mcts_start(mcts)
    try:
        baseline = mcts.search(iterations)
    finally:
        mcts.shutdown()

    _raise_if_cancelled(cancel_event)
    report = analyze_weaknesses(baseline, rom_path, pc_state_path)
    _emit(on_event, {"type": "weakness_report", "data": report.to_dict()})

    if baseline.best_deathless_win_rate is not None and baseline.best_deathless_win_rate >= 0.80:
        message = "Current team is strong - no box pulls needed"
        _emit(on_event, {"type": "prepare_complete", "data": _prepare_complete_payload(baseline, None)})
        return PrepareResult(baseline, report, [], message)

    if not report.pc_accessible:
        message = (
            "ERROR: The PC save state is mid-battle or cannot access the PC. The prepare command needs --pc-state "
            "from inside a Pokemon Center standing in front of the PC, before the battle. "
            "--battle-state should remain the in-battle save for the target fight."
        )
        _emit(on_event, {"type": "error", "message": message})
        _emit(
            on_event,
            {
                "type": "box_candidate",
                "data": {
                    "name": "PC state required - box scan skipped",
                    "box": 0,
                    "slot": 0,
                    "level": 0,
                    "deathless_win_rate": baseline.best_deathless_win_rate
                    or baseline.win_probability,
                    "improvement": 0.0,
                    "rank": 1,
                    "note": "Provide --pc-state from an overworld PC to scan boxes.",
                },
            },
        )
        _emit(on_event, {"type": "prepare_complete", "data": _prepare_complete_payload(baseline, None)})
        return PrepareResult(baseline, report, [], message)

    battle_state = _read_battle_state(rom_path, battle_state_path)
    calculator = DamageCalculator()
    _emit(on_event, {"type": "started", "message": "Scanning PC boxes"})
    scan = scan_pc_boxes(rom_path, pc_state_path, on_event, cancel_event, calculator)
    _raise_if_cancelled(cancel_event)
    _emit(
        on_event,
        {
            "type": "started",
            "message": (
                f"Scored {len(scan.roster)} boxed Pokemon"
                + (f" with {len(scan.bag)} usable bag item slots" if scan.bag else "")
            ),
        },
    )
    evaluations = identify_candidates(
        scan.roster, report, battle_state, calculator, scan.bag, limit=10,
        graveyard_box=config.NUZLOCKE_GRAVEYARD_BOX if nuzlocke else None,
    )
    _raise_if_cancelled(cancel_event)
    candidates = test_candidates(evaluations, baseline, report, battle_state, scan, on_event)
    best_candidate = candidates[0] if candidates else None
    team_plan = _team_plan_payload(candidates, battle_state, scan, calculator)
    message = (
        "No usable boxed Pokemon with calc coverage was found"
        if not candidates
        else "Box candidate scan complete - best calc team plan is ready"
    )
    complete_payload = _prepare_complete_payload(baseline, best_candidate)
    complete_payload["team_plan"] = team_plan
    _emit(on_event, {"type": "prepare_complete", "data": complete_payload})
    return PrepareResult(baseline, report, candidates, message, team_plan)


def analyze_weaknesses(
    baseline: SearchResult,
    rom_path: str,
    save_state_path: str,
) -> WeaknessReport:
    deathless = baseline.best_deathless_win_rate or 0.0
    sack = baseline.best_sack_win_rate or 0.0
    ranked = baseline.ranked_lines or baseline.flowchart.children
    faintiest = max(ranked, key=lambda node: node.faint_rate, default=None)
    most_common_faint_rate = faintiest.faint_rate if faintiest else baseline.faint_probability
    battle_lost_on_turn = faintiest.turn if faintiest else 1
    most_common_faint_slot = 0
    needs_box_pull = deathless < 0.60 or sack < 0.70

    if deathless >= 0.80:
        problem = "current team is strong"
    elif battle_lost_on_turn <= 1 or most_common_faint_rate >= 0.50:
        problem = "lead is weak to enemy's first move"
    elif battle_lost_on_turn == 2:
        problem = "backup slot is failing around turn 2"
    else:
        problem = "late-game survivability is low"

    pc_accessible, pc_error = detect_pc_accessible(rom_path, save_state_path)
    return WeaknessReport(
        baseline_deathless_win_rate=deathless,
        baseline_sack_win_rate=sack,
        most_common_faint_slot=most_common_faint_slot,
        most_common_faint_rate=most_common_faint_rate,
        battle_lost_on_turn=battle_lost_on_turn,
        problem_description=problem,
        needs_box_pull=needs_box_pull,
        pc_accessible=pc_accessible,
        pc_error=pc_error,
    )


def detect_pc_accessible(rom_path: str, save_state_path: str) -> tuple[bool, str | None]:
    instance = MGBAInstance(rom_path, save_state_path, 66)
    try:
        reader = StateReader(instance)
        state = reader.read()
        if _has_enemy_party(state) and _screen_looks_battle_command(instance, reader):
            return (
                False,
                "ERROR: --pc-state appears to be mid-battle. Use --battle-state for the fight "
                "and --pc-state for an overworld Pokemon Center PC save.",
            )
        return True, None
    finally:
        instance.shutdown()


def detect_battle_state(rom_path: str, battle_state_path: str) -> bool:
    instance = MGBAInstance(rom_path, battle_state_path, 65)
    try:
        reader = StateReader(instance)
        state = reader.read()
        return _looks_like_battle(state) or (
            _has_enemy_party(state) and _screen_looks_battle_command(instance, reader)
        )
    finally:
        instance.shutdown()


def scan_pc_boxes(
    rom_path: str,
    pc_state_path: str,
    on_event: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    calculator: DamageCalculator | None = None,
    allow_midbattle_snapshot: bool = False,
) -> BoxScanResult:
    instance = MGBAInstance(rom_path, pc_state_path, 69)
    try:
        reader = StateReader(instance)
        state = reader.read()
        if not allow_midbattle_snapshot and (_looks_like_battle(state) or (
            _has_enemy_party(state) and _screen_looks_battle_command(instance, reader)
        )):
            empty_pointers = SavePointers(None, None, None, "mid-battle")
            return BoxScanResult([], [], [], empty_pointers)

        calculator = calculator or DamageCalculator()
        snapshot = read_save_snapshot(
            instance,
            RomNameResolver(rom_path),
            calculator.species_by_num,
            calculator.moves_by_num,
            calculator.moves,
        )
        roster = list(snapshot.boxes)
        seen: set[tuple[int, int, str, int]] = set()
        unique_roster: list[DecodedPokemon] = []
        for mon in roster:
            _raise_if_cancelled(cancel_event)
            key = (mon.box, mon.slot, mon.name, mon.species_id)
            if key in seen:
                continue
            seen.add(key)
            unique_roster.append(mon)
            _emit(
                on_event,
                {
                    "type": "box_candidate",
                    "data": {
                        "name": mon.display_name,
                        "box": mon.box,
                        "slot": mon.slot,
                        "level": mon.level,
                        "deathless_win_rate": 0.0,
                        "improvement": 0.0,
                        "rank": len(unique_roster),
                        "species": mon.species,
                        "held_item": mon.held_item,
                        "note": _scanned_note(mon),
                    },
                },
            )
        return BoxScanResult(unique_roster, list(snapshot.party), list(snapshot.bag), snapshot.pointers)
    finally:
        instance.shutdown()


def identify_candidates(
    roster: list[DecodedPokemon],
    report: WeaknessReport,
    battle_state: BattleState,
    calculator: DamageCalculator,
    bag: list[BagItem],
    limit: int = 10,
    graveyard_box: int | None = None,
) -> list[CandidateEvaluation]:
    if not roster:
        return []
    # Nuzlocke: the graveyard box holds fainted (dead) Pokemon — never field them.
    if graveyard_box is not None:
        roster = [mon for mon in roster if mon.box != graveyard_box]
        if not roster:
            return []
    current_names = {name.casefold() for name in battle_state.player_names if name}
    candidates = [mon for mon in roster if mon.name.casefold() not in current_names]
    if not candidates:
        candidates = roster
    evaluations = [
        evaluation
        for mon in candidates
        if (evaluation := _evaluate_box_candidate(mon, report, battle_state, calculator, bag)) is not None
    ]
    return sorted(evaluations, key=lambda item: item.score, reverse=True)[:limit]


def test_candidates(
    candidates: list[CandidateEvaluation],
    baseline: SearchResult,
    report: WeaknessReport,
    battle_state: BattleState,
    scan: BoxScanResult,
    on_event: ProgressCallback | None = None,
) -> list[BoxCandidateResult]:
    baseline_rate = baseline.best_deathless_win_rate or baseline.win_probability
    results: list[BoxCandidateResult] = []
    for rank, evaluation in enumerate(candidates, start=1):
        mon = evaluation.mon
        team = _team_after_candidate(battle_state, evaluation, scan)
        result = BoxCandidateResult(
            name=mon.display_name,
            box=mon.box,
            slot=mon.slot,
            level=mon.level,
            deathless_win_rate=evaluation.estimated_win_rate,
            improvement=evaluation.improvement,
            rank=rank,
            note=evaluation.note,
            species=mon.species,
            held_item=mon.held_item,
            suggested_item=evaluation.suggested_item,
            replace_slot=evaluation.replace_slot,
            replace_name=evaluation.replace_name,
            score=evaluation.score,
            plan=evaluation.plan,
            matchup_summary=evaluation.matchup_summary,
            team=team,
        )
        results.append(result)
        _emit(on_event, {"type": "box_candidate", "data": result.to_dict()})
    return results


def _evaluate_box_candidate(
    mon: DecodedPokemon,
    report: WeaknessReport,
    battle_state: BattleState,
    calculator: DamageCalculator,
    bag: list[BagItem],
) -> CandidateEvaluation | None:
    match = calculator.matched_trainer(battle_state)
    if match is None:
        return _fallback_candidate_evaluation(mon, report, battle_state)

    item_options = _candidate_item_options(mon, bag)
    best: CandidateEvaluation | None = None
    for item_name in item_options:
        candidate_set = _calc_set_from_decoded(mon, calculator, item_name)
        if candidate_set is None:
            continue
        score, summary = _score_candidate_set(candidate_set, mon.moves, match.sets, calculator)
        if score <= 0 or not summary:
            continue
        replace_slot, replace_name = _choose_replacement_slot(battle_state, report, calculator)
        note = _candidate_note(mon, item_name, replace_slot, replace_name, match.battle.trainer_name, summary)
        plan = _candidate_plan(mon, item_name, replace_name, match.battle.trainer_name, summary)
        baseline_rate = report.baseline_deathless_win_rate or report.baseline_sack_win_rate
        improvement = min(0.55, max(0.03, (score - 70.0) / 260.0))
        estimated = min(0.98, baseline_rate + improvement)
        evaluation = CandidateEvaluation(
            mon=mon,
            estimated_win_rate=estimated,
            improvement=estimated - baseline_rate,
            score=score,
            suggested_item=item_name,
            replace_slot=replace_slot,
            replace_name=replace_name,
            note=note,
            plan=plan,
            matchup_summary=summary,
        )
        if best is None or evaluation.score > best.score:
            best = evaluation
    return best


def _fallback_candidate_evaluation(
    mon: DecodedPokemon,
    report: WeaknessReport,
    battle_state: BattleState,
) -> CandidateEvaluation | None:
    if not mon.moves:
        return None
    replace_slot, replace_name = _choose_replacement_slot(battle_state, report, None)
    score = mon.level * 2 + len(mon.moves) * 8
    baseline_rate = report.baseline_deathless_win_rate or report.baseline_sack_win_rate
    estimated = min(0.90, baseline_rate + min(0.25, score / 500.0))
    item = mon.held_item
    return CandidateEvaluation(
        mon=mon,
        estimated_win_rate=estimated,
        improvement=estimated - baseline_rate,
        score=score,
        suggested_item=item,
        replace_slot=replace_slot,
        replace_name=replace_name,
        note=f"Trainer match unavailable; ranked by decoded level and moves. Replace {replace_name or 'weak slot'} with {mon.display_name}.",
        plan=[
            f"Swap {mon.display_name} into {replace_name or 'the weakest current slot'}.",
            "Re-run solve from the new battle save so emulator trials validate the line.",
        ],
        matchup_summary=[],
    )


def _score_candidate_set(
    candidate: PokemonCalcSet,
    move_names: tuple[str, ...],
    enemy_sets: tuple[Any, ...],
    calculator: DamageCalculator,
) -> tuple[float, list[dict[str, Any]]]:
    if not move_names:
        return 0.0, []
    candidate_speed = _speed(candidate, calculator)
    scores: list[float] = []
    summary: list[dict[str, Any]] = []
    for enemy_set in enemy_sets:
        enemy = enemy_set.pokemon
        enemy_speed = _speed(enemy, calculator)
        offensive = _best_damaging(calculator.rank_move_names(candidate, enemy, move_names))
        defensive = _best_damaging(calculator.rank_move_names(enemy, candidate, enemy_set.moves))
        if offensive is None and defensive is None:
            continue
        offense_pct = offensive.average_percent * 100 if offensive else 0.0
        enemy_pct = defensive.max_percent * 100 if defensive else 0.0
        outspeeds = candidate_speed >= enemy_speed
        candidate_ohko = bool(offensive and offensive.ko_chance >= 1.0)
        enemy_ohko = bool(defensive and defensive.ko_chance >= 1.0)
        candidate_2hko = bool(offensive and offensive.average_percent >= 50)
        enemy_2hko = bool(defensive and defensive.max_percent >= 50)
        offensive_crit = (
            calculator.estimate_move(candidate, enemy, offensive.move_name, DamageContext(critical=True))
            if offensive
            else None
        )
        defensive_crit = (
            calculator.estimate_move(enemy, candidate, defensive.move_name, DamageContext(critical=True))
            if defensive
            else None
        )
        enemy_crit_ohko = bool(defensive_crit and defensive_crit.ko_chance >= 1.0)
        secondary_penalty = _enemy_secondary_penalty(defensive.move_name if defensive else "", defensive, enemy_speed >= candidate_speed, calculator)
        score = offense_pct
        if candidate_ohko:
            score += 70
        elif candidate_2hko:
            score += 25
        if outspeeds:
            score += 15
        score += max(0.0, 100.0 - enemy_pct) * 0.65
        if enemy_ohko:
            score -= 90
        elif enemy_crit_ohko and not candidate_ohko:
            score -= 35
        elif enemy_2hko and not candidate_ohko:
            score -= 25
        score -= secondary_penalty
        scores.append(score)
        summary.append(
            {
                "enemy": enemy.species,
                "best_move": offensive.move_name if offensive else "",
                "damage": _format_damage_range(offensive),
                "damage_min": offensive.min_percent if offensive else 0.0,
                "damage_max": offensive.max_percent if offensive else 0.0,
                "crit_damage": _format_damage_range(offensive_crit),
                "crit_damage_min": offensive_crit.min_percent if offensive_crit else 0.0,
                "crit_damage_max": offensive_crit.max_percent if offensive_crit else 0.0,
                "ko_chance": offensive.ko_chance if offensive else 0.0,
                "secondary_effects": _move_secondary_notes(offensive.move_name if offensive else "", calculator),
                "enemy_best_move": defensive.move_name if defensive else "",
                "enemy_damage": _format_damage_range(defensive),
                "enemy_damage_min": defensive.min_percent if defensive else 0.0,
                "enemy_damage_max": defensive.max_percent if defensive else 0.0,
                "enemy_crit_damage": _format_damage_range(defensive_crit),
                "enemy_crit_damage_min": defensive_crit.min_percent if defensive_crit else 0.0,
                "enemy_crit_damage_max": defensive_crit.max_percent if defensive_crit else 0.0,
                "enemy_ko_chance": defensive.ko_chance if defensive else 0.0,
                "enemy_crit_ko_chance": defensive_crit.ko_chance if defensive_crit else 0.0,
                "enemy_secondary_effects": _move_secondary_notes(defensive.move_name if defensive else "", calculator),
                "enemy_risk_notes": _risk_notes(defensive, defensive_crit, defensive.move_name if defensive else "", enemy_speed >= candidate_speed, calculator),
                "outspeeds": outspeeds,
                "score": round(score, 1),
            }
        )
    if not scores:
        return 0.0, []
    top_scores = sorted(scores, reverse=True)
    # Reward a mon that has one standout job, but keep full-party consistency
    # relevant so glass cannons with one good calc do not always win.
    final_score = top_scores[0] * 0.45 + (sum(top_scores) / len(top_scores)) * 0.55
    summary.sort(key=lambda item: item["score"], reverse=True)
    return final_score, summary


def _calc_set_from_decoded(
    mon: DecodedPokemon,
    calculator: DamageCalculator,
    item_name: str | None,
) -> PokemonCalcSet | None:
    if not calculator._species_data(mon.species):  # noqa: SLF001 - local calculator helper
        return None
    max_hp = mon.max_hp
    if max_hp is None:
        species_data = calculator._species_data(mon.species)  # noqa: SLF001
        max_hp = calculator._stat(species_data, "hp", mon.level, mon.nature, mon.evs, mon.ivs) if species_data else None  # noqa: SLF001
    return PokemonCalcSet(
        species=mon.species,
        level=mon.level,
        nature=mon.nature,
        evs=mon.evs,
        ivs=mon.ivs,
        held_item=item_name,
        hp=max_hp,
        max_hp=max_hp,
    )


def _candidate_item_options(mon: DecodedPokemon, bag: list[BagItem]) -> list[str | None]:
    options: list[str | None] = [mon.held_item, None]
    for item in bag:
        if item.pocket in {"Key Items", "Poke Balls", "TMs/HMs"}:
            continue
        if _is_useful_battle_item(item.name):
            options.append(item.name)
    deduped: list[str | None] = []
    seen: set[str] = set()
    for option in options:
        key = _normalize(option)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
    return deduped


def _is_useful_battle_item(name: str) -> bool:
    normalized = _normalize(name)
    if not normalized:
        return False
    if normalized in {
        "sitrusberry",
        "oranberry",
        "lumberry",
        "leftovers",
        "blacksludge",
        "focussash",
        "focusband",
        "choiceband",
        "choicespecs",
        "choicescarf",
        "lifeorb",
        "expertbelt",
        "assaultvest",
        "eviolite",
        "rockyhelmet",
        "brightpowder",
        "laxincense",
        "widelens",
        "muscleband",
        "wiseglasses",
        "quickclaw",
    }:
        return True
    return normalized.endswith("berry") or normalized.endswith("gem") or normalized.endswith("plate")


def _best_damaging(ranges: tuple[DamageRange, ...]) -> DamageRange | None:
    damaging = [item for item in ranges if item.max_damage > 0]
    return damaging[0] if damaging else None


def _move_secondary_notes(move_name: str, calculator: DamageCalculator) -> list[str]:
    move = calculator.moves.get(_normalize(move_name))
    if not move:
        return []
    notes: list[str] = []
    effects = []
    if move.get("secondary"):
        effects.append(move["secondary"])
    effects.extend(move.get("secondaries") or [])
    seen: set[str] = set()
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        chance = effect.get("chance")
        prefix = f"{chance}% " if chance else ""
        label = ""
        if effect.get("volatileStatus") == "flinch":
            label = "flinch"
        elif effect.get("status"):
            label = _status_label(str(effect["status"]))
        elif effect.get("boosts"):
            boost_parts = [f"{stat} {value:+d}" for stat, value in effect["boosts"].items()]
            label = "stat change " + ", ".join(boost_parts)
        if label:
            text = prefix + label
            if text not in seen:
                seen.add(text)
                notes.append(text)
    if move.get("recoil"):
        notes.append("recoil")
    return notes


def _enemy_secondary_penalty(
    move_name: str,
    damage: DamageRange | None,
    enemy_moves_first: bool,
    calculator: DamageCalculator,
) -> float:
    if damage is None:
        return 0.0
    penalty = 0.0
    notes = _move_secondary_notes(move_name, calculator)
    for note in notes:
        normalized = _normalize(note)
        chance = _note_chance(note)
        if "flinch" in normalized and enemy_moves_first:
            penalty += chance * 0.55
        elif any(status in normalized for status in ("burn", "paralysis", "poison", "sleep", "freeze")):
            penalty += chance * 0.35
        elif "statchange" in normalized:
            penalty += chance * 0.18
    return penalty


def _risk_notes(
    damage: DamageRange | None,
    crit_damage: DamageRange | None,
    move_name: str,
    enemy_moves_first: bool,
    calculator: DamageCalculator,
) -> list[str]:
    notes: list[str] = []
    if crit_damage and crit_damage.ko_chance >= 1.0:
        notes.append(f"crit can KO ({_format_damage_range(crit_damage)})")
    elif crit_damage:
        notes.append(f"crit range {_format_damage_range(crit_damage)}")
    for effect in _move_secondary_notes(move_name, calculator):
        if "flinch" in _normalize(effect):
            if enemy_moves_first:
                notes.append(f"{effect} if enemy moves first")
            else:
                notes.append(f"{effect}, but only matters if you move after")
        else:
            notes.append(effect)
    if damage and damage.accuracy < 1:
        notes.append(f"{damage.accuracy * 100:.0f}% accurate")
    return notes


def _status_label(status: str) -> str:
    return {
        "brn": "burn",
        "par": "paralysis",
        "psn": "poison",
        "tox": "toxic",
        "slp": "sleep",
        "frz": "freeze",
    }.get(status, status)


def _note_chance(note: str) -> float:
    try:
        if "%" in note:
            return float(note.split("%", 1)[0])
    except ValueError:
        pass
    return 100.0


def _speed(pokemon: PokemonCalcSet, calculator: DamageCalculator) -> int:
    species_data = calculator._species_data(pokemon.species)  # noqa: SLF001
    if not species_data:
        return 0
    return calculator._stat(species_data, "spe", pokemon.level, pokemon.nature, pokemon.evs, pokemon.ivs)  # noqa: SLF001


def _choose_replacement_slot(
    state: BattleState,
    report: WeaknessReport,
    calculator: DamageCalculator | None,
) -> tuple[int, str]:
    empty_slots = [index for index, max_hp in enumerate(state.player_max_hp) if max_hp <= 0]
    if empty_slots:
        return empty_slots[0], ""
    live_slots = [index for index, max_hp in enumerate(state.player_max_hp) if max_hp > 0]
    if not live_slots:
        return 0, ""
    if calculator is not None:
        scored: list[tuple[float, int]] = []
        for slot in live_slots:
            risk = calculator.enemy_peak_risk_to_player_slot(state, slot)
            best = risk.best_damage
            hp_ratio = state.player_hp[slot] / state.player_max_hp[slot] if state.player_max_hp[slot] else 0
            risk_score = (best.max_percent if best else 0.0) - hp_ratio * 0.25
            scored.append((risk_score, slot))
        if scored:
            slot = max(scored)[1]
            return slot, _state_player_name(state, slot)
    if 0 <= report.most_common_faint_slot < len(state.player_max_hp) and state.player_max_hp[report.most_common_faint_slot] > 0:
        slot = report.most_common_faint_slot
        return slot, _state_player_name(state, slot)
    slot = min(live_slots, key=lambda index: state.player_hp[index] / state.player_max_hp[index])
    return slot, _state_player_name(state, slot)


def _candidate_note(
    mon: DecodedPokemon,
    item_name: str | None,
    replace_slot: int,
    replace_name: str,
    trainer_name: str,
    summary: list[dict[str, Any]],
) -> str:
    best = summary[0] if summary else {}
    item_text = f" with {item_name}" if item_name else ""
    replace_text = f"Replace slot {replace_slot + 1} ({replace_name})" if replace_name else "Add to team"
    if best:
        return (
            f"{replace_text} with {mon.display_name}{item_text}. "
            f"Best calc into {trainer_name}: {best.get('best_move')} vs {best.get('enemy')} "
            f"for {best.get('damage')}; takes {best.get('enemy_damage')} back."
        )
    return f"{replace_text} with {mon.display_name}{item_text}."


def _candidate_plan(
    mon: DecodedPokemon,
    item_name: str | None,
    replace_name: str,
    trainer_name: str,
    summary: list[dict[str, Any]],
) -> list[str]:
    plan = [
        f"Bring {mon.display_name}" + (f" holding {item_name}" if item_name else "") + (f" over {replace_name}" if replace_name else "") + ".",
    ]
    for matchup in summary[:3]:
        speed_text = "outspeeds" if matchup.get("outspeeds") else "may move second"
        risk_text = ""
        risks = matchup.get("enemy_risk_notes") or []
        if risks:
            risk_text = " Risk: " + "; ".join(str(item) for item in risks[:2]) + "."
        plan.append(
            f"Into {matchup.get('enemy')}, click {matchup.get('best_move')} ({matchup.get('damage')}); "
            f"{speed_text}; worst known hit back is {matchup.get('enemy_best_move')} ({matchup.get('enemy_damage')}); "
            f"enemy crit is {matchup.get('enemy_crit_damage')}.{risk_text}"
        )
    plan.append(f"After swapping, save before {trainer_name} and run Solve so emulator trials prove the line.")
    return plan


def _team_after_candidate(
    state: BattleState,
    evaluation: CandidateEvaluation,
    scan: BoxScanResult,
) -> list[str]:
    team = [
        f"{_state_player_name(state, index)} {state.player_hp[index]}/{state.player_max_hp[index]}"
        for index, max_hp in enumerate(state.player_max_hp)
        if max_hp > 0
    ]
    label = evaluation.mon.display_name
    if evaluation.suggested_item:
        label = f"{label} @ {evaluation.suggested_item}"
    if 0 <= evaluation.replace_slot < len(team):
        team[evaluation.replace_slot] = label
    else:
        team.append(label)
    return team[:6]


def _team_plan_payload(
    candidates: list[BoxCandidateResult],
    battle_state: BattleState,
    scan: BoxScanResult,
    calculator: DamageCalculator,
) -> dict[str, Any]:
    match = calculator.matched_trainer(battle_state)
    plan_candidates = list(candidates)
    current_team = [
        {
            "slot": index + 1,
            "name": _state_player_name(battle_state, index),
            "hp": battle_state.player_hp[index],
            "max_hp": battle_state.player_max_hp[index],
        }
        for index, max_hp in enumerate(battle_state.player_max_hp)
        if max_hp > 0
    ]
    return {
        "trainer": match.battle.trainer_name if match else "",
        "location": match.battle.location if match else "",
        "matched_hp_error": match.hp_error if match else None,
        "current_team": current_team,
        "decoded_party_from_pc_save": [mon.display_name for mon in scan.party],
        "box_count": len(scan.roster),
        "bag_items": [asdict(item) for item in scan.bag if _is_useful_battle_item(item.name)],
        "best_candidate": candidates[0].to_dict() if candidates else None,
        "all_candidates": [candidate.to_dict() for candidate in candidates],
        **_craft_full_team_plan(plan_candidates, battle_state, scan, calculator),
        "verified_emulator_plan": None,
        "storage_pointer_source": scan.pointers.source,
    }


def _craft_full_team_plan(
    candidates: list[BoxCandidateResult],
    battle_state: BattleState,
    scan: BoxScanResult,
    calculator: DamageCalculator,
) -> dict[str, Any]:
    team = _base_team_slots(battle_state)
    # The battle snapshot does not carry held-item ownership.  Hydrate the current
    # slots from the pre-fight save so the crafted plan preserves real items and can
    # be applied without guessing which copy belongs to which Pokemon.
    for index, mon in enumerate(scan.party[:6]):
        if index >= len(team) or team[index] is None:
            continue
        team[index].update(
            {
                "source": "party",
                "party_slot": index + 1,
                "item": mon.held_item,
                "item_id": mon.held_item_id,
            }
        )
    assignments: dict[str, int] = {}
    replace_queue = _replacement_queue(battle_state, calculator)
    empty_count = sum(slot is None for slot in team[:6])
    # A collection of individually useful matchups is not proof that replacing the
    # entire party is better. Fill genuine empty slots; if the party is already full,
    # make only the single strongest replacement and leave multi-swap validation to
    # the emulator search.
    replace_queue = replace_queue[: max(1, empty_count)]

    for candidate in candidates:
        if not replace_queue:
            break
        if _normalize(candidate.name) in assignments:
            continue
        slot = replace_queue.pop(0)
        replaced = team[slot]["name"] if team[slot] is not None else ""
        team[slot] = {
            "slot": slot + 1,
            "name": candidate.name,
            "species": candidate.species,
            "source": "box",
            "box": candidate.box,
            "box_slot": candidate.slot,
            "level": candidate.level,
            "item": candidate.suggested_item or candidate.held_item,
            "item_id": next(
                (
                    mon.held_item_id
                    for mon in scan.roster
                    if mon.box == candidate.box and mon.slot == candidate.slot
                ),
                0,
            ),
            "replaces": replaced,
            "rank": candidate.rank,
        }
        assignments[_normalize(candidate.name)] = slot

    compact_team = [
        slot
        for slot in team
        if slot is not None and slot.get("name") and slot.get("max_hp", 1) != 0
    ]
    _optimize_crafted_items(compact_team, scan, battle_state, calculator)
    apply_requests, apply_error = _apply_requests_for_team(compact_team, scan)
    planned_names = {_normalize(slot["name"]) for slot in compact_team}
    planned_candidates = [
        candidate
        for candidate in candidates
        if _normalize(candidate.name) in planned_names or not planned_names
    ]
    stateful = build_stateful_turn_plan(candidates, battle_state, scan, calculator, compact_team)
    return {
        "full_team": compact_team,
        "play_by_play": stateful.get("play_by_play") or _calc_play_by_play(planned_candidates, calculator, battle_state),
        "lead": stateful.get("lead"),
        "success_estimate": stateful.get("success_estimate", 0.0),
        "planner_result": stateful.get("planner_result", "partial-line"),
        "assumptions": stateful.get("assumptions", []),
        "item_recommendations": stateful.get("item_recommendations", []),
        "apply_requests": apply_requests,
        "apply_ready": apply_error is None,
        "apply_error": apply_error,
        "risk_policy": (
            "Calc-only line: prefer a clean OHKO or 2HKO, avoid answers that die to a normal hit, "
            "carry HP/status forward, charge switch turns, model forced sendouts, flag enemy crit KOs, "
            "and treat flinch/status/stat-drop secondary effects as line risks."
        ),
    }


def _apply_requests_for_team(
    team: list[dict[str, Any]], scan: BoxScanResult
) -> tuple[list[dict[str, Any]], str | None]:
    """Compile the visual team plan into exact, inventory-safe emulator sources."""
    selected_boxes = {
        (int(slot["box"]), int(slot["box_slot"]))
        for slot in team
        if slot.get("source") == "box" and slot.get("box") and slot.get("box_slot")
    }
    eligible = list(scan.party) + [
        mon for mon in scan.roster if (mon.box, mon.slot) in selected_boxes
    ]
    item_ids: dict[str, list[int]] = {}
    for mon in eligible:
        if mon.held_item_id and mon.held_item:
            item_ids.setdefault(_normalize(mon.held_item), []).append(mon.held_item_id)

    requests: list[dict[str, Any]] = []
    wanted_counts: dict[int, int] = {}
    owned_counts: dict[int, int] = {}
    for values in item_ids.values():
        for item_id in values:
            owned_counts[item_id] = owned_counts.get(item_id, 0) + 1

    for slot in team:
        source = slot.get("source")
        request: dict[str, Any] = {"source": source}
        if source == "box":
            if int(slot.get("box") or 0) == config.NUZLOCKE_GRAVEYARD_BOX:
                return [], f"Box {config.NUZLOCKE_GRAVEYARD_BOX} is the Nuzlocke graveyard."
            request.update({"box": slot.get("box"), "box_slot": slot.get("box_slot")})
        else:
            party_slot = int(slot.get("party_slot") or slot.get("slot") or 0)
            if party_slot < 1 or party_slot > len(scan.party):
                return [], f"Party slot {party_slot} is not present in the selected pre-fight save."
            request.update({"source": "party", "party_slot": party_slot})

        item_name = slot.get("item")
        item_id = 0
        if item_name:
            ids = item_ids.get(_normalize(str(item_name))) or []
            if not ids:
                return [], f"{item_name} is recommended but is not currently held by an eligible Pokemon."
            item_id = ids[0]
            wanted_counts[item_id] = wanted_counts.get(item_id, 0) + 1
        request["item_id"] = item_id
        requests.append(request)

    for item_id, count in wanted_counts.items():
        if count > owned_counts.get(item_id, 0):
            return [], "The item plan asks for more held-item copies than the save owns."
    return requests, None


def _optimize_crafted_items(
    compact_team: list[dict[str, Any]],
    scan: BoxScanResult,
    battle_state: BattleState,
    calculator: DamageCalculator,
) -> None:
    decoded_by_name = {_normalize(mon.display_name): mon for mon in scan.roster}
    fake_out_sweepers = []
    for slot in compact_team:
        if slot.get("item"):
            continue
        mon = decoded_by_name.get(_normalize(slot.get("name")))
        if mon is None:
            continue
        move_ids = {_normalize(move) for move in mon.moves}
        if "fakeout" in move_ids and move_ids & {"vitalthrow", "forcepalm", "brickbreak", "drainpunch", "closecombat"}:
            fake_out_sweepers.append(slot)
    donors = [
        slot
        for slot in compact_team
        if _normalize(slot.get("item")) == "sitrusberry"
        and _normalize(slot.get("name")) not in {"carnivine"}
    ]
    if fake_out_sweepers and donors:
        target = fake_out_sweepers[0]
        donor = donors[0]
        donor["item"] = None
        donor["item_id"] = 0
        target["item"] = "Sitrus Berry"
        target["item_id"] = 520
        target["item_note"] = f"Move Sitrus Berry from {donor.get('name')} for the planned line."

    _move_low_value_sitrus(compact_team, battle_state, calculator)


def _move_low_value_sitrus(
    team: list[dict[str, Any]], battle_state: BattleState, calculator: DamageCalculator
) -> None:
    """Move one spare Sitrus from a low-risk bench slot to a materially exposed slot."""
    risks: dict[int, float] = {}
    for slot in team:
        if slot.get("source") != "party":
            continue
        index = int(slot.get("party_slot") or slot.get("slot") or 0) - 1
        if index < 0 or index >= len(battle_state.player_max_hp):
            continue
        peak = calculator.enemy_peak_risk_to_player_slot(battle_state, index)
        risks[int(slot["slot"])] = peak.best_damage.max_percent if peak.best_damage else 0.0

    donors = [
        slot for slot in team
        if _normalize(slot.get("item")) == "sitrusberry"
        and int(slot.get("slot") or 0) != 1
        and int(slot.get("slot") or 0) in risks
    ]
    recipients = [
        slot for slot in team
        if not slot.get("item") and int(slot.get("slot") or 0) in risks
    ]
    if not donors or not recipients:
        return
    donor = min(donors, key=lambda slot: risks[int(slot["slot"])])
    recipient = max(recipients, key=lambda slot: risks[int(slot["slot"])])
    donor_risk = risks[int(donor["slot"])]
    recipient_risk = risks[int(recipient["slot"])]
    if recipient_risk < donor_risk + 0.15:
        return
    item_id = int(donor.get("item_id") or 520)
    donor["item"] = None
    donor["item_id"] = 0
    recipient["item"] = "Sitrus Berry"
    recipient["item_id"] = item_id
    recipient["item_note"] = (
        f"Move Sitrus Berry from {donor.get('name')}; this slot faces the larger normal-hit range."
    )


def _base_team_slots(battle_state: BattleState) -> list[dict[str, Any] | None]:
    slots: list[dict[str, Any] | None] = []
    max_len = max(6, len(battle_state.player_max_hp))
    for index in range(max_len):
        max_hp = battle_state.player_max_hp[index] if index < len(battle_state.player_max_hp) else 0
        hp = battle_state.player_hp[index] if index < len(battle_state.player_hp) else 0
        name = _state_player_name(battle_state, index)
        if max_hp <= 0:
            slots.append(None)
            continue
        slots.append(
            {
                "slot": index + 1,
                "name": name,
                "source": "current",
                "hp": hp,
                "max_hp": max_hp,
                "item": None,
                "replaces": "",
            }
        )
    return slots[:6]


def _replacement_queue(battle_state: BattleState, calculator: DamageCalculator) -> list[int]:
    empty_slots = [index for index in range(6) if index >= len(battle_state.player_max_hp) or battle_state.player_max_hp[index] <= 0]
    live_slots = [index for index, max_hp in enumerate(battle_state.player_max_hp[:6]) if max_hp > 0]
    scored: list[tuple[float, int]] = []
    for slot in live_slots:
        hp_ratio = battle_state.player_hp[slot] / battle_state.player_max_hp[slot] if battle_state.player_max_hp[slot] else 0.0
        risk = calculator.enemy_peak_risk_to_player_slot(battle_state, slot)
        damage = risk.best_damage.max_percent if risk.best_damage else 0.0
        score = damage * 100.0 + (1.0 - hp_ratio) * 25.0
        if not _player_slot_has_known_moves(battle_state, slot):
            score += 15.0
        scored.append((score, slot))
    return empty_slots + [slot for _score, slot in sorted(scored, reverse=True)]


def _player_slot_has_known_moves(battle_state: BattleState, slot: int) -> bool:
    if slot >= len(battle_state.player_move_names_by_slot):
        return False
    return any(move and not move.casefold().startswith("unknown move") for move in battle_state.player_move_names_by_slot[slot])


def _calc_play_by_play(
    candidates: list[BoxCandidateResult],
    calculator: DamageCalculator,
    battle_state: BattleState,
) -> list[dict[str, Any]]:
    match = calculator.matched_trainer(battle_state)
    if match is None:
        return []
    steps: list[dict[str, Any]] = []
    for turn, enemy_set in enumerate(match.sets, start=1):
        enemy_name = enemy_set.pokemon.species
        answer = _best_answer_for_enemy(candidates, enemy_name)
        if answer is None:
            steps.append(
                {
                    "turn": turn,
                    "enemy": enemy_name,
                    "answer": "",
                    "action": f"No boxed calc answer found for {enemy_name}; keep current team line flexible here.",
                    "calc": "No reliable decoded move matchup.",
                    "risks": ["Re-run Solve after team changes if this trainer slot is still failing."],
                    "consistency": "unknown",
                }
            )
            continue
        candidate, matchup = answer
        risks = list(matchup.get("enemy_risk_notes") or [])
        own_secondary = matchup.get("secondary_effects") or []
        if own_secondary:
            risks.append("Your move secondary: " + ", ".join(str(item) for item in own_secondary))
        steps.append(
            {
                "turn": turn,
                "enemy": enemy_name,
                "answer": candidate.name,
                "action": _line_action(candidate, matchup),
                "calc": _line_calc(matchup),
                "risks": risks or ["No major crit/secondary risk flagged by the calc."],
                "consistency": _line_consistency(matchup),
                "score": matchup.get("score", 0.0),
            }
        )
    return steps


def _best_answer_for_enemy(
    candidates: list[BoxCandidateResult],
    enemy_name: str,
) -> tuple[BoxCandidateResult, dict[str, Any]] | None:
    answers: list[tuple[float, BoxCandidateResult, dict[str, Any]]] = []
    for candidate in candidates:
        for matchup in candidate.matchup_summary:
            if _normalize(matchup.get("enemy")) != _normalize(enemy_name):
                continue
            score = float(matchup.get("score") or 0.0)
            if matchup.get("ko_chance", 0.0) >= 1.0:
                score += 35.0
            if matchup.get("enemy_ko_chance", 0.0) >= 1.0:
                score -= 120.0
            if matchup.get("enemy_crit_ko_chance", 0.0) >= 1.0:
                score -= 45.0
            if matchup.get("outspeeds"):
                score += 10.0
            if matchup.get("enemy_risk_notes"):
                score -= min(25.0, len(matchup["enemy_risk_notes"]) * 7.0)
            answers.append((score, candidate, matchup))
    if not answers:
        return None
    _score, candidate, matchup = max(answers, key=lambda item: item[0])
    return candidate, matchup


def _line_action(candidate: BoxCandidateResult, matchup: dict[str, Any]) -> str:
    item_text = f" @ {candidate.suggested_item or candidate.held_item}" if candidate.suggested_item or candidate.held_item else ""
    speed_text = "before it moves" if matchup.get("outspeeds") else "and be ready to take the hit first"
    return (
        f"Use {candidate.name}{item_text} into {matchup.get('enemy')}; "
        f"click {matchup.get('best_move') or 'best damaging move'} {speed_text}."
    )


def _line_calc(matchup: dict[str, Any]) -> str:
    enemy_return = (
        "no meaningful hit back"
        if not matchup.get("enemy_best_move")
        else f"{matchup.get('enemy_best_move')} for {matchup.get('enemy_damage')} (crit {matchup.get('enemy_crit_damage')})"
    )
    hp_text = _remaining_after_hit_text(matchup)
    return (
        f"{matchup.get('best_move') or 'Move'} does {matchup.get('damage')} "
        f"(crit {matchup.get('crit_damage')}); enemy best response is {enemy_return}. {hp_text}"
    )


def _remaining_after_hit_text(matchup: dict[str, Any]) -> str:
    if matchup.get("ko_chance", 0.0) >= 1.0 and matchup.get("outspeeds"):
        return "If the roll KOs, it should not take a return hit."
    enemy_max = float(matchup.get("enemy_damage_max") or 0.0)
    enemy_crit_max = float(matchup.get("enemy_crit_damage_max") or 0.0)
    if enemy_max <= 0:
        return "No return damage expected from the known set."
    normal_remaining = max(0, round((1.0 - enemy_max) * 100))
    if enemy_crit_max >= 1.0:
        return f"Worst normal hit leaves about {normal_remaining}%+; crit can KO."
    crit_remaining = max(0, round((1.0 - enemy_crit_max) * 100))
    return f"Worst normal hit leaves about {normal_remaining}%+; worst crit leaves about {crit_remaining}%+."


def _line_consistency(matchup: dict[str, Any]) -> str:
    if matchup.get("enemy_ko_chance", 0.0) >= 1.0:
        return "bad: enemy normal hit can KO this answer"
    if matchup.get("enemy_crit_ko_chance", 0.0) >= 1.0:
        return "risky: normal hit is acceptable, but enemy crit can KO"
    if matchup.get("ko_chance", 0.0) >= 1.0:
        return "strong: guaranteed KO by damage rolls"
    if matchup.get("damage_max", 0.0) >= 0.5:
        return "workable: likely 2HKO; watch enemy secondary effects"
    return "chip line: damage is low, use only if the matchup gives safe progress"


def _format_damage_range(damage: DamageRange | None) -> str:
    if damage is None:
        return "0-0%"
    return f"{damage.min_percent * 100:.1f}-{damage.max_percent * 100:.1f}%"


def _normalize(value: str | None) -> str:
    return "".join(char for char in (value or "").casefold() if char.isalnum())


def _state_player_name(state: BattleState, slot: int) -> str:
    if slot < len(state.player_names) and state.player_names[slot]:
        return state.player_names[slot]
    return f"slot {slot + 1}"


def _scanned_note(mon: DecodedPokemon) -> str:
    moves = ", ".join(mon.moves[:4]) if mon.moves else "no decoded moves"
    item = f" @ {mon.held_item}" if mon.held_item else ""
    return f"Decoded from PC storage as {mon.species} Lv.{mon.level}{item}; moves: {moves}."


def _read_battle_state(rom_path: str, battle_state_path: str) -> BattleState:
    instance = MGBAInstance(rom_path, battle_state_path, 70)
    try:
        return StateReader(instance).read()
    finally:
        instance.shutdown()


def _looks_like_battle(state: BattleState) -> bool:
    return state.menu_ready and not state.battle_over and any(state.enemy_max_hp)


def _has_enemy_party(state: BattleState) -> bool:
    return not state.battle_over and any(max_hp > 0 for max_hp in state.enemy_max_hp)


def _screen_looks_battle_command(instance: MGBAInstance, reader: StateReader) -> bool:
    try:
        return InputController(instance, reader)._screen_looks_battle_command()
    except Exception:
        return False


def _read_selected_box_mon(instance: MGBAInstance, box: int, slot: int) -> BoxPokemon:
    name = _read_pokemon_name(instance, 0x02021D00, 10)
    species = instance.read_u16(0x02021D0C)
    level = instance.read_u8(0x02021D10)
    hp = instance.read_u16(0x02021D14)
    max_hp = instance.read_u16(0x02021D16)
    if species == 0 or level > 100 or max_hp > 999 or hp > max_hp:
        return BoxPokemon("", box, slot, 0, 0, 0, 0)
    return BoxPokemon(name or f"Species {species}", box, slot, level, hp, max_hp, species)


def _read_pokemon_name(instance: MGBAInstance, address: int, length: int) -> str:
    chars = []
    for offset in range(length):
        value = instance.read_u8(address + offset)
        if value in (0x00, 0xFF):
            break
        if 0xBB <= value <= 0xD4:
            chars.append(chr(ord("A") + value - 0xBB))
        elif 0xD5 <= value <= 0xEE:
            chars.append(chr(ord("a") + value - 0xD5))
        elif 0xA1 <= value <= 0xAA:
            chars.append(str(value - 0xA1))
        elif value == 0x7F:
            chars.append(" ")
    return "".join(chars).strip()


def _move_pc_cursor_next_slot(instance: MGBAInstance, slot: int) -> None:
    if slot % 6 == 0:
        instance.send_input("DOWN", 2)
        instance.advance_frames(8)
        for _ in range(5):
            instance.send_input("LEFT", 2)
            instance.advance_frames(4)
    else:
        instance.send_input("RIGHT", 2)
        instance.advance_frames(8)


def _move_pc_cursor_next_box(instance: MGBAInstance) -> None:
    instance.send_input("R", 3)
    instance.advance_frames(30)


def print_prepare_report(result: PrepareResult) -> None:
    report = result.weakness_report
    marker = " STRUGGLING" if report.baseline_deathless_win_rate < 0.60 else ""
    print("CURRENT TEAM ANALYSIS")
    print("═══════════════════════════════════")
    print(f"Best deathless win rate: {report.baseline_deathless_win_rate:.0%}{marker}")
    print(f"Best sack win rate: {report.baseline_sack_win_rate:.0%}")
    print(
        "Most common faint: "
        f"slot {report.most_common_faint_slot} "
        f"(turn {report.battle_lost_on_turn}, {report.most_common_faint_rate:.0%} of trials)"
    )
    print(f"Battle usually lost on: turn {report.battle_lost_on_turn}")
    print(f"Problem: {report.problem_description}")
    if report.baseline_deathless_win_rate >= 0.80:
        print("Current team is strong - no box pulls needed")
        return
    print("Recommendation: find a better lead from box")
    if report.pc_error:
        print()
        print(report.pc_error)
    print()
    print("BOX OPTIMIZER RESULTS")
    print("══════════════════════════════════════════════")
    print("BASELINE (current team):")
    print(
        f"  Deathless win rate: {report.baseline_deathless_win_rate:.0%} | "
        f"Faint rate: {result.baseline.faint_probability:.0%}"
    )
    if result.candidates:
        print()
        print("ALTERNATIVE SWAPS (ranked):")
        for candidate in result.candidates:
            print(
                f"#{candidate.rank}  {candidate.name} Box {candidate.box} "
                f"- Deathless: {candidate.deathless_win_rate:.0%} | "
                f"Improvement: {candidate.improvement:+.0%}"
            )
        _print_calc_team_plan(result.team_plan)
    else:
        print()
        print("No box Pokemon significantly improves this matchup")
        print("Best available line with current team is shown by the baseline solver.")
    print()
    print("NEXT STEPS:")
    if report.pc_error:
        print("1. Create a save state in a Pokemon Center standing in front of the PC")
        print("2. Re-run prepare with --battle-state for the fight and --pc-state for the PC")
        print("3. Run solve on the new battle save state after applying any swap")
    else:
        print("1. Build the crafted calc team shown above, including item choices")
        print("2. Save a new battle save state at the trainer action menu")
        print("3. Run Solve on that new battle save to emulator-check the calc line")


def _print_calc_team_plan(team_plan: dict[str, Any] | None) -> None:
    if not team_plan:
        return
    full_team = team_plan.get("full_team") or []
    play_by_play = team_plan.get("play_by_play") or []
    if full_team:
        print()
        print("CRAFTED CALC TEAM")
        print("══════════════════════════════════════════════")
        for slot in full_team:
            item_text = f" @ {slot.get('item')}" if slot.get("item") else ""
            if slot.get("source") == "box":
                replace_text = f" over {slot.get('replaces')}" if slot.get("replaces") else ""
                print(
                    f"Slot {slot.get('slot')}: {slot.get('name')}{item_text} "
                    f"(Box {slot.get('box')}, slot {slot.get('box_slot')}{replace_text})"
                )
            else:
                print(f"Slot {slot.get('slot')}: {slot.get('name')} ({slot.get('hp')}/{slot.get('max_hp')} HP)")
    if play_by_play:
        print()
        print("STEP-BY-STEP CALC LINE")
        print("══════════════════════════════════════════════")
        for step in play_by_play:
            print(f"Turn {step.get('turn')} vs {step.get('enemy')}: {step.get('action')}")
            print(f"  Calc: {step.get('calc')}")
            print(f"  Consistency: {step.get('consistency')}")
            for risk in (step.get("risks") or [])[:4]:
                print(f"  Risk: {risk}")
        if team_plan.get("risk_policy"):
            print()
            print(f"Policy: {team_plan['risk_policy']}")


def _emit_solver_event(on_event: ProgressCallback | None, data: dict[str, Any]) -> None:
    if "node" in data:
        _emit(on_event, {"type": "node", "data": data["node"]})
    if "progress" in data:
        _emit(on_event, {"type": "progress", "data": data["progress"]})


def _emit_initial_state(on_event: ProgressCallback | None, rom_path: str, save_state_path: str) -> None:
    instance = MGBAInstance(rom_path, save_state_path, 67)
    try:
        reader = StateReader(instance)
        state = reader.read()
        legal_actions = len(ActionEnumerator().legal_actions(state))
        _emit(
            on_event,
            {
                "type": "battle_state",
                "data": {
                    "player_hp": state.player_hp,
                    "player_max_hp": state.player_max_hp,
                    "enemy_hp": state.enemy_hp,
                    "enemy_max_hp": state.enemy_max_hp,
                    "is_doubles": state.is_doubles,
                    "legal_actions": legal_actions,
                },
            },
        )
    finally:
        instance.shutdown()


def _prepare_complete_payload(
    baseline: SearchResult,
    candidate: BoxCandidateResult | None,
) -> dict[str, Any]:
    return {
        "baseline_win_rate": baseline.best_deathless_win_rate or baseline.win_probability,
        "best_swap_name": candidate.name if candidate else "",
        "best_swap_box": candidate.box if candidate else 0,
        "best_swap_slot": candidate.slot if candidate else 0,
        "improved_win_rate": candidate.deathless_win_rate if candidate else baseline.win_probability,
        "improvement": candidate.improvement if candidate else 0.0,
    }


def _emit(on_event: ProgressCallback | None, message: dict[str, Any]) -> None:
    if on_event is not None:
        on_event(message)
