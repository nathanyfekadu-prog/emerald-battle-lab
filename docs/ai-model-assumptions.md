# AI Model Assumptions

The planner approximates Run & Bun AI. It does not perfectly reproduce the game's AI code or every RNG branch.

The current model is mostly implemented in `optimizer/turn_planner.py`, especially:

- `_ai_move_choices(...)`
- `_ai_status_score(...)`
- `_ai_setup_score(...)`
- `_ai_branch_confidence(...)`
- `_ai_hard_switch_target(...)`
- switch and tactical-sac helpers

The damage estimates used by these functions come from `battle/damage_calc.py`.

## Enemy Move Scoring

The enemy move model assigns each available enemy move a score, models which attack wins the AI's random damage-roll comparison, applies the documented +6 (80%) / +8 (20%) highest-damage outcomes, and splits exact top-score ties evenly.

Conceptually:

```text
damage-roll winner -> documented random score outcome -> highest final score -> tie split
```

The roll competition helper is `_damage_roll_winner_probabilities(...)` in `optimizer/turn_planner.py`. Strictly weaker attacks receive zero probability when their damage roll cannot win and no separate move-specific AI rule makes them competitive.

## Damage Preference

The AI strongly values damage.

The model:

- ranks damaging moves with `DamageCalculator.rank_move_names(...)`
- gives high value to the move with the highest damage
- gives lower value to chip damage
- treats very low damage as ineffective
- increases score when a move has KO chance
- uses max enemy damage for planning pressure

## KO Moves

The model prefers KO moves, especially if the enemy is faster or has priority.

In `_ai_move_choices(...)`:

- a move with KO chance gets a score bonus
- if the enemy is faster, the reason becomes `fast kill`
- if the enemy is slower, the reason becomes `slow kill`
- priority gets additional value when the player can KO the enemy and the enemy would otherwise move second

## Priority

Priority is valued when it secures kills or lets the enemy move before dying.

The model checks move priority and speed order. It does not perfectly model every priority interaction, but it explicitly rewards priority before death.

## Setup Moves

Setup is only valued when it appears useful.

The model:

- blocks setup if the player can KO the enemy
- checks that the boosted stat has value
- checks current boost stages and diminishing returns
- checks whether setup improves damage or speed outcomes
- avoids treating setup moves like Howl as infinitely repeatable free turns

Examples of setup moves represented in `SETUP_MOVE_BOOSTS` include:

- Bulk Up
- Calm Mind
- Coil
- Dragon Dance
- Hone Claws
- Howl
- Nasty Plot
- Quiver Dance
- Shell Smash
- Shift Gear
- Swords Dance
- Work Up

## Status and Disruption

The model may choose status or disruption when valuable.

Player and enemy scoring include support for categories such as:

- sleep
- burn
- paralysis
- poison
- Toxic
- Leech Seed
- Protect-style moves
- confusion
- attack drops
- special attack drops
- speed drops
- hazards and screens on the enemy AI side

The model scores these moves based on context. For example:

- sleep is stronger if the target cannot immediately KO
- burn is stronger into physical attackers
- paralysis is stronger when it can flip turn order
- Toxic is better against bulkier targets
- Leech Seed is better when it creates progress
- Protect is more valuable with residual pressure
- recovery follows the documented HP/speed gates, refuses at full HP or 85%+, accounts for Toxic and incoming damage versus healing, and values Rest wake-up resources

## Switching Logic

Switching logic is approximate.

The planner tries to avoid useless switching and repeated back-to-back pivots unless there is a tactical reason. It considers:

- whether the active Pokemon dies
- whether another team member survives the incoming hit
- whether a switch-in has a better action
- whether the current active has a faster guaranteed KO
- whether a bait pivot creates enough payoff
- whether Pursuit catches the switch
- whether a previous switch pattern is repeating

AI hard switches become explicit 50/50 contingency branches when the active enemy has no useful scored move, remains above half HP, and a legal safer back Pokemon exists. Both the switch and stay-in continuations are replayed.

## Tactical Sacks

The calc-only Line Finder can report tactical sacks.

The intended behavior is:

- avoid sacks when a cleaner switch, faster KO, or bait pivot exists
- use sacks only when the line appears blocked otherwise
- label the turn as `tactical-sac`
- reduce confidence heavily
- include a risk note explaining why the sack was selected

This is still heuristic, not a proof that the sack is mandatory.

## Known Weak Spots

- The model is not the actual Run & Bun AI source code.
- Move scoring probabilities are approximate, not exact RNG tables.
- AI switching is especially approximate.
- Doubles support exists in data structures and calculator context, but the current Line Finder is primarily tuned for singles-style planning.
- Starting weather, Reflect, and Light Screen from `/api/calc/sim` are wired into damage calculations. Dynamic field-changing moves and every location-specific permanent field effect are not yet fully stateful.
- Some mechanics from the reference docs are implemented directly, but the docs are not dynamically parsed at runtime.
- Branch probabilities can be misleading when the score model is wrong.
- Status moves with multi-turn consequences are simplified.
- Secondary effects are usually modeled as risk and disruption, not as exhaustive alternate timelines.
- Crit-aware mode stress-tests enemy crits, but the normal planner does not enumerate every possible hax branch.
- Post-KO enemy sendout uses a highest-pressure heuristic instead of always assuming simple party order.
- Unknown or missing move data can cause the planner to stop rather than invent damage.
- Item recommendations use a split-aware available pool and matchup scoring, but they do not prove every legal item route or inventory state.

## Practical Guidance for Contributors

When improving AI behavior:

- Add tests for the specific battle pattern that failed.
- Keep the scoring understandable and local.
- Prefer reducing impossible or silly lines over increasing confidence.
- Preserve state carry-forward after every turn.
- Update these docs if the model meaningfully changes.
