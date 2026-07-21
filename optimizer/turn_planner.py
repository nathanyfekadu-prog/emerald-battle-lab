from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any

from battle.battle_state import BattleState
from battle.damage_calc import (
    ATTACK_DROP_MOVES,
    BURN_MOVES,
    CONFUSION_MOVES,
    PARALYSIS_MOVES,
    LEECH_SEED_MOVES,
    POISON_MOVES,
PROTECT_MOVES,
    SETUP_MOVE_BOOSTS,
    SLEEP_MOVES,
    SPECIAL_ATTACK_DROP_MOVES,
    SPEED_DROP_MOVES,
    TOXIC_MOVES,
    DamageCalculator,
    DamageContext,
    DamageRange,
    PokemonCalcSet,
)
from optimizer.gen3_save import DecodedPokemon

RNB_PROTECT_MOVES = PROTECT_MOVES | {"defendorder"}
DIRECT_RECOVERY_MOVES = {
    "recover", "roost", "softboiled", "slackoff", "synthesis", "moonlight",
    "morningsun", "milkdrink", "healorder", "shoreup", "strengthsap",
}
CONFUSE_BERRIES = {"figyberry", "wikiberry", "magoberry", "aguavberry", "iapapaberry"}
PINCH_BOOST_BERRIES = {
    "liechiberry": "atk",
    "ganlonberry": "def",
    "salacberry": "spe",
    "petayaberry": "spa",
    "apicotberry": "spd",
}


@lru_cache(maxsize=200_000)
def _cached_calc_set(
    species: str,
    level: int,
    nature: str | None,
    hp: int,
    max_hp: int,
    evs: tuple[tuple[str, int], ...] | None,
    ivs: tuple[tuple[str, int], ...] | None,
    ability: str | None,
    held_item: str | None,
    status: str | None,
    boosts: tuple[tuple[str, int], ...],
    gender: str | None = None,
    ability_on: bool = True,
    allies_fainted: int = 0,
) -> PokemonCalcSet:
    """Reuse immutable calc inputs across the thousands of repeated route evaluations."""
    return PokemonCalcSet(
        species=species,
        level=level,
        nature=nature,
        hp=hp,
        max_hp=max_hp,
        evs=dict(evs) if evs is not None else None,
        ivs=dict(ivs) if ivs is not None else None,
        ability=ability,
        held_item=held_item,
        status=status,
        boosts=dict(boosts),
        gender=gender,
        ability_on=ability_on,
        allies_fainted=allies_fainted,
    )
SELF_KO_MOVES = {"explosion", "selfdestruct", "mistyexplosion"}
STATUS_CURE_BERRIES = {
    "burn": {"rawstberry", "lumberry"},
    "paralysis": {"cheriberry", "lumberry"},
    "sleep": {"chestoberry", "lumberry"},
    "poison": {"pechaberry", "lumberry"},
    "toxic": {"pechaberry", "lumberry"},
    "freeze": {"aspearberry", "lumberry"},
}
CONFUSION_CURE_BERRIES = {"persimberry", "lumberry"}
EARLY_HELD_ITEMS = {
    "Oran Berry",
    "Pecha Berry",
    "Cheri Berry",
    "Chesto Berry",
    "Leppa Berry",
    "Silk Scarf",
    "Never-Melt Ice",
    "Soft Sand",
    "Black Glasses",
    "Poison Barb",
    "Miracle Seed",
    "Silver Powder",
    "Hard Stone",
    "Wide Lens",
    "Muscle Band",
    "Wise Glasses",
    "Rawst Berry",
    "Persim Berry",
    "Aspear Berry",
    "Sitrus Berry",
    "Lum Berry",
}
MIDGAME_HELD_ITEMS = EARLY_HELD_ITEMS | {
    "Sharp Beak",
    "Pixie Plate",
    "Black Belt",
    "Dragon Fang",
    "Charcoal",
    "Magnet",
    "Mystic Water",
    "Twisted Spoon",
    "Spell Tag",
    "Dark Gem",
    "Poison Gem",
    "Ground Gem",
    "Steel Gem",
    "White Herb",
    "Shed Shell",
}
LATEGAME_HELD_ITEMS = MIDGAME_HELD_ITEMS | {
    "Assault Vest",
    "Leftovers",
    "Choice Band",
    "Choice Scarf",
    "Choice Specs",
    "Rocky Helmet",
    "Rock Gem",
    "Electric Gem",
    "Grass Gem",
    "Flying Gem",
    "Fire Gem",
    "Psychic Gem",
    "Bug Gem",
    "Normal Gem",
    "Ghost Gem",
    "Ice Gem",
    "Fighting Gem",
    "Water Gem",
    "Fairy Gem",
    "Dragon Gem",
    "Focus Sash",
}
SECTION_HELD_ITEM_POOL = {
    "brawlysplit": {"Oran Berry", "Pecha Berry", "Cheri Berry", "Chesto Berry", "Leppa Berry", "Wide Lens"},
    "roxannesplit": EARLY_HELD_ITEMS - {"Sitrus Berry", "Lum Berry", "Aspear Berry", "Persim Berry"},
    "wattsonsplit": EARLY_HELD_ITEMS,
    "flannerysplit": MIDGAME_HELD_ITEMS,
    "normansplit": MIDGAME_HELD_ITEMS,
    "winonasplit": LATEGAME_HELD_ITEMS,
    "tate&lizasplit": LATEGAME_HELD_ITEMS,
    "juansplit": LATEGAME_HELD_ITEMS,
}
HELD_ITEM_LOCATIONS = {
    "orannberry": "Berry trees on Routes 102/104/111",
    "oranberry": "Berry trees on Routes 102/104/111",
    "sitrusberry": "Berry trees from Route 110 onward",
    "lumberry": "Berry trees from Route 111 onward",
    "softsand": "Route 109 beach NPC",
    "wiseglasses": "Route 116 hidden, through Rusturf Tunnel",
    "muscleband": "Rusturf Tunnel, past smashable rocks",
    "hardstone": "Rusturf Tunnel north-west corner",
    "widelens": "Rustboro Trainer School NPC",
    "poisonbarb": "Route 104, Petalburg Woods Cut access",
    "miracleseed": "Petalburg Woods Cut access",
    "silverpowder": "Petalburg Woods Cut access",
    "nevermeltice": "Granite Cave",
    "blackglasses": "Slateport Battle Tent NPC",
    "silkscarf": "Dewford Town NPC",
    "charcoal": "Lavaridge Town NPC",
    "magnet": "New Mauville",
    "mysticwater": "Abandoned Ship",
    "twistedspoon": "Abandoned Ship",
    "blackbelt": "Route 115 by Meteor Falls southern exit",
    "whiteherb": "Route 104 after obtaining 3 badges",
}
BERRY_ITEM_IDS = {
    "oranberry",
    "sitrusberry",
    "lumberry",
    "rawstberry",
    "cheriberry",
    "chestoberry",
    "pechaberry",
    "persimberry",
    "aspearberry",
    "leppaberry",
}


@dataclass
class PlannedMember:
    name: str
    species: str
    level: int
    max_hp: int
    hp: int
    moves: tuple[str, ...]
    item: str | None = None
    ability: str | None = None
    ability_on: bool = True
    nature: str | None = None
    evs: dict[str, int] | None = None
    ivs: dict[str, int] | None = None
    source: str = "current"
    slot: int = 0
    status: str | None = None
    boosts: dict[str, int] = field(default_factory=dict)
    sleep_turns: int = 0
    toxic_counter: int = 0
    leech_seeded: bool = False
    confused_turns: int = 0
    protected: bool = False
    flinched: bool = False
    trapped: bool = False
    salt_cured: bool = False
    syrup_bomb_turns: int = 0
    heal_blocked_turns: int = 0
    sound_blocked_turns: int = 0
    turns_out: int = 0
    consumed_item: bool = False

    def calc_set(self) -> PokemonCalcSet:
        return _cached_calc_set(
            self.species,
            self.level,
            self.nature,
            max(0, self.hp),
            self.max_hp,
            tuple(sorted(self.evs.items())) if self.evs else None,
            tuple(sorted(self.ivs.items())) if self.ivs else None,
            self.ability,
            None if self.consumed_item else self.item,
            self.status,
            tuple(sorted(self.boosts.items())),
            ability_on=self.ability_on,
        )

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def known_moves(self) -> tuple[str, ...]:
        return tuple(move for move in self.moves if move and not _is_unknown_move(move))


@dataclass
class PlannedEnemy:
    name: str
    pokemon: PokemonCalcSet
    moves: tuple[str, ...]
    max_hp: int
    hp: int
    ability: str | None = None
    ability_on: bool = True
    status: str | None = None
    boosts: dict[str, int] = field(default_factory=dict)
    sleep_turns: int = 0
    toxic_counter: int = 0
    leech_seeded: bool = False
    confused_turns: int = 0
    protected: bool = False
    flinched: bool = False
    trapped: bool = False
    salt_cured: bool = False
    syrup_bomb_turns: int = 0
    heal_blocked_turns: int = 0
    sound_blocked_turns: int = 0
    turns_out: int = 0
    consumed_item: bool = False

    def calc_set(self) -> PokemonCalcSet:
        return _cached_calc_set(
            self.pokemon.species,
            self.pokemon.level,
            self.pokemon.nature,
            max(0, self.hp),
            self.max_hp,
            tuple(sorted(self.pokemon.evs.items())) if self.pokemon.evs else None,
            tuple(sorted(self.pokemon.ivs.items())) if self.pokemon.ivs else None,
            self.ability if self.ability is not None else self.pokemon.ability,
            None if self.consumed_item else self.pokemon.held_item,
            self.status,
            tuple(sorted(self.boosts.items())),
            self.pokemon.gender,
            self.ability_on,
            self.pokemon.allies_fainted,
        )

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass(frozen=True)
class MoveChoice:
    move_name: str
    score: float
    probability: float
    damage: DamageRange | None = None
    reason: str = ""


@dataclass(frozen=True)
class PlayerAction:
    kind: str
    move_name: str = ""
    target_slot: int | None = None
    score: float = 0.0
    damage: DamageRange | None = None
    reason: str = ""


def build_stateful_turn_plan(
    candidates: list[Any],
    battle_state: BattleState,
    scan: Any,
    calculator: DamageCalculator,
    full_team: list[dict[str, Any]],
    max_turns: int = 30,
) -> dict[str, Any]:
    match = calculator.matched_trainer(battle_state)
    if match is None:
        return {"play_by_play": [], "lead": None, "success_estimate": 0.0, "assumptions": ["Trainer match unavailable."]}

    team = _planned_team(full_team, candidates, battle_state, scan, calculator)
    enemies = [
        PlannedEnemy(
            name=known.pokemon.species,
            pokemon=known.pokemon,
            moves=known.moves,
            max_hp=known.pokemon.max_hp or _max_hp(known.pokemon, calculator),
            hp=known.pokemon.max_hp or _max_hp(known.pokemon, calculator),
        )
        for known in match.sets
    ]
    if not team or not enemies:
        return {"play_by_play": [], "lead": None, "success_estimate": 0.0, "assumptions": ["No usable team or enemy sets."]}
    item_recommendations = recommend_held_items(team, enemies, calculator, section=match.battle.section)

    enemy_index = _initial_enemy_index(enemies, battle_state)
    active_index = _best_player_answer_for_field(team, enemies, enemy_index, calculator, active_index=None)
    if active_index is None:
        active_index = _first_alive_member(team)
    turns: list[dict[str, Any]] = []
    assumptions = [
        "Stateful calc plan: player damage uses low rolls, enemy damage uses high rolls.",
        "AI move odds approximate the RnB AI document's score-based choices; exact RNG branches are summarized as probabilities.",
        "AI hard switches are modeled as 50% branches when the active enemy has no useful move and a safer back mon.",
        "Post-KO sendout chooses the highest-pressure remaining enemy, not simple party order.",
    ]
    confidence = 1.0
    lead = _member_label(team[active_index]) if active_index is not None else None
    if active_index is not None:
        team[active_index].turns_out = 0

    turn = 1
    while turn <= max_turns and active_index is not None and any(enemy.alive for enemy in enemies):
        enemy = enemies[enemy_index]
        active = team[active_index]
        # A zero sleep counter means the battler wakes before choices are made.
        # Keeping the stale status until action resolution made planners believe
        # an awake opponent was still disabled and miss legal re-sleep lines.
        if enemy.status == "sleep" and enemy.sleep_turns <= 0:
            enemy.status = None
        if active.status == "sleep" and active.sleep_turns <= 0:
            active.status = None
        if _planner_is_stalled(team, enemy, calculator):
            turns.append(
                _turn_dict(
                    turn,
                    enemy,
                    active,
                    f"Planner stops at {enemy.name}.",
                    "No remaining planned team member has reliable decoded damage or a useful setup/status line into this enemy.",
                    ["Current-party move data is unknown, so the calc refuses to invent a fake winning line."],
                    "blocked: no reliable decoded progress",
                    confidence * 0.35,
                )
            )
            break
        if not active.alive:
            active_index = _best_player_answer_for_field(team, enemies, enemy_index, calculator, active_index=None)
            if active_index is None:
                break
            team[active_index].turns_out = 0
            active = team[active_index]
            turns.append(
                _turn_dict(
                    turn,
                    enemy,
                    active,
                    f"Forced send: bring {_member_label(active)} into {enemy.name}.",
                    "Free send after a faint; no switch damage taken.",
                    ["Forced sendout handled before selecting the next attack."],
                    "forced-send",
                    1.0,
                )
            )
            turn += 1
            continue

        switch_target = _switch_target_if_needed_for_field(team, enemies, enemy_index, active_index, calculator)
        if switch_target is not None:
            incoming = team[switch_target]
            ai_switch = _ai_hard_switch_target(enemies, enemy_index, incoming, calculator)
            if ai_switch is not None:
                old_enemy = enemy
                enemy_index = ai_switch
                enemies[enemy_index].turns_out = 0
                turns.append(
                    _turn_dict(
                        turn,
                        old_enemy,
                        incoming,
                        f"Switch {_member_label(active)} -> {_member_label(incoming)}; AI has a hard-switch window and may pivot to {enemies[enemy_index].name}.",
                        "AI hard switch condition met: active enemy has no useful move and is above half HP; modeled as a 50% switch branch.",
                        ["AI switch chance approximated from the RnB AI document."],
                        "ai-switch-branch",
                        0.5,
                    )
                )
                active_index = switch_target
                team[active_index].turns_out = 0
                confidence *= 0.5
                turn += 1
                continue

            choices = _ai_move_choices(enemy, active, team, calculator)
            enemy_choice = choices[0] if choices else None
            retargeted_choice = _retarget_enemy_choice(enemy, incoming, enemy_choice, calculator)
            risks = _choice_risks(retargeted_choice, incoming, enemy, calculator)
            damage_taken = _apply_enemy_action(enemy, incoming, retargeted_choice, calculator)
            active_index = switch_target
            team[active_index].turns_out = 0
            confidence *= _survival_confidence(incoming, retargeted_choice)
            confidence *= _ai_branch_confidence(
                [_retarget_enemy_choice(enemy, incoming, choice, calculator) for choice in choices],
                incoming,
                enemy,
                calculator,
            )
            turns.append(
                _turn_dict(
                    turn,
                    enemy,
                    incoming,
                    f"Switch {_member_label(active)} -> {_member_label(incoming)}.",
                    _switch_calc_text(enemy, incoming, enemy_choice, retargeted_choice, damage_taken, choices),
                    risks,
                    "switch-cost" if incoming.alive else "bad-switch",
                    confidence,
                )
            )
            _end_of_turn(incoming, enemy, calculator)
            turn += 1
            continue

        action = _best_player_action(active, enemy, team, calculator)
        if _action_is_unreliable(action):
            turns.append(
                _turn_dict(
                    turn,
                    enemy,
                    active,
                    f"Planner stops at {enemy.name}.",
                    (
                        f"{active.name} is active, but its decoded move list does not contain a reliable "
                        "damaging/status/setup action for this matchup."
                    ),
                    [
                        "Current-party move data is unknown or unusable here.",
                        "The planner stopped instead of inventing Unknown move damage.",
                    ],
                    "blocked: unknown current moves",
                    confidence * 0.35,
                )
            )
            break
        ai_switch = _ai_hard_switch_target(enemies, enemy_index, active, calculator)
        if ai_switch is not None:
            old_enemy = enemy
            incoming_enemy = enemies[ai_switch]
            enemy_index = ai_switch
            incoming_enemy.turns_out = 0
            switched_action = _retarget_player_action(active, incoming_enemy, action, calculator)
            events = [
                (
                    f"{old_enemy.name} has no useful scored move, so AI may hard-switch to "
                    f"{incoming_enemy.name}."
                )
            ]
            if active.alive:
                skipped = _skip_turn(active)
                if skipped:
                    events.append(f"{active.name} cannot move this turn.")
                else:
                    dealt = _apply_player_action(active, incoming_enemy, switched_action, calculator)
                    events.append(_player_action_text(active, incoming_enemy, switched_action, dealt))
            _end_of_turn(active, incoming_enemy, calculator)
            confidence *= 0.5
            if not incoming_enemy.alive:
                _mark_enemy_allies_fainted(enemies, ai_switch)
                next_enemy = _choose_next_enemy(enemies, team, active_index, calculator)
                if next_enemy != enemy_index:
                    events.append(
                        f"{incoming_enemy.name} faints; AI sends {enemies[next_enemy].name} as the highest-pressure remaining mon."
                    )
                    enemy_index = next_enemy
                    enemies[enemy_index].turns_out = 0
            turns.append(
                _turn_dict(
                    turn,
                    incoming_enemy,
                    active,
                    f"{_member_label(active)} clicked {action.move_name}; AI hard-switch branch to {incoming_enemy.name}.",
                    " ".join(events),
                    [
                        "AI switch chance approximated from the RnB AI document.",
                        "Move damage is retargeted into the switch-in.",
                    ],
                    "ai-switch-branch",
                    confidence,
                )
            )
            if not active.alive:
                    active_index = _best_player_answer_for_field(team, enemies, enemy_index, calculator, active_index=None)
            if active_index is not None:
                team[active_index].turns_out += 1
            turn += 1
            continue
        choices = _ai_move_choices(enemy, active, team, calculator)
        enemy_choice = choices[0] if choices else None
        enemy_risks = _choice_risks(enemy_choice, active, enemy, calculator) + _branch_risk_notes(
            choices,
            active,
            enemy,
            calculator,
        )
        confidence *= _player_action_confidence(action, calculator, active)
        confidence *= _ai_branch_confidence(choices, active, enemy, calculator)

        enemy_first = _enemy_moves_before_player(enemy, active, enemy_choice.move_name if enemy_choice else "", action.move_name, calculator)
        if enemy_first and not _will_skip_turn(enemy) and _choice_kills_current(enemy_choice, active):
            turns.append(
                _turn_dict(
                    turn,
                    enemy,
                    active,
                    f"Planner stops before sacking {_member_label(active)} into {enemy.name}.",
                    (
                        f"{enemy.name} is expected to move first with {enemy_choice.move_name if enemy_choice else 'its best move'} "
                        f"and KO {active.name}; no safer decoded switch or faster KO was found."
                    ),
                    enemy_risks + ["The planner stops here because this is not a clean nuzlocke line."],
                    "blocked: unavoidable death",
                    confidence * 0.25,
                )
            )
            break
        events: list[str] = []
        if enemy_first:
            skipped = _skip_turn(enemy)
            if skipped:
                events.append(f"{enemy.name} cannot move this turn.")
            else:
                damage_taken = _apply_enemy_action(enemy, active, enemy_choice, calculator)
                events.append(_enemy_action_text(enemy, active, enemy_choice, damage_taken, choices))
        if active.alive:
            skipped = _skip_turn(active)
            if skipped:
                events.append(f"{active.name} cannot move this turn.")
            else:
                action = _refresh_player_action(active, enemy, action, calculator)
                dealt = _apply_player_action(active, enemy, action, calculator)
                events.append(_player_action_text(active, enemy, action, dealt))
        if not enemy_first and enemy.alive:
            skipped = _skip_turn(enemy)
            if skipped:
                events.append(f"{enemy.name} cannot move this turn.")
            else:
                damage_taken = _apply_enemy_action(enemy, active, enemy_choice, calculator)
                events.append(_enemy_action_text(enemy, active, enemy_choice, damage_taken, choices))

        _end_of_turn(active, enemy, calculator)
        enemy_fainted = not enemy.alive
        active_fainted = not active.alive
        risks = list(enemy_risks)
        if action.damage and action.damage.accuracy < 1:
            risks.append(f"{action.move_name} is {action.damage.accuracy * 100:.0f}% accurate.")
        if enemy_fainted:
            _mark_enemy_allies_fainted(enemies, enemy_index)
            next_enemy = _choose_next_enemy(enemies, team, active_index, calculator)
            if next_enemy != enemy_index:
                events.append(f"{enemy.name} faints; AI sends {enemies[next_enemy].name} as the highest-pressure remaining mon.")
                enemy_index = next_enemy
                enemies[enemy_index].turns_out = 0
        if active_fainted:
            events.append(f"{active.name} faints; next turn is a forced send.")
            active_index = None
        turns.append(
            _turn_dict(
                turn,
                enemy,
                active,
                _action_header(active, action, enemy),
                " ".join(events),
                risks or ["No major crit/secondary risk flagged on this turn."],
                _turn_consistency(active, enemy, enemy_choice, action),
                confidence,
            )
        )
        if active_index is None:
            active_index = (
                _best_player_answer_for_field(team, enemies, enemy_index, calculator, active_index=None)
                if any(enemy.alive for enemy in enemies)
                else None
            )
        if active_index is not None:
            team[active_index].turns_out += 1
        if enemy.alive:
            enemy.turns_out += 1
        turn += 1

    success = any(enemy.alive for enemy in enemies) is False and any(member.alive for member in team)
    return {
        "play_by_play": turns,
        "lead": lead,
        "success_estimate": round(max(0.0, min(1.0, confidence if success else confidence * 0.35)), 3),
        "assumptions": assumptions,
        "planner_result": "win-line" if success else "partial-line",
        "item_recommendations": item_recommendations,
    }


