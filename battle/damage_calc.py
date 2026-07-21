from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from battle.action import Action
from battle.battle_state import BattleState
from trainer_data.loader import load_trainer_battles_for_mode, normalize_game_mode
from trainer_data.models import TrainerBattle, TrainerPokemon


DEFAULT_CALC_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "calc_data.json"
DEFAULT_GEN3_CALC_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "calc_data_gen3.json"
DEFAULT_TRAINER_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "trainer_battles.json"
STAT_NAMES = {"hp", "atk", "def", "spa", "spd", "spe"}
BOOSTABLE_STATS = {"atk", "def", "spa", "spd", "spe"}
NATURE_BOOSTS: dict[str, tuple[str, str]] = {
    "Lonely": ("atk", "def"),
    "Brave": ("atk", "spe"),
    "Adamant": ("atk", "spa"),
    "Naughty": ("atk", "spd"),
    "Bold": ("def", "atk"),
    "Relaxed": ("def", "spe"),
    "Impish": ("def", "spa"),
    "Lax": ("def", "spd"),
    "Timid": ("spe", "atk"),
    "Hasty": ("spe", "def"),
    "Jolly": ("spe", "spa"),
    "Naive": ("spe", "spd"),
    "Modest": ("spa", "atk"),
    "Mild": ("spa", "def"),
    "Quiet": ("spa", "spe"),
    "Rash": ("spa", "spd"),
    "Calm": ("spd", "atk"),
    "Gentle": ("spd", "def"),
    "Sassy": ("spd", "spe"),
    "Careful": ("spd", "spa"),
}
TYPE_BOOST_ITEMS = {
    "blackbelt": "Fighting",
    "blackglasses": "Dark",
    "charcoal": "Fire",
    "dragonfang": "Dragon",
    "hardstone": "Rock",
    "magnet": "Electric",
    "metalcoat": "Steel",
    "miracleseed": "Grass",
    "mysticwater": "Water",
    "nevermeltice": "Ice",
    "poisonbarb": "Poison",
    "sharpbeak": "Flying",
    "silkscarf": "Normal",
    "silverpowder": "Bug",
    "softsand": "Ground",
    "spelltag": "Ghost",
    "twistedspoon": "Psychic",
    "pixieplate": "Fairy",
    "dreadplate": "Dark",
    "dracoplate": "Dragon",
    "earthplate": "Ground",
    "fistplate": "Fighting",
    "flameplate": "Fire",
    "icicleplate": "Ice",
    "insectplate": "Bug",
    "ironplate": "Steel",
    "meadowplate": "Grass",
    "mindplate": "Psychic",
    "skyplate": "Flying",
    "splashplate": "Water",
    "spookyplate": "Ghost",
    "stoneplate": "Rock",
    "toxicplate": "Poison",
    "zapplate": "Electric",
}
TYPE_RESIST_BERRIES = {
    "babiriberry": "Steel",
    "chartiberry": "Rock",
    "chilanberry": "Normal",
    "chopleberry": "Fighting",
    "cobaberry": "Flying",
    "colburberry": "Dark",
    "habanberry": "Dragon",
    "kasibberry": "Ghost",
    "kebiaberry": "Poison",
    "occaberry": "Fire",
    "passhoberry": "Water",
    "payapaberry": "Psychic",
    "rindoberry": "Grass",
    "roseliberry": "Fairy",
    "shucaberry": "Ground",
    "tangaberry": "Bug",
    "wacanberry": "Electric",
    "yacheberry": "Ice",
}
WEATHERLESS_ABILITIES = {"cloudnine", "airlock"}
SCREEN_IGNORING_MOVES = {"brickbreak", "psychicfangs", "defog"}
ATTACK_DROP_MOVES = {
    "growl": 1,
    "babydolleyes": 1,
    "charm": 2,
    "featherdance": 2,
    "nobleroar": 1,
    "partingshot": 1,
}
SPECIAL_ATTACK_DROP_MOVES = {
    "confide": 1,
    "eerieimpulse": 2,
    "nobleroar": 1,
    "partingshot": 1,
}
SLEEP_MOVES = {"sleeppowder", "spore", "hypnosis", "grasswhistle", "sing", "darkvoid", "yawn"}
BURN_MOVES = {"willowisp"}
POISON_MOVES = {"poisonpowder", "poisongas"}
TOXIC_MOVES = {"toxic"}
PARALYSIS_MOVES = {"stunspore", "thunderwave", "glare", "nuzzle", "zapcannon"}
SPEED_DROP_MOVES = {"cottonspore": 2, "scaryface": 2, "stringshot": 2, "electroweb": 1, "icywind": 1}
LEECH_SEED_MOVES = {"leechseed"}
PROTECT_MOVES = {"protect", "detect"}
CONFUSION_MOVES = {"confuseray", "supersonic", "sweetkiss", "swagger", "flatter", "teeterdance"}
UTILITY_SETUP_MOVES = {
    "bulkup",
    "calmmind",
    "coil",
    "dragondance",
    "growth",
    "honeclaws",
    "howl",
    "nastyplot",
    "quiverdance",
    "swordsdance",
    "workup",
}
SETUP_MOVE_BOOSTS: dict[str, dict[str, int]] = {
    "bulkup": {"atk": 1, "def": 1},
    "calmmind": {"spa": 1, "spd": 1},
    "coil": {"atk": 1, "def": 1},
    "dragondance": {"atk": 1, "spe": 1},
    "honeclaws": {"atk": 1},
    "howl": {"atk": 1},
    "nastyplot": {"spa": 2},
    "quiverdance": {"spa": 1, "spd": 1, "spe": 1},
    "shellsmash": {"atk": 2, "spa": 2, "spe": 2},
    "shiftgear": {"atk": 1, "spe": 2},
    "swordsdance": {"atk": 2},
    "workup": {"atk": 1, "spa": 1},
}


@dataclass(frozen=True)
class PokemonCalcSet:
    species: str
    level: int = 50
    nature: str | None = None
    hp: int | None = None
    max_hp: int | None = None
    evs: dict[str, int] | None = None
    ivs: dict[str, int] | None = None
    ability: str | None = None
    held_item: str | None = None
    status: str | None = None
    boosts: dict[str, int] | None = None
    gender: str | None = None
    ability_on: bool = True
    allies_fainted: int = 0
    stat_overrides: dict[str, int] | None = None


@dataclass(frozen=True)
class FieldState:
    weather: str | None = None
    terrain: str | None = None
    is_doubles: bool = False
    is_reflect: bool = False
    is_light_screen: bool = False
    is_aurora_veil: bool = False
    is_helping_hand: bool = False
    is_friend_guard: bool = False
    is_battery: bool = False
    is_power_spot: bool = False
    is_steely_spirit: bool = False
    is_flower_gift_attacker: bool = False
    is_flower_gift_defender: bool = False
    is_aura_break: bool = False
    is_fairy_aura: bool = False
    is_dark_aura: bool = False
    is_sword_of_ruin: bool = False
    is_beads_of_ruin: bool = False
    is_tablets_of_ruin: bool = False
    is_vessel_of_ruin: bool = False
    targets: int = 1


@dataclass(frozen=True)
class DamageContext:
    field: FieldState = FieldState()
    critical: bool = False
    spread: bool = False
    parental_bond: bool = False
    turn_order: str = "unknown"
    defender_is_switching: bool = False


@dataclass(frozen=True)
class DamageRange:
    move_name: str
    min_damage: int
    max_damage: int
    rolls: tuple[int, ...]
    min_percent: float
    max_percent: float
    average_percent: float
    ko_chance: float
    type_multiplier: float
    accuracy: float
    expected_damage: float
    effective_power: int
    attack_stat: int
    defense_stat: int
    modifiers: tuple[str, ...] = ()
    reason: str = ""

    @property
    def score(self) -> float:
        return self.expected_damage + self.ko_chance * 100.0


@dataclass(frozen=True)
class MovePrediction:
    action: Action
    damage: DamageRange | None
    score: float


@dataclass(frozen=True)
class KnownPokemonSet:
    pokemon: PokemonCalcSet
    moves: tuple[str, ...]


@dataclass(frozen=True)
class TrainerMatch:
    battle: TrainerBattle
    sets: tuple[KnownPokemonSet, ...]
    hp_error: int


@dataclass(frozen=True)
class MatchupRisk:
    best_move: str | None
    best_damage: DamageRange | None
    ranked: tuple[DamageRange, ...]
    safe_to_stay_in: bool
    outspeeds: bool = False
    defender_hp: int = 0


@dataclass(frozen=True)
class SwitchDecision:
    allowed: bool
    reason: str
    active_risk: MatchupRisk
    switch_risk: MatchupRisk
    active_future_value: float = 0.0
    switch_future_value: float = 0.0


def _calc_set_cache_key(s: PokemonCalcSet) -> tuple:
    """Hashable identity of everything estimate_move reads off a calc set."""
    return (
        s.species, s.level, s.nature, s.hp, s.max_hp,
        tuple(sorted(s.evs.items())) if s.evs else None,
        tuple(sorted(s.ivs.items())) if s.ivs else None,
        s.ability, s.held_item, s.status,
        tuple(sorted(s.boosts.items())) if s.boosts else None,
        s.gender, s.ability_on, s.allies_fainted,
        tuple(sorted(s.stat_overrides.items())) if s.stat_overrides else None,
    )


