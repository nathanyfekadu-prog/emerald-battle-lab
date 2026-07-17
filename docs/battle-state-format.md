# Battle State Format

The planner uses battle state as memory. It is not just the current screen. It should carry forward HP, status, stat boosts, fainted Pokemon, field effects, consumed items, and active Pokemon after every simulated turn.

There are two related state shapes in the repo:

- `battle/battle_state.py` defines `BattleState`, the compact emulator-facing snapshot.
- `optimizer/turn_planner.py` expands that into mutable `PlannedMember` and `PlannedEnemy` objects for calc-only planning.

For the current Line Finder, the important memory is the planner state, not only `BattleState`.

## Compact `BattleState`

`BattleState` contains:

```python
BattleState(
    player_hp=[...],
    player_max_hp=[...],
    player_fainted=[...],
    enemy_hp=[...],
    enemy_max_hp=[...],
    enemy_fainted=[...],
    battle_over=False,
    player_won=False,
    is_doubles=False,
    menu_ready=True,
    player_names=[...],
    enemy_names=[...],
    player_move_names=[...],
    player_move_names_by_slot=[[...]],
    player_species=[...],
    enemy_species=[...],
)
```

This is enough for emulator snapshots and trainer matching, but it does not store the full planner memory for calc-only Line Finder runs.

## Planner State

The calc planner represents each player Pokemon as `PlannedMember` and each enemy Pokemon as `PlannedEnemy`.

Player-side `PlannedMember` fields include:

- `name`
- `species`
- `level`
- `max_hp`
- `hp`
- `moves`
- `item`
- `ability`
- `ability_on`
- `nature`
- `evs`
- `ivs`
- `source`
- `slot`
- `status`
- `boosts`
- `sleep_turns`
- `toxic_counter`
- `leech_seeded`
- `confused_turns`
- `protected`
- `flinched`
- `trapped`
- `salt_cured`
- `syrup_bomb_turns`
- `heal_blocked_turns`
- `sound_blocked_turns`
- `turns_out`
- `consumed_item`

Enemy-side `PlannedEnemy` fields are similar, but the base calc set is stored as `pokemon: PokemonCalcSet`.

`PokemonCalcSet` is the calculator-facing shape:

- `species`
- `level`
- `nature`
- `hp`
- `max_hp`
- `evs`
- `ivs`
- `ability`
- `held_item`
- `status`
- `boosts`
- `gender`
- `ability_on`
- `allies_fainted`

## JSON-Style Example

This is a practical documentation shape for the planner's memory. It is not currently a single dataclass in the repo; it combines fields from `BattleState`, `PlannedMember`, `PlannedEnemy`, `DamageContext`, and the Line Finder request.

```json
{
  "turn": 8,
  "battle_type": "singles",
  "player_active_index": 2,
  "player_active": {
    "name": "Nidoqueen",
    "species": "Nidoqueen",
    "level": 30,
    "hp": 55,
    "max_hp": 109,
    "status": null,
    "boosts": {
      "atk": 0,
      "def": 0,
      "spa": 0,
      "spd": 0,
      "spe": 0
    },
    "item": "Oran Berry",
    "consumed_item": false,
    "ability": "Poison Point",
    "ability_on": true,
    "moves": ["Sludge Bomb", "Mud Shot", "Bite", "Double Kick"],
    "temporary_effects": {
      "sleep_turns": 0,
      "toxic_counter": 0,
      "leech_seeded": false,
      "confused_turns": 0,
      "protected": false,
      "flinched": false,
      "trapped": false,
      "salt_cured": false,
      "syrup_bomb_turns": 0,
      "heal_blocked_turns": 0,
      "sound_blocked_turns": 0,
      "turns_out": 3
    }
  },
  "player_party": [
    {
      "slot": 0,
      "name": "Palpitoad",
      "species": "Palpitoad",
      "hp": 99,
      "max_hp": 99,
      "status": null,
      "boosts": {},
      "item": "Oran Berry",
      "consumed_item": false,
      "ability": "Water Absorb",
      "moves": ["Mud Shot", "Water Pulse", "Round", "Supersonic"],
      "fainted": false
    },
    {
      "slot": 1,
      "name": "Shellder",
      "species": "Shellder",
      "hp": 0,
      "max_hp": 71,
      "status": null,
      "boosts": {},
      "item": "Oran Berry",
      "consumed_item": true,
      "ability": "Skill Link",
      "moves": ["Icicle Spear", "Razor Shell", "Ice Shard", "Protect"],
      "fainted": true
    }
  ],
  "enemy_active_index": 1,
  "enemy_active": {
    "name": "Furfrou",
    "species": "Furfrou",
    "level": 30,
    "hp": 22,
    "max_hp": 88,
    "status": null,
    "boosts": {},
    "item": null,
    "consumed_item": false,
    "ability": "Fur Coat",
    "ability_on": true,
    "moves": ["Return", "Headbutt", "Baby-Doll Eyes", "Sand Attack"],
    "temporary_effects": {
      "sleep_turns": 0,
      "toxic_counter": 0,
      "leech_seeded": false,
      "confused_turns": 0,
      "protected": false,
      "flinched": false,
      "trapped": false,
      "salt_cured": false,
      "syrup_bomb_turns": 0,
      "heal_blocked_turns": 0,
      "sound_blocked_turns": 0,
      "turns_out": 2
    }
  },
  "enemy_party_index": 1,
  "enemy_party": [
    {
      "index": 0,
      "species": "PreviousEnemy",
      "hp": 0,
      "max_hp": 70,
      "fainted": true
    },
    {
      "index": 1,
      "species": "Furfrou",
      "hp": 22,
      "max_hp": 88,
      "fainted": false
    }
  ],
  "field_state": {
    "weather": null,
    "terrain": null,
    "is_doubles": false,
    "reflect": false,
    "light_screen": false,
    "aurora_veil": false
  },
  "risk_policy": {
    "mode": "normal",
    "enemy_damage_rolls": "high_rolls",
    "player_damage_rolls": "low_rolls",
    "crit_safe": false,
    "notes": [
      "Normal mode reports enemy crit KOs as risks.",
      "Crit-aware mode estimates enemy damaging moves as critical hits."
    ]
  }
}
```

## Where This Differs From the Code

The exact example above is a documentation format, not a literal object currently passed around.

Actual code differences:

- `BattleState` does not include status, boosts, items, abilities, field state, or risk policy.
- `PlannedMember` and `PlannedEnemy` are Python dataclasses, not nested JSON.
- Field state lives in `battle.damage_calc.FieldState` and is passed through `DamageContext` when needed.
- `/api/calc/sim` currently has request fields for `weather`, `reflect`, and `light_screen`, but the current implementation does not wire them into the calc simulation.
- The Line Finder response contains summarized turn rows, final `team` payloads, final `enemies` payloads, item recommendations, and matchup rows, not the complete nested memory object above.

Future work should be careful to preserve this memory after every turn. Resetting HP, boosts, status, consumed items, active indexes, or residual flags between turns will make the line finder produce unrealistic lines.
