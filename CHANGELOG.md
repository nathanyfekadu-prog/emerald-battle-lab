# Changelog

## 2026-07-13 — Planner/GBA cross-validation and checkpointed overworld runner

- Added symbolic emulator policies: calculator lines are executed by live move name and Pokemon name, including doubles field slots, so a divergent battle fails closed instead of pressing the same numeric menu slot on the wrong Pokemon.
- Added `POST /api/simulator/validate-planner-line`; the Line Finder can replay its result 30 times in the real Run & Bun state and reports total clears, deathless clears, blackouts, unfinished runs, errors, and policy-divergence notes.
- Added savestate writing to the native libmgba bridge and Lua fallback, with verified save/advance/reload behavior on the supplied ROM.
- Added a checkpointed overworld route runner with verified tile movement, repeated-block detection, generic interaction/button scripts, durable JSON logs, and a resumable savestate after every milestone.
- Added an Overworld panel to the Simulator tab for safe checkpoints, manual tile routes, and a real bag-opening probe. The Run & Bun pause-menu cursor was RAM-verified so BAG is selected directly rather than guessed through a wrapping menu.
- Added per-turn GBA screenshots to simulator events and an animated multi-game replay wall in the browser.
- Verification: 129 tests pass; Breeder Corgi is recognized exactly from the supplied `.ss2`; a named `Mud Shot` policy executed with a captured real-game frame; the supplied `.ss1` opened the Items bag and wrote two resumable checkpoints.

One entry per push, newest first. Early entries are reconstructed from the
project's docs and test artifacts (the repo predates version control). This file
mirrors the Changelog tab in the web UI — update both together.