# The line finder / contingency flowchart replay the same deterministic sim thousands of
# times, so identical (attacker, defender, move, context) calcs repeat constantly. The
# result is a frozen DamageRange, safe to share. Capped to keep long sessions bounded.
_ESTIMATE_CACHE_MAX = 400_000
_CACHE_MISS = object()  # sentinel: None is a legitimate cached result


class DamageCalculator:
    def __init__(
        self,
        data_path: str | Path = DEFAULT_CALC_DATA_PATH,
        *,
        default_field: FieldState | None = None,
        game_mode: str = "run-and-bun",
    ):
        self.game_mode = normalize_game_mode(game_mode)
        selected_path = data_path
        if self.game_mode == "pokemon-emerald" and Path(data_path) == DEFAULT_CALC_DATA_PATH:
            selected_path = DEFAULT_GEN3_CALC_DATA_PATH
        self.data_path = Path(selected_path)
        self.data = _load_calc_data(self.data_path)
        self.species = self.data.get("species", {})
        self.species_by_num = {int(key): value for key, value in self.data.get("speciesByNum", {}).items()}
        self.moves = self.data.get("moves", {})
        self.moves_by_num = {int(key): value for key, value in self.data.get("movesByNum", {}).items()}
        self.items = self.data.get("items", {})
        self.items_by_num = {int(key): value for key, value in self.data.get("itemsByNum", {}).items()}
        self.type_chart = self.data.get("typeChart", {})
        self._trainer_battles: list[TrainerBattle] | None = None
        self._trainer_match_cache: dict[tuple[int, ...], TrainerMatch | None] = {}
        self._estimate_cache: dict[tuple, DamageRange | None] = {}
        # Planner code makes many nested estimate_move calls. A calculator-scoped field
        # keeps those calls consistent without threading the same weather/screen object
        # through every tactical helper. Explicit contexts still override this default.
        self.default_field = default_field or FieldState()

    def estimate_move(
        self,
        attacker: PokemonCalcSet,
        defender: PokemonCalcSet,
        move_name: str,
        context: DamageContext | None = None,
    ) -> DamageRange | None:
        if context is None:
            context = DamageContext(field=self.default_field)
        elif context.field == FieldState() and self.default_field != FieldState():
            context = replace(context, field=self.default_field)
        cache_key = (
            _calc_set_cache_key(attacker),
            _calc_set_cache_key(defender),
            _normalize_name(move_name),
            context,
        )
        cached = self._estimate_cache.get(cache_key, _CACHE_MISS)
        if cached is not _CACHE_MISS:
            return cached
        result = self._estimate_move_uncached(attacker, defender, move_name, context)
        if len(self._estimate_cache) >= _ESTIMATE_CACHE_MAX:
            self._estimate_cache.clear()
        self._estimate_cache[cache_key] = result
        return result

    def _estimate_move_uncached(
        self,
        attacker: PokemonCalcSet,
        defender: PokemonCalcSet,
        move_name: str,
        context: DamageContext,
    ) -> DamageRange | None:
        move = self.moves.get(_normalize_name(move_name))
        attacker_data = self._species_data(attacker.species)
        defender_data = self._species_data(defender.species)
        if not move or not attacker_data or not defender_data:
            return None
        modifiers: list[str] = []
        if move.get("category") == "Status" or int(move.get("basePower") or 0) <= 0:
            attacker, defender = _effective_ability_sets(attacker, defender, move, modifiers)
            if _status_move_blocked_by_ability(move, attacker, defender):
                return DamageRange(
                    move.get("name", move_name),
                    0,
                    0,
                    tuple([0] * 16),
                    0,
                    0,
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                    0,
                    0,
                    modifiers=("Good as Gold",),
                    reason="status_blocked_by_ability",
                )
            return DamageRange(
                move.get("name", move_name),
                0,
                0,
                tuple([0] * 16),
                0,
                0,
                0,
                0,
                1,
                1,
                0,
                0,
                0,
                0,
                reason="status_or_zero_power",
            )

        category = str(move["category"])
        attacker, defender = _effective_ability_sets(attacker, defender, move, modifiers)
        attack_stat = self._attack_stat_for_move(move, category)
        defense_stat = self._defense_stat_for_move(move, category)
        level = max(1, attacker.level)
        is_critical = (context.critical or _is_forced_crit(attacker, defender)) and _can_crit(attacker, defender)
        if context.critical and not is_critical:
            modifiers.append("crit blocked")
        attack = self._effective_stat(
            attacker,
            attacker_data,
            attack_stat,
            is_attacker=True,
            critical=is_critical,
            category=category,
            context=context,
            modifiers=modifiers,
        )
        defense = self._effective_stat(
            defender,
            defender_data,
            defense_stat,
            is_attacker=False,
            critical=is_critical,
            category=category,
            context=context,
            modifiers=modifiers,
        )
        move_id = _normalize_name(str(move["name"]))
        if move_id in {"explosion", "selfdestruct", "mistyexplosion"}:
            defense = max(1, defense // 2)
            modifiers.append("RnB self-KO defense halved")
        power = self._effective_power(move, attacker, defender, attack, defense, context, modifiers)
        base = math.floor(math.floor(math.floor((2 * level / 5 + 2) * power * attack / defense) / 50) + 2)

        move_type = str(move["type"])
        if _normalize_name(str(move["name"])).startswith("hiddenpower"):
            move_type = _hidden_power_type(attacker.ivs)
            modifiers.append(f"Hidden Power {move_type}")
        move_type = _ability_move_type(move_type, move, category, attacker.ability, modifiers)
        move_type = _ate_ability_type(move_type, category, attacker.ability, modifiers)
        if move_type == "???":
            move_type = "Normal"
        stab = self._stab(move_type, attacker_data, attacker.ability, modifiers)
        type_multiplier = self._type_multiplier(move_type, defender_data.get("types", []))
        if (
            type_multiplier == 0.0
            and move_type in {"Normal", "Fighting"}
            and "Ghost" in defender_data.get("types", [])
            and _normalized(attacker.ability) in {"scrappy", "mindseye"}
        ):
            type_multiplier = 1.0
            modifiers.append(attacker.ability or "Scrappy")
        if self._immune_by_item_or_ability(move_type, defender, modifiers):
            type_multiplier = 0.0
        move_for_accuracy = dict(move)
        move_for_accuracy["_attacker_types"] = tuple(attacker_data.get("types", ()))
        accuracy = _modified_accuracy(move.get("accuracy", 100), attacker, defender, move_for_accuracy, context.field, modifiers)
        modifier = self._final_modifier(
            move,
            move_type,
            category,
            attacker,
            defender,
            attacker_data,
            defender_data,
            stab,
            type_multiplier,
            DamageContext(
                field=context.field,
                critical=is_critical,
                spread=context.spread,
                parental_bond=context.parental_bond,
                turn_order=context.turn_order,
                defender_is_switching=context.defender_is_switching,
            ),
            modifiers,
        )
        rolls = tuple(max(1, math.floor(base * modifier * random_factor / 100)) for random_factor in range(85, 101))
        if type_multiplier == 0.0 or modifier == 0.0:
            rolls = tuple([0] * 16)
        max_hp = defender.max_hp or self._stat(defender_data, "hp", defender.level, defender.nature, defender.evs, defender.ivs)
        current_hp = defender.hp or max_hp
        if max_hp <= 0 or current_hp <= 0:
            return None
        rolls = self._apply_survival_items(rolls, defender, current_hp, max_hp, modifiers)
        ko_rolls = sum(roll >= current_hp for roll in rolls)
        average_damage = sum(rolls) / len(rolls)
        ko_chance = ko_rolls / len(rolls) * accuracy
        if _normalized(defender.held_item) == "focusband" and ko_chance > 0:
            ko_chance *= 0.9
            modifiers.append("Focus Band")
        return DamageRange(
            move_name=str(move["name"]),
            min_damage=min(rolls),
            max_damage=max(rolls),
            rolls=rolls,
            min_percent=min(rolls) / max_hp,
            max_percent=max(rolls) / max_hp,
            average_percent=average_damage / max_hp,
            ko_chance=ko_chance,
            type_multiplier=type_multiplier,
            accuracy=accuracy,
            expected_damage=average_damage * accuracy,
            effective_power=power,
            attack_stat=attack,
            defense_stat=defense,
            modifiers=tuple(modifiers),
        )

    def _attack_stat_for_move(self, move: dict[str, Any], category: str) -> str:
        move_id = _normalize_name(str(move["name"]))
        if move_id == "bodypress":
            return "def"
        return "atk" if category == "Physical" else "spa"

    def _defense_stat_for_move(self, move: dict[str, Any], category: str) -> str:
        override = move.get("overrideDefensiveStat")
        if override in BOOSTABLE_STATS:
            return str(override)
        return "def" if category == "Physical" else "spd"

    def predict_actions(self, state: BattleState, actions: list[tuple[Action, ...]]) -> list[MovePrediction]:
        predictions: list[MovePrediction] = []
        for turn_action in actions:
            score = 0.0
            best_damage: DamageRange | None = None
            for index, action in enumerate(turn_action):
                if not action.is_move:
                    continue
                attacker_slot = index if state.is_doubles and index < 2 else 0
                defender_slot = action.target_slot if action.target_slot is not None else _first_live_slot(state.enemy_fainted, state.enemy_max_hp)
                if defender_slot is None:
                    continue
                move_name = _move_name_for_slot(state, attacker_slot, action.move_slot)
                attacker = self._pokemon_from_state(state, "player", attacker_slot)
                defender = self._pokemon_from_state(state, "enemy", defender_slot)
                context = DamageContext(
                    field=FieldState(is_doubles=state.is_doubles),
                    spread=state.is_doubles and _is_spread_move(self.moves.get(_normalize_name(move_name), {})),
                )
                damage = self.estimate_move(attacker, defender, move_name, context)
                if damage is None:
                    continue
                move_score = damage.score
                if damage.reason == "status_or_zero_power":
                    move_score = self.status_move_score(state, move_name, attacker_slot)
                score += move_score
                if best_damage is None or damage.score > best_damage.score:
                    best_damage = damage
            predictions.append(MovePrediction(turn_action[0], best_damage, score))
        return predictions

    def priority_key(self, state: BattleState, turn_action: tuple[Action, ...]) -> tuple[float, float, int]:
        predictions = self.predict_actions(state, [turn_action])
        prediction_score = predictions[0].score if predictions else 0.0
        best_damage = predictions[0].damage if predictions else None
        active_risk = self.enemy_peak_risk_to_player_slot(state, 0)

        switch_bonus = 0.0
        switch_survival = 0.0
        for action in turn_action:
            if not action.is_switch or action.switch_target is None:
                continue
            decision = self.switch_decision(state, action.switch_target)
            if not decision.allowed:
                return (-10000.0, 0.0, 0)
            switch_bonus = max(switch_bonus, _switch_relief_score(decision))
            switch_survival = max(
                switch_survival,
                1.0 - (decision.switch_risk.best_damage.ko_chance if decision.switch_risk.best_damage else 0.0),
            )

        danger_penalty = 0.0
        if active_risk.best_damage is not None:
            if active_risk.outspeeds and active_risk.best_damage.ko_chance >= 1.0:
                danger_penalty = 500.0
            elif active_risk.outspeeds and active_risk.best_damage.ko_chance > 0:
                danger_penalty = 180.0 * active_risk.best_damage.ko_chance

        if any(action.is_switch for action in turn_action):
            return (prediction_score + switch_bonus, switch_survival, int(switch_bonus))
        damage = best_damage
        if damage is None:
            return (-danger_penalty, 0.0, 0)
        return (prediction_score - danger_penalty, damage.ko_chance, damage.max_damage)

    def status_move_score(self, state: BattleState, move_name: str, active_slot: int = 0) -> float:
        move_id = _normalize_name(move_name)
        move = self.moves.get(move_id, {})
        if move and move.get("category") != "Status":
            return 0.0
        active_risk = self.enemy_peak_risk_to_player_slot(state, active_slot)
        best_enemy = active_risk.best_damage
        best_enemy_move = self.moves.get(_normalize_name(active_risk.best_move or ""), {})
        enemy_category = str(best_enemy_move.get("category", ""))
        enemy_damage = best_enemy.max_percent if best_enemy else 0.0
        player_damage = self.player_best_damage_to_active_enemy(state, active_slot)
        player_damage_pct = player_damage.max_percent if player_damage else 0.0
        score = 0.0

        if move_id in SLEEP_MOVES:
            score = 85.0
            if player_damage_pct < 0.25:
                score += 20.0
            if best_enemy and best_enemy.ko_chance > 0:
                score += 20.0
        elif move_id in BURN_MOVES and enemy_category == "Physical":
            score = 55.0 + enemy_damage * 80.0
        elif move_id in ATTACK_DROP_MOVES and enemy_category == "Physical":
            stages = ATTACK_DROP_MOVES[move_id]
            score = 25.0 + stages * 20.0 + enemy_damage * 55.0
            if enemy_damage < 0.35:
                score += 15.0
            if player_damage_pct < 0.25:
                score += 10.0
        elif move_id in SPECIAL_ATTACK_DROP_MOVES and enemy_category == "Special":
            stages = SPECIAL_ATTACK_DROP_MOVES[move_id]
            score = 25.0 + stages * 20.0 + enemy_damage * 55.0
            if enemy_damage < 0.35:
                score += 15.0
            if player_damage_pct < 0.25:
                score += 10.0
        elif move_id in PARALYSIS_MOVES:
            score = 35.0
            if active_risk.outspeeds:
                score += 35.0
            if player_damage_pct < 0.25:
                score += 10.0
        elif move_id in TOXIC_MOVES:
            score = 30.0
            if player_damage_pct < 0.25:
                score += 25.0
            if enemy_damage < 0.45:
                score += 20.0
        elif move_id in POISON_MOVES:
            score = 22.0
            if player_damage_pct < 0.25:
                score += 18.0
            if enemy_damage < 0.35:
                score += 12.0
        elif move_id in SPEED_DROP_MOVES:
            score = 25.0 + SPEED_DROP_MOVES[move_id] * 12.0
            if active_risk.outspeeds:
                score += 30.0
        elif move_id == "reflect" and enemy_category == "Physical":
            score = 35.0 + enemy_damage * 65.0
        elif move_id == "lightscreen" and enemy_category == "Special":
            score = 35.0 + enemy_damage * 65.0
        elif move_id == "auroraveil":
            score = 35.0 + enemy_damage * 65.0
        elif move_id == "leechseed":
            score = 30.0
            if player_damage_pct < 0.25:
                score += 25.0
            if enemy_damage < 0.4:
                score += 15.0
        elif move_id in PROTECT_MOVES:
            score = 18.0
            if best_enemy and best_enemy.ko_chance > 0:
                score += 12.0
        elif move_id in CONFUSION_MOVES:
            score = 20.0
            if player_damage_pct < 0.25:
                score += 10.0
        elif move_id in UTILITY_SETUP_MOVES:
            score = 20.0
            if enemy_damage < 0.35:
                score += 25.0
            if player_damage_pct < 0.25:
                score += 20.0

        if best_enemy and best_enemy.ko_chance >= 1.0 and not move_id in SLEEP_MOVES:
            score -= 80.0
        return max(0.0, score)

    def switch_decision(self, state: BattleState, target_slot: int) -> SwitchDecision:
        active_risk = self.enemy_peak_risk_to_player_slot(state, 0)
        switch_risk = self.enemy_peak_risk_to_player_slot(state, target_slot)
        active_future_value = self.future_defensive_value(state, 0)
        switch_future_value = self.future_defensive_value(state, target_slot)
        active_damage = active_risk.best_damage.max_percent if active_risk.best_damage else 0.0
        risk_relief = _risk_score(active_risk) - _risk_score(switch_risk)
        active_best_damage = self.player_best_damage_to_active_enemy(state, 0)
        switch_best_damage = self.player_best_damage_to_active_enemy(state, target_slot)
        active_damage_out = active_best_damage.max_percent if active_best_damage else 0.0
        switch_damage_out = switch_best_damage.max_percent if switch_best_damage else 0.0

        if _risk_can_ohko(active_risk) and _risk_score(switch_risk) + 50.0 < _risk_score(active_risk):
            return SwitchDecision(
                True,
                "current_enemy_can_ohko",
                active_risk,
                switch_risk,
                active_future_value,
                switch_future_value,
            )

        bad_damage_under_pressure = (
            active_best_damage is not None
            and active_damage_out < 0.25
            and active_damage >= 0.45
            and (
                risk_relief > 40.0
                or switch_damage_out >= active_damage_out + 0.20
            )
            and not _risk_can_ohko(switch_risk)
        )
        if bad_damage_under_pressure:
            return SwitchDecision(
                True,
                "bad_damage_under_pressure",
                active_risk,
                switch_risk,
                active_future_value,
                switch_future_value,
            )

        should_preserve = (
            active_future_value >= switch_future_value + 0.25
            and active_future_value >= 0.55
            and active_damage >= 0.25
            and risk_relief > 25.0
        )
        if should_preserve:
            return SwitchDecision(
                True,
                "preserve_active_for_later",
                active_risk,
                switch_risk,
                active_future_value,
                switch_future_value,
            )

        return SwitchDecision(
            False,
            "prefer_highest_damage_move",
            active_risk,
            switch_risk,
            active_future_value,
            switch_future_value,
        )

    def player_best_damage_to_active_enemy(self, state: BattleState, player_slot: int) -> DamageRange | None:
        enemy_slot = _first_live_slot(state.enemy_fainted, state.enemy_max_hp)
        if enemy_slot is None:
            return None
        return self.player_best_damage_to_enemy_slot(state, player_slot, enemy_slot)

    def player_best_damage_to_enemy_slot(
        self,
        state: BattleState,
        player_slot: int,
        enemy_slot: int,
    ) -> DamageRange | None:
        move_names = _player_move_names_for_slot(state, player_slot)
        if not move_names:
            return None
        attacker = self._pokemon_from_state(state, "player", player_slot)
        defender = self._pokemon_from_state(state, "enemy", enemy_slot)
        ranked = self.rank_move_names(
            attacker,
            defender,
            move_names,
            DamageContext(field=FieldState(is_doubles=state.is_doubles)),
        )
        damaging = [item for item in ranked if item.max_damage > 0]
        return damaging[0] if damaging else None

    def enemy_risk_to_player_slot(
        self,
        state: BattleState,
        player_slot: int,
        enemy_boosts: dict[str, int] | None = None,
    ) -> MatchupRisk:
        enemy_slot = _first_live_slot(state.enemy_fainted, state.enemy_max_hp)
        if enemy_slot is None:
            return MatchupRisk(None, None, tuple(), True, False, 0)
        return self.enemy_risk_to_player_slot_from_enemy_slot(
            state,
            enemy_slot,
            player_slot,
            enemy_boosts,
        )

    def enemy_peak_risk_to_player_slot(self, state: BattleState, player_slot: int) -> MatchupRisk:
        risks = [self.enemy_risk_to_player_slot(state, player_slot)]
        for boosts in self._enemy_setup_scenarios(state):
            risks.append(self.enemy_risk_to_player_slot(state, player_slot, boosts))
        return max(risks, key=_risk_score)

    def future_defensive_value(self, state: BattleState, player_slot: int) -> float:
        values: list[float] = []
        for enemy_slot, max_hp in enumerate(state.enemy_max_hp):
            if max_hp <= 0:
                continue
            if enemy_slot < len(state.enemy_fainted) and state.enemy_fainted[enemy_slot]:
                continue
            if enemy_slot == _first_live_slot(state.enemy_fainted, state.enemy_max_hp):
                continue
            risk = self.enemy_risk_to_player_slot_from_enemy_slot(state, enemy_slot, player_slot)
            if risk.best_damage is None:
                continue
            values.append(max(0.0, 1.0 - risk.best_damage.max_percent))
        return max(values, default=0.0)

    def enemy_risk_to_player_slot_from_enemy_slot(
        self,
        state: BattleState,
        enemy_slot: int,
        player_slot: int,
        enemy_boosts: dict[str, int] | None = None,
    ) -> MatchupRisk:
        enemy_set = self._enemy_known_set(state, enemy_slot)
        if enemy_set is None or not enemy_set.moves:
            return MatchupRisk(None, None, tuple(), True, False, 0)
        defender = self._pokemon_from_state(state, "player", player_slot)
        attacker = enemy_set.pokemon
        if enemy_boosts:
            merged = dict(attacker.boosts or {})
            merged.update(enemy_boosts)
            attacker = replace(attacker, boosts=merged)
        ranked = self.rank_move_names(
            attacker,
            defender,
            enemy_set.moves,
            DamageContext(
                field=FieldState(is_doubles=state.is_doubles),
                turn_order="first" if _speed_guess(attacker, self) >= _speed_guess(defender, self) else "last",
            ),
        )
        best = ranked[0] if ranked else None
        outspeeds = bool(best and _speed_guess(attacker, self) >= _speed_guess(defender, self))
        safe = best is None or not (outspeeds and best.ko_chance >= 1.0)
        return MatchupRisk(
            best_move=best.move_name if best is not None else None,
            best_damage=best,
            ranked=ranked,
            safe_to_stay_in=safe,
            outspeeds=outspeeds,
            defender_hp=defender.hp or defender.max_hp or 0,
        )

    def matched_trainer(self, state: BattleState) -> TrainerMatch | None:
        fingerprint = tuple(value for value in state.enemy_max_hp if value > 0)
        if fingerprint in self._trainer_match_cache:
            return self._trainer_match_cache[fingerprint]
        best: TrainerMatch | None = None
        for battle in self._load_trainer_battles():
            if len(battle.party) != len(fingerprint):
                continue
            sets = tuple(self._known_set_from_trainer_mon(mon) for mon in battle.party)
            expected = tuple(item.pokemon.max_hp or 0 for item in sets)
            hp_error = sum(abs(a - b) for a, b in zip(expected, fingerprint))
            if best is None or hp_error < best.hp_error:
                best = TrainerMatch(battle=battle, sets=sets, hp_error=hp_error)
        if best is not None and best.hp_error > max(2, len(fingerprint)):
            best = None
        self._trainer_match_cache[fingerprint] = best
        return best

    def rank_move_names(
        self,
        attacker: PokemonCalcSet,
        defender: PokemonCalcSet,
        move_names: list[str] | tuple[str, ...],
        context: DamageContext | None = None,
    ) -> tuple[DamageRange, ...]:
        ranges = [
            damage
            for move_name in move_names
            if (damage := self.estimate_move(attacker, defender, move_name, context)) is not None
        ]
        return tuple(sorted(ranges, key=lambda damage: damage.score, reverse=True))

    def stay_in_risk(
        self,
        attacker: PokemonCalcSet,
        defender: PokemonCalcSet,
        move_names: list[str] | tuple[str, ...],
        context: DamageContext | None = None,
        max_acceptable_ko_chance: float = 0.0,
    ) -> MatchupRisk:
        ranked = self.rank_move_names(attacker, defender, move_names, context)
        best = ranked[0] if ranked else None
        safe = best is None or best.ko_chance <= max_acceptable_ko_chance
        return MatchupRisk(
            best_move=best.move_name if best is not None else None,
            best_damage=best,
            ranked=ranked,
            safe_to_stay_in=safe,
        )

    def _pokemon_from_state(self, state: BattleState, side: str, slot: int) -> PokemonCalcSet:
        names = state.player_names if side == "player" else state.enemy_names
        species_ids = state.player_species if side == "player" else state.enemy_species
        hp_values = state.player_hp if side == "player" else state.enemy_hp
        max_hp_values = state.player_max_hp if side == "player" else state.enemy_max_hp
        species_name = names[slot] if slot < len(names) else ""
        if slot < len(species_ids) and species_ids[slot] in self.species_by_num:
            species_name = self.species_by_num[species_ids[slot]]
        if not self._species_data(species_name):
            species_name = _best_species_guess(names[slot] if slot < len(names) else "", self.species)
        max_hp = max_hp_values[slot] if slot < len(max_hp_values) else None
        hp = hp_values[slot] if slot < len(hp_values) else max_hp
        return PokemonCalcSet(
            species=species_name or "missingno",
            level=_estimate_level(species_name, max_hp, self.species) or 50,
            hp=hp,
            max_hp=max_hp,
        )

    def _enemy_known_set(self, state: BattleState, enemy_slot: int) -> KnownPokemonSet | None:
        match = self.matched_trainer(state)
        if match is None or enemy_slot >= len(match.sets):
            return None
        known = match.sets[enemy_slot]
        hp = state.enemy_hp[enemy_slot] if enemy_slot < len(state.enemy_hp) else known.pokemon.hp
        max_hp = state.enemy_max_hp[enemy_slot] if enemy_slot < len(state.enemy_max_hp) else known.pokemon.max_hp
        return KnownPokemonSet(
            pokemon=PokemonCalcSet(**{**known.pokemon.__dict__, "hp": hp, "max_hp": max_hp}),
            moves=known.moves,
        )

    def _enemy_setup_scenarios(self, state: BattleState) -> list[dict[str, int]]:
        enemy_slot = _first_live_slot(state.enemy_fainted, state.enemy_max_hp)
        if enemy_slot is None:
            return []
        enemy_set = self._enemy_known_set(state, enemy_slot)
        if enemy_set is None:
            return []
        scenarios: list[dict[str, int]] = []
        for move_name in enemy_set.moves:
            boosts = SETUP_MOVE_BOOSTS.get(_normalize_name(move_name))
            if not boosts:
                continue
            scenarios.append(dict(boosts))
            scenarios.append({stat: min(6, value * 2) for stat, value in boosts.items()})
        return scenarios

    def _known_set_from_trainer_mon(self, mon: TrainerPokemon) -> KnownPokemonSet:
        species = mon.species
        level = mon.level or 50
        species_data = self._species_data(species)
        exact_stats = mon.exact_stats or {}
        max_hp = exact_stats.get("hp")
        if max_hp is None and species_data:
            max_hp = self._stat(species_data, "hp", level, mon.nature, None, None)
        return KnownPokemonSet(
            pokemon=PokemonCalcSet(
                species=species,
                level=level,
                nature=mon.nature,
                max_hp=max_hp,
                hp=max_hp,
                ability=mon.ability,
                held_item=mon.held_item,
                stat_overrides=exact_stats or None,
            ),
            moves=tuple(mon.moves),
        )

    def _load_trainer_battles(self) -> list[TrainerBattle]:
        if self._trainer_battles is None:
            try:
                self._trainer_battles = load_trainer_battles_for_mode(self.game_mode)
            except (OSError, ValueError, KeyError):
                self._trainer_battles = []
        return self._trainer_battles

    def _species_data(self, species_name: str) -> dict[str, Any] | None:
        return self.species.get(_normalize_name(species_name))

    def _effective_stat(
        self,
        pokemon: PokemonCalcSet,
        species: dict[str, Any],
        stat_name: str,
        *,
        is_attacker: bool,
        critical: bool,
        category: str,
        context: DamageContext,
        modifiers: list[str],
    ) -> int:
        value = int((pokemon.stat_overrides or {}).get(stat_name) or 0)
        if value <= 0:
            value = self._stat(species, stat_name, pokemon.level, pokemon.nature, pokemon.evs, pokemon.ivs)
        stage = max(-6, min(6, (pokemon.boosts or {}).get(stat_name, 0)))
        if critical and ((is_attacker and stage < 0) or (not is_attacker and stage > 0)):
            stage = 0
            modifiers.append(f"crit_ignored_{stat_name}_boost")
        value = math.floor(value * _boost_multiplier(stage))
        ability = _normalized(pokemon.ability)
        item = _normalized(pokemon.held_item)
        species_id = _normalize_name(str(species.get("name", pokemon.species)))
        if is_attacker:
            if stat_name == "atk" and ability in {"hugepower", "purepower"}:
                value *= 2
                modifiers.append(pokemon.ability or "Huge Power")
            if stat_name == "atk" and ability == "hustle" and category == "Physical":
                value = math.floor(value * 1.5)
                modifiers.append("Hustle")
            if stat_name in {"atk", "spa"} and ability == "defeatist" and pokemon.hp is not None and pokemon.max_hp and pokemon.hp <= pokemon.max_hp / 2:
                value = math.floor(value * 0.5)
                modifiers.append("Defeatist")
            if stat_name == "atk" and ability == "slowstart" and pokemon.ability_on and category == "Physical":
                value = math.floor(value * 0.5)
                modifiers.append("Slow Start")
            if stat_name == "atk" and ability == "gorillatactics" and category == "Physical":
                value = math.floor(value * 1.5)
                modifiers.append("Gorilla Tactics")
            if stat_name == "atk" and ability == "orichalcumpulse" and _effective_weather(context.field, pokemon, pokemon) in {"sun", "harshsun"}:
                value = math.floor(value * 4 / 3)
                modifiers.append("Orichalcum Pulse")
            if stat_name == "atk" and ability == "guts" and pokemon.status:
                value = math.floor(value * 1.5)
                modifiers.append("Guts")
            if stat_name == "spa" and ability == "solarpower" and _effective_weather(context.field, pokemon, pokemon) in {"sun", "harshsun"}:
                value = math.floor(value * 1.5)
                modifiers.append("Solar Power")
            if stat_name == "spa" and ability == "hadronengine" and context.field.terrain == "electric":
                value = math.floor(value * 4 / 3)
                modifiers.append("Hadron Engine")
            if stat_name == "atk" and ability == "flowergift" and _effective_weather(context.field, pokemon, pokemon) in {"sun", "harshsun"}:
                value = math.floor(value * 1.5)
                modifiers.append("Flower Gift")
            if stat_name == "spa" and ability in {"plus", "minus"} and pokemon.ability_on:
                value = math.floor(value * 1.5)
                modifiers.append(pokemon.ability or "Plus/Minus")
            if _protosynthesis_boosts_stat(pokemon, species, stat_name, context.field):
                value = math.floor(value * (1.5 if stat_name == "spe" else 1.3))
                modifiers.append(pokemon.ability or "Protosynthesis/Quark Drive")
            if stat_name == "atk" and item == "choiceband":
                value = math.floor(value * 1.5)
                modifiers.append("Choice Band")
            if stat_name == "spa" and item == "choicespecs":
                value = math.floor(value * 1.5)
                modifiers.append("Choice Specs")
            if item == "lightball" and species_id == "pikachu" and stat_name in {"atk", "spa"}:
                value *= 2
                modifiers.append("Light Ball")
            if item == "thickclub" and species_id in {"cubone", "marowak", "marowakalola"} and stat_name == "atk":
                value *= 2
                modifiers.append("Thick Club")
            if item == "deepseatooth" and species_id == "clamperl" and stat_name == "spa":
                value *= 2
                modifiers.append("Deep Sea Tooth")
            if item == "souldew" and species_id in {"latios", "latias"} and stat_name == "spa":
                value = math.floor(value * 1.5)
                modifiers.append("Soul Dew")
        else:
            if stat_name == "def" and ability == "furcoat":
                value *= 2
                modifiers.append("Fur Coat")
            if stat_name == "def" and ability == "marvelscale" and pokemon.status:
                value = math.floor(value * 1.5)
                modifiers.append("Marvel Scale")
            if stat_name == "def" and ability == "grasspelt" and context.field.terrain == "grassy":
                value = math.floor(value * 1.5)
                modifiers.append("Grass Pelt")
            if stat_name == "spd" and ability == "icescales":
                value *= 2
                modifiers.append("Ice Scales")
            if stat_name == "spd" and ability == "flowergift" and _effective_weather(context.field, pokemon, pokemon) in {"sun", "harshsun"}:
                value = math.floor(value * 1.5)
                modifiers.append("Flower Gift")
            if stat_name == "spd" and item == "assaultvest":
                value = math.floor(value * 1.5)
                modifiers.append("Assault Vest")
            if item == "eviolite" and species.get("evos") and stat_name in {"def", "spd"}:
                value = math.floor(value * 1.5)
                modifiers.append("Eviolite")
            if item == "deepseascale" and species_id == "clamperl" and stat_name == "spd":
                value *= 2
                modifiers.append("Deep Sea Scale")
            if item == "souldew" and species_id in {"latios", "latias"} and stat_name == "spd":
                value = math.floor(value * 1.5)
                modifiers.append("Soul Dew")
            if _protosynthesis_boosts_stat(pokemon, species, stat_name, context.field):
                value = math.floor(value * (1.5 if stat_name == "spe" else 1.3))
                modifiers.append(pokemon.ability or "Protosynthesis/Quark Drive")
        return max(1, value)

    def _effective_power(
        self,
        move: dict[str, Any],
        attacker: PokemonCalcSet,
        defender: PokemonCalcSet,
        attack: int,
        defense: int,
        context: DamageContext,
        modifiers: list[str],
    ) -> int:
        power = int(move["basePower"])
        move_id = _normalize_name(str(move["name"]))
        if move_id in {"facade"} and attacker.status in {"burn", "poison", "toxic", "paralysis"}:
            power *= 2
            modifiers.append("Facade")
        if move_id in {"hex", "infernalparade"} and defender.status:
            power *= 2
            modifiers.append(move["name"])
        if move_id == "venoshock" and defender.status in {"poison", "toxic"}:
            power *= 2
            modifiers.append("Venoshock")
        if move_id == "brine" and defender.hp is not None and defender.max_hp and defender.hp <= defender.max_hp / 2:
            power *= 2
            modifiers.append("Brine")
        if move_id == "retaliate" and attacker.allies_fainted > 0:
            power *= 2
            modifiers.append("Retaliate ally fainted")
        if move_id == "pursuit" and context.defender_is_switching:
            power *= 2
            modifiers.append("Pursuit switch")
        if move_id == "acrobatics" and not attacker.held_item:
            power *= 2
            modifiers.append("Acrobatics no item")
        if move_id == "storedpower":
            power = 20 + 20 * sum(max(0, stage) for stage in (attacker.boosts or {}).values())
            modifiers.append("Stored Power")
        if move_id == "punishment":
            power = min(200, 60 + 20 * sum(max(0, stage) for stage in (defender.boosts or {}).values()))
            modifiers.append("Punishment")
        if move_id == "electroball":
            power = _electro_ball_power(_speed_guess(attacker, self), _speed_guess(defender, self))
            modifiers.append("Electro Ball")
        if move_id == "gyroball":
            power = min(150, max(1, math.floor(25 * _speed_guess(defender, self) / max(1, _speed_guess(attacker, self))) + 1))
            modifiers.append("Gyro Ball")
        if move_id == "fling":
            power = _fling_power(attacker.held_item)
            modifiers.append("Fling")
        if move_id == "weatherball" and _effective_weather(context.field, attacker, defender):
            power = 100
            modifiers.append("Weather Ball")
        ability = _normalized(attacker.ability)
        if ability == "normalize":
            power = math.floor(power * 1.2)
            modifiers.append("Normalize power")
        if ability == "technician" and power <= 60:
            power = math.floor(power * 1.5)
            modifiers.append("Technician")
        if ability == "flareboost" and attacker.status == "burn" and str(move["category"]) == "Special":
            power = math.floor(power * 1.5)
            modifiers.append("Flare Boost")
        if ability == "toxicboost" and attacker.status in {"poison", "toxic"} and str(move["category"]) == "Physical":
            power = math.floor(power * 1.5)
            modifiers.append("Toxic Boost")
        if ability in {"aerilate", "dragonize", "galvanize", "pixilate", "refrigerate"} and str(move["type"]) == "Normal":
            power = math.floor(power * 1.2)
            modifiers.append(f"{attacker.ability} power")
        if ability == "reckless" and (move.get("recoil") or move.get("hasCrashDamage")):
            power = math.floor(power * 1.2)
            modifiers.append("Reckless")
        if ability == "ironfist" and _has_flag(move, "punch"):
            power = math.floor(power * 1.2)
            modifiers.append("Iron Fist")
        if ability == "strongjaw" and _has_flag(move, "bite"):
            power = math.floor(power * 1.5)
            modifiers.append("Strong Jaw")
        if ability == "megalauncher" and _has_flag(move, "pulse"):
            power = math.floor(power * 1.5)
            modifiers.append("Mega Launcher")
        if ability == "toughclaws" and _has_flag(move, "contact"):
            power = math.floor(power * 1.3)
            modifiers.append("Tough Claws")
        if ability == "sharpness" and _has_flag(move, "slicing"):
            power = math.floor(power * 1.5)
            modifiers.append("Sharpness")
        if ability == "steelyspirit" and str(move["type"]) == "Steel":
            power = math.floor(power * 1.5)
            modifiers.append("Steely Spirit")
        if ability == "sandforce" and _effective_weather(context.field, attacker, defender) == "sand" and str(move["type"]) in {"Rock", "Ground", "Steel"}:
            power = math.floor(power * 1.3)
            modifiers.append("Sand Force")
        if ability == "analytic" and (context.turn_order != "first" or context.defender_is_switching):
            power = math.floor(power * 1.3)
            modifiers.append("Analytic")
        if ability == "punkrock" and _has_flag(move, "sound"):
            power = math.floor(power * 1.3)
            modifiers.append("Punk Rock")
        if ability == "sheerforce" and (move.get("secondaries") or _has_flag(move, "sheerforce")):
            power = math.floor(power * 1.3)
            modifiers.append("Sheer Force")
        return max(1, power)

    def _stab(self, move_type: str, attacker_data: dict[str, Any], ability: str | None, modifiers: list[str]) -> float:
        if move_type not in attacker_data.get("types", []):
            return 1.0
        if _normalized(ability) == "adaptability":
            modifiers.append("Adaptability")
            return 2.0
        return 1.5

    def _final_modifier(
        self,
        move: dict[str, Any],
        move_type: str,
        category: str,
        attacker: PokemonCalcSet,
        defender: PokemonCalcSet,
        attacker_data: dict[str, Any],
        defender_data: dict[str, Any],
        stab: float,
        type_multiplier: float,
        context: DamageContext,
        modifiers: list[str],
    ) -> float:
        modifier = stab * type_multiplier
        weather = _effective_weather(context.field, attacker, defender)
        if weather in {"sun", "harshsun"}:
            if move_type == "Fire":
                modifier *= 1.5
                modifiers.append("sun")
            elif move_type == "Water":
                modifier *= 0.5
                modifiers.append("sun water drop")
        elif weather in {"rain", "heavyrain"}:
            if move_type == "Water":
                modifier *= 1.5
                modifiers.append("rain")
            elif move_type == "Fire":
                modifier *= 0.5
                modifiers.append("rain fire drop")
        if context.field.terrain == "electric" and move_type == "Electric":
            modifier *= 1.5
            modifiers.append("Electric Terrain")
        if context.field.terrain == "grassy" and move_type == "Grass":
            modifier *= 1.5
            modifiers.append("Grassy Terrain")
        if context.field.terrain == "psychic" and move_type == "Psychic":
            modifier *= 1.5
            modifiers.append("Psychic Terrain")
        if context.field.terrain == "misty" and move_type == "Dragon":
            modifier *= 0.5
            modifiers.append("Misty Terrain")
        if context.spread or (context.field.is_doubles and _is_spread_move(move)):
            modifier *= 0.5 if self.game_mode == "pokemon-emerald" else 0.75
            modifiers.append("spread")
        if context.field.is_helping_hand:
            modifier *= 1.5
            modifiers.append("Helping Hand")
        if context.field.is_friend_guard:
            modifier *= 0.75
            modifiers.append("Friend Guard")
        if context.field.is_battery and category == "Special":
            modifier *= 1.3
            modifiers.append("Battery")
        if context.field.is_power_spot:
            modifier *= 1.3
            modifiers.append("Power Spot")
        if context.field.is_steely_spirit and move_type == "Steel":
            modifier *= 1.5
            modifiers.append("Steely Spirit ally")
        if context.field.is_flower_gift_attacker and category == "Physical" and weather in {"sun", "harshsun"}:
            modifier *= 1.5
            modifiers.append("Flower Gift ally")
        if context.field.is_flower_gift_defender and category == "Special" and weather in {"sun", "harshsun"}:
            modifier *= 2 / 3
            modifiers.append("Flower Gift defender ally")
        if context.critical:
            modifier *= 2.0 if self.game_mode == "pokemon-emerald" else 1.5
            modifiers.append("critical")
        if _screen_applies(move, category, context):
            modifier *= 2 / 3 if context.field.is_doubles else 0.5
            modifiers.append("screen")
        if category == "Physical" and attacker.status == "burn" and _normalized(attacker.ability) != "guts" and _normalize_name(str(move["name"])) != "facade":
            modifier *= 0.5
            modifiers.append("burn")
        modifier *= _attacker_item_damage_modifier(attacker, move_type, category, type_multiplier, modifiers)
        modifier *= _defender_item_damage_modifier(defender, move_type, type_multiplier, modifiers)
        modifier *= _ability_damage_modifier(
            attacker,
            defender,
            attacker_data,
            defender_data,
            move,
            move_type,
            category,
            type_multiplier,
            context,
            modifiers,
        )
        if context.parental_bond:
            modifier *= 1.25
            modifiers.append("Parental Bond")
        return modifier

    def _immune_by_item_or_ability(self, move_type: str, defender: PokemonCalcSet, modifiers: list[str]) -> bool:
        ability = _normalized(defender.ability)
        item = _normalized(defender.held_item)
        immunities = {
            "levitate": {"Ground"},
            "voltabsorb": {"Electric"},
            "motordrive": {"Electric"},
            "lightningrod": {"Electric"},
            "waterabsorb": {"Water"},
            "stormdrain": {"Water"},
            "flashfire": {"Fire"},
            "wellbakedbody": {"Fire"},
            "sapsipper": {"Grass"},
            "dryskin": {"Water"},
            "eartheater": {"Ground"},
            "wellearthed": {"Electric"},
        }
        if move_type in immunities.get(ability, set()):
            modifiers.append(defender.ability or "immunity ability")
            return True
        if move_type == "Ground" and item == "airballoon":
            modifiers.append("Air Balloon")
            return True
        return False

    def _apply_survival_items(
        self,
        rolls: tuple[int, ...],
        defender: PokemonCalcSet,
        current_hp: int,
        max_hp: int,
        modifiers: list[str],
    ) -> tuple[int, ...]:
        item = _normalized(defender.held_item)
        ability = _normalized(defender.ability)
        if current_hp == max_hp and (item == "focussash" or ability == "sturdy"):
            capped = tuple(min(roll, max(0, current_hp - 1)) if roll >= current_hp else roll for roll in rolls)
            if capped != rolls:
                modifiers.append("Focus Sash" if item == "focussash" else "Sturdy")
            return capped
        return rolls

    def _stat(
        self,
        species: dict[str, Any],
        stat_name: str,
        level: int,
        nature: str | None,
        evs: dict[str, int] | None,
        ivs: dict[str, int] | None,
    ) -> int:
        base = int(species["baseStats"][stat_name])
        ev = (evs or {}).get(stat_name, 0)
        iv = (ivs or {}).get(stat_name, 31)
        if stat_name == "hp":
            return math.floor(((2 * base + iv + math.floor(ev / 4)) * level) / 100) + level + 10
        value = math.floor(((2 * base + iv + math.floor(ev / 4)) * level) / 100) + 5
        return math.floor(value * _nature_modifier(nature, stat_name))

    def _type_multiplier(self, attacking_type: str, defender_types: list[str]) -> float:
        chart = self.type_chart.get(attacking_type, {})
        multiplier = 1.0
        for defender_type in defender_types:
            multiplier *= float(chart.get(defender_type, 1.0))
        return multiplier


@lru_cache(maxsize=2)
def default_calculator(game_mode: str = "run-and-bun") -> DamageCalculator | None:
    path = DEFAULT_GEN3_CALC_DATA_PATH if normalize_game_mode(game_mode) == "pokemon-emerald" else DEFAULT_CALC_DATA_PATH
    if not path.is_file():
        return None
    return DamageCalculator(path, game_mode=game_mode)


def _load_calc_data(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _move_name_for_slot(state: BattleState, active_slot: int, move_slot: int | None) -> str:
    if move_slot is None:
        return ""
    if 0 <= active_slot < len(state.player_move_names_by_slot):
        names = state.player_move_names_by_slot[active_slot]
        if 0 <= move_slot < len(names):
            return names[move_slot]
    if active_slot == 0 and 0 <= move_slot < len(state.player_move_names):
        return state.player_move_names[move_slot]
    return ""


def _player_move_names_for_slot(state: BattleState, active_slot: int) -> list[str]:
    if 0 <= active_slot < len(state.player_move_names_by_slot):
        names = [name for name in state.player_move_names_by_slot[active_slot] if name]
        if names:
            return names
    if active_slot == 0:
        return [name for name in state.player_move_names if name]
    return []


def _first_live_slot(fainted: list[bool], max_hp: list[int]) -> int | None:
    for index, hp in enumerate(max_hp):
        if hp > 0 and (index >= len(fainted) or not fainted[index]):
            return index
    return None


def _nature_modifier(nature: str | None, stat_name: str) -> float:
    if not nature:
        return 1.0
    pair = NATURE_BOOSTS.get(nature.title())
    if pair is None:
        return 1.0
    if pair[0] == stat_name:
        return 1.1
    if pair[1] == stat_name:
        return 0.9
    return 1.0


def _can_crit(attacker: PokemonCalcSet, defender: PokemonCalcSet) -> bool:
    if _normalized(defender.ability) in {"battlearmor", "shellarmor", "magmaarmor"}:
        return False
    return True


def _boost_multiplier(stage: int) -> float:
    if stage >= 0:
        return (2 + stage) / 2
    return 2 / (2 - stage)


def _attacker_item_damage_modifier(
    attacker: PokemonCalcSet,
    move_type: str,
    category: str,
    type_multiplier: float,
    modifiers: list[str],
) -> float:
    item = _normalized(attacker.held_item)
    if not item:
        return 1.0
    if item == "lifeorb":
        modifiers.append("Life Orb")
        return 1.3
    if item == "expertbelt" and type_multiplier > 1:
        modifiers.append("Expert Belt")
        return 1.2
    if item == "muscleband" and category == "Physical":
        modifiers.append("Muscle Band")
        return 1.1
    if item == "wiseglasses" and category == "Special":
        modifiers.append("Wise Glasses")
        return 1.1
    if item.endswith("gem") and item.removesuffix("gem") == _normalize_name(move_type):
        modifiers.append(attacker.held_item or "Gem")
        return 1.3
    if TYPE_BOOST_ITEMS.get(item) == move_type:
        modifiers.append(attacker.held_item or "type boost item")
        return 1.2
    if item.endswith("memory") and item.removesuffix("memory") == _normalize_name(move_type):
        modifiers.append(attacker.held_item or "Memory")
        return 1.2
    if item.endswith("drive") and item.removesuffix("drive") == _normalize_name(move_type):
        modifiers.append(attacker.held_item or "Drive")
        return 1.2
    if item in {"adamantorb", "lustrousorb", "griseousorb"}:
        boosted = {
            "adamantorb": {"Steel", "Dragon"},
            "lustrousorb": {"Water", "Dragon"},
            "griseousorb": {"Ghost", "Dragon"},
        }
        if move_type in boosted[item]:
            modifiers.append(attacker.held_item or "Orb")
            return 1.2
    return 1.0


def _defender_item_damage_modifier(
    defender: PokemonCalcSet,
    move_type: str,
    type_multiplier: float,
    modifiers: list[str],
) -> float:
    item = _normalized(defender.held_item)
    if TYPE_RESIST_BERRIES.get(item) == move_type and type_multiplier > 1:
        modifiers.append(defender.held_item or "resist berry")
        return 0.5
    return 1.0


def _ability_damage_modifier(
    attacker: PokemonCalcSet,
    defender: PokemonCalcSet,
    attacker_data: dict[str, Any],
    defender_data: dict[str, Any],
    move: dict[str, Any],
    move_type: str,
    category: str,
    type_multiplier: float,
    context: DamageContext,
    modifiers: list[str],
) -> float:
    modifier = 1.0
    attacker_ability = _normalized(attacker.ability)
    defender_ability = _normalized(defender.ability)
    move_id = _normalize_name(str(move["name"]))
    if defender_ability == "wonderguard" and type_multiplier <= 1:
        modifiers.append("Wonder Guard")
        return 0.0
    if defender_ability == "bulletproof" and _has_flag(move, "bullet"):
        modifiers.append("Bulletproof")
        return 0.0
    if defender_ability == "soundproof" and _has_flag(move, "sound") and move_id != "clangoroussoul":
        modifiers.append("Soundproof")
        return 0.0
    if defender_ability == "windrider" and _has_flag(move, "wind"):
        modifiers.append("Wind Rider")
        return 0.0
    if defender_ability == "disguise" and defender.hp is not None and defender.max_hp and defender.hp >= defender.max_hp:
        modifiers.append("Disguise")
        return 0.0
    if defender_ability == "terashell" and defender.hp is not None and defender.max_hp and defender.hp >= defender.max_hp:
        modifier *= 0.5
        modifiers.append("Tera Shell")
    if attacker_ability in {"blaze", "overgrow", "torrent", "swarm"}:
        ability_type = {"blaze": "Fire", "overgrow": "Grass", "torrent": "Water", "swarm": "Bug"}[attacker_ability]
        if move_type == ability_type and attacker.hp is not None and attacker.max_hp and attacker.hp <= attacker.max_hp / 3:
            modifier *= 1.5
            modifiers.append(attacker.ability or "pinch ability")
    if attacker_ability == "steelworker" and move_type == "Steel":
        modifier *= 1.5
        modifiers.append("Steelworker")
    if attacker_ability == "dragonsmaw" and move_type == "Dragon":
        modifier *= 1.5
        modifiers.append("Dragon's Maw")
    if attacker_ability == "rockypayload" and move_type == "Rock":
        modifier *= 1.5
        modifiers.append("Rocky Payload")
    if attacker_ability == "transistor" and move_type == "Electric":
        modifier *= 1.3
        modifiers.append("Transistor")
    if attacker_ability == "stakeout" and attacker.ability_on:
        modifier *= 2.0
        modifiers.append("Stakeout")
    if attacker_ability == "waterbubble" and move_type == "Water":
        modifier *= 2.0
        modifiers.append("Water Bubble")
    if attacker_ability == "supremeoverlord" and attacker.allies_fainted > 0:
        modifier *= 1 + min(5, attacker.allies_fainted) * 0.1
        modifiers.append("Supreme Overlord")
    if attacker_ability == "neuroforce" and type_multiplier > 1:
        modifier *= 1.25
        modifiers.append("Neuroforce")
    if attacker_ability == "tintedlens" and 0 < type_multiplier < 1:
        modifier *= 2.0
        modifiers.append("Tinted Lens")
    if attacker_ability == "sniper" and context.critical:
        modifier *= 1.5
        modifiers.append("Sniper")
    aura_active = (
        (move_type == "Dark" and (attacker_ability == "darkaura" or defender_ability == "darkaura" or context.field.is_dark_aura))
        or (move_type == "Fairy" and (attacker_ability == "fairyaura" or defender_ability == "fairyaura" or context.field.is_fairy_aura))
    )
    if aura_active:
        if attacker_ability == "aurabreak" or defender_ability == "aurabreak" or context.field.is_aura_break:
            modifier *= 0.75
            modifiers.append("Aura Break")
        else:
            modifier *= 4 / 3
            modifiers.append("Aura")
    if defender_ability == "thickfat" and move_type in {"Fire", "Ice"}:
        modifier *= 0.5
        modifiers.append("Thick Fat")
    if defender_ability == "heatproof" and move_type == "Fire":
        modifier *= 0.5
        modifiers.append("Heatproof")
    if defender_ability == "dryskin" and move_type == "Fire":
        modifier *= 1.25
        modifiers.append("Dry Skin fire")
    if defender_ability == "waterbubble" and move_type == "Fire":
        modifier *= 0.5
        modifiers.append("Water Bubble")
    if defender_ability == "purifyingsalt" and move_type == "Ghost":
        modifier *= 0.5
        modifiers.append("Purifying Salt")
    if defender_ability == "fluffy" and _has_flag(move, "contact") and move_type != "Fire":
        modifier *= 0.5
        modifiers.append("Fluffy")
    if defender_ability == "fluffy" and move_type == "Fire":
        modifier *= 2.0
        modifiers.append("Fluffy fire")
    if defender_ability in {"filter", "solidrock", "prismarmor"} and type_multiplier > 1:
        modifier *= 0.75
        modifiers.append(defender.ability or "Filter")
    if defender_ability == "multiscale" and defender.hp is not None and defender.max_hp and defender.hp >= defender.max_hp:
        modifier *= 0.5
        modifiers.append("Multiscale")
    if defender_ability == "shadowshield" and defender.hp is not None and defender.max_hp and defender.hp >= defender.max_hp:
        modifier *= 0.5
        modifiers.append("Shadow Shield")
    if defender_ability == "iceface" and category == "Physical":
        modifier *= 0.0
        modifiers.append("Ice Face")
    if defender_ability == "punkrock" and _has_flag(move, "sound"):
        modifier *= 0.5
        modifiers.append("Punk Rock defender")
    if (defender_ability == "tabletsruin" or context.field.is_tablets_of_ruin) and attacker_ability != "tabletsruin" and category == "Physical":
        modifier *= 0.75
        modifiers.append("Tablets of Ruin")
    if (defender_ability == "vesselofruin" or context.field.is_vessel_of_ruin) and attacker_ability != "vesselofruin" and category == "Special":
        modifier *= 0.75
        modifiers.append("Vessel of Ruin")
    if (attacker_ability == "swordofruin" or context.field.is_sword_of_ruin) and defender_ability != "swordofruin" and category == "Physical":
        modifier *= 4 / 3
        modifiers.append("Sword of Ruin")
    if (attacker_ability == "beadsofruin" or context.field.is_beads_of_ruin) and defender_ability != "beadsofruin" and category == "Special":
        modifier *= 4 / 3
        modifiers.append("Beads of Ruin")
    if move_id == "earthquake" and context.field.terrain == "grassy":
        modifier *= 0.5
        modifiers.append("Grassy Terrain Earthquake")
    if move_id in {"bulldoze", "magnitude"} and context.field.terrain == "grassy":
        modifier *= 0.5
        modifiers.append("Grassy Terrain ground spread")
    return modifier


def _screen_applies(move: dict[str, Any], category: str, context: DamageContext) -> bool:
    if context.critical or _normalize_name(str(move["name"])) in SCREEN_IGNORING_MOVES:
        return False
    return (
        context.field.is_aurora_veil
        or (category == "Physical" and context.field.is_reflect)
        or (category == "Special" and context.field.is_light_screen)
    )


def _effective_weather(field: FieldState, attacker: PokemonCalcSet, defender: PokemonCalcSet) -> str | None:
    if _normalized(attacker.held_item) == "utilityumbrella" or _normalized(defender.held_item) == "utilityumbrella":
        return None
    if _normalized(attacker.ability) in WEATHERLESS_ABILITIES or _normalized(defender.ability) in WEATHERLESS_ABILITIES:
        return None
    if _normalized(attacker.ability) == "megasol":
        return "sun"
    weather = _normalized(field.weather)
    aliases = {
        "sunnyday": "sun",
        "sunshine": "sun",
        "desolateland": "harshsun",
        "raindance": "rain",
        "primordialsea": "heavyrain",
        "sandstorm": "sand",
        "hail": "hail",
        "snow": "snow",
    }
    return aliases.get(weather, weather or None)


def _effective_ability_sets(
    attacker: PokemonCalcSet,
    defender: PokemonCalcSet,
    move: dict[str, Any],
    modifiers: list[str],
) -> tuple[PokemonCalcSet, PokemonCalcSet]:
    if not attacker.ability_on and attacker.ability:
        attacker = replace(attacker, ability=None)
    if not defender.ability_on and defender.ability:
        defender = replace(defender, ability=None)
    attacker_ability = _normalized(attacker.ability) if attacker.ability_on else ""
    defender_ability = _normalized(defender.ability) if defender.ability_on else ""
    if attacker_ability == "neutralizinggas" and defender_ability != "neutralizinggas":
        defender = replace(defender, ability=None, ability_on=False)
        modifiers.append("Neutralizing Gas")
        defender_ability = ""
    if defender_ability == "neutralizinggas" and attacker_ability != "neutralizinggas":
        attacker = replace(attacker, ability=None, ability_on=False)
        modifiers.append("Neutralizing Gas")
        attacker_ability = ""
    if _ignores_defender_ability(attacker, move) and defender_ability:
        defender = replace(defender, ability=None, ability_on=False)
        modifiers.append(attacker.ability or str(move.get("name") or "Mold Breaker"))
    return attacker, defender


def _ignores_defender_ability(attacker: PokemonCalcSet, move: dict[str, Any]) -> bool:
    ability = _normalized(attacker.ability) if attacker.ability_on else ""
    move_id = _normalize_name(str(move.get("name") or move.get("id") or ""))
    return ability in {"moldbreaker", "teravolt", "turboblaze"} or move_id in {
        "lightthatburnsthesky",
        "menacingmoonrazemaelstrom",
        "moongeistbeam",
        "photongeyser",
        "searingsunrazesmash",
        "sunsteelstrike",
        "gmaxdrumsolo",
        "gmaxfireball",
        "gmaxhydrosnipe",
    }


def _status_move_blocked_by_ability(move: dict[str, Any], attacker: PokemonCalcSet, defender: PokemonCalcSet) -> bool:
    if attacker.species == defender.species and attacker.ability == defender.ability:
        return False
    defender_ability = _normalized(defender.ability) if defender.ability_on else ""
    return defender_ability == "goodasgold"


def _is_forced_crit(attacker: PokemonCalcSet, defender: PokemonCalcSet) -> bool:
    return _normalized(attacker.ability) == "merciless" and defender.status in {"poison", "toxic"}


def _protosynthesis_boosts_stat(
    pokemon: PokemonCalcSet,
    species: dict[str, Any],
    stat_name: str,
    field: FieldState,
) -> bool:
    ability = _normalized(pokemon.ability) if pokemon.ability_on else ""
    item = _normalized(pokemon.held_item)
    active = (
        (ability == "protosynthesis" and _effective_weather(field, pokemon, pokemon) in {"sun", "harshsun"})
        or (ability == "quarkdrive" and field.terrain == "electric")
        or (item == "boosterenergy" and ability in {"protosynthesis", "quarkdrive"})
    )
    if not active or stat_name == "hp":
        return False
    stats = {
        stat: int(species["baseStats"][stat])
        for stat in ("atk", "def", "spa", "spd", "spe")
    }
    best = max(stats.values())
    return stats.get(stat_name) == best


def _ability_move_type(
    move_type: str,
    move: dict[str, Any],
    category: str,
    ability: str | None,
    modifiers: list[str],
) -> str:
    normalized = _normalized(ability)
    if normalized == "normalize":
        modifiers.append("Normalize")
        return "Normal"
    if normalized == "liquidvoice" and _has_flag(move, "sound"):
        modifiers.append("Liquid Voice")
        return "Water"
    return move_type


def _hidden_power_type(ivs: dict[str, int] | None) -> str:
    values = {stat: (ivs or {}).get(stat, 31) for stat in ("hp", "atk", "def", "spe", "spa", "spd")}
    order = ["Fighting", "Flying", "Poison", "Ground", "Rock", "Bug", "Ghost", "Steel", "Fire", "Water", "Grass", "Electric", "Psychic", "Ice", "Dragon", "Dark"]
    bits = (
        (values["hp"] % 2)
        + 2 * (values["atk"] % 2)
        + 4 * (values["def"] % 2)
        + 8 * (values["spe"] % 2)
        + 16 * (values["spa"] % 2)
        + 32 * (values["spd"] % 2)
    )
    return order[math.floor(bits * 15 / 63)]


def _ate_ability_type(move_type: str, category: str, ability: str | None, modifiers: list[str]) -> str:
    if move_type != "Normal":
        return move_type
    ability_type = {
        "aerilate": "Flying",
        "dragonize": "Dragon",
        "galvanize": "Electric",
        "pixilate": "Fairy",
        "refrigerate": "Ice",
    }.get(_normalized(ability))
    if ability_type:
        modifiers.append(ability or "ate ability")
        return ability_type
    return move_type


def _has_flag(move: dict[str, Any], flag: str) -> bool:
    return bool(move.get("flags", {}).get(flag))


def _is_spread_move(move: dict[str, Any]) -> bool:
    return move.get("target") in {"allAdjacent", "allAdjacentFoes"}


def _speed_guess(pokemon: PokemonCalcSet, calculator: DamageCalculator) -> int:
    data = calculator._species_data(pokemon.species)
    if not data:
        return 100
    return calculator._effective_stat(
        pokemon,
        data,
        "spe",
        is_attacker=True,
        critical=False,
        category="Physical",
        context=DamageContext(),
        modifiers=[],
    )


def _risk_score(risk: MatchupRisk) -> float:
    if risk.best_damage is None:
        return 0.0
    return (
        risk.best_damage.ko_chance * 1000.0
        + risk.best_damage.max_percent * 100.0
        + (100.0 if risk.outspeeds else 0.0)
    )


def _risk_can_ohko(risk: MatchupRisk) -> bool:
    return risk.best_damage is not None and risk.best_damage.max_damage >= risk.defender_hp > 0


def _switch_relief_score(decision: SwitchDecision) -> float:
    active_score = _risk_score(decision.active_risk)
    switch_score = _risk_score(decision.switch_risk)
    if active_score <= 0:
        return 0.0
    relief = active_score - switch_score
    if decision.reason == "current_enemy_can_ohko":
        relief += 600.0
    elif decision.reason == "bad_damage_under_pressure":
        relief += 250.0
    elif decision.reason == "preserve_active_for_later":
        relief += 150.0 * max(0.0, decision.active_future_value - decision.switch_future_value)
    if _risk_can_ohko(decision.switch_risk):
        relief -= 500.0
    return relief


def _electro_ball_power(attacker_speed: int, defender_speed: int) -> int:
    ratio = attacker_speed / max(1, defender_speed)
    if ratio >= 4:
        return 150
    if ratio >= 3:
        return 120
    if ratio >= 2:
        return 80
    if ratio >= 1:
        return 60
    return 40


def _fling_power(item: str | None) -> int:
    powers = {
        "ironball": 130,
        "hardstone": 100,
        "rarebone": 100,
        "deepseatooth": 90,
        "thickclub": 90,
        "assaultvest": 80,
        "dousedrive": 70,
        "burndrive": 70,
        "shockdrive": 70,
        "chilldrive": 70,
        "dragonfang": 70,
        "poisonbarb": 70,
        "powerherb": 10,
        "flameorb": 30,
        "toxicorb": 30,
    }
    return powers.get(_normalized(item), 30)


def _normalized(value: str | None) -> str:
    return _normalize_name(value or "")


def _modified_accuracy(
    value: Any,
    attacker: PokemonCalcSet,
    defender: PokemonCalcSet,
    move: dict[str, Any],
    field: FieldState,
    modifiers: list[str],
) -> float:
    if _normalized(attacker.ability) == "noguard" or _normalized(defender.ability) == "noguard":
        modifiers.append("No Guard")
        return 1.0
    if _normalize_name(str(move.get("name") or move.get("id") or "")) == "thunderwave":
        attacker_types = move.get("_attacker_types")
        if attacker_types and "Electric" in attacker_types:
            modifiers.append("RnB Electric Thunder Wave")
            return 1.0
    accuracy = _accuracy_fraction(value)
    attacker_item = _normalized(attacker.held_item)
    defender_item = _normalized(defender.held_item)
    if attacker_item == "widelens":
        accuracy *= 1.1
        modifiers.append("Wide Lens")
    if defender_item in {"brightpowder", "laxincense"}:
        accuracy *= 0.9
        modifiers.append(defender.held_item or "evasion item")
    if _normalized(attacker.ability) == "compoundeyes":
        accuracy *= 1.3
        modifiers.append("Compound Eyes")
    if _normalized(attacker.ability) == "victorystar":
        accuracy *= 1.1
        modifiers.append("Victory Star")
    if _normalized(attacker.ability) == "hustle" and str(move.get("category") or "") == "Physical":
        accuracy *= 0.8
        modifiers.append("Hustle accuracy")
    weather = _effective_weather(field, attacker, defender)
    if _normalized(defender.ability) == "sandveil" and weather == "sand":
        accuracy *= 0.8
        modifiers.append("Sand Veil")
    if _normalized(defender.ability) == "snowcloak" and weather in {"hail", "snow"}:
        accuracy *= 0.8
        modifiers.append("Snow Cloak")
    return max(0.0, min(1.0, accuracy))


def _accuracy_fraction(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value) / 100.0))
    except (TypeError, ValueError):
        return 1.0


_NAME_STRIP_RE = re.compile(r"[^a-z0-9]+")


@lru_cache(maxsize=None)
def _normalize_name(value: str) -> str:
    return _NAME_STRIP_RE.sub("", value.casefold())


def _best_species_guess(value: str, species: dict[str, Any]) -> str:
    normalized = _normalize_name(value)
    return normalized if normalized in species else ""


def _estimate_level(species_name: str, max_hp: int | None, species: dict[str, Any]) -> int | None:
    if not max_hp or max_hp <= 0:
        return None
    data = species.get(_normalize_name(species_name))
    if not data:
        return None
    base_hp = int(data["baseStats"]["hp"])
    best_level = min(
        range(1, 101),
        key=lambda level: abs((math.floor(((2 * base_hp + 31) * level) / 100) + level + 10) - max_hp),
    )
    return best_level
