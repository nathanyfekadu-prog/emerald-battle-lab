# Pokemon Battle Solver

**Track:** Apps for your life

**Tagline:** A cartridge-verified battle search engine for Pokemon Emerald challenge runs.

## Inspiration

Hard Pokemon ROM hacks turn important fights into long manual experiments. A player saves before the battle, tries a line, resets, changes one move, and repeats the process until something survives the opponent's AI and damage rolls. A calculator can suggest an attack, but it can't prove that the whole sequence works inside the game.

I wanted the equivalent of a chess engine for one specific target: Pokemon Run & Bun, an Emerald-based challenge hack. The program should read the current position, search possible decisions, play its answer, and accept the answer only when the emulator confirms the win.

## What it does

Pokemon Battle Solver reads a local mGBA checkpoint and decodes the player's party, current HP, active Pokemon, moves, enemy team, battle type, and trainer identity. It can then:

- compare complete battle lines rather than isolated damage rolls;
- model switches, forced replacements, status, singles, and doubles;
- replay a selected policy through real emulator inputs;
- save the turn log, final state, and MP4 recording;
- reject a proposed result when the cartridge disagrees with the internal model.

The largest recorded test clears eight connected trainers from Breeder Corgi through Trainer Chelle. The route passed twice with no cuts, no state reload after the run began, and no use of the Nuzlocke graveyard box. The committed Judge Demo exposes a sanitized version of that evidence without requiring a ROM.

## How I built it

The project uses Python and FastAPI for the application layer. A small native C bridge connects to libmgba so Python can advance frames, read RAM, send button inputs, save checkpoints, and capture frames without running eight visible emulator windows.

The state reader turns game memory into battle objects. The search layer enumerates legal moves and switches, uses calculator estimates to rank branches, and can run multiple emulator copies against the same checkpoint. The replay controller then executes the chosen policy in mGBA. Recorded cartridge state wins every disagreement with the calculator.

The browser interface contains four working surfaces: a ROM-free judge sample, a Strategy Lab for matchup and contingency analysis, a one-fight simulator, and a connected gauntlet runner. There are 150 automated tests covering state reads, damage calculations, doubles behavior, planning, controller safeguards, preparation, and output generation.

## How I used Codex

Codex was the engineering agent for the project, not a chat feature added at the end. I used it to trace Emerald and Run & Bun memory structures, build the libmgba bridge, write controller code, investigate replay divergence, add doubles handling, repair route automation, build the browser interface, and grow the regression suite.

The most useful Codex work happened when predictions failed. A calculator line could look correct while the game selected a different target, opened a forced-switch menu, or consumed extra frames. Codex compared state snapshots, recorder logs, controller code, and captured frames; we then changed the implementation and added a regression test. That loop produced the cartridge-proof gate used by the current demo.

**Submission field:** add the `/feedback` session ID from the Codex task containing most of the core work.

## Challenges

Run & Bun changes enough mechanics, trainer data, and AI behavior that an ordinary Generation III calculator is only a ranking aid. Emulator automation also fails in mundane ways: one missed menu, one unexpected target prompt, or one timing difference can invalidate a twenty-turn line.

The hard engineering choice was to stop treating the planner as the authority. A line is marked verified only after repeated emulator playback and a complete recording. Some routes still use saved cartridge playbooks when the general search misses; the interface labels planner-only output separately from verified evidence.

## Accomplishments

- Live mGBA memory decoding recognizes Trainer Chelle and reconstructs both teams, moves, and HP.
- The connected route recorder completed eight fights across 94 turns, twice, without a mid-run state reload.
- The app records failed experiments instead of presenting them as wins.
- A judge can inspect sanitized sample evidence without installing Pokemon or providing a ROM.
- The full suite currently passes 150 tests.

## What I learned

Game automation needs a source of truth. My first instinct was to keep improving calculator accuracy, but a slightly better approximation still isn't proof. The useful architecture came from letting the model search cheaply and making the emulator verify expensively.

I also learned that reproducibility has several meanings. Replaying one exact save state proves controller execution. Sampling different RNG outcomes tests whether the line is range-safe. Replaying a connected route without reloading tests whether healing, party changes, consumed items, and overworld movement actually carry forward.

## What's next

The next engineering target is removing the remaining saved-playbook fallback so every recorded policy comes directly from general search. After that, I would package the native bridge for more operating systems, calibrate confidence against cartridge outcomes, and add versioned memory maps for other Emerald builds.

## Running it

The repository does not contain a Pokemon ROM. Judge Demo works without one. Live search requires a locally supplied Pokemon Run & Bun ROM and compatible mGBA checkpoint; setup instructions are in the README.

## Links to add before submission

- Public demo video under three minutes: `[YOUTUBE_DEMO_URL]`
- Longer uncut cartridge proof: `[UNCUT_PROOF_URL]`
- Repository: `[REPOSITORY_URL]`
- Codex `/feedback` session ID: `[FEEDBACK_SESSION_ID]`

