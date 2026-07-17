# Doubles battle support

The planner treats doubles as a two-position board rather than two singles battles played at once.

## Positions and actions

- Battlefield slots are stable positions: slot 1 (left) and slot 2 (right).
- `BattleState.player_active_slots` and `enemy_active_slots` map those positions to party indices.
- `Action.actor_slot` identifies which battlefield position acts. `target_slot` identifies the opposing battlefield position, not a party index.
- A fainted position generates a forced replacement choice while its surviving partner can still move.
- Two positions cannot switch to the same bench Pokemon in one turn.

Older emulator states that do not expose active-slot memory remain compatible and fall back to party slots 0 and 1. This fallback is explicit; it is not used once active-slot data is present.

## Calc line finder

The doubles simulator models:

- two chosen player leads and two ordered enemy leads;
- priority and speed ordering across all four actors;
- per-slot voluntary switches and faint replacements;
- enemy move plus target selection;
- spread damage using the doubles modifier;
- `allAdjacent` friendly fire, including the user's partner or the enemy's partner;
- `allAdjacentFoes` damage without friendly fire;
- entry abilities, status, items, end-of-turn effects, and HP carry;
- a doubles-specific lead and pivot line search.

The custom doubles builder accepts Showdown-style opponent sets. Reordering foe slot 1 and foe slot 2 changes initial targeting and replacement order without changing the stored trainer database.

## Contingency flowchart

The doubles flowchart replays independent uncertainty axes:

1. enemy field slot;
2. selected move;
3. selected target slot;
4. accuracy outcome;
5. distinct damage roll;
6. resulting HP state.

Each replay uses a full doubles board signature containing both active pairs and every battler's HP, status, boosts, item-consumption state, and other persistent effects. Routes that reach an equivalent state rejoin. The normal chart is time/node budgeted; **Explore every branch** continues with progress, speed, queue, and cancellation reporting.

## PDF output

Doubles PDFs print the four opening positions before turn 1. Each turn then lists one instruction per acting field slot, including exact targets and switches, before the detailed event log and resulting HP.