def recommend_held_items(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    calculator: DamageCalculator,
    *,
    section: str | None = None,
    available_items: list[str] | tuple[str, ...] | set[str] | None = None,
    unavailable_items: list[str] | tuple[str, ...] | set[str] | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    pool = _available_held_items(section, available_items)
    if calculator.game_mode == "pokemon-emerald":
        # Never suggest a post-Gen-III item in Emerald mode.  Explicit imported
        # inventory is still accepted below, but the built-in pool is generation-safe.
        pool = {item for item in pool if _normalize(item) in calculator.items}
    unavailable_ids = {_normalize(item) for item in (unavailable_items or ())}
    pool = {item for item in pool if _normalize(item) not in unavailable_ids}
    for member in team:
        # Preserve an imported item for damage evaluation, except when the caller
        # explicitly says this battle occurs before that resource can be obtained.
        if member.item and _normalize(member.item) not in unavailable_ids:
            pool.add(member.item)
    scored: list[tuple[float, int, str, str]] = []
    for index, member in enumerate(team):
        if not member.alive:
            continue
        for item in sorted(pool):
            if _normalize(item) == _normalize(member.item):
                continue
            score, reason = _score_item_for_member(member, item, enemies, calculator)
            if score >= 5.0:
                scored.append((score, index, item, reason))
    scored.sort(reverse=True, key=lambda row: row[0])
    used_members: set[int] = set()
    used_unique_items: set[str] = set()
    recommendations: list[dict[str, Any]] = []
    for score, member_index, item, reason in scored:
        item_id = _normalize(item)
        if member_index in used_members:
            continue
        if item_id not in BERRY_ITEM_IDS and item_id in used_unique_items:
            continue
        member = team[member_index]
        recommendations.append(
            {
                "pokemon": member.name,
                "species": member.species,
                "current_item": member.item,
                "suggested_item": item,
                "score": round(score, 1),
                "reason": reason,
                "source": (
                    "Pokémon Emerald held-item pool (must be present in the current bag)"
                    if calculator.game_mode == "pokemon-emerald"
                    else HELD_ITEM_LOCATIONS.get(item_id, "Available held-item pool for this split")
                ),
            }
        )
        used_members.add(member_index)
        if item_id not in BERRY_ITEM_IDS:
            used_unique_items.add(item_id)
        if len(recommendations) >= limit:
            break
    return recommendations


def _available_held_items(
    section: str | None,
    extra_items: list[str] | tuple[str, ...] | set[str] | None,
) -> set[str]:
    section_id = _normalize(section)
    pool = set(SECTION_HELD_ITEM_POOL.get(section_id, LATEGAME_HELD_ITEMS if section_id else EARLY_HELD_ITEMS))
    if extra_items:
        pool.update(item for item in extra_items if item)
    return pool


def _score_item_for_member(
    member: PlannedMember,
    item: str,
    enemies: list[PlannedEnemy],
    calculator: DamageCalculator,
) -> tuple[float, str]:
    current_set = member.calc_set()
    item_set = replace(current_set, held_item=item)
    best_score = 0.0
    best_reason = ""
    for enemy in enemies:
        if not enemy.alive:
            continue
        enemy_set = enemy.calc_set()
        current = _first_damage(calculator.rank_move_names(current_set, enemy_set, member.known_moves))
        improved = _first_damage(calculator.rank_move_names(item_set, enemy_set, member.known_moves))
        if improved is not None:
            current_max = current.max_damage if current else 0
            enemy_hp = max(1, enemy.hp)
            score = max(0.0, improved.max_damage - current_max) / max(1, enemy.max_hp) * 85.0
            if current is None or _normalize(improved.move_name) != _normalize(current.move_name):
                score += 4.0
            if improved.min_damage >= enemy_hp and not (current and current.min_damage >= enemy_hp):
                score += 55.0
            elif improved.max_damage >= enemy_hp and not (current and current.max_damage >= enemy_hp):
                score += 32.0
            if improved.max_damage * 2 >= enemy_hp and not (current and current.max_damage * 2 >= enemy_hp):
                score += 14.0
            if current and improved.accuracy > current.accuracy and improved.max_damage >= enemy_hp * 0.45:
                score += (improved.accuracy - current.accuracy) * 65.0
            if score > best_score:
                before = _damage_text(current) if current else "no useful damage"
                after = _damage_text(improved)
                best_score = score
                best_reason = f"{item} improves {improved.move_name} into {enemy.name}: {before} -> {after}."

        defensive_score, defensive_reason = _recovery_item_score(member, item, enemy, calculator)
        if defensive_score > best_score:
            best_score = defensive_score
            best_reason = defensive_reason
    return best_score, best_reason


def _first_damage(ranked: tuple[DamageRange, ...]) -> DamageRange | None:
    return next((damage for damage in ranked if damage.max_damage > 0), None)


def _damage_text(damage: DamageRange) -> str:
    return f"{damage.move_name} {damage.min_damage}-{damage.max_damage}"


def _recovery_item_score(
    member: PlannedMember,
    item: str,
    enemy: PlannedEnemy,
    calculator: DamageCalculator,
) -> tuple[float, str]:
    item_id = _normalize(item)
    if item_id not in {"oranberry", "sitrusberry"} | CONFUSE_BERRIES:
        return 0.0, ""
    ranked = calculator.rank_move_names(enemy.calc_set(), member.calc_set(), enemy.moves)
    incoming = _first_damage(ranked)
    if incoming is None or incoming.max_damage <= 0 or incoming.max_damage >= member.hp:
        return 0.0, ""
    hp_after = member.hp - incoming.max_damage
    if item_id == "oranberry":
        threshold = member.max_hp // 2
        heal = min(10, member.max_hp - hp_after)
    elif item_id == "sitrusberry":
        threshold = member.max_hp // 2
        heal = min(max(1, member.max_hp // 4), member.max_hp - hp_after)
    else:
        threshold = member.max_hp // 4
        heal = min(max(1, member.max_hp // 2), member.max_hp - hp_after)
    if hp_after > threshold or heal <= 0:
        return 0.0, ""
    score = 8.0 + heal / max(1, member.max_hp) * 45.0
    if hp_after + heal > incoming.max_damage:
        score += 12.0
    return score, f"{item} can trigger after {enemy.name}'s {incoming.move_name}, restoring about {heal} HP."


def _planned_team(
    full_team: list[dict[str, Any]],
    candidates: list[Any],
    battle_state: BattleState,
    scan: Any,
    calculator: DamageCalculator,
) -> list[PlannedMember]:
    decoded_by_box = {(mon.box, mon.slot): mon for mon in getattr(scan, "roster", [])}
    decoded_by_name = {_normalize(mon.display_name): mon for mon in getattr(scan, "roster", [])}
    candidates_by_name = {_normalize(candidate.name): candidate for candidate in candidates}
    members: list[PlannedMember] = []
    for slot in full_team:
        if slot.get("source") == "box":
            mon = decoded_by_box.get((slot.get("box"), slot.get("box_slot"))) or decoded_by_name.get(_normalize(slot.get("name")))
            if mon is None:
                continue
            candidate = candidates_by_name.get(_normalize(slot.get("name")))
            item = getattr(candidate, "suggested_item", None) or slot.get("item") or mon.held_item
            members.append(_member_from_decoded(mon, calculator, item, len(members)))
        else:
            state_slot = int(slot.get("slot", len(members) + 1)) - 1
            members.append(_member_from_state(battle_state, state_slot, calculator, len(members)))
    return members[:6]


def _initial_enemy_index(enemies: list[PlannedEnemy], battle_state: BattleState) -> int:
    for index, enemy in enumerate(enemies):
        if index < len(battle_state.enemy_fainted) and battle_state.enemy_fainted[index]:
            enemy.hp = 0
            continue
        if index < len(battle_state.enemy_hp) and battle_state.enemy_hp[index] > 0:
            enemy.hp = min(enemy.max_hp, battle_state.enemy_hp[index])
            return index
        if enemy.alive:
            return index
    return 0


def _planner_is_stalled(
    team: list[PlannedMember],
    enemy: PlannedEnemy,
    calculator: DamageCalculator,
) -> bool:
    if not enemy.alive:
        return False
    for member in team:
        if not member.alive:
            continue
        action = _best_player_action(member, enemy, team, calculator)
        if action.damage and action.damage.max_damage > 0:
            return False
        if action.score > 0 and action.move_name and not _is_unknown_move(action.move_name):
            return False
    return True


def _action_is_unreliable(action: PlayerAction) -> bool:
    if not action.move_name or _is_unknown_move(action.move_name):
        return True
    if action.damage and action.damage.max_damage > 0:
        return False
    return action.score <= 0


def _member_from_decoded(
    mon: DecodedPokemon,
    calculator: DamageCalculator,
    item: str | None,
    slot: int,
) -> PlannedMember:
    species_data = calculator._species_data(mon.species)  # noqa: SLF001
    max_hp = mon.max_hp
    if max_hp is None and species_data:
        max_hp = calculator._stat(species_data, "hp", mon.level, mon.nature, mon.evs, mon.ivs)  # noqa: SLF001
    max_hp = max_hp or 1
    return PlannedMember(
        name=mon.display_name,
        species=mon.species,
        level=mon.level,
        max_hp=max_hp,
        hp=max_hp,
        moves=tuple(mon.moves),
        item=item,
        nature=mon.nature,
        evs=mon.evs,
        ivs=mon.ivs,
        source="box",
        slot=slot,
    )


def _member_from_state(
    battle_state: BattleState,
    state_slot: int,
    calculator: DamageCalculator,
    slot: int,
) -> PlannedMember:
    calc_set = calculator._pokemon_from_state(battle_state, "player", state_slot)  # noqa: SLF001
    max_hp = calc_set.max_hp or _max_hp(calc_set, calculator)
    hp = calc_set.hp or max_hp
    moves: tuple[str, ...] = ()
    if 0 <= state_slot < len(battle_state.player_move_names_by_slot):
        moves = tuple(move for move in battle_state.player_move_names_by_slot[state_slot] if move)
    if state_slot == 0 and not moves:
        moves = tuple(move for move in battle_state.player_move_names if move)
    return PlannedMember(
        name=battle_state.player_names[state_slot] if state_slot < len(battle_state.player_names) else calc_set.species,
        species=calc_set.species,
        level=calc_set.level,
        max_hp=max_hp,
        hp=hp,
        moves=moves,
        item=calc_set.held_item,
        ability=calc_set.ability,
        nature=calc_set.nature,
        evs=calc_set.evs,
        ivs=calc_set.ivs,
        source="current",
        slot=slot,
    )


def _best_player_answer(
    team: list[PlannedMember],
    enemy: PlannedEnemy,
    calculator: DamageCalculator,
    active_index: int | None,
) -> int | None:
    scored: list[tuple[float, int]] = []
    for index, member in enumerate(team):
        if not member.alive:
            continue
        action = _best_player_action(member, enemy, team, calculator)
        if _action_is_unreliable(action):
            action_floor = -260.0
        else:
            action_floor = 0.0
        incoming = _ai_move_choices(enemy, member, team, calculator)
        incoming_damage = incoming[0].damage.max_percent if incoming and incoming[0].damage else 0.0
        score = action.score + action_floor - incoming_damage * 90.0
        if action.damage and action.damage.ko_chance >= 1.0:
            score += 80.0
        if incoming and incoming[0].damage and incoming[0].damage.ko_chance >= 1.0:
            score -= 140.0
        if index == active_index:
            score += 12.0 if not _action_is_unreliable(action) else -30.0
        if member.known_moves:
            score += 10.0
        else:
            score -= 80.0
        scored.append((score, index))
    return max(scored, default=(0.0, None))[1]


def _best_player_answer_for_field(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    enemy_index: int,
    calculator: DamageCalculator,
    active_index: int | None,
) -> int | None:
    enemy = enemies[enemy_index]
    scored: list[tuple[float, int]] = []
    for index, member in enumerate(team):
        if not member.alive:
            continue
        score = _member_answer_score(member, enemy, team, calculator)
        score -= _future_preservation_penalty(team, enemies, enemy_index, index, calculator)
        if index == active_index:
            score += 10.0
        scored.append((score, index))
    return max(scored, default=(0.0, None))[1]


def _member_answer_score(
    member: PlannedMember,
    enemy: PlannedEnemy,
    team: list[PlannedMember],
    calculator: DamageCalculator,
) -> float:
    action = _best_player_action(member, enemy, team, calculator)
    incoming = _ai_move_choices(enemy, member, team, calculator)
    incoming_damage = incoming[0].damage.max_percent if incoming and incoming[0].damage else 0.0
    score = action.score - incoming_damage * 90.0
    if _action_is_unreliable(action):
        score -= 260.0
    if action.damage and action.damage.ko_chance >= 1.0:
        score += 80.0
    if incoming and incoming[0].damage and incoming[0].damage.max_damage >= member.hp:
        score -= 180.0
    if member.known_moves:
        score += 10.0
    else:
        score -= 80.0
    return score


def _future_preservation_penalty(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    current_enemy_index: int,
    member_index: int,
    calculator: DamageCalculator,
) -> float:
    member = team[member_index]
    if not member.alive or not member.known_moves:
        return 0.0
    penalty = 0.0
    for index, enemy in enumerate(enemies):
        if index == current_enemy_index or not enemy.alive:
            continue
        scores = sorted(
            (
                (_member_answer_score(other, enemy, team, calculator), other_index)
                for other_index, other in enumerate(team)
                if other.alive
            ),
            reverse=True,
        )
        if not scores or scores[0][1] != member_index:
            continue
        best = scores[0][0]
        second = scores[1][0] if len(scores) > 1 else -300.0
        margin = best - second
        if margin <= 35.0:
            continue
        enemy_weight = 1.4 if _normalize(enemy.name) in {"manectric", "lucario"} else 1.0
        penalty += min(95.0, margin * 0.45) * enemy_weight
    return penalty


def _switch_target_if_needed_for_field(
    team: list[PlannedMember],
    enemies: list[PlannedEnemy],
    enemy_index: int,
    active_index: int,
    calculator: DamageCalculator,
) -> int | None:
    enemy = enemies[enemy_index]
    active = team[active_index]
    active_action = _best_player_action(active, enemy, team, calculator)
    incoming = _ai_move_choices(enemy, active, team, calculator)
    enemy_kills = bool(incoming and incoming[0].damage and incoming[0].damage.max_damage >= active.hp)
    # A kill threat the enemy never gets to use (we outspeed and KO it first) is not a reason to switch.
    if enemy_kills and _player_kos_first(active, enemy, active_action, incoming[0] if incoming else None, calculator):
        enemy_kills = False
    if not enemy_kills and _active_can_stay_and_progress(active, enemy, active_action, incoming[0] if incoming else None, calculator):
        return None
    best_index = _best_player_answer_for_field(team, enemies, enemy_index, calculator, active_index)
    if best_index is None or best_index == active_index:
        return None
    best_member = team[best_index]
    best_action = _best_player_action(best_member, enemy, team, calculator)
    best_incoming = _ai_move_choices(enemy, best_member, team, calculator)
    best_dies = bool(best_incoming and best_incoming[0].damage and best_incoming[0].damage.max_damage >= best_member.hp)
    if best_dies:
        return None
    if enemy_kills:
        return best_index
    if _action_is_unreliable(active_action) and not _action_is_unreliable(best_action):
        return best_index
    active_score = _member_answer_score(active, enemy, team, calculator) - _future_preservation_penalty(
        team, enemies, enemy_index, active_index, calculator
    )
    best_score = _member_answer_score(best_member, enemy, team, calculator) - _future_preservation_penalty(
        team, enemies, enemy_index, best_index, calculator
    )
    if active_score < best_score - 75.0:
        return best_index
    return None


def _switch_target_if_needed(
    team: list[PlannedMember],
    active_index: int,
    enemy: PlannedEnemy,
    calculator: DamageCalculator,
) -> int | None:
    active = team[active_index]
    active_action = _best_player_action(active, enemy, team, calculator)
    incoming = _ai_move_choices(enemy, active, team, calculator)
    enemy_kills = bool(incoming and incoming[0].damage and incoming[0].damage.ko_chance >= 1.0)
    # A kill threat the enemy never gets to use (we outspeed and KO it first) is not a reason to switch.
    if enemy_kills and _player_kos_first(active, enemy, active_action, incoming[0] if incoming else None, calculator):
        enemy_kills = False
    if not enemy_kills and _active_can_stay_and_progress(active, enemy, active_action, incoming[0] if incoming else None, calculator):
        return None
    best_index = _best_player_answer(team, enemy, calculator, active_index)
    if best_index is None or best_index == active_index:
        return None
    best_member = team[best_index]
    best_action = _best_player_action(best_member, enemy, team, calculator)
    best_incoming = _ai_move_choices(enemy, best_member, team, calculator)
    best_dies = bool(best_incoming and best_incoming[0].damage and best_incoming[0].damage.ko_chance >= 1.0)
    if best_dies:
        return None
    if enemy_kills:
        return best_index
    if _action_is_unreliable(active_action) and not _action_is_unreliable(best_action):
        return best_index
    if active_action.score < best_action.score - 35.0:
        return best_index
    if active_action.damage is None and best_action.damage is not None:
        return best_index
    return None


def _player_kos_first(
    active: PlannedMember,
    enemy: PlannedEnemy,
    action: PlayerAction,
    enemy_choice: MoveChoice | None,
    calculator: DamageCalculator,
) -> bool:
    """True if our attack is a guaranteed KO this turn AND we move first — so the enemy never
    gets to act. In that case its "it could kill me" threat is irrelevant and we should stay
    in and finish, not bail (which wastes a turn and takes a free hit)."""
    if not action.damage or action.damage.min_damage < enemy.hp:
        return False
    if action.damage.accuracy < 0.9:
        return False
    enemy_move = enemy_choice.move_name if enemy_choice else ""
    return not _enemy_moves_before_player(enemy, active, enemy_move, action.move_name, calculator)


def _active_can_stay_and_progress(
    active: PlannedMember,
    enemy: PlannedEnemy,
    action: PlayerAction,
    enemy_choice: MoveChoice | None,
    calculator: DamageCalculator,
) -> bool:
    if not action.damage or action.damage.max_damage <= 0:
        return False
    if action.damage.accuracy < 0.85:
        return False
    # If we outspeed and our attack KOs the enemy this turn, it never acts — stay and finish.
    if _player_kos_first(active, enemy, action, enemy_choice, calculator):
        return True
    if _choice_kills_current(enemy_choice, active):
        return False
    if action.damage.min_damage >= enemy.hp:
        return True
    progress_vs_current_hp = action.damage.min_damage / max(1, enemy.hp)
    progress_vs_max_hp = action.damage.min_damage / max(1, enemy.max_hp)
    return progress_vs_current_hp >= 0.55 or progress_vs_max_hp >= 0.45


def _best_player_action(
    member: PlannedMember,
    enemy: PlannedEnemy,
    team: list[PlannedMember],
    calculator: DamageCalculator,
) -> PlayerAction:
    actions: list[PlayerAction] = []
    attacker = member.calc_set()
    defender = enemy.calc_set()
    for damage in _ranked_known_damage(calculator, attacker, defender, member.known_moves):
        score = damage.min_percent * 100.0 + damage.ko_chance * 95.0
        if _normalize(damage.move_name) == "fakeout" and member.turns_out > 0:
            continue
        if _normalize(damage.move_name) == "fakeout" and member.turns_out == 0:
            followups = [
                item
                for item in _ranked_known_damage(calculator, attacker, defender, member.known_moves)
                if _normalize(item.move_name) != "fakeout"
            ]
            followup = followups[0] if followups else None
            if followup and damage.min_damage + followup.min_damage >= enemy.hp:
                score += 90.0
            else:
                score += 55.0 + _enemy_pressure(enemy, member, team, calculator) * 35.0
        if damage.max_percent >= 0.5:
            score += 18.0
        # A reliable attack that removes roughly a third of the target is already
        # making concrete progress.  Previously, a generic stat drop could outscore
        # a super-effective 2–3HKO (notably Mudkip's Water Gun into Roxanne's
        # Nosepass), causing the planner to stall or pivot into a much worse answer.
        if damage.min_percent >= 1 / 3:
            score += 25.0
        if damage.accuracy < 1:
            score -= (1.0 - damage.accuracy) * 25.0
        actions.append(PlayerAction("move", damage.move_name, score=score, damage=damage, reason="best damage"))

    enemy_pressure = _enemy_pressure(enemy, member, team, calculator)
    for move_name in member.known_moves:
        move = calculator.moves.get(_normalize(move_name))
        if not move or move.get("category") != "Status":
            continue
        move_id = _normalize(move_name)
        score = 0.0
        reason = ""
        if move_id in SLEEP_MOVES and enemy.status is None:
            score = 70.0 + enemy_pressure * 30.0
            reason = "sleep buys a setup/attack turn"
        elif move_id in BURN_MOVES and enemy.status is None and _enemy_best_category(enemy, member, calculator) == "Physical":
            score = 48.0 + enemy_pressure * 45.0
            reason = "burn cuts physical pressure"
        elif move_id in PARALYSIS_MOVES and enemy.status is None:
            score = 42.0 + (20.0 if _speed(enemy.calc_set(), calculator) > _speed(member.calc_set(), calculator) else 0.0)
            reason = "paralysis slows enemy"
        elif move_id in TOXIC_MOVES and enemy.status is None:
            score = 36.0 + enemy_pressure * 30.0
            if _best_direct_damage_percent(calculator, attacker, defender, member.known_moves) < 0.25:
                score += 24.0
            reason = "toxic gives a bulky target a real timer"
        elif move_id in POISON_MOVES and enemy.status is None:
            score = 24.0 + enemy_pressure * 22.0
            if _best_direct_damage_percent(calculator, attacker, defender, member.known_moves) < 0.25:
                score += 12.0
            reason = "poison chip creates progress"
        elif move_id in LEECH_SEED_MOVES and not enemy.leech_seeded:
            score = 34.0 + enemy_pressure * 25.0
            if _best_direct_damage_percent(calculator, attacker, defender, member.known_moves) < 0.25:
                score += 22.0
            reason = "Leech Seed creates progress and recovery"
        elif move_id in RNB_PROTECT_MOVES and _residual_pressure(enemy) > 0:
            score = 30.0 + _residual_pressure(enemy) * 85.0
            reason = "Protect converts residual damage safely"
        elif move_id in DIRECT_RECOVERY_MOVES | {"rest"} and member.hp < member.max_hp:
            missing = (member.max_hp - member.hp) / max(1, member.max_hp)
            heal = _recovery_amount(member, move_id, calculator)
            enemy_choices = _ai_move_choices(enemy, member, team, calculator)
            incoming = enemy_choices[0] if enemy_choices else None
            incoming_max = incoming.damage.max_damage if incoming and incoming.damage else 0
            enemy_first = _enemy_moves_before_player(
                enemy, member, incoming.move_name if incoming else "", move_name, calculator,
            )
            # Recovery cannot rescue a mon that is knocked out before it can move.
            if not (enemy_first and incoming_max >= member.hp):
                score = 28.0 + missing * 75.0 + enemy_pressure * 18.0
                if member.hp <= incoming_max < member.hp + heal:
                    score += 80.0
                if move_id == "rest" and member.status is None:
                    score -= 18.0
                reason = "recovery preserves the line through the next hit"
        elif move_id in CONFUSION_MOVES and enemy.confused_turns <= 0:
            score = 22.0 + enemy_pressure * 15.0
            reason = "confusion adds a disruption chance"
        elif (
            move_id in ATTACK_DROP_MOVES
            and enemy.boosts.get("atk", 0) > -6
            and _enemy_best_category(enemy, member, calculator) == "Physical"
        ):
            score = 38.0 + enemy_pressure * 35.0 + ATTACK_DROP_MOVES[move_id] * 8.0
            score -= abs(min(0, enemy.boosts.get("atk", 0))) * 24.0
            reason = "attack drop creates a safer pivot"
        elif (
            move_id in SPECIAL_ATTACK_DROP_MOVES
            and enemy.boosts.get("spa", 0) > -6
            and _enemy_best_category(enemy, member, calculator) == "Special"
        ):
            score = 38.0 + enemy_pressure * 35.0 + SPECIAL_ATTACK_DROP_MOVES[move_id] * 8.0
            score -= abs(min(0, enemy.boosts.get("spa", 0))) * 24.0
            reason = "special attack drop creates a safer pivot"
        elif move_id in SPEED_DROP_MOVES and enemy.boosts.get("spe", 0) > -6:
            score = 34.0 + SPEED_DROP_MOVES[move_id] * 8.0
            score -= abs(min(0, enemy.boosts.get("spe", 0))) * 18.0
            reason = "speed drop can flip turn order"
        elif move_id in SETUP_MOVE_BOOSTS and any(
            member.boosts.get(stat, 0) < 6 for stat in SETUP_MOVE_BOOSTS[move_id]
        ):
            enemy_choices = _ai_move_choices(enemy, member, team, calculator)
            incoming = enemy_choices[0].damage.max_percent if enemy_choices and enemy_choices[0].damage else 0.0
            if incoming < 0.45:
                score = 36.0 + (0.45 - incoming) * 55.0
                reason = "setup while enemy cannot immediately punish"
        if score > 0:
            accuracy = _move_accuracy(move_name, calculator, member)
            score -= (1.0 - accuracy) * 20.0
            actions.append(PlayerAction("move", str(move.get("name", move_name)), score=score, reason=reason))

    if not actions:
        return PlayerAction("move", member.moves[0] if member.moves else "", score=-50.0, reason="no reliable decoded move")
    return max(actions, key=lambda action: action.score)


def _ai_move_choices(
    enemy: PlannedEnemy,
    player: PlannedMember,
    team: list[PlannedMember],
    calculator: DamageCalculator,
    *,
    force_crit: bool = False,
    partner: PlannedEnemy | None = None,
) -> list[MoveChoice]:
    attacker = enemy.calc_set()
    defender = player.calc_set()
    context = DamageContext(critical=True) if force_crit else None
    damages = {damage.move_name: damage for damage in calculator.rank_move_names(attacker, defender, enemy.moves, context)}
    max_damage = max((damage.max_damage for damage in damages.values()), default=0)
    player_can_ko = _has_ko(calculator, player.calc_set(), enemy.calc_set(), player.known_moves, use_min=False, current_hp=enemy.hp)
    enemy_faster = _speed(attacker, calculator) >= _speed(defender, calculator)
    raw: list[MoveChoice] = []
    for move_name in enemy.moves:
        move = calculator.moves.get(_normalize(move_name), {})
        damage = damages.get(str(move.get("name", move_name))) or damages.get(move_name)
        score = -20.0
        reason = "unusable"
        if _normalize(move_name) == "fakeout" and enemy.turns_out > 0:
            raw.append(MoveChoice(str(move.get("name", move_name)), -20.0, 0.0, None, "Fake Out only works first turn"))
            continue
        if damage and damage.max_damage > 0:
            if damage.max_damage < max(1, int(player.max_hp * 0.05)):
                score = -6.0
                reason = "ineffective damage"
            else:
                score = 6.4 if damage.max_damage == max_damage else max(0.0, damage.max_percent * 6.0)
                reason = "highest damage" if damage.max_damage == max_damage else "chip damage"
                if damage.ko_chance > 0:
                    score += 6.0 if _move_priority(move_name, calculator) > 0 or enemy_faster else 3.0
                    reason = "fast kill" if _move_priority(move_name, calculator) > 0 or enemy_faster else "slow kill"
                if _move_priority(move_name, calculator) > 0 and player_can_ko and not enemy_faster:
                    score += 11.0
                    reason = "priority before death"
                if _has_secondary(move, "flinch") and enemy_faster:
                    score += 2.0
        elif move:
            score, reason = _ai_status_score(move_name, enemy, player, team, calculator, partner=partner)
        raw.append(MoveChoice(str(move.get("name", move_name)), score, 0.0, damage, reason))
    if calculator.game_mode == "pokemon-emerald":
        # Vanilla Emerald trainers use Gen III battle AI, not Run & Bun's custom
        # +6/+8 damage-roll scoring table.  Without per-trainer AI flag bytes in the
        # spreadsheet, branch over every tied best-scoring action and report that
        # uncertainty instead of inventing Run & Bun probabilities.
        usable = [choice for choice in raw if choice.score > -10]
        if not usable:
            usable = raw
        top = max((choice.score for choice in usable), default=0.0)
        winners = [choice for choice in usable if abs(choice.score - top) < 1e-9]
        chance = 1.0 / max(1, len(winners))
        return [MoveChoice(choice.move_name, choice.score, chance, choice.damage, f"Emerald AI: {choice.reason}") for choice in winners]
    # RnB does not softmax every legal move. It rolls damage for attacks, awards the
    # highest rolled attack +6 (80%) or +8 (20%), then chooses the highest final score;
    # exact ties are random. Approximate the damage-roll winner analytically, then
    # enumerate those documented score outcomes against the status/setup scores.
    damage_indices = [i for i, choice in enumerate(raw) if choice.damage and choice.damage.max_damage > 0 and choice.score > -10]
    probabilities = [0.0] * len(raw)

    def award(scores: dict[int, float], scenario_probability: float) -> None:
        if not scores or scenario_probability <= 0:
            return
        top = max(scores.values())
        winners = [index for index, score in scores.items() if abs(score - top) < 1e-9]
        for index in winners:
            probabilities[index] += scenario_probability / len(winners)

    status_scores = {i: choice.score for i, choice in enumerate(raw) if i not in damage_indices and choice.score > -10}
    if damage_indices:
        winner_probabilities = _damage_roll_winner_probabilities([raw[i].damage for i in damage_indices])
        for local_index, win_probability in enumerate(winner_probabilities):
            index = damage_indices[local_index]
            choice = raw[index]
            move = calculator.moves.get(_normalize(choice.move_name), {})
            bonus = 0.0
            if choice.damage and choice.damage.ko_chance > 0:
                bonus += 6.0 if _move_priority(choice.move_name, calculator) > 0 or enemy_faster else 3.0
            if _move_priority(choice.move_name, calculator) > 0 and player_can_ko and not enemy_faster:
                bonus += 11.0
            if _has_secondary(move, "flinch") and enemy_faster:
                bonus += 2.0
            award({**status_scores, index: 6.0 + bonus}, win_probability * 0.8)
            award({**status_scores, index: 8.0 + bonus}, win_probability * 0.2)
    else:
        award(status_scores, 1.0)

    total = sum(probabilities)
    if total <= 0:
        # All moves can genuinely be unusable (for example Fake Out after turn one).
        probabilities = [1.0 / len(raw) if raw else 0.0 for _ in raw]
        total = 1.0
    choices = [
        MoveChoice(choice.move_name, choice.score, probabilities[index] / total, choice.damage, choice.reason)
        for index, choice in enumerate(raw)
    ]
    return sorted(choices, key=lambda choice: (choice.probability, choice.score), reverse=True)


def _damage_roll_winner_probabilities(damages: list[DamageRange | None]) -> list[float]:
    """Probability each attack supplies the highest AI damage roll.

    Pairwise CDF products are exact when there are no ties. Normalizing the tied mass
    gives symmetric, stable tie handling without enumerating up to 16^4 roll tuples.
    """
    valid_rolls = [tuple(damage.rolls) if damage is not None else (0,) for damage in damages]
    return list(_damage_roll_winner_probabilities_cached(tuple(valid_rolls)))


@lru_cache(maxsize=100_000)
def _damage_roll_winner_probabilities_cached(
    valid_rolls: tuple[tuple[int, ...], ...],
) -> tuple[float, ...]:
    weights: list[float] = []
    for index, rolls in enumerate(valid_rolls):
        chance = 0.0
        for roll in rolls:
            beats_all = 1.0
            for other_index, other_rolls in enumerate(valid_rolls):
                if other_index == index:
                    continue
                beats_all *= sum(other <= roll for other in other_rolls) / max(1, len(other_rolls))
            chance += beats_all / max(1, len(rolls))
        weights.append(chance)
    total = sum(weights)
    if not weights:
        return ()
    return tuple(weight / total for weight in weights) if total > 0 else tuple(1.0 / len(weights) for _ in weights)


def _ai_side_has_move(enemy: PlannedEnemy, partner: PlannedEnemy | None, move_id: str) -> bool:
    """True if the AI mon or (in doubles) its live partner carries the given move.

    Backs the RnB AI doc's "AI mon or its partner has Hex" style clauses; in singles
    `partner` is None and only the active enemy's moves are checked."""
    if any(_normalize(m) == move_id for m in enemy.moves):
        return True
    if partner is not None and partner.alive and any(_normalize(m) == move_id for m in partner.moves):
        return True
    return False


def _ai_status_score(
    move_name: str,
    enemy: PlannedEnemy,
    player: PlannedMember,
    team: list[PlannedMember],
    calculator: DamageCalculator,
    *,
    partner: PlannedEnemy | None = None,
) -> tuple[float, str]:
    move_id = _normalize(move_name)
    player_can_ko = _has_ko(calculator, player.calc_set(), enemy.calc_set(), player.known_moves, use_min=False, current_hp=enemy.hp)
    if move_id == "fakeout" and enemy.turns_out == 0:
        return 9.0, "Fake Out first turn"
    if move_id in {"stealthrock", "spikes", "toxicspikes"}:
        return (8.5 if enemy.turns_out == 0 else 6.5), "hazard AI"
    if move_id in {"protect", "detect", "kingsshield"}:
        return 5.0 if enemy.turns_out == 0 else 6.0, "protect AI"
    if move_id in DIRECT_RECOVERY_MOVES | {"rest"}:
        return _ai_recovery_score(move_id, enemy, player, calculator)
    if move_id in {"reflect", "lightscreen", "auroraveil"}:
        return 7.0, "screen AI"
    if move_id in PARALYSIS_MOVES and player.status is None:
        return (8.0 if _speed(player.calc_set(), calculator) > _speed(enemy.calc_set(), calculator) else 7.0), "paralysis AI"
    if move_id in BURN_MOVES and player.status is None:
        # Will-o-Wisp (RnB AI doc, data/rnb_ai_document.txt): base +6. ONLY ~37% of the
        # time are the bonuses checked — +1 if the target has a physical attacking move,
        # +1 if the AI (or its partner) has Hex. The rest of the time it stays +6. Using
        # the same expected-value convention as the damaging-move scores (80/20 -> 6.4),
        # the bonuses contribute 0.37 each, so WoW lands just under the top attack instead
        # of deterministically beating it.
        bonus = 0
        if _player_has_physical_move(player, calculator):
            bonus += 1
        if _ai_side_has_move(enemy, partner, "hex"):
            bonus += 1
        return 6.0 + 0.37 * bonus, "burn AI"
    if move_id in TOXIC_MOVES and player.status is None:
        return 7.4 if player.hp / max(1, player.max_hp) > 0.35 else 5.8, "toxic AI"
    if move_id in POISON_MOVES and player.status is None:
        return 6.2, "poison AI"
    if move_id in SLEEP_MOVES and player.status is None and not player_can_ko:
        return 7.0, "sleep AI"
    if move_id in LEECH_SEED_MOVES and not player.leech_seeded:
        return 7.2 if not player_can_ko else 5.2, "Leech Seed AI"
    if move_id in CONFUSION_MOVES and player.confused_turns <= 0:
        return 6.0, "confusion AI"
    if move_id in SETUP_MOVE_BOOSTS:
        if player_can_ko:
            return -20.0, "setup blocked by player KO"
        return _ai_setup_score(move_id, enemy, player, calculator)
    if move_id in ATTACK_DROP_MOVES | SPECIAL_ATTACK_DROP_MOVES | SPEED_DROP_MOVES:
        return 6.0, "stat control AI"
    return 0.0, "low-value status"


def _ai_recovery_score(
    move_id: str,
    enemy: PlannedEnemy,
    player: PlannedMember,
    calculator: DamageCalculator,
) -> tuple[float, str]:
    """Run & Bun AI document's `Should AI Recover` decision, as an expected score."""
    hp_fraction = enemy.hp / max(1, enemy.max_hp)
    if hp_fraction >= 1.0:
        return -20.0, "recovery blocked at full HP"
    if hp_fraction >= 0.85:
        return -6.0, "recovery discouraged above 85% HP"
    if enemy.status == "toxic":
        return 5.0, "recovery AI declines while badly poisoned"

    weather = _normalize(getattr(calculator.default_field, "weather", None) or "")
    recovery_fraction = 1.0 if move_id == "rest" else 0.5
    if move_id in {"synthesis", "moonlight", "morningsun"} and weather in {"sun", "harshsun", "sunnyday"}:
        recovery_fraction = 2 / 3
    heal = max(1, int(enemy.max_hp * recovery_fraction))
    player_damage = max(
        (damage.max_damage for damage in _ranked_known_damage(
            calculator, player.calc_set(), enemy.calc_set(), player.known_moves,
        )),
        default=0,
    )
    if player_damage >= heal:
        return 5.0, "recovery AI declines because damage matches healing"

    enemy_faster = _speed(enemy.calc_set(), calculator) >= _speed(player.calc_set(), calculator)
    can_ko_now = player_damage >= enemy.hp
    can_ko_after_heal = player_damage >= min(enemy.max_hp, enemy.hp + heal)
    recover_probability = 0.0
    if enemy_faster:
        if can_ko_now and not can_ko_after_heal:
            recover_probability = 1.0
        elif not can_ko_now:
            if hp_fraction < 0.4:
                recover_probability = 1.0
            elif hp_fraction < 0.66:
                recover_probability = 0.5
    else:
        if hp_fraction < 0.5:
            recover_probability = 1.0
        elif hp_fraction < 0.7:
            recover_probability = 0.75

    score_when_recovering = 7.0
    if move_id == "rest" and recover_probability > 0:
        item = _normalize(enemy.calc_set().held_item)
        ability = _normalize(enemy.calc_set().ability)
        has_sleep_plan = (
            item in {"lumberry", "chestoberry"}
            or any(_normalize(move) in {"sleeptalk", "snore"} for move in enemy.moves)
            or ability in {"shedskin", "earlybird"}
            or (ability == "hydration" and weather in {"rain", "heavyrain"})
        )
        if has_sleep_plan:
            score_when_recovering = 8.0
    expected = 5.0 + recover_probability * (score_when_recovering - 5.0)
    return expected, f"recovery AI ({round(recover_probability * 100)}% should-recover check)"


def _ai_setup_score(
    move_id: str,
    enemy: PlannedEnemy,
    player: PlannedMember,
    calculator: DamageCalculator,
) -> tuple[float, str]:
    current_boosts = dict(enemy.boosts)
    next_boosts = _setup_boost_result(enemy, SETUP_MOVE_BOOSTS.get(move_id, {}))
    useful_stats = [
        stat
        for stat in next_boosts
        if next_boosts.get(stat, 0) > current_boosts.get(stat, 0)
        and _setup_stat_has_value(stat, enemy, player, calculator)
    ]
    if not useful_stats:
        return -12.0, "setup has no clear payoff"
    if all(current_boosts.get(stat, 0) >= 6 for stat in useful_stats):
        return -20.0, "setup already capped"

    current_enemy = enemy.calc_set()
    boosted_enemy = replace(current_enemy, boosts=next_boosts)
    current_ranked = _ranked_known_damage(calculator, current_enemy, player.calc_set(), enemy.moves)
    boosted_ranked = _ranked_known_damage(calculator, boosted_enemy, player.calc_set(), enemy.moves)
    best_current = current_ranked[0] if current_ranked else None
    best_boosted = boosted_ranked[0] if boosted_ranked else None
    current_damage = best_current.max_percent if best_current else 0.0
    boosted_damage = best_boosted.max_percent if best_boosted else 0.0
    improvement = max(0.0, boosted_damage - current_damage)
    stage_gain = sum(max(0, next_boosts.get(stat, 0) - current_boosts.get(stat, 0)) for stat in useful_stats)
    existing_stage = max((max(0, current_boosts.get(stat, 0)) for stat in useful_stats), default=0)
    incoming = _best_direct_damage_percent(calculator, player.calc_set(), current_enemy, player.known_moves)

    score = 4.0 + stage_gain * 0.8 + improvement * 22.0
    reason = "setup AI"
    if best_current and best_current.max_damage >= player.hp:
        score -= 7.0
        reason = "attack already KOs"
    elif best_boosted and best_boosted.max_damage >= player.hp:
        score += 2.0
        reason = "setup can unlock KO"
    if current_damage >= 0.50:
        score -= 2.0
    if incoming >= 0.35:
        score -= (incoming - 0.35) * 18.0
    if incoming >= 0.65:
        score -= 3.5
        reason = "setup too punishable"
    if improvement < 0.04 and any(stat in {"atk", "spa"} for stat in useful_stats):
        score -= 2.5
    score -= existing_stage * 2.4
    if existing_stage >= 1:
        score -= 1.5
        reason = "setup already boosted"
    if existing_stage >= 2:
        score -= 4.0
    return max(-20.0, score), reason


def _setup_boost_result(enemy: PlannedEnemy, boosts: dict[str, int]) -> dict[str, int]:
    result = dict(enemy.boosts)
    ability = _normalize(enemy.calc_set().ability)
    for stat, raw_amount in boosts.items():
        amount = raw_amount
        if ability == "contrary":
            amount = -amount
        if ability == "simple":
            amount *= 2
        result[stat] = max(-6, min(6, result.get(stat, 0) + amount))
    return result


def _setup_stat_has_value(
    stat: str,
    enemy: PlannedEnemy,
    player: PlannedMember,
    calculator: DamageCalculator,
) -> bool:
    if stat == "atk":
        return _enemy_has_damaging_category(enemy, player, calculator, "Physical")
    if stat == "spa":
        return _enemy_has_damaging_category(enemy, player, calculator, "Special")
    if stat == "spe":
        return bool(_ranked_known_damage(calculator, enemy.calc_set(), player.calc_set(), enemy.moves)) and _speed(
            enemy.calc_set(), calculator
        ) < _speed(player.calc_set(), calculator)
    if stat == "def":
        return _player_has_damaging_category(player, calculator, "Physical")
    if stat == "spd":
        return _player_has_damaging_category(player, calculator, "Special")
    return False


def _enemy_has_damaging_category(
    enemy: PlannedEnemy,
    player: PlannedMember,
    calculator: DamageCalculator,
    category: str,
) -> bool:
    ranked = _ranked_known_damage(calculator, enemy.calc_set(), player.calc_set(), enemy.moves)
    names = {_normalize(damage.move_name) for damage in ranked if damage.max_damage > 0}
    return any(
        _normalize(move_name) in names and calculator.moves.get(_normalize(move_name), {}).get("category") == category
        for move_name in enemy.moves
    )


def _ai_hard_switch_target(
    enemies: list[PlannedEnemy],
    enemy_index: int,
    incoming_player: PlannedMember,
    calculator: DamageCalculator,
) -> int | None:
    if calculator.game_mode == "pokemon-emerald":
        return None
    active = enemies[enemy_index]
    if active.trapped:
        return None
    if active.max_hp <= 0 or active.hp / active.max_hp < 0.5:
        return None
    choices = _ai_move_choices(active, incoming_player, [incoming_player], calculator)
    if choices and any(choice.score > -5.0 for choice in choices):
        return None
    target = _choose_next_enemy(enemies, [incoming_player], 0, calculator, exclude=enemy_index)
    if target == enemy_index:
        return None
    return target


def _best_damage_move(
    attacker: PokemonCalcSet,
    defender: PokemonCalcSet,
    moves: tuple[str, ...],
    calculator: DamageCalculator,
) -> DamageRange | None:
    """Highest-max-damage move `attacker` has into `defender` (None if it has none)."""
    best: DamageRange | None = None
    for move_name in moves:
        damage = calculator.estimate_move(attacker, defender, move_name)
        if damage is None or damage.max_damage <= 0:
            continue
        if best is None or damage.max_damage > best.max_damage:
            best = damage
    return best


def _switch_in_score(
    enemy: PlannedEnemy,
    player: PlannedMember | None,
    calculator: DamageCalculator,
) -> int:
    """Post-KO switch-in score for one benched enemy vs the player's active mon.

    Implements the RnB "Post-KO Switch-in AI - Switch Scores" table verbatim
    (saved at data/rnb_post_ko_switchin_ai.pdf). Damage races compare % of max HP;
    OHKO checks use the live HP of the mon being hit. "Faster"/"slower" are strict
    (a speed tie is neither, so it falls through to the default 0).
    """
    if player is None or not player.alive:
        return 0

    ai_spe = _speed(enemy.calc_set(), calculator)
    pl_spe = _speed(player.calc_set(), calculator)
    faster = ai_spe > pl_spe
    slower = ai_spe < pl_spe

    ai_dmg = _best_damage_move(enemy.calc_set(), player.calc_set(), enemy.moves, calculator)
    pl_dmg = _best_damage_move(player.calc_set(), enemy.calc_set(), player.moves, calculator)
    ai_ohkos = bool(ai_dmg and ai_dmg.max_damage >= player.hp)        # AI kills the player's mon
    ai_is_ohkod = bool(pl_dmg and pl_dmg.max_damage >= enemy.hp)      # player kills the switch-in
    deals_more = (ai_dmg.max_percent if ai_dmg else 0.0) > (pl_dmg.max_percent if pl_dmg else 0.0)

    # Special cases override the matchup score.
    species = _normalize(enemy.pokemon.species)
    if species == "ditto":
        return 2
    if species in ("wynaut", "wobbuffet") and not (slower and ai_is_ohkod):
        return 2

    # General table, highest applicable condition wins (read top-down).
    if faster and ai_ohkos:
        return 5
    if slower and ai_ohkos and not ai_is_ohkod:
        return 4
    if faster and deals_more:
        return 3
    if slower and deals_more:
        return 2
    if faster:
        return 1
    if slower and ai_is_ohkod:
        return -1
    return 0


def _choose_next_enemy(
    enemies: list[PlannedEnemy],
    team: list[PlannedMember],
    active_index: int | None,
    calculator: DamageCalculator,
    exclude: int | None = None,
) -> int:
    """Pick the enemy's post-KO switch-in per the RnB switch-score table: highest
    switch-in score against the player's active mon, ties broken by party order."""
    if calculator.game_mode == "pokemon-emerald":
        return next((index for index, enemy in enumerate(enemies) if index != exclude and enemy.alive), 0)
    player = team[active_index] if active_index is not None and 0 <= active_index < len(team) else None
    best_index: int | None = None
    best_score: int | None = None
    for index, enemy in enumerate(enemies):
        if index == exclude or not enemy.alive:
            continue
        score = _switch_in_score(enemy, player, calculator)
        # Strictly-greater keeps the earliest party slot on ties (party-order tiebreak).
        if best_score is None or score > best_score:
            best_score = score
            best_index = index
    return best_index if best_index is not None else 0


def _mark_enemy_allies_fainted(enemies: list[PlannedEnemy], fainted_index: int) -> None:
    for index, enemy in enumerate(enemies):
        if index != fainted_index and enemy.alive:
            enemy.pokemon = replace(enemy.pokemon, allies_fainted=enemy.pokemon.allies_fainted + 1)


def _apply_player_action(
    active: PlannedMember,
    enemy: PlannedEnemy,
    action: PlayerAction,
    calculator: DamageCalculator,
) -> int:
    if _is_sound_blocked(active, action.move_name, calculator):
        return 0
    move = calculator.moves.get(_normalize(action.move_name), {})
    if enemy.protected and not _protect_bypassed(active, move):
        _apply_self_ko_move(active, action.move_name)
        return 0
    if action.damage is not None and _disguise_blocks(enemy):
        enemy.ability_on = False
        return 0
    if action.damage and action.damage.max_damage > 0:
        damage = min(enemy.hp, max(0, action.damage.min_damage))
        if enemy.protected:
            damage = max(1, damage // 4)
        enemy.hp -= damage
        _apply_contact_aftermath(active, enemy, action.move_name, damage, calculator)
        # Held HP berries activate immediately after the hit/recoil that crosses
        # their threshold.  Deferring this to the generic end-of-turn pass loses
        # the real mid-turn HP state (most visibly after Wild Charge).
        _try_consume_hp_berry(enemy, source=active)
        _try_consume_hp_berry(active, source=enemy)
        _apply_self_ko_move(active, action.move_name)
        return damage
    if action.damage is not None:
        if _is_status_damage_result(action.damage, move):
            _apply_status_action(active, enemy, action.move_name, calculator, target_is_enemy=True)
        else:
            _apply_ability_on_immunity(enemy, active, move, calculator)
        _apply_self_ko_move(active, action.move_name)
        return 0
    _apply_status_action(active, enemy, action.move_name, calculator, target_is_enemy=True)
    return 0


def _apply_enemy_action(
    enemy: PlannedEnemy,
    player: PlannedMember,
    choice: MoveChoice | None,
    calculator: DamageCalculator,
) -> int:
    if choice is None:
        return 0
    if _is_sound_blocked(enemy, choice.move_name, calculator):
        return 0
    move = calculator.moves.get(_normalize(choice.move_name), {})
    if player.protected and not _protect_bypassed(enemy, move):
        _apply_self_ko_move(enemy, choice.move_name)
        return 0
    if choice.damage is not None and _disguise_blocks(player):
        player.ability_on = False
        return 0
    if choice.damage and choice.damage.max_damage > 0:
        damage = min(player.hp, max(0, choice.damage.max_damage))
        if player.protected:
            damage = max(1, damage // 4)
        player.hp -= damage
        _apply_contact_aftermath_enemy(enemy, player, choice.move_name, damage, calculator)
        _try_consume_hp_berry(player, source=enemy)
        _try_consume_hp_berry(enemy, source=player)
        _apply_self_ko_move(enemy, choice.move_name)
        return damage
    if choice.damage is not None:
        if _is_status_damage_result(choice.damage, move):
            _apply_status_action(enemy, player, choice.move_name, calculator, target_is_enemy=False)
        else:
            _apply_ability_on_immunity(player, enemy, move, calculator)
        _apply_self_ko_move(enemy, choice.move_name)
        return 0
    _apply_status_action(enemy, player, choice.move_name, calculator, target_is_enemy=False)
    return 0


def _retarget_player_action(
    active: PlannedMember,
    enemy: PlannedEnemy,
    action: PlayerAction,
    calculator: DamageCalculator,
) -> PlayerAction:
    if action.kind != "move" or not action.move_name:
        return action
    move = calculator.moves.get(_normalize(action.move_name), {})
    if move.get("category") == "Status":
        return PlayerAction(action.kind, action.move_name, score=action.score, reason=action.reason)
    damage = calculator.estimate_move(active.calc_set(), enemy.calc_set(), action.move_name)
    if damage is None:
        return PlayerAction(action.kind, action.move_name, score=-25.0, reason="retargeted into switch-in")
    return PlayerAction(
        action.kind,
        damage.move_name,
        score=damage.min_percent * 100.0 + damage.ko_chance * 95.0,
        damage=damage,
        reason="retargeted into switch-in",
    )


def _refresh_player_action(
    active: PlannedMember,
    enemy: PlannedEnemy,
    action: PlayerAction,
    calculator: DamageCalculator,
) -> PlayerAction:
    if action.kind != "move" or not action.move_name or action.damage is None:
        return action
    move = calculator.moves.get(_normalize(action.move_name), {})
    if move.get("category") == "Status":
        return action
    damage = calculator.estimate_move(active.calc_set(), enemy.calc_set(), action.move_name)
    if damage is None:
        return action
    return PlayerAction(action.kind, damage.move_name, action.target_slot, action.score, damage, action.reason)


def _retarget_enemy_choice(
    enemy: PlannedEnemy,
    incoming: PlannedMember,
    choice: MoveChoice | None,
    calculator: DamageCalculator,
) -> MoveChoice | None:
    if choice is None or not choice.move_name:
        return choice
    damage = calculator.estimate_move(enemy.calc_set(), incoming.calc_set(), choice.move_name)
    return MoveChoice(choice.move_name, choice.score, choice.probability, damage, choice.reason)


def _is_status_damage_result(damage: DamageRange, move: dict[str, Any]) -> bool:
    return move.get("category") == "Status" or damage.reason in {"status_or_zero_power", "status_blocked_by_ability"}


def _apply_status_action(
    user: PlannedMember | PlannedEnemy,
    target: PlannedMember | PlannedEnemy,
    move_name: str,
    calculator: DamageCalculator,
    *,
    target_is_enemy: bool,
) -> None:
    move_id = _normalize(move_name)
    if _is_sound_blocked(user, move_name, calculator):
        return
    if _status_move_blocked(user, target, move_name, calculator):
        return
    if move_id in DIRECT_RECOVERY_MOVES:
        heal = _strength_sap_amount(target, calculator) if move_id == "strengthsap" else _recovery_amount(user, move_id, calculator)
        user.hp = min(user.max_hp, user.hp + heal)
        if move_id == "strengthsap":
            _boost(target, "atk", -1, source=user)
    elif move_id == "rest" and user.hp < user.max_hp:
        user.hp = user.max_hp
        _set_status(user, "sleep", source=user)
        if user.status == "sleep":
            user.sleep_turns = 2
    elif move_id == "gastroacid":
        target.ability_on = False
    elif move_id == "worryseed":
        _set_member_ability(target, "Insomnia")
    elif move_id == "simplebeam":
        _set_member_ability(target, "Simple")
    elif move_id == "skillswap":
        user_ability = user.calc_set().ability
        target_ability = target.calc_set().ability
        _set_member_ability(user, target_ability)
        _set_member_ability(target, user_ability)
    elif move_id == "roleplay":
        _set_member_ability(user, target.calc_set().ability)
    elif move_id == "entrainment":
        _set_member_ability(target, user.calc_set().ability)
    elif move_id in SLEEP_MOVES and target.status is None and _can_receive_status(target, "sleep", calculator, user):
        _set_status(target, "sleep", source=user)
        if target.status == "sleep":
            target.sleep_turns = 1
    elif move_id in BURN_MOVES and target.status is None and _can_receive_status(target, "burn", calculator, user):
        _set_status(target, "burn", source=user)
    elif move_id in PARALYSIS_MOVES and target.status is None and _can_receive_status(target, "paralysis", calculator, user):
        _set_status(target, "paralysis", source=user)
    elif move_id in TOXIC_MOVES and target.status is None and _can_receive_status(target, "toxic", calculator, user):
        _set_status(target, "toxic", source=user)
        target.toxic_counter = 0
    elif move_id in POISON_MOVES and target.status is None and _can_receive_status(target, "poison", calculator, user):
        _set_status(target, "poison", source=user)
    elif move_id in LEECH_SEED_MOVES and not target.leech_seeded and _can_be_seeded(target, calculator):
        target.leech_seeded = True
    elif move_id in RNB_PROTECT_MOVES:
        user.protected = True
    elif move_id in CONFUSION_MOVES and target.confused_turns <= 0:
        target.confused_turns = 2
        _try_consume_confusion_berry(target, source=user)
    elif move_id in ATTACK_DROP_MOVES:
        _boost(target, "atk", -ATTACK_DROP_MOVES[move_id], source=user)
    elif move_id in SPECIAL_ATTACK_DROP_MOVES:
        _boost(target, "spa", -SPECIAL_ATTACK_DROP_MOVES[move_id], source=user)
    elif move_id in SPEED_DROP_MOVES:
        _boost(target, "spe", -SPEED_DROP_MOVES[move_id], source=user)
    elif move_id in SETUP_MOVE_BOOSTS:
        for stat, amount in SETUP_MOVE_BOOSTS[move_id].items():
            _boost(user, stat, amount)
    elif move_id == "bellydrum" and user.hp > user.max_hp // 2:
        user.hp = max(1, user.hp - max(1, user.max_hp // 2))
        user.boosts["atk"] = 6


def _apply_self_ko_move(member: PlannedMember | PlannedEnemy, move_name: str) -> None:
    if _normalize(move_name) in SELF_KO_MOVES:
        member.hp = 0


def _apply_contact_aftermath(
    active: PlannedMember,
    enemy: PlannedEnemy,
    move_name: str,
    damage: int,
    calculator: DamageCalculator,
) -> None:
    move = calculator.moves.get(_normalize(move_name), {})
    _apply_recoil_or_drain(active, enemy, damage, move)
    _apply_guaranteed_secondary(active, enemy, move, calculator)
    _apply_defender_contact_ability(enemy, active, move, damage, calculator)
    _apply_attacker_ko_ability(active, enemy, calculator)


def _apply_contact_aftermath_enemy(
    enemy: PlannedEnemy,
    player: PlannedMember,
    move_name: str,
    damage: int,
    calculator: DamageCalculator,
) -> None:
    move = calculator.moves.get(_normalize(move_name), {})
    _apply_recoil_or_drain(enemy, player, damage, move)
    _apply_guaranteed_secondary(enemy, player, move, calculator)
    _apply_defender_contact_ability(player, enemy, move, damage, calculator)
    _apply_attacker_ko_ability(enemy, player, calculator)


def _apply_recoil_or_drain(
    member: PlannedMember | PlannedEnemy,
    target: PlannedMember | PlannedEnemy,
    damage: int,
    move: dict[str, Any],
) -> None:
    if move.get("recoil") and _normalize(member.calc_set().ability) != "rockhead":
        num, den = move["recoil"]
        member.hp = max(0, member.hp - max(1, int(damage * abs(num) / max(1, den))))
    if move.get("drain"):
        num, den = move["drain"]
        if _normalize(target.calc_set().ability) == "liquidooze":
            member.hp = max(0, member.hp - max(1, int(damage * num / max(1, den))))
        elif member.heal_blocked_turns <= 0:
            member.hp = min(member.max_hp, member.hp + max(1, int(damage * num / max(1, den))))


def _apply_defender_contact_ability(
    defender: PlannedMember | PlannedEnemy,
    attacker: PlannedMember | PlannedEnemy,
    move: dict[str, Any],
    damage: int,
    calculator: DamageCalculator,
) -> None:
    ability = _normalize(defender.calc_set().ability)
    move_type = _move_effective_type(move, attacker, calculator)
    if damage <= 0:
        _apply_ability_on_immunity(defender, attacker, move, calculator)
        return
    if ability == "stamina":
        _boost(defender, "def", 1)
    if ability == "watercompaction" and move_type == "Water":
        _boost(defender, "def", 2)
    if ability == "steamengine" and move_type in {"Fire", "Water"}:
        _boost(defender, "spe", 6)
    if ability == "thermalexchange" and move_type == "Fire":
        _boost(defender, "atk", 1)
    if ability == "windrider" and _has_flag(move, "wind"):
        _boost(defender, "atk", 1)
    if ability == "berserk" and defender.alive and defender.hp <= defender.max_hp // 2 < defender.hp + damage:
        _boost(defender, "spa", 1)
    if ability == "angershell" and defender.alive and defender.hp <= defender.max_hp // 2 < defender.hp + damage:
        _boost(defender, "atk", 1)
        _boost(defender, "spa", 1)
        _boost(defender, "spe", 1)
        _boost(defender, "def", -1)
        _boost(defender, "spd", -1)
    if ability == "weakarmor" and str(move.get("category") or "") == "Physical":
        _boost(defender, "def", -1)
        _boost(defender, "spe", 2)
    if ability == "sandspit":
        defender.weather = "sand" if hasattr(defender, "weather") else getattr(defender, "weather", "sand")
    if ability == "seedsower":
        _boost(defender, "def", 0)
    if ability == "cottondown":
        _boost(attacker, "spe", -1, source=defender)
    if not _makes_contact(move, attacker):
        return
    if ability in {"roughskin", "ironbarbs"} and _takes_residual(attacker):
        attacker.hp = max(0, attacker.hp - max(1, attacker.max_hp // 8))
    elif ability == "aftermath" and not defender.alive and _takes_residual(attacker):
        attacker.hp = max(0, attacker.hp - max(1, attacker.max_hp // 4))
    elif ability == "innardsout" and not defender.alive and _takes_residual(attacker):
        attacker.hp = max(0, attacker.hp - min(attacker.hp, damage))
    elif ability == "spicyspray" and _can_receive_status(attacker, "burn", calculator, defender):
        _set_status(attacker, "burn", source=defender)
    elif ability in {"static"} and _can_receive_status(attacker, "paralysis", calculator, defender):
        _apply_chance_status(attacker, "paralysis", 30, source=defender)
    elif ability in {"flamebody"} and _can_receive_status(attacker, "burn", calculator, defender):
        _apply_chance_status(attacker, "burn", 30, source=defender)
    elif ability in {"poisonpoint"} and _can_receive_status(attacker, "poison", calculator, defender):
        _apply_chance_status(attacker, "poison", 30, source=defender)
    elif ability == "effectspore":
        if _can_receive_status(attacker, "sleep", calculator, defender):
            _apply_chance_status(attacker, "sleep", 10, source=defender)
        elif _can_receive_status(attacker, "paralysis", calculator, defender):
            _apply_chance_status(attacker, "paralysis", 10, source=defender)
        elif _can_receive_status(attacker, "poison", calculator, defender):
            _apply_chance_status(attacker, "poison", 10, source=defender)
    elif ability in {"gooey", "tanglinghair"}:
        _boost(attacker, "spe", -1, source=defender)
    elif ability == "mummy":
        _set_member_ability(attacker, "Mummy")
    elif ability == "lingeringaroma":
        _set_member_ability(attacker, "Lingering Aroma")
    elif ability == "wanderingspirit":
        defender_ability = defender.calc_set().ability
        attacker_ability = attacker.calc_set().ability
        _set_member_ability(defender, attacker_ability)
        _set_member_ability(attacker, defender_ability)


def _apply_attacker_ko_ability(
    attacker: PlannedMember | PlannedEnemy,
    target: PlannedMember | PlannedEnemy,
    calculator: DamageCalculator,
) -> None:
    if target.alive:
        return
    ability = _normalize(attacker.calc_set().ability)
    if ability in {"moxie", "chillingneigh"} or ability == "asoneglastrier":
        _boost(attacker, "atk", 1)
    elif ability in {"grimneigh", "soulheart"} or ability == "asonespectrier":
        _boost(attacker, "spa", 1)
    elif ability == "battlebond":
        _boost(attacker, "atk", 1)
        _boost(attacker, "spa", 1)
        _boost(attacker, "spe", 1)
    elif ability == "beastboost":
        _boost(attacker, _best_boost_stat(attacker, calculator), 1)


def _apply_ability_on_immunity(
    defender: PlannedMember | PlannedEnemy,
    attacker: PlannedMember | PlannedEnemy,
    move: dict[str, Any],
    calculator: DamageCalculator,
) -> None:
    ability = _normalize(defender.calc_set().ability)
    move_type = _move_effective_type(move, attacker, calculator)
    if ability in {"voltabsorb", "waterabsorb", "dryskin", "eartheater"} and move_type in {"Electric", "Water", "Ground"}:
        if (
            (ability == "voltabsorb" and move_type == "Electric")
            or (ability in {"waterabsorb", "dryskin"} and move_type == "Water")
            or (ability == "eartheater" and move_type == "Ground")
        ) and defender.heal_blocked_turns <= 0:
            defender.hp = min(defender.max_hp, defender.hp + max(1, defender.max_hp // 4))
    elif ability in {"motordrive", "lightningrod"} and move_type == "Electric":
        _boost(defender, "spe" if ability == "motordrive" else "spa", 1)
    elif ability == "stormdrain" and move_type == "Water":
        _boost(defender, "spa", 1)
    elif ability == "flashfire" and move_type == "Fire":
        _boost(defender, "atk", 0)
    elif ability == "wellbakedbody" and move_type == "Fire":
        _boost(defender, "def", 2)
    elif ability == "sapsipper" and move_type == "Grass":
        _boost(defender, "atk", 1)
    elif ability == "windrider" and _has_flag(move, "wind"):
        _boost(defender, "atk", 1)


def _apply_chance_status(
    member: PlannedMember | PlannedEnemy,
    status: str,
    chance: int,
    source: PlannedMember | PlannedEnemy | None = None,
) -> None:
    if chance >= 100:
        _set_status(member, status, source=source)


def _protect_bypassed(user: PlannedMember | PlannedEnemy, move: dict[str, Any]) -> bool:
    ability = _normalize(user.calc_set().ability)
    return ability in {"unseenfist", "piercingdrill"} and _makes_contact(move, user)


def _makes_contact(move: dict[str, Any], user: PlannedMember | PlannedEnemy) -> bool:
    if _normalize(user.calc_set().ability) == "longreach":
        return False
    return _has_flag(move, "contact")


def _move_effective_type(move: dict[str, Any], user: PlannedMember | PlannedEnemy, calculator: DamageCalculator) -> str:
    move_type = str(move.get("type") or "")
    ability = _normalize(user.calc_set().ability)
    if ability == "normalize":
        return "Normal"
    if ability == "liquidvoice" and _has_flag(move, "sound"):
        return "Water"
    if move_type == "Normal":
        return {
            "aerilate": "Flying",
            "dragonize": "Dragon",
            "galvanize": "Electric",
            "pixilate": "Fairy",
            "refrigerate": "Ice",
        }.get(ability, move_type)
    return move_type


def _best_boost_stat(member: PlannedMember | PlannedEnemy, calculator: DamageCalculator) -> str:
    species = calculator._species_data(member.calc_set().species)  # noqa: SLF001
    if not species:
        return "atk"
    stats = {stat: int(species["baseStats"][stat]) for stat in ("atk", "def", "spa", "spd", "spe")}
    return max(stats, key=lambda stat: stats[stat])


def _set_member_ability(member: PlannedMember | PlannedEnemy, ability: str | None) -> None:
    if _normalize(member.calc_set().held_item) == "abilityshield":
        return
    if isinstance(member, PlannedMember):
        member.ability = ability
    else:
        member.ability = ability
    member.ability_on = True


def _apply_guaranteed_secondary(
    user: PlannedMember | PlannedEnemy,
    target: PlannedMember | PlannedEnemy,
    move: dict[str, Any],
    calculator: DamageCalculator,
) -> None:
    for effect in _additional_effects(move, user):
        if _additional_effect_chance(effect, user, target, move) < 100:
            continue
        _apply_additional_effect(user, target, effect, move, calculator)


def _apply_additional_effect(
    user: PlannedMember | PlannedEnemy,
    target: PlannedMember | PlannedEnemy,
    effect: dict[str, Any],
    move: dict[str, Any],
    calculator: DamageCalculator,
) -> None:
    status = _status_name(str(effect.get("status") or ""))
    if status and target.status is None and _can_receive_status(target, status, calculator, user):
        _set_status(target, status, source=user)
        if status == "sleep" and target.status == "sleep":
            target.sleep_turns = max(target.sleep_turns, 1)
        elif status == "toxic" and target.status == "toxic":
            target.toxic_counter = 0

    volatile = _normalize(str(effect.get("volatileStatus") or ""))
    move_id = _normalize(str(move.get("id") or move.get("name") or ""))
    target_ability = _normalize(target.calc_set().ability)
    if volatile == "flinch" and target_ability not in {"innerfocus", "shielddust"}:
        target.flinched = True
    elif volatile == "confusion" and target_ability != "owntempo":
        target.confused_turns = max(target.confused_turns, 2)
        _try_consume_confusion_berry(target, source=user)
    elif volatile == "saltcure":
        target.salt_cured = True
    elif volatile == "syrupbomb":
        target.syrup_bomb_turns = max(target.syrup_bomb_turns, 3)
    elif volatile == "healblock":
        target.heal_blocked_turns = max(target.heal_blocked_turns, 2)
    elif volatile == "sparklingaria" and target.status == "burn":
        target.status = None
    elif volatile in {"partiallytrapped", "trapped"}:
        target.trapped = True
    elif volatile == "soundblock":
        target.sound_blocked_turns = max(target.sound_blocked_turns, 2)

    if move_id in {"anchorshot", "spiritshackle"}:
        target.trapped = True
    if move_id == "throatchop":
        target.sound_blocked_turns = max(target.sound_blocked_turns, 2)

    for stat, amount in (effect.get("boosts") or {}).items():
        _boost(target, stat, int(amount), source=user if int(amount) < 0 else None)
    for stat, amount in ((effect.get("self") or {}).get("boosts") or {}).items():
        _boost(user, stat, int(amount))


def _additional_effects(move: dict[str, Any], user: PlannedMember | PlannedEnemy | None = None) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def add_effect(effect: dict[str, Any]) -> None:
        key = (
            effect.get("chance"),
            effect.get("status"),
            effect.get("volatileStatus"),
            effect.get("sideCondition"),
            effect.get("source"),
            tuple(sorted((effect.get("boosts") or {}).items())),
            tuple(sorted(((effect.get("self") or {}).get("boosts") or {}).items())),
        )
        if key in seen:
            return
        seen.add(key)
        effects.append(effect)

    if isinstance(move.get("secondary"), dict):
        add_effect(move["secondary"])
    for item in (move.get("secondaries") or []):
        if isinstance(item, dict):
            add_effect(item)

    move_id = _normalize(str(move.get("id") or move.get("name") or ""))
    if move_id in {"anchorshot", "spiritshackle"}:
        add_effect({"chance": 100, "volatileStatus": "trapped"})
    if move_id in {"stoneaxe", "ceaselessedge"}:
        add_effect({"chance": 100, "sideCondition": "stealthrock" if move_id == "stoneaxe" else "spikes"})
    if move_id == "genesissupernova":
        add_effect({"chance": 100, "sideCondition": "psychicterrain"})
    if move_id == "eeriespell":
        add_effect({"chance": 100, "sideCondition": "ppreduce"})
    if move_id == "diamondstorm":
        add_effect({"chance": 50, "self": {"boosts": {"def": 2}}})
    if move_id == "clangoroussoulblaze":
        add_effect({"chance": 100, "self": {"boosts": {"atk": 1, "def": 1, "spa": 1, "spd": 1, "spe": 1}}})
    if move_id == "throatchop":
        add_effect({"chance": 100, "volatileStatus": "soundblock"})
    if move_id == "direclaw":
        add_effect({"chance": 17, "status": "psn"})
        add_effect({"chance": 17, "status": "par"})
        add_effect({"chance": 17, "status": "slp"})
    if move_id == "triattack":
        add_effect({"chance": 7, "status": "brn"})
        add_effect({"chance": 7, "status": "frz"})
        add_effect({"chance": 7, "status": "par"})
    if move_id == "secretpower":
        add_effect({"chance": int((move.get("secondary") or {}).get("chance") or 30), "status": "par"})

    if user is not None and str(move.get("category") or "") != "Status":
        ability = _normalize(user.calc_set().ability)
        item = _normalize(user.calc_set().held_item)
        if ability == "poisontouch" and _has_flag(move, "contact"):
            add_effect({"chance": 30, "status": "psn", "source": "ability"})
        if ability == "stench":
            add_effect({"chance": 10, "volatileStatus": "flinch", "source": "ability"})
        if item in {"kingsrock", "razorfang"}:
            add_effect({"chance": 10, "volatileStatus": "flinch", "source": "item"})
    return effects


def _additional_effect_chance(
    effect: dict[str, Any],
    user: PlannedMember | PlannedEnemy | None,
    target: PlannedMember | PlannedEnemy | None,
    move: dict[str, Any],
) -> int:
    try:
        chance = int(effect.get("chance") or 0)
    except (TypeError, ValueError):
        chance = 0
    if chance <= 0:
        return 0
    if user is not None and _normalize(user.calc_set().ability) == "sheerforce" and effect.get("source") not in {"ability", "item"}:
        return 0
    if target is not None and _additional_effect_blocked(target):
        return 0
    if (
        user is not None
        and _normalize(user.calc_set().ability) == "serenegrace"
        and effect.get("source") not in {"ability", "item"}
    ):
        chance *= 2
    return max(0, min(100, chance))


def _additional_effect_blocked(target: PlannedMember | PlannedEnemy) -> bool:
    calc_set = target.calc_set()
    return _normalize(calc_set.ability) == "shielddust" or _normalize(calc_set.held_item) == "covertcloak"


def _has_flag(move: dict[str, Any], flag: str) -> bool:
    return bool((move.get("flags") or {}).get(flag))


def _is_sound_blocked(user: PlannedMember | PlannedEnemy, move_name: str, calculator: DamageCalculator) -> bool:
    if user.sound_blocked_turns <= 0:
        return False
    move = calculator.moves.get(_normalize(move_name), {})
    return _has_flag(move, "sound")


def _status_move_blocked(
    user: PlannedMember | PlannedEnemy,
    target: PlannedMember | PlannedEnemy,
    move_name: str,
    calculator: DamageCalculator,
) -> bool:
    move_id = _normalize(move_name)
    if move_id in DIRECT_RECOVERY_MOVES | RNB_PROTECT_MOVES | set(SETUP_MOVE_BOOSTS) | {"rest", "bellydrum"}:
        return False
    move = calculator.moves.get(move_id, {})
    ability = _normalize(target.calc_set().ability)
    if ability == "goodasgold":
        return True
    if ability == "magicbounce":
        if move_id in SLEEP_MOVES and user.status is None and _can_receive_status(user, "sleep", calculator, target):
            _set_status(user, "sleep", source=target)
            if user.status == "sleep":
                user.sleep_turns = 1
        elif move_id in BURN_MOVES and user.status is None and _can_receive_status(user, "burn", calculator, target):
            _set_status(user, "burn", source=target)
        elif move_id in PARALYSIS_MOVES and user.status is None and _can_receive_status(user, "paralysis", calculator, target):
            _set_status(user, "paralysis", source=target)
        elif move_id in TOXIC_MOVES and user.status is None and _can_receive_status(user, "toxic", calculator, target):
            _set_status(user, "toxic", source=target)
        elif move_id in POISON_MOVES and user.status is None and _can_receive_status(user, "poison", calculator, target):
            _set_status(user, "poison", source=target)
        return True
    if ability == "overcoat" and _has_flag(move, "powder"):
        return True
    if ability in {"aromaveil", "oblivious"} and move_id in {"taunt", "encore", "disable", "healblock", "captivate", "attract"}:
        return True
    if ability == "owntempo" and move_id in CONFUSION_MOVES:
        return True
    return False


def _set_status(
    member: PlannedMember | PlannedEnemy,
    status: str,
    source: PlannedMember | PlannedEnemy | None = None,
) -> None:
    member.status = _status_name(status)
    _try_consume_status_berry(member, source=source)


def _try_consume_status_berry(
    member: PlannedMember | PlannedEnemy,
    source: PlannedMember | PlannedEnemy | None = None,
) -> bool:
    if member.status is None or getattr(member, "consumed_item", False):
        return False
    item = _normalize(getattr(member, "item", None) or getattr(member.calc_set(), "held_item", None))
    if item not in STATUS_CURE_BERRIES.get(member.status, set()):
        return False
    if _berry_blocked_by_source(source):
        return False
    member.status = None
    member.sleep_turns = 0
    member.toxic_counter = 0
    _consume_held_berry(member)
    return True


def _try_consume_confusion_berry(
    member: PlannedMember | PlannedEnemy,
    source: PlannedMember | PlannedEnemy | None = None,
) -> bool:
    if member.confused_turns <= 0 or getattr(member, "consumed_item", False):
        return False
    item = _normalize(getattr(member, "item", None) or getattr(member.calc_set(), "held_item", None))
    if item not in CONFUSION_CURE_BERRIES:
        return False
    if _berry_blocked_by_source(source):
        return False
    member.confused_turns = 0
    _consume_held_berry(member)
    return True


def _consume_held_berry(member: PlannedMember | PlannedEnemy) -> None:
    member.consumed_item = True
    if _normalize(member.calc_set().ability) == "cheekpouch" and member.heal_blocked_turns <= 0 and member.hp > 0:
        member.hp = min(member.max_hp, member.hp + max(1, member.max_hp // 3))


def _try_consume_hp_berry(
    member: PlannedMember | PlannedEnemy,
    source: PlannedMember | PlannedEnemy | None = None,
) -> bool:
    """Consume an HP berry as soon as its threshold is crossed.

    Run & Bun uses the familiar held-berry timing: the heal belongs to the hit
    that caused it, not to a later free-standing end-of-turn event.  Returning a
    boolean lets callers/UI report the activation without guessing from net HP.
    """
    if not member.alive or getattr(member, "consumed_item", False):
        return False
    if member.heal_blocked_turns > 0 or _berry_blocked_by_source(source):
        return False
    item = _normalize(getattr(member, "item", None) or getattr(member.calc_set(), "held_item", None))
    ability = _normalize(member.calc_set().ability)
    multiplier = 2 if ability == "ripen" else 1
    heal = 0
    threshold = member.max_hp // 2
    if item == "oranberry" and member.hp <= threshold:
        heal = 10 * multiplier
    elif item == "sitrusberry" and member.hp <= threshold:
        heal = max(1, member.max_hp // 4) * multiplier
    elif item in CONFUSE_BERRIES and member.hp <= member.max_hp // 4:
        heal = max(1, member.max_hp // 2) * multiplier
    if heal <= 0:
        return False
    member.hp = min(member.max_hp, member.hp + heal)
    _consume_held_berry(member)
    return True


def _berry_blocked_by_source(source: PlannedMember | PlannedEnemy | None) -> bool:
    return _normalize(source.calc_set().ability) in {"unnerve", "asonespectrier", "asoneglastrier"} if source else False


def _can_receive_status(
    target: PlannedMember | PlannedEnemy,
    status: str,
    calculator: DamageCalculator,
    source: PlannedMember | PlannedEnemy | None = None,
) -> bool:
    status = _status_name(status)
    if target.status is not None:
        return False
    ability = _normalize(target.calc_set().ability)
    source_ability = _normalize(source.calc_set().ability) if source is not None else ""
    types = set(_member_types(target, calculator))
    if ability in {"purifyingsalt", "comatose"}:
        return False
    if status in {"poison", "toxic"}:
        return source_ability == "corrosion" or not ({"Poison", "Steel"} & types or ability in {"immunity", "pastelveil"})
    if status == "burn":
        return "Fire" not in types and ability not in {"waterveil", "waterbubble", "thermalexchange"}
    if status == "paralysis":
        return "Electric" not in types and ability != "limber"
    if status == "sleep":
        return ability not in {"insomnia", "vitalspirit", "sweetveil"}
    if status == "freeze":
        return "Ice" not in types and ability != "magmaarmor"
    return True


def _can_be_seeded(target: PlannedMember | PlannedEnemy, calculator: DamageCalculator) -> bool:
    return "Grass" not in set(_member_types(target, calculator))


def _member_types(member: PlannedMember | PlannedEnemy, calculator: DamageCalculator) -> tuple[str, ...]:
    species_data = calculator._species_data(member.calc_set().species)  # noqa: SLF001
    return tuple(species_data.get("types", ())) if species_data else ()


def _takes_residual(member: PlannedMember | PlannedEnemy) -> bool:
    return _normalize(member.calc_set().ability) != "magicguard"


def _salt_cure_fraction(member: PlannedMember | PlannedEnemy, calculator: DamageCalculator) -> float:
    types = set(_member_types(member, calculator))
    return 1.0 / 4.0 if types & {"Water", "Steel"} else 1.0 / 8.0


def _end_of_turn(player: PlannedMember, enemy: PlannedEnemy, calculator: DamageCalculator) -> None:
    for member, opponent in ((player, enemy), (enemy, player)):
        if not member.alive:
            continue
        if not _takes_residual(member):
            pass
        elif member.status in {"poison", "toxic"} and _normalize(member.calc_set().ability) == "poisonheal":
            if member.heal_blocked_turns <= 0:
                member.hp = min(member.max_hp, member.hp + max(1, member.max_hp // 8))
        elif member.status == "burn":
            divisor = 32 if _normalize(member.calc_set().ability) == "heatproof" else 16
            member.hp = max(0, member.hp - max(1, member.max_hp // divisor))
        elif member.status == "poison":
            member.hp = max(0, member.hp - max(1, member.max_hp // 8))
        elif member.status == "toxic":
            member.toxic_counter = max(1, min(15, member.toxic_counter + 1))
            member.hp = max(0, member.hp - max(1, member.max_hp * member.toxic_counter // 16))
        if member.leech_seeded and member.alive and _takes_residual(member):
            seeded_damage = min(member.hp, max(1, member.max_hp // 8))
            member.hp -= seeded_damage
            if opponent.alive and opponent.heal_blocked_turns <= 0:
                opponent.hp = min(opponent.max_hp, opponent.hp + seeded_damage)
        if member.salt_cured and member.alive and _takes_residual(member):
            fraction = _salt_cure_fraction(member, calculator)
            member.hp = max(0, member.hp - max(1, int(member.max_hp * fraction)))
        if member.syrup_bomb_turns > 0 and member.alive:
            _boost(member, "spe", -1)
            member.syrup_bomb_turns -= 1
        ability = _normalize(member.calc_set().ability)
        if ability == "speedboost" and member.alive:
            _boost(member, "spe", 1)
        if ability == "baddreams" and opponent.alive and opponent.status == "sleep" and _takes_residual(opponent):
            opponent.hp = max(0, opponent.hp - max(1, opponent.max_hp // 8))
        if ability == "shedskin" and member.status is not None:
            pass
        if ability == "hydration" and member.status is not None:
            pass
        if member.confused_turns > 0:
            member.confused_turns -= 1
        if member.heal_blocked_turns > 0:
            member.heal_blocked_turns -= 1
        if member.sound_blocked_turns > 0:
            member.sound_blocked_turns -= 1
        item = _normalize(getattr(member, "item", None) or getattr(member.calc_set(), "held_item", None))
        if getattr(member, "consumed_item", False):
            continue
        if member.heal_blocked_turns > 0:
            continue
        if _normalize(opponent.calc_set().ability) in {"unnerve", "asonespectrier", "asoneglastrier"}:
            continue
        berry_threshold = member.max_hp // 2 if _normalize(member.calc_set().ability) == "gluttony" else member.max_hp // 2
        berry_multiplier = 2 if _normalize(member.calc_set().ability) == "ripen" else 1
        if item == "sitrusberry" and member.hp > 0 and member.hp <= berry_threshold:
            member.hp = min(member.max_hp, member.hp + max(1, member.max_hp // 4) * berry_multiplier)
            if _normalize(member.calc_set().ability) == "cheekpouch":
                member.hp = min(member.max_hp, member.hp + max(1, member.max_hp // 3))
            member.consumed_item = True
        elif item == "oranberry" and member.hp > 0 and member.hp <= member.max_hp // 2:
            member.hp = min(member.max_hp, member.hp + 10 * berry_multiplier)
            if _normalize(member.calc_set().ability) == "cheekpouch":
                member.hp = min(member.max_hp, member.hp + max(1, member.max_hp // 3))
            member.consumed_item = True
        elif item in CONFUSE_BERRIES and member.hp > 0 and member.hp <= member.max_hp // 4:
            member.hp = min(member.max_hp, member.hp + max(1, member.max_hp // 2) * berry_multiplier)
            if _normalize(member.calc_set().ability) == "cheekpouch":
                member.hp = min(member.max_hp, member.hp + max(1, member.max_hp // 3))
            member.consumed_item = True
        elif item in PINCH_BOOST_BERRIES and member.hp > 0 and member.hp <= member.max_hp // 4:
            _boost(member, PINCH_BOOST_BERRIES[item], berry_multiplier, blockable=False)
            if _normalize(member.calc_set().ability) == "cheekpouch" and member.heal_blocked_turns <= 0:
                member.hp = min(member.max_hp, member.hp + max(1, member.max_hp // 3))
            member.consumed_item = True
        elif item == "leftovers":
            member.hp = min(member.max_hp, member.hp + max(1, member.max_hp // 16))
    player.protected = False
    enemy.protected = False


def _skip_turn(member: PlannedMember | PlannedEnemy) -> bool:
    if member.flinched:
        member.flinched = False
        if _normalize(member.calc_set().ability) == "steadfast":
            _boost(member, "spe", 1)
        return True
    if member.status == "sleep" and member.sleep_turns > 0:
        member.sleep_turns -= 1
        return True
    if member.status == "sleep":
        # The counter reached zero on the previous sleeping turn.  Wake before
        # acting so a legal status move can put this battler back to sleep later.
        member.status = None
    return False


def _will_skip_turn(member: PlannedMember | PlannedEnemy) -> bool:
    """Pure check for a guaranteed lost turn.

    Decision code must use this before treating a nominally lethal move as an
    incoming KO.  Calling ``_skip_turn`` while planning would consume a sleep
    turn before the battle actually advances.
    """
    return bool(
        member.flinched
        or (member.status == "sleep" and member.sleep_turns > 0)
    )


def _enemy_moves_before_player(
    enemy: PlannedEnemy,
    player: PlannedMember,
    enemy_move: str,
    player_move: str,
    calculator: DamageCalculator,
) -> bool:
    enemy_prio = _move_priority_for(enemy_move, enemy, player, calculator)
    player_prio = _move_priority_for(player_move, player, enemy, calculator)
    if enemy_prio != player_prio:
        return enemy_prio > player_prio
    if _normalize(enemy.calc_set().ability) == "stall" and _normalize(player.calc_set().ability) != "stall":
        return False
    if _normalize(player.calc_set().ability) == "stall" and _normalize(enemy.calc_set().ability) != "stall":
        return True
    return _speed(enemy.calc_set(), calculator) >= _speed(player.calc_set(), calculator)


def _ranked_known_damage(
    calculator: DamageCalculator,
    attacker: PokemonCalcSet,
    defender: PokemonCalcSet,
    moves: tuple[str, ...],
) -> tuple[DamageRange, ...]:
    return tuple(damage for damage in calculator.rank_move_names(attacker, defender, moves) if damage.max_damage > 0)


def _best_damage(
    calculator: DamageCalculator,
    attacker: PokemonCalcSet,
    defender: PokemonCalcSet,
    moves: tuple[str, ...],
    *,
    use_min: bool,
    current_hp: int,
) -> DamageRange | None:
    for damage in _ranked_known_damage(calculator, attacker, defender, moves):
        damage_value = damage.min_damage if use_min else damage.max_damage
        if damage_value >= current_hp:
            return damage
    ranked = _ranked_known_damage(calculator, attacker, defender, moves)
    return ranked[0] if ranked else None


def _has_ko(
    calculator: DamageCalculator,
    attacker: PokemonCalcSet,
    defender: PokemonCalcSet,
    moves: tuple[str, ...],
    *,
    use_min: bool,
    current_hp: int,
) -> bool:
    for damage in _ranked_known_damage(calculator, attacker, defender, moves):
        damage_value = damage.min_damage if use_min else damage.max_damage
        if damage_value >= current_hp:
            return True
    return False


def _enemy_pressure(
    enemy: PlannedEnemy,
    player: PlannedMember,
    team: list[PlannedMember],
    calculator: DamageCalculator,
) -> float:
    choices = _ai_move_choices(enemy, player, team, calculator)
    if not choices or not choices[0].damage:
        return 0.0
    return min(1.0, choices[0].damage.max_percent)


def _best_direct_damage_percent(
    calculator: DamageCalculator,
    attacker: PokemonCalcSet,
    defender: PokemonCalcSet,
    moves: tuple[str, ...],
) -> float:
    ranked = _ranked_known_damage(calculator, attacker, defender, moves)
    return ranked[0].max_percent if ranked else 0.0


def _residual_pressure(member: PlannedMember | PlannedEnemy) -> float:
    pressure = 0.0
    if member.status == "burn":
        pressure += 1.0 / 16.0
    elif member.status == "poison":
        pressure += 1.0 / 8.0
    elif member.status == "toxic":
        pressure += max(1, member.toxic_counter + 1) / 16.0
    if member.leech_seeded:
        pressure += 1.0 / 8.0
    if member.salt_cured:
        pressure += 1.0 / 8.0
    return min(0.5, pressure)


def _enemy_best_category(enemy: PlannedEnemy, player: PlannedMember, calculator: DamageCalculator) -> str:
    ranked = calculator.rank_move_names(enemy.calc_set(), player.calc_set(), enemy.moves)
    best = next((damage for damage in ranked if damage.max_damage > 0), None)
    if not best:
        return ""
    move = calculator.moves.get(_normalize(best.move_name), {})
    return str(move.get("category") or "")


def _player_has_physical_move(player: PlannedMember, calculator: DamageCalculator) -> bool:
    return any(calculator.moves.get(_normalize(move), {}).get("category") == "Physical" for move in player.known_moves)


def _player_has_damaging_category(player: PlannedMember, calculator: DamageCalculator, category: str) -> bool:
    return any(calculator.moves.get(_normalize(move), {}).get("category") == category for move in player.known_moves)


def _speed(pokemon: PokemonCalcSet, calculator: DamageCalculator) -> int:
    species_data = calculator._species_data(pokemon.species)  # noqa: SLF001
    if not species_data:
        return 0
    value = calculator._stat(species_data, "spe", pokemon.level, pokemon.nature, pokemon.evs, pokemon.ivs)  # noqa: SLF001
    stage = max(-6, min(6, (pokemon.boosts or {}).get("spe", 0)))
    value = int(value * _boost_multiplier(stage))
    ability = _normalize(pokemon.ability)
    if pokemon.status and ability == "quickfeet":
        value = int(value * 1.5)
    if pokemon.held_item is None and ability == "unburden":
        value *= 2
    if ability == "slowstart" and pokemon.ability_on:
        value //= 2
    if pokemon.status == "paralysis" and ability != "quickfeet":
        value //= 4
    return value


def _boost(
    member: PlannedMember | PlannedEnemy,
    stat: str,
    amount: int,
    *,
    source: PlannedMember | PlannedEnemy | None = None,
    blockable: bool = True,
) -> None:
    if amount == 0:
        return
    ability = _normalize(member.calc_set().ability)
    if amount < 0 and blockable and _stat_drop_blocked(member, stat):
        if ability == "mirrorarmor" and source is not None:
            _boost(source, stat, amount, source=member, blockable=True)
        return
    if ability == "contrary":
        amount = -amount
    if ability == "simple":
        amount *= 2
    old = member.boosts.get(stat, 0)
    member.boosts[stat] = max(-6, min(6, member.boosts.get(stat, 0) + amount))
    if amount < 0 and source is not None and member.boosts.get(stat, 0) < old:
        if ability == "defiant":
            _boost(member, "atk", 2, blockable=False)
        elif ability == "competitive":
            _boost(member, "spa", 2, blockable=False)


INTIMIDATE_IMMUNE_ABILITIES = {"innerfocus", "oblivious", "owntempo", "scrappy"}


def _apply_entry_ability(
    incoming: PlannedMember | PlannedEnemy,
    opponent: PlannedMember | PlannedEnemy,
    calculator: DamageCalculator,
) -> list[str]:
    """Apply the incoming mon's on-entry ability against the current opponent.

    Returns human-readable event strings for the line output.
    """
    events: list[str] = []
    if not incoming.alive or not opponent.alive:
        return events
    ability = _normalize(incoming.calc_set().ability)
    if ability == "intimidate":
        target_ability = _normalize(opponent.calc_set().ability)
        if target_ability in INTIMIDATE_IMMUNE_ABILITIES:
            events.append(f"{incoming.name}'s Intimidate is blocked by {opponent.name}'s ability.")
            return events
        before = opponent.boosts.get("atk", 0)
        _boost(opponent, "atk", -1, source=incoming)
        after = opponent.boosts.get("atk", 0)
        if after < before:
            events.append(f"{incoming.name}'s Intimidate drops {opponent.name}'s Attack to stage {after}.")
        else:
            events.append(f"{incoming.name}'s Intimidate does not lower {opponent.name}'s Attack.")
        if target_ability == "rattled":
            _boost(opponent, "spe", 1, blockable=False)
            events.append(f"{opponent.name}'s Rattled raises its Speed.")
    return events


def _stat_drop_blocked(member: PlannedMember | PlannedEnemy, stat: str) -> bool:
    ability = _normalize(member.calc_set().ability)
    if ability in {"clearbody", "whitesmoke", "fullmetalbody", "mirrorarmor"}:
        return True
    if ability == "hypercutter" and stat == "atk":
        return True
    if ability == "bigpecks" and stat == "def":
        return True
    if ability in {"keeneye", "illuminate", "mindseye"} and stat in {"accuracy", "acc"}:
        return True
    return False


def _max_hp(pokemon: PokemonCalcSet, calculator: DamageCalculator) -> int:
    species_data = calculator._species_data(pokemon.species)  # noqa: SLF001
    if not species_data:
        return pokemon.max_hp or pokemon.hp or 1
    return calculator._stat(species_data, "hp", pokemon.level, pokemon.nature, pokemon.evs, pokemon.ivs)  # noqa: SLF001


def _move_priority(move_name: str, calculator: DamageCalculator) -> int:
    return int((calculator.moves.get(_normalize(move_name), {}) or {}).get("priority") or 0)


def _move_priority_for(
    move_name: str,
    user: PlannedMember | PlannedEnemy,
    target: PlannedMember | PlannedEnemy,
    calculator: DamageCalculator,
) -> int:
    move = calculator.moves.get(_normalize(move_name), {}) or {}
    priority = int(move.get("priority") or 0)
    ability = _normalize(user.calc_set().ability)
    if ability == "prankster" and move.get("category") == "Status":
        priority += 1
    if ability == "galewings" and str(move.get("type") or "") == "Flying":
        priority += 1
    if ability == "triage" and _is_healing_move(move_name):
        priority += 3
    if priority > 0 and _priority_blocked(user, target):
        return -20
    return priority


def _priority_blocked(user: PlannedMember | PlannedEnemy, target: PlannedMember | PlannedEnemy) -> bool:
    return _normalize(target.calc_set().ability) in {"queenlymajesty", "dazzling", "armortail"}


def _is_healing_move(move_name: str) -> bool:
    return _normalize(move_name) in DIRECT_RECOVERY_MOVES | {"rest", "drainingkiss"}


def _recovery_amount(
    member: PlannedMember | PlannedEnemy,
    move_id: str,
    calculator: DamageCalculator,
) -> int:
    """Gen-style recovery amount, including the weather-sensitive recovery moves."""
    if move_id == "rest":
        return max(0, member.max_hp - member.hp)
    weather = _normalize(getattr(calculator.default_field, "weather", None) or "")
    fraction = 0.5
    if move_id in {"synthesis", "moonlight", "morningsun"}:
        if weather in {"sun", "harshsun", "sunnyday"}:
            fraction = 2 / 3
        elif weather in {"rain", "heavyrain", "sand", "sandstorm", "hail", "snow"}:
            fraction = 0.25
    elif move_id == "shoreup" and weather in {"sand", "sandstorm"}:
        fraction = 2 / 3
    return max(1, int(member.max_hp * fraction))


def _strength_sap_amount(
    target: PlannedMember | PlannedEnemy,
    calculator: DamageCalculator,
) -> int:
    calc_set = target.calc_set()
    species = calculator._species_data(calc_set.species)  # noqa: SLF001
    if not species:
        return 1
    attack = calculator._stat(species, "atk", calc_set.level, calc_set.nature, calc_set.evs, calc_set.ivs)  # noqa: SLF001
    stage = max(-6, min(6, (calc_set.boosts or {}).get("atk", 0)))
    return max(1, int(attack * _boost_multiplier(stage)))


def _move_accuracy(
    move_name: str,
    calculator: DamageCalculator,
    user: PlannedMember | PlannedEnemy | None = None,
) -> float:
    if _normalize(move_name) == "thunderwave" and user is not None and "Electric" in set(_member_types(user, calculator)):
        return 1.0
    value = (calculator.moves.get(_normalize(move_name), {}) or {}).get("accuracy", 100)
    if value is True:
        return 1.0
    try:
        return max(0.0, min(1.0, float(value) / 100.0))
    except (TypeError, ValueError):
        return 1.0


def _disguise_blocks(member: PlannedMember | PlannedEnemy) -> bool:
    calc_set = member.calc_set()
    return _normalize(calc_set.ability) == "disguise" and calc_set.ability_on


# When True, secondary effects and crit-KO chances are treated as if they never
# fire. Used to compute the "best-case / no secondary effects" line.
_IGNORE_SECONDARY = False


def set_ignore_secondary(value: bool) -> None:
    """Toggle best-case mode (no secondary effects, no crit KOs) for confidence."""
    global _IGNORE_SECONDARY
    _IGNORE_SECONDARY = bool(value)


def ignore_secondary_enabled() -> bool:
    return _IGNORE_SECONDARY


# When True, secondary-effect disruption is softened in the REPORTED confidence only.
# Planning always runs with this False, so the line the planner picks is unchanged; the
# calc loop flips it on solely while computing the number it shows. A single flinch or
# stat drop rarely loses an already-winning line, so counting it at full weight every
# turn understates a clean line's true reliability.
_SOFTEN_REPORTING = False
_SOFTEN_FACTOR = 0.42


_ENTRY_TURN = False


def set_entry_turn(value: bool) -> None:
    """When True, branch safety is being evaluated for a switch-entry turn: the
    incoming mon does not act, so flinch secondaries cannot disrupt the line."""
    global _ENTRY_TURN
    _ENTRY_TURN = value


def set_soften_reporting(value: bool) -> None:
    global _SOFTEN_REPORTING
    _SOFTEN_REPORTING = bool(value)


def _survival_confidence(member: PlannedMember, choice: MoveChoice | None) -> float:
    if choice and choice.damage and choice.damage.ko_chance >= 1.0:
        return 0.15
    return 1.0


def _player_action_confidence(
    action: PlayerAction,
    calculator: DamageCalculator,
    user: PlannedMember | PlannedEnemy | None = None,
) -> float:
    if not action.move_name:
        return 0.35
    confidence = _move_accuracy(action.move_name, calculator, user)
    move_id = _normalize(action.move_name)
    if move_id in CONFUSION_MOVES:
        confidence *= 0.72
    return max(0.05, min(1.0, confidence))


def _ai_branch_confidence(
    choices: list[MoveChoice],
    target: PlannedMember,
    attacker: PlannedEnemy,
    calculator: DamageCalculator,
) -> float:
    if not choices:
        return 1.0
    if attacker.status == "sleep" and attacker.sleep_turns > 0:
        # Enemy is guaranteed to spend this turn asleep; its move branches cannot fire.
        return 1.0
    safe_probability = 0.0
    for choice in choices:
        safe_probability += choice.probability * _choice_safety(choice, target, attacker, calculator)
    return max(0.05, min(1.0, safe_probability))


def _choice_safety(
    choice: MoveChoice,
    target: PlannedMember,
    attacker: PlannedEnemy,
    calculator: DamageCalculator,
) -> float:
    accuracy = _move_accuracy(choice.move_name, calculator, attacker)
    disruption = _status_disruption_risk(choice.move_name, target, calculator)
    ko_risk = 0.0
    crit_ko_risk = 0.0
    if choice.damage is not None and choice.damage.max_damage <= 0:
        move = calculator.moves.get(_normalize(choice.move_name), {})
        if move.get("category") != "Status":
            # Immune target: a damaging move that deals 0 also can't fire secondaries.
            return 1.0
    if choice.damage:
        # Use the move's REAL KO probability (fraction of damage rolls that KO, already
        # scaled by accuracy), not just "a max roll could KO". A move that only KOs on a
        # high roll is a small risk, not a near-certain one.
        ko_risk = max(0.0, min(1.0, choice.damage.ko_chance))
        if ko_risk >= 1.0:
            return 0.0
        if not _IGNORE_SECONDARY and choice.damage.max_damage < target.hp:
            critical = calculator.estimate_move(attacker.calc_set(), target.calc_set(), choice.move_name, DamageContext(critical=True))
            if critical and critical.max_damage >= target.hp:
                # Only a crit would KO: use this attacker/move's REAL crit rate (high-crit
                # moves, Scope Lens, Super Luck stack — e.g. Togekiss Air Cutter is 100%).
                crit_ko_risk = accuracy * crit_rate(attacker, target, choice.move_name, calculator)
        disruption = max(disruption, _secondary_disruption_risk(choice.move_name, calculator, attacker, target))
        # A hit that barely dents the target can still flinch / drop / status it, but
        # that rarely flips the outcome. Scale disruption by how hard the hit actually
        # lands so a weak attacker no longer collapses confidence on a clean win line.
        dmg_frac = min(1.0, choice.damage.max_damage / max(1, target.hp))
        disruption *= 0.3 + 0.7 * dmg_frac
    if _SOFTEN_REPORTING:
        disruption *= _SOFTEN_FACTOR
    # disruption needs the move to land; ko_risk and crit_ko_risk already fold in accuracy.
    threat = max(accuracy * disruption, ko_risk, crit_ko_risk)
    return max(0.0, 1.0 - threat)


# Crit-rate model ported from the bundled calculator (web/static/rnbcalc/js/crit_rate.js).
# Modern stage table; Emerald uses its own Gen III table below.
_CRIT_STAGE_RATES = (0.0625, 0.125, 0.5, 1.0)
_EMERALD_CRIT_STAGE_RATES = (0.0625, 0.125, 0.25, 1 / 3, 0.5)
# Pre-normalized (casefolded, alphanumeric only) so these build at import time without
# depending on _normalize, which is defined later in the module.
_HIGH_CRIT_MOVES = {
    "aeroblast", "aircutter", "attackorder", "blazekick", "crabhammer",
    "crosschop", "crosspoison", "drillrun", "karatechop", "leafblade",
    "nightslash", "poisontail", "psychocut", "razorleaf", "razorwind",
    "shadowclaw", "skyattack", "slash", "spacialrend", "stoneedge",
}
_ALWAYS_CRIT_MOVES = {
    "frostbreath", "stormthrow", "surgingstrikes", "wickedblow",
    "flowertrick", "zippyzap",
}
_HIGH_CRIT_ITEMS = {"razorclaw", "scopelens"}
_CRIT_BLOCK_ABILITIES = {"battlearmor", "shellarmor", "magmaarmor"}


def crit_rate(attacker, defender, move_name: str, calculator: DamageCalculator) -> float:
    """Probability the attacker's move crits, accounting for high-crit moves/items,
    Super Luck, Focus Energy, always-crit moves, and crit-blocking abilities."""
    move_id = _normalize(move_name)
    try:
        atk = attacker.calc_set()
        dfn = defender.calc_set()
    except Exception:
        return 0.0625
    if _normalize(getattr(dfn, "ability", "") or "") in _CRIT_BLOCK_ABILITIES:
        return 0.0
    if move_id in _ALWAYS_CRIT_MOVES:
        return 1.0
    if _normalize(getattr(atk, "ability", "") or "") == "merciless" and getattr(defender, "status", None) in {"poison", "toxic"}:
        return 1.0
    stage = 0
    if move_id in _HIGH_CRIT_MOVES:
        stage += 1
    if _normalize(getattr(atk, "item", "") or "") in _HIGH_CRIT_ITEMS:
        stage += 1
    if _normalize(getattr(atk, "ability", "") or "") == "superluck":
        stage += 1
    if getattr(attacker, "focus_energy", False):
        stage += 2
    rates = _EMERALD_CRIT_STAGE_RATES if calculator.game_mode == "pokemon-emerald" else _CRIT_STAGE_RATES
    return rates[min(stage, len(rates) - 1)]


def flinch_chance(move_name: str, calculator: DamageCalculator, user=None, target=None) -> float:
    """Chance the move flinches the target (only meaningful if the user moves first)."""
    move = calculator.moves.get(_normalize(move_name), {})
    best = 0.0
    for effect in _additional_effects(move, user):
        if _normalize(str(effect.get("volatileStatus") or "")) == "flinch":
            best = max(best, _additional_effect_chance(effect, user, target, move) / 100.0)
    return best


def _status_disruption_risk(move_name: str, target: PlannedMember, calculator: DamageCalculator) -> float:
    if _IGNORE_SECONDARY:
        return 0.0
    move_id = _normalize(move_name)
    if move_id in SLEEP_MOVES and target.status is None and _can_receive_status(target, "sleep", calculator):
        return 0.85
    if move_id in PARALYSIS_MOVES and target.status is None and _can_receive_status(target, "paralysis", calculator):
        return 0.55
    if move_id in BURN_MOVES and target.status is None and _can_receive_status(target, "burn", calculator) and _player_has_physical_move(target, calculator):
        return 0.45
    if move_id in TOXIC_MOVES and target.status is None and _can_receive_status(target, "toxic", calculator):
        return 0.40
    if move_id in POISON_MOVES and target.status is None and _can_receive_status(target, "poison", calculator):
        return 0.25
    if move_id in LEECH_SEED_MOVES and not target.leech_seeded and _can_be_seeded(target, calculator):
        return 0.35
    if move_id in CONFUSION_MOVES and target.confused_turns <= 0:
        return 0.28
    if move_id in ATTACK_DROP_MOVES | SPECIAL_ATTACK_DROP_MOVES | SPEED_DROP_MOVES:
        return 0.18
    if move_id in SETUP_MOVE_BOOSTS:
        return 0.30
    return 0.0


def _secondary_disruption_risk(
    move_name: str,
    calculator: DamageCalculator,
    user: PlannedMember | PlannedEnemy | None = None,
    target: PlannedMember | PlannedEnemy | None = None,
) -> float:
    if _IGNORE_SECONDARY:
        return 0.0
    move = calculator.moves.get(_normalize(move_name), {})
    risk = 0.0
    for effect in _additional_effects(move, user):
        chance = _additional_effect_chance(effect, user, target, move) / 100.0
        if chance <= 0:
            continue
        status = _status_name(str(effect.get("status") or ""))
        if status and (target is None or _can_receive_status(target, status, calculator, user)):
            weight = 0.75 if status in {"sleep", "freeze"} else 0.55
            if status == "burn" and target is not None and not _player_has_physical_move(target, calculator):
                weight = 0.25
            risk = max(risk, chance * weight)
        volatile = _normalize(str(effect.get("volatileStatus") or ""))
        if volatile == "flinch":
            if not _ENTRY_TURN:
                risk = max(risk, chance * 0.75)
        elif volatile == "confusion":
            risk = max(risk, chance * 0.40)
        elif volatile in {"saltcure", "syrupbomb", "healblock", "partiallytrapped", "trapped", "soundblock"}:
            risk = max(risk, chance * 0.30)
        drops = {stat: amount for stat, amount in (effect.get("boosts") or {}).items() if int(amount) < 0}
        if drops:
            risk = max(risk, chance * 0.40)
        self_boosts = (effect.get("self") or {}).get("boosts") or {}
        if self_boosts:
            risk = max(risk, chance * 0.28)
        side_condition = str(effect.get("sideCondition") or "")
        if side_condition:
            side_weight = {"ppreduce": 0.08, "psychicterrain": 0.15}.get(side_condition, 0.25)
            risk = max(risk, chance * side_weight)
    return risk


def _choice_kills_current(choice: MoveChoice | None, target: PlannedMember) -> bool:
    return bool(choice and choice.damage and choice.damage.max_damage >= target.hp)


def _choice_risks(
    choice: MoveChoice | None,
    target: PlannedMember,
    attacker: PlannedEnemy | None = None,
    calculator: DamageCalculator | None = None,
) -> list[str]:
    if choice is None:
        return []
    risks = [f"AI likely {choice.move_name} ({choice.probability:.0%}, score {choice.score:.1f}, {choice.reason})."]
    if choice.damage:
        crit = choice.damage.ko_chance
        if crit >= 1.0:
            risks.append(f"{choice.move_name} can KO {target.name}.")
        if choice.damage.accuracy < 1:
            risks.append(f"{choice.move_name} is {choice.damage.accuracy * 100:.0f}% accurate.")
    if attacker is not None and calculator is not None and choice.move_name:
        critical = calculator.estimate_move(
            attacker.calc_set(),
            target.calc_set(),
            choice.move_name,
            DamageContext(critical=True),
        )
        if (
            critical
            and critical.max_damage >= target.hp
            and not (choice.damage and choice.damage.max_damage >= target.hp)
            and crit_rate(attacker, target, choice.move_name, calculator) > 0
        ):
            risks.append(f"Crit {choice.move_name} can KO {target.name} from {target.hp}/{target.max_hp}.")
        risks.extend(_move_secondary_risks(choice.move_name, calculator, attacker, target))
    return risks


def _branch_risk_notes(
    choices: list[MoveChoice],
    target: PlannedMember,
    attacker: PlannedEnemy,
    calculator: DamageCalculator,
) -> list[str]:
    notes: list[str] = []
    for choice in choices[1:5]:
        safety = _choice_safety(choice, target, attacker, calculator)
        if safety >= 0.82:
            continue
        if choice.damage and choice.damage.max_damage >= target.hp:
            notes.append(f"Alternate AI branch: {choice.move_name} ({choice.probability:.0%}) can KO {target.name}.")
        else:
            notes.append(
                f"Alternate AI branch: {choice.move_name} ({choice.probability:.0%}) can disrupt the line ({choice.reason})."
            )
    return notes


def _move_secondary_risks(
    move_name: str,
    calculator: DamageCalculator,
    user: PlannedMember | PlannedEnemy | None = None,
    target: PlannedMember | PlannedEnemy | None = None,
) -> list[str]:
    move = calculator.moves.get(_normalize(move_name), {})
    risks: list[str] = []
    seen: set[str] = set()
    for effect in _additional_effects(move, user):
        chance = _additional_effect_chance(effect, user, target, move)
        if chance <= 0:
            continue
        move_label = str(move.get("name", move_name))
        volatile = _normalize(str(effect.get("volatileStatus") or ""))
        if volatile == "flinch":
            risks.append(f"{move_label} has a {chance}% flinch chance if it moves first.")
        if effect.get("status"):
            risks.append(f"{move_label} has a {chance}% {_status_name(str(effect['status']))} chance.")
        if volatile == "confusion":
            risks.append(f"{move_label} has a {chance}% confusion chance.")
        if volatile == "saltcure":
            risks.append(f"{move_label} applies Salt Cure chip after it connects.")
        if volatile == "syrupbomb":
            risks.append(f"{move_label} drops Speed over the next three turns after it connects.")
        if volatile == "healblock":
            risks.append(f"{move_label} can block healing for the next turns.")
        if volatile in {"partiallytrapped", "trapped"}:
            risks.append(f"{move_label} can trap the target.")
        if volatile == "soundblock":
            risks.append(f"{move_label} can prevent sound moves for two turns.")
        drops = {stat: amount for stat, amount in (effect.get("boosts") or {}).items() if int(amount) < 0}
        if drops:
            summary = ", ".join(f"{stat} {amount}" for stat, amount in drops.items())
            risks.append(f"{move_label} has a {chance}% stat-drop chance ({summary}).")
        self_boosts = (effect.get("self") or {}).get("boosts") or {}
        if self_boosts:
            summary = ", ".join(f"{stat} +{amount}" if int(amount) > 0 else f"{stat} {amount}" for stat, amount in self_boosts.items())
            risks.append(f"{move_label} has a {chance}% self-boost chance ({summary}).")
        if effect.get("sideCondition"):
            risks.append(f"{move_label} can apply {_side_condition_label(str(effect['sideCondition']))} as an additional effect.")
    deduped: list[str] = []
    for risk in risks:
        if risk in seen:
            continue
        seen.add(risk)
        deduped.append(risk)
    return deduped


def _side_condition_label(value: str) -> str:
    return {
        "stealthrock": "Stealth Rock",
        "spikes": "Spikes",
        "psychicterrain": "Psychic Terrain",
        "ppreduce": "PP reduction",
    }.get(value, value)


def _switch_calc_text(
    enemy: PlannedEnemy,
    incoming: PlannedMember,
    chosen_into_outgoing: MoveChoice | None,
    retargeted_choice: MoveChoice | None,
    damage_taken: int,
    choices: list[MoveChoice],
) -> str:
    if chosen_into_outgoing is None:
        return f"{incoming.name} switches in safely; no known enemy action."
    odds = ", ".join(f"{item.move_name} {item.probability:.0%}" for item in choices[:3])
    move_name = retargeted_choice.move_name if retargeted_choice else chosen_into_outgoing.move_name
    return (
        f"{enemy.name} likely chooses {chosen_into_outgoing.move_name} into the outgoing slot; "
        f"{move_name} hits {incoming.name} for {damage_taken} HP "
        f"and ends at {incoming.hp}/{incoming.max_hp}. AI odds: {odds}."
    )


def _enemy_action_text(
    enemy: PlannedEnemy,
    active: PlannedMember,
    choice: MoveChoice | None,
    damage_taken: int,
    choices: list[MoveChoice],
) -> str:
    if choice is None:
        return f"{enemy.name} has no modeled action."
    odds = ", ".join(f"{item.move_name} {item.probability:.0%}" for item in choices[:3])
    if damage_taken:
        return f"{enemy.name} uses {choice.move_name} for {damage_taken}; {active.name} is {active.hp}/{active.max_hp}. AI odds: {odds}."
    return f"{enemy.name} uses {choice.move_name}. AI odds: {odds}."


def _player_action_text(active: PlannedMember, enemy: PlannedEnemy, action: PlayerAction, damage: int) -> str:
    if damage:
        return f"{active.name} uses {action.move_name} for {damage}; {enemy.name} is {enemy.hp}/{enemy.max_hp}."
    return f"{active.name} uses {action.move_name} ({action.reason})."


def _action_header(active: PlannedMember, action: PlayerAction, enemy: PlannedEnemy) -> str:
    return f"{_member_label(active)} vs {enemy.name}: click {action.move_name or 'best available move'}."


def _turn_consistency(
    active: PlannedMember,
    enemy: PlannedEnemy,
    enemy_choice: MoveChoice | None,
    action: PlayerAction,
) -> str:
    if not active.alive:
        return "failed: planned active faints"
    if not enemy.alive:
        return "progress: enemy KO'd and HP carried forward"
    if enemy_choice and enemy_choice.damage and enemy_choice.damage.ko_chance >= 1.0:
        return "risky: enemy has a KO roll"
    if action.damage and action.damage.ko_chance >= 1.0:
        return "strong: player has a KO roll"
    return "stateful: HP/status carried into next turn"


def _turn_dict(
    turn: int,
    enemy: PlannedEnemy,
    active: PlannedMember,
    action: str,
    calc: str,
    risks: list[str],
    consistency: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "turn": turn,
        "enemy": enemy.name,
        "answer": active.name,
        "action": action,
        "calc": calc,
        "risks": risks,
        "consistency": consistency,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "your_hp": f"{active.hp}/{active.max_hp}",
        "enemy_hp": f"{enemy.hp}/{enemy.max_hp}",
    }


def _member_label(member: PlannedMember) -> str:
    item = f" @ {member.item}" if member.item and not member.consumed_item else ""
    return f"{member.name}{item}"


@lru_cache(maxsize=8192)
def _normalize(value: str | None) -> str:
    return "".join(char for char in (value or "").casefold() if char.isalnum())


def _is_unknown_move(move_name: str) -> bool:
    return _normalize(move_name).startswith("unknownmove")


def _first_alive_member(team: list[PlannedMember]) -> int | None:
    for index, member in enumerate(team):
        if member.alive:
            return index
    return None


def _status_name(status: str) -> str:
    return {
        "brn": "burn",
        "par": "paralysis",
        "psn": "poison",
        "tox": "toxic",
        "slp": "sleep",
        "frz": "freeze",
    }.get(status, status)


def _has_secondary(move: dict[str, Any], effect_name: str) -> bool:
    effects = []
    if isinstance(move.get("secondary"), dict):
        effects.append(move["secondary"])
    effects.extend(item for item in (move.get("secondaries") or []) if isinstance(item, dict))
    return any(str(effect.get("volatileStatus") or effect.get("status") or "").casefold() == effect_name for effect in effects)


def _boost_multiplier(stage: int) -> float:
    if stage >= 0:
        return (2 + stage) / 2
    return 2 / (2 - stage)


# ---------------------------------------------------------------------------
# Doubles helpers
# ---------------------------------------------------------------------------

_SPREAD_TARGETS = {"allAdjacent", "allAdjacentFoes"}
_PARTNER_TARGETS = {"adjacentAlly", "allyTeam", "allies"}


def _is_spread_in_doubles(move_name: str, calculator: DamageCalculator) -> bool:
    move = calculator.moves.get(_normalize(move_name), {})
    return move.get("target") in _SPREAD_TARGETS


def _targets_partner(move_name: str, calculator: DamageCalculator) -> bool:
    move = calculator.moves.get(_normalize(move_name), {})
    return move.get("target") in _PARTNER_TARGETS


def _doubles_spread_penalty(partner: PlannedEnemy | None, move_name: str, calculator: DamageCalculator) -> float:
    """Doubles EQ/spread partner-hit penalty per the RnB AI document."""
    if partner is None or not partner.alive:
        return 0.0
    move_id = _normalize(move_name)
    move = calculator.moves.get(move_id, {})
    if move.get("target") not in _SPREAD_TARGETS:
        return 0.0
    # Look up partner types from species data
    species_data = calculator._species_data(partner.pokemon.species)  # noqa: SLF001
    partner_types: list[str] = list(species_data.get("types", [])) if species_data else []
    grounded_immune_abilities = {"levitate", "airballoon", "magnetrise"}
    partner_ability = _normalize(partner.calc_set().ability or "")
    is_flying = "Flying" in partner_types
    is_immune = partner_ability in grounded_immune_abilities or is_flying
    if is_immune:
        return 2.0 if move_id in {"earthquake", "magnitude"} else 0.5
    partner_type_ids = [t.lower() for t in partner_types]
    if any(t in {"fire", "poison", "electric", "rock"} for t in partner_type_ids):
        return -10.0
    return -3.0


def _doubles_enemy_target(
    enemy: PlannedEnemy,
    player_a: PlannedMember,
    player_b: PlannedMember | None,
    partner: PlannedEnemy | None,
    calculator: DamageCalculator,
    *,
    force_crit: bool = False,
) -> tuple[MoveChoice | None, int]:
    """Pick the best (move, player_target_slot) for an enemy mon in doubles.

    Returns (best_choice, slot) where slot 0 = player_a, slot 1 = player_b, slot -1 = spread.
    Scoring follows the RnB AI document: each move is scored vs each valid target, the
    highest (move, target) wins. Spread moves target both players simultaneously (+1 bonus).
    """
    players = [(player_a, 0), (player_b, 1)] if player_b and player_b.alive else [(player_a, 0)]
    # Single live target: no slot choice
    if len(players) == 1:
        choices = _ai_move_choices(enemy, player_a, [player_a], calculator, force_crit=force_crit, partner=partner)
        return (choices[0] if choices else None), 0

    best_score: float = -999.0
    best_choice: MoveChoice | None = None
    best_slot: int = 0

    for move_name in enemy.moves:
        move = calculator.moves.get(_normalize(move_name), {})
        move_target = move.get("target", "normal")

        # Partner-targeting moves: Helping Hand, Coaching, etc. — not aimed at players
        if move_target in _PARTNER_TARGETS or move_target == "self" or move_target in {"allySide", "allyTeam"}:
            continue

        is_spread = move_target in _SPREAD_TARGETS

        if is_spread:
            # Score the spread move: evaluate vs player_a (lead target), combine context
            choices_a = _ai_move_choices(enemy, player_a, [player_a, player_b], calculator, force_crit=force_crit, partner=partner)
            base = next((c for c in choices_a if _normalize(c.move_name) == _normalize(move_name)), None)
            if base is None:
                continue
            # Spread bonus (+1 from doc), partner penalty
            partner_mod = _doubles_spread_penalty(partner, move_name, calculator)
            spread_bonus = 1.0
            total_score = base.score + spread_bonus + partner_mod
            if total_score > best_score:
                best_score = total_score
                best_choice = MoveChoice(base.move_name, total_score, base.probability, base.damage, "spread doubles")
                best_slot = -1
        else:
            # Score vs each player target, pick best
            for player, slot in players:
                choices = _ai_move_choices(enemy, player, [player_a, player_b], calculator, force_crit=force_crit, partner=partner)
                candidate = next((c for c in choices if _normalize(c.move_name) == _normalize(move_name)), None)
                if candidate is None:
                    continue
                if candidate.score > best_score:
                    best_score = candidate.score
                    best_choice = candidate
                    best_slot = slot

    if best_choice is None:
        # Fallback: any move vs any target
        for player, slot in players:
            choices = _ai_move_choices(enemy, player, [player_a, player_b], calculator, force_crit=force_crit, partner=partner)
            if choices and choices[0].score > best_score:
                best_score = choices[0].score
                best_choice = choices[0]
                best_slot = slot

    return best_choice, best_slot


def _doubles_player_targets(
    member_a: PlannedMember,
    member_b: PlannedMember | None,
    enemy_a: PlannedEnemy,
    enemy_b: PlannedEnemy | None,
    team: list[PlannedMember],
    calculator: DamageCalculator,
) -> tuple[int, int]:
    """Return (target_for_a, target_for_b) as indices into [enemy_a, enemy_b].

    Default assignment is a→0 (enemy_a) and b→1 (enemy_b).  When one enemy slot
    is empty both converge on the survivor.  A player mon is redirected to the other
    enemy if its best action against the default target has score <= 0 and the other
    enemy is a live threatening target.
    """
    alive_b = enemy_b is not None and enemy_b.alive
    if not alive_b:
        return 0, 0

    def _score(member: PlannedMember, enemy: PlannedEnemy) -> float:
        action = _best_player_action(member, enemy, team, calculator)
        score = action.score
        # Strongly prefer securing a KO: removing an enemy halves incoming damage.
        if action.damage and action.damage.min_damage >= enemy.hp:
            score += 400.0
        elif action.damage and action.damage.max_damage >= enemy.hp:
            score += 150.0
        return score

    a0 = _score(member_a, enemy_a)
    a1 = _score(member_a, enemy_b)  # type: ignore[arg-type]
    if member_b is None or not member_b.alive:
        return (0 if a0 >= a1 else 1), 0
    b0 = _score(member_b, enemy_a)
    b1 = _score(member_b, enemy_b)  # type: ignore[arg-type]
    # Pick the assignment (split or doubled-up) with the highest combined score.
    assignments = [
        (a0 + b1, 0, 1),
        (a1 + b0, 1, 0),
        (a0 + b0, 0, 0),
        (a1 + b1, 1, 1),
    ]
    _, target_a, target_b = max(assignments, key=lambda x: x[0])
    return target_a, target_b
