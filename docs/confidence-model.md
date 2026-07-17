# Confidence Model

Confidence is a heuristic stability score for the proposed line. It is not an exact win probability.

A line with `94%` confidence should not be read as "this works exactly 94% of the time." It means the current planner believes the line is structurally stable under the modeled assumptions, move accuracy, enemy branch uncertainty, survival checks, and mechanics certainty. Luck risks such as enemy crits and secondary effects are reported separately as `realistic_confidence` and `hax_outlook`.

## Conceptual Model

The useful mental model is:

```text
line_confidence =
    accuracy_factor
  x ai_branch_factor
  x survival_factor
  x mechanics_certainty_factor
```

The code does not literally store every factor under those names. Instead, confidence is multiplied down during planning by helper functions such as:

- `_player_action_confidence(...)`
- `_ai_branch_confidence(...)`
- `_survival_confidence(...)`

The output is clamped between `0.0` and `1.0`.

## What Reduces Confidence

### Move Accuracy

Player move confidence starts from move accuracy.

If the player's chosen move is inaccurate, the line becomes less stable. Confusion-inducing player moves receive an additional penalty because they rely on disruption instead of direct deterministic progress.

### AI Branch Uncertainty

Enemy moves are modeled as scored branches. The planner follows the Run & Bun AI document's highest-score rule: attacks compete on rolled damage, the winning attack receives the documented +6/+8 score roll, and exact top-score ties split evenly. Move-specific random scoring is still simplified where the reference has many conditional checks.

Confidence drops when:

- multiple enemy moves are plausible
- alternate branches can KO
- alternate branches can disrupt the line
- AI hard-switch branches are possible

### Damage Rolls

The planner is conservative about damage:

- player damage planning generally values lower rolls
- enemy damage pressure generally uses higher rolls
- KO rolls and survival thresholds affect line stability

If a line depends on favorable damage rolls, confidence should be lower.

The linear recommendation continues to use conservative damage for choosing a safe line. The exhaustive flowchart is different: it expands each distinct resulting HP state from the calculator's real roll list (plus misses), so a low roll, high roll, crit, and KO can lead to different later instructions instead of being presented as one certain timeline.

### Luck-Adjusted Confidence

The simulator also returns `realistic_confidence`, which keeps the older cumulative luck penalties:

```json
{
  "confidence": 0.941,
  "realistic_confidence": 0.667
}
```

Use `confidence` to judge whether the plan is sound. Use `realistic_confidence` and `hax_outlook` to judge how much the line is exposed to enemy crits, flinches, poison, paralysis, and similar bad branches.

### Crit Risk

Normal mode reports enemy crit KOs as risks when they can break the line.

Crit-aware mode goes further: enemy damaging moves are estimated as crits for enemy choices, switch checks, and survival. A crit-aware win line is stricter than a normal line.

Crit risk is represented in `realistic_confidence`, `hax_outlook`, and turn risk notes when a critical hit would KO the target.

### Secondary Effects

Secondary effects can reduce confidence when they can disrupt the line.

Examples:

- flinch
- freeze
- paralysis
- burn
- poison
- stat drops
- confusion
- healing block
- sound move block
- speed drops

The planner flags these as risk notes and folds them into `realistic_confidence` / `hax_outlook`. It does not fully branch every secondary-effect timeline.

### Status Disruption

Status moves and status-causing attacks reduce stability when they can stop or weaken the planned answer.

Examples:

- sleep can remove turns
- paralysis can flip speed order or cause full paralysis
- burn can weaken physical attackers
- Toxic can put a timer on bulky pivots
- Leech Seed can reverse progress
- confusion can steal turns

### Tactical Sacks

Tactical sacks reduce confidence heavily.

A sack means the planner thinks the line may need to spend a Pokemon to preserve or create progress. This can be legitimate, but it is less stable than a clean line because it often depends on exact sendout, exact HP, and exact AI behavior.

### Incomplete Mechanics

Confidence is also affected by mechanics certainty.

The calculator and planner include many Run & Bun changes, but the system is still an approximation. Any missing or simplified mechanic means the number is less trustworthy.

### Uncertain AI Behavior

The planner approximates Run & Bun AI from reference docs and observed behavior.

When the AI model is wrong, confidence can be wrong. The number should be treated as "confidence under this model," not a guarantee about the actual game.

## Current Code Behavior

In the calc-only Line Finder, confidence starts at `1.0` and is multiplied down turn by turn.

Examples from `web/server.py` and `optimizer/turn_planner.py`:

- blocked lines use a reduced confidence
- tactical sacks multiply confidence down sharply
- AI hard-switch branches multiply confidence down
- player action confidence uses move accuracy
- AI branch confidence sums the modeled safe probability of possible enemy choices
- unavoidable enemy KO pressure can reduce survival confidence
- luck-adjusted confidence is preserved as `realistic_confidence`

The final response field is:

```json
{
  "confidence": 0.941,
  "realistic_confidence": 0.667
}
```

The UI displays that as a percentage.

## How To Interpret It

High confidence means:

- the line uses accurate moves
- the enemy has few dangerous alternate branches
- enemy crits and secondaries are not likely to break the line
- survival margins are good
- the planner's known mechanics cover the situation

Low confidence means:

- the line may still be the best found line
- but it is exposed to misses, hax, alternate AI choices, sacks, or uncertain mechanics

Zero or near-zero confidence usually means the line is blocked, repeatedly unstable, or depends on branches the planner considers unsafe.

## What Confidence Is Not

Confidence is not:

- a full Monte Carlo simulation
- a mathematically exact probability
- a guarantee that the line wins in-game
- proof that no better line exists
- proof that the AI must pick the displayed move

It is a practical warning system for how much trust to place in the displayed line.