## Push 22 — Empirical emulator simulator rebuilt around verified live state *(2026-07-13)*
- Added a first-class **Simulator** workspace with a preflight battle fingerprint, trainer/location match, both active movesets, live game frame, fixed-line confidence leaderboard, Wilson clear-rate ranges, and expandable turn-by-turn replay logs.
- Replaced the incorrect player move buffer (which was actually showing Arcanine's moves) with the verified Run & Bun battle-mon structures. Move names now update after every switch; the supplied state reads Nidoqueen vs Arcanine and exactly matches Breeder Corgi on Route 116.
- Candidate discovery is followed by an equal **30-replay confidence pass** for each of the top three distinct full lines. Final recommendations use observed clears, blackouts, faints, HP, incomplete runs, and errors rather than a two-sample search estimate.
- Fixed forced-faint handling using live battler HP, corrected Emerald's 1+5 party-screen cursor navigation, and added HP-authoritative win/blackout detection for Run & Bun states that never set the vanilla outcome flag.
- Verified a full 20-turn Corgi rollout across Nidoqueen, Palpitoad, Drednaw, Salazzle, and Monferno with dynamic move labels and no emulator errors. Full automated suite: 123 passing.

## Push 21 — Flowchart always finishes, covers every move, and misses are real misses *(2026-07-01)*
- Fixed the calc hanging forever on branchy fights: the contingency flowchart was being built in exhaustive mode with **no node cap** (`_contingency_flowchart(..., exhaustive=True)` with an unlimited budget), so `/api/calc/sim` could spin for 30+ minutes and never return. The tree is now built **best-first by cumulative path probability** (a heap of pending positions instead of depth-first recursion), under a hard node budget (1500) and a 25s wall clock — the calc always comes back, and the budget is always spent on the likeliest lines first.
- **Every enemy move with any nonzero AI probability gets its own explicit branch** in the user-facing chart (exhaustive mode has no "Other moves — mostly X" catch-all any more), so whatever the foe clicks, there is a branch that matches. The non-exhaustive/default mode keeps the floors + catch-all.
- Lines the budget can't reach are closed with a marked **"Rare line → continue in Co-pilot"** truncation leaf (rendered dotted purple, with its odds) instead of a silent dead end. Because expansion is best-first, only the least likely tails ever truncate — each well under 1% of attempts on the worst stress fight (Breeder Corgi), and **typical fights and bosses (e.g. Brawly) map completely with zero truncation**.
- Realism fix in the KO fork: `ko_chance` is damage rolls × accuracy, so when even the **minimum** roll KOs, the only "no KO" outcome is a **miss**. That branch is now labeled "misses" and the pinned timeline undoes the attack entirely (both battlers restored), instead of inventing a "survives on a low roll (1 HP left)" state that cannot occur in the real game. New `player_no_ko_means_miss` flag in the turn fork metadata.
- The flowchart is now built for the **item loadout the user will actually play**: when the recommended-items line beats the imported-items line, the chart follows the optimized team, and `contingency_flowchart_note` (shown in the flowchart header) says which loadout it assumes.
- Engine speed (what buys the coverage): a memo cache on `DamageCalculator.estimate_move` keyed by the full battler/move/context state, an `lru_cache` on the planner's `_normalize`, and a per-build **decision cache** that memoizes per-turn decisions (enemy move choices, player action, switch target, sac target, branch confidence) by the battle-state signature entering the turn — replayed lines drop from ~183ms to ~10ms. State signatures also stopped fragmenting on `turns_out` (the engine only reads "first turn out or not"), so reconverged lines merge more.
- Tests: `test_contingency_flowchart_always_terminates_and_marks_cut_lines`, `test_kill_fork_no_ko_branch_is_a_miss_when_min_roll_kills`; the full suite runs ~2.5x faster from the caches.

## Push 20 — Interactive Co-pilot tab *(2026-06-17)*
- New **Co-pilot** tab: step through a battle turn by turn instead of reading a static tree. It names the lead and the exact move to click, predicts what the foe is likely to do (with the resulting HP projected for each option), you pick what actually happened and tweak HP if a roll differed, and it re-advises from the real position.
- Everything is editable mid-fight — your active mon, current HP, status, and the foe's mon/HP/status — and the recommendation updates live. Switches are recommended when the matchup calls for it, not just moves.
- Backed by a new stateless `POST /api/coach/step` that reuses the exact decision engine the line-finder and flowchart use (`_best_calc_answer` / `_calc_switch_target` / `_best_player_action` / `_calc_enemy_choices`), so its advice matches the rest of the app. The flowchart stays — the co-pilot is the interactive companion to it.

## Push 19 — Flowchart branches every move deep into the fight (memoized DAG) *(2026-06-17)*
- The contingency flowchart now keeps forking on **every** plausible enemy move at **every** turn while the line is still at least 0.5% likely cumulatively (was a 2% floor that collapsed branches to a single line after a few turns). So deep mid-fight positions — "if Boltund picks a different move on turn 6" — get their own pathway instead of a dead end. This is the "I need a pathway for every move, not just the first few turns" fix.
- **State memoization** is what makes deep all-move branching finite: lines that reconverge to the same battle state (which happens hard once a mon faints — the dying mon's move choice stops mattering) share one subtree, built once. Repeat arrivals link back through a compact "↳ rejoins an earlier line" merge-leaf instead of re-expanding. Without this, every-move-every-turn exploded exponentially and hung serialization; e.g. Breeder Corgi went from a multi-minute hang to ~8s.
- Skipped the redundant held-item recomputation that ran on every flowchart node (it was the dominant per-node build cost; the tree never uses it).
- New merge-leaf node type rendered in the flowchart view (dotted blue "rejoins line"). Bounds: path floor 0.005, depth 40, 4000-node safety cap; worst real fight ~8s, most sub-second.

## Push 18 — Flowchart covers every enemy move you can actually face *(2026-06-17)*
- The contingency flowchart now gives **every plausible enemy move its own followable branch** (down to a ~2% floor), instead of only forking on moves ≥5% likely and folding the rest into a single "Other moves — mostly X" catch-all that replayed just one tail move. The root cause of "it said the foe would do X, it did Y, and the chart had nothing for Y": a turn only forked when ≥2 moves cleared the 5% floor, and any move between 2% and 5% was hidden inside the catch-all.
- A turn now forks on the enemy move whenever the foe has ≥2 realistic moves (≥2% each), and each gets an explicit `uses <move> (n%)` branch replayed to its own end. Crit/KO forks still nest beneath the pinned move, so you keep the "X uses Wild Charge → and then if it crits…" nesting.
- New `_CONTINGENCY_MOVE_FLOOR` (0.02) drives enemy-move branching, separate from the crit/KO axes which still use the 5% floor. Node budget raised 220→500 to fit the wider trees; cumulative path-probability gating still stops unlikely tangents early so it doesn't blow up.
- Singles only for now — the 2v2 line is still not branched.

## Push 17 — Deep contingency flowchart in its own PDF *(2026-06-15)*
- The contingency flowchart now branches on the full enemy move distribution, not just the top 2–3 near-tied moves: every move down to a 5% floor gets its own branch, plus a catch-all ("Other moves — mostly X (Y%)") that folds the long tail so the odds sum to ~100% — answering "it picks Wild Charge 41% / Flare Blitz 37%, so what does it do the other 12%?".
- New crit fork axis: when the enemy's attack would flip the active mon from surviving to fainting on a critical hit, the chart forks "crits (X%) vs no crit", and the crit branch replays the planner's real response (the switch/move you'd actually make after the crit KO). Crits that change nothing are not branched, and crit forks are skipped in crit-aware mode (everything already crits there).
- Each fork axis fires at most once per turn, so a turn pinned on an enemy move can still fork on a crit beneath it — giving the "X uses Wild Charge → and then if it crits…" nesting.
- Raised the bounds now that it's a standalone document: depth 5→7, total-node budget 60→160, branch floor 0.12→0.05 (so ~6% crit branches survive).
- The flowchart moved out of the Battle Plan PDF into its own **Contingency Flowchart PDF** (new "Contingency flowchart (PDF)" button, prints landscape on a wider canvas). The Battle Plan PDF is back to just the linear lines.
- Singles only for now — the 2v2 line is still not branched.

## Push 16 — Emulator memory writes (team/item test harness foundation) *(2026-06-13)*
- Added memory-write support to the mGBA bridge — the missing prerequisite for setting up a planned team / moving held items so the emulator bruteforce can test the line planner's configuration: WRITE8/WRITE16/WRITE32/WRITEBLOCK in the C bridge (compiles against libmgba) and the Lua fallback bridge, exposed as `write_u8/u16/u32/write_block` on MGBAInstance.
- New `emulator/state_writer.py`: `StateWriter` sets battler HP today (offsets already confirmed) for exact test positions, and held items once the offset is probed — it refuses to guess, because Run & Bun's in-battle battler struct is custom (HP at 0x58, not vanilla 0x28).
- config: added a `PLAYER_ITEM_OFFSET` hook (None until probed on the ROM).
- Verified: C bridge compiles against libmgba; write commands format correctly and base64 round-trips through WRITEBLOCK.
- Still to do (needs the ROM + probing to build/validate): full planned-team injection (encrypted Gen-3 party encoder), the probed item offset, and routing MCTS action selection through the line planner on a planner-cooked team.

## Push 15 — Contingency flowchart *(2026-06-13)*
- The Battle Plan now includes a contingency flowchart: instead of one assumed timeline, it branches on the two things that are genuinely uncertain in a real fight — whether a non-guaranteed attack actually KOs, and which move the enemy AI picks when two are near-tied (e.g. Will-O-Wisp vs an attack) — and replays each branch to its own end.
- Each branch is a faithful alternate line, not a guess: the builder pins one outcome at the fork turn (enemy move, or attack-KOs-yes/no) and re-runs the same deterministic sim, so the continuation is exactly what the planner would do from there.
- Bounded so it stays readable: forks only fire on meaningful uncertainty (KO odds between 5% and 95%, or ≥2 enemy moves within a near-tie), with depth, probability, and total-node caps.
- Added to the printable Battle Plan PDF as its own section right after Line 1; nothing else was removed or reordered.
- Singles only for now — the 2v2 line is not branched yet.

## Push 14 — Canonical enemy AI: switch-in scoring + Will-o-Wisp *(2026-06-13)*
- Enemy post-KO switch-in now follows the RnB "Post-KO Switch-in AI - Switch Scores" table verbatim (saved at data/rnb_post_ko_switchin_ai.pdf): discrete +5/+4/+3/+2/+1/0/−1 conditions plus the Ditto/Wynaut/Wobbuffet special cases, with ties broken by party order.
- Fixed the switch-in tie-break: the old continuous heuristic broke ties toward the *last* party slot, so e.g. Breeder Corgi sent Lucario instead of the earlier-slot answer. The table sends the earliest party member on a tie.
- Fixed Will-o-Wisp / burn scoring: it was a flat +7 against physical attackers, which beat the top attack (+6.4) every time and made mons like Corgi's Arcanine throw Will-O-Wisp deterministically. Per the AI doc the +1 physical-target and +1 Hex bonuses only apply ~37% of the time, so WoW now scores +6 base + 0.37 per bonus — landing just under the top attack, so the AI attacks unless it genuinely can't.
- Will-o-Wisp scoring works in doubles too: the physical-target bonus already applied, and the Hex bonus now also counts the AI's *partner's* Hex (per the doc's "AI mon or its partner has Hex"), threaded through the doubles enemy-targeting path.
- Saved the source references into the repo: data/rnb_post_ko_switchin_ai.pdf and data/rnb_ai_document.pdf.

## Push 13 — Doubles line search *(2026-06-13)*
- Doubles line finder now runs a real local line search (the 2v2 analog of the singles search) instead of a single greedy pass — it explores far more lines and can surface niche plays the greedy pass never reached.
- Alternate openings: the search tries non-default lead pairs (ranked by the doubles lead heuristic) and keeps the best, e.g. an Intimidate/bait/sleep-immune lead that beats the raw best-damage pair.
- Forced and suppressed voluntary pivots at the lowest-confidence turns: it forces each bench mon into a slot, or pins a mon in place, hill-climbing toward the most stable line (bait pivots, planned sacrifices).
- Both doubles entry points (team-select for larger boxes and the ≤4 standalone path) now route through the search with a proper progress stage; per-roster ranking stays on the cheap greedy pass so team selection stays fast.
- The chosen line records what the search changed (alternate leads + forced pivots) under `line_search`.
- Closes the singles/doubles gap: doubles used to run a handful of greedy sims while singles ran 100+ with full line search.
- Added 8 doubles tests: forced leads, invalid-lead fallback, forced/suppressed pivots, candidate generation, the never-worse-than-greedy invariant, lead exploration, and search routing.

## Push 12 — Greedy ideal items for singles too *(2026-06-12)*
- The greedy held-item search (adopt a recommended item only if it improves the simulated line) now powers single battles as well as doubles, via a shared helper — so defensive berries are never blindly swapped for offensive items that lose a line.
- Singles "ideal item" line now evaluates BOTH the greedy subset and the recommender's full set with the full line search and keeps the better of the two, so the optimized line never falls below the previous all-or-nothing behaviour.
- STAB type-boost items (Miracle Seed, Mystic Water, Soft Sand, etc.) and Sitrus Berry are offered as greedy candidates in both formats — catching upgrades the singles-tuned recommender misses (e.g. Wise Glasses +10% vs Miracle Seed +20% on a Grass attacker).
- Line-quality ranking gained an enemy-HP-remaining tiebreaker: between two otherwise-equal lines, the one that chipped the enemies more wins, so offensive item upgrades register even on non-winning partial lines.
- Verified scaling against the daycare gauntlet: Sr. And Jr. Anna And Meg (the doubles fight before Chelle) holds at 0.82 with ideal items; Boss Chelle reaches 0.71 with a box at her level and 0.84 with a lightly (L36) over-levelled box — confirming confidence climbs as the player levels, exactly as a real playthrough would.

## Push 11 — Doubles confidence model + ideal-item lines *(2026-06-12)*
- Board-redundancy confidence: a non-lethal disruption (paralysis/burn/flinch) on one slot no longer tanks the line when its partner is still alive and acting — reflecting real 2v2 dynamics, where one mon's disruption rarely flips a fight you're winning.
- Immunity-aware threats: a damaging move that deals 0 (e.g. Discharge into a Ground type) can no longer drain confidence with phantom secondary-effect risk.
- Voluntary pivots: a slot facing lethal combined damage from both enemies switches to the bench mon that tanks the incoming hits best (e.g. Ground type in vs Discharge) instead of dying.
- Reliable-action override: low-accuracy status picks (e.g. Sing at 55%) are swapped for the slot's best guaranteed-damage move, since a missed status turn is pure confidence loss in doubles.
- Doubles now fields up to 6 party mons (2 active + 4 bench), not 4; bench fills and post-KO promotions reset turns-out so Fake Out is correctly available once per entry.
- Player move accuracy and unplanned-death trade costs are folded into the doubles confidence the same way singles already does.
- Greedy "ideal item" lines: recommended held items are adopted one at a time and kept only if they improve the simulated line, so defensive berries are never stripped wholesale; STAB type-boost items (Miracle Seed, Mystic Water, etc.) the singles recommender missed are now candidates.
- Team selection ranks rosters by their item-equipped line; the optimized-item line is only surfaced when it genuinely beats the current-items line.
- Fixed a state-leak where the base simulation mutated a roster's HP, poisoning every subsequent item trial (caused phantom multi-death lines).
- Result: Sr. And Jr. Anna And Meg (2v2) line raised from 0.285 to 0.82 with recommended items.

## Push 10 — Doubles priority + flinch fixes *(2026-06-12)*
- Actor queue now sorts by (move_priority, speed) instead of speed alone — Fake Out (priority +3), Bullet Punch (+1), Quick Attack (+1), etc. correctly fire before faster mons.
- Flinch and sleep are checked before each actor moves: a player flinched by Fake Out or Iron Head can no longer act on that turn. Event log shows "X flinched and couldn't move!".
- Accounts for ability-granted priority: Prankster status moves, Gale Wings flying moves, Triage healing moves all respected.
- Post-KO switch-in AI (doubles): enemy slot A evaluates bench candidates against player slot A only; slot B against slot B only — per the Post-KO Switch-in AI document.
- Player bench fills also slot-matched: replacement for slot A is scored against the enemy in slot A; slot B against slot B.
- Turn labels now show start-of-turn combatants (fixed bug where post-promotion enemies appeared in the current-turn header).

## Push 9 — Double battle AI *(2026-06-12)*
- Full 2v2 doubles sim: two enemy leads + bench, two player leads + bench, all executing in speed order each turn.
- Enemy AI picks the best (move, target) pair for each of its active mons — considers spread moves (Earthquake, Discharge, Dazzling Gleam etc.), partner synergies (EQ partner immune → bonus; partner takes it → penalty), Fake Out first-turn priority, and support moves like Follow Me and Helping Hand.
- Spread moves apply the doubles 2/3 damage modifier and hit both player mons simultaneously.
- Player targeting: assigns each player mon to its best enemy matchup; both slots act each turn.
- Confidence model extended to doubles: each player slot accumulates threat branches from the enemy mons targeting it; both are multiplied per turn.
- Faint handling: slot-aware bench selection — when a player slot faints it brings in the best matchup against the enemy in that slot; enemy post-KO switch-in follows the RnB rule (slot A evaluates replacements against player slot A only, slot B against slot B only).
- 70 doubles trainers now fully supported in the Line Finder.
- Trainer selector shows [2v2] prefix for all double battles; the output panel shows a doubles header with 2v2 context.
- Team select for doubles: runs candidate 4-mon rosters (2 leads + 2 bench) scored against the first two enemies, picks best.

## Push 8 — Strategy toolbox expansion *(2026-06-12)*
- Ten new strategy concepts in the toolbox: win condition preservation, AI move baiting, speed control, setup window creation, PP stalling, hazard management, resource conservation, death fodder routing, sack order optimization, and pivot chain construction.
- Each entry now carries when-to-consider, viability conditions, an example sequence, and its scope (single-turn / multi-turn / full-battle).
- New automatic detections: pivot chains, speed-control moves, hazard moves, immunity baits, fodder routing, sack ordering, and win-condition preservation are bolded in the PDF when the plan uses them.

## Push 7 — Progress bar + changelog *(2026-06-12)*
- Accurate line-finder progress bar: the backend stages every simulation budget and reports completed sims vs planned total.
- The sim now runs off the event loop, so the UI stays responsive while it works.
- Changelog tab in the web UI and this CHANGELOG.md.

## Push 6 — Chelle update: smarter search, honest confidence *(2026-06-12)*
- Fixed confidence double-counting: planned sacrifices/trades now pay a flat stability cost instead of zeroing the line (the death was already the plan).
- Switch-entry turns no longer count flinch branches (the switch-in does not act) and judge crit/KO threats from pre-hit HP.
- Sleeping enemies with guaranteed sleep turns no longer contribute threat branches; Shell Armor blocks crit-risk notes.
- Line search: replays the sim forcing different switch decisions at the weakest turns, including compound bait-pivot pairs (mon in, real answer back in).
- Team hill-climbing over the whole box, Intimidate-pivot roster variants, and lead exploration (e.g. sleep-immune lead into a Sing user).
- Pre-battle prep strategies: pre-status (enter already poisoned to block enemy status) and pre-damage (Endeavor/Flail baits) tried automatically; adopted as "Line 4" when better.
- Strategy Toolbox appendix in the PDF: every strategy the planner knows, with the used ones bolded plus where they fire.
- Result: Daycare Boss Chelle line confidence raised from 0.0 to 0.71 with recommended items / 0.70 with prep.

## Push 5 — Report & planning upgrades *(Breeder Corgi work)*
- Battle Plan printable PDF: plain-English steps, risks, confidence breakdown.
- Held-item recommendations with obtainable sources; optimized-item alternate line.
- Best-case line, flinch/crit hax outlook, berry budget, threat-answer table, heart-scale advisor.
- Tactical sacrifice and bait-pivot scoring; switch-streak guards against ping-ponging.

## Push 4 — Line Finder (calc sim) *(early project)*
- Stateful turn-by-turn line finder: carries HP forward, picks best answers, models AI branches and hard switches.
- Confidence model: accuracy × AI-branch × survival × crit-safety multiplied per turn.
- Showdown-style box import, team-of-6 selection from a larger box, crit-safe mode.

## Push 3 — Run & Bun damage calc + trainer data *(early project)*
- Ported the RnB damage calculator (items, abilities, natures, IVs/EVs, crit model).
- Imported every trainer battle (parties, items, abilities, moves) into `trainer_battles.json`.
- RnB AI document modeled: score-based enemy move choice with probability branches.

## Push 2 — Web UI + live solve view *(early project)*
- FastAPI server with WebSocket updates; React UI with battle tree, plan view, and box candidates.
- Prepare mode: decode the PC box from a save state and craft candidate teams.

## Push 1 — Emulator battle solver (MCTS) *(early project)*
- mGBA-driven battle solver: pool of emulator instances, RNG desync, memory probing of party structs.
- MCTS whole-fight search with deathless-line priority, sack penalties, and nuzlocke weighting.
- CLI: `solve` / `probe` / `calibrate` / `prepare` commands with flowchart + JSON output.
# Doubles board mode

- Added explicit left/right active-slot mappings and actor-slot actions for the emulator solver.
- Added custom doubles opponent/tournament-round imports with lockable player and enemy leads.
- Added player and enemy spread damage, including `allAdjacent` friendly fire and doubles damage modifiers.
- Added doubles contingency replay for move, target, accuracy, damage-roll, and remaining-HP branches.
- Added per-slot UI instructions and a four-position doubles setup table in the battle-plan PDF.
