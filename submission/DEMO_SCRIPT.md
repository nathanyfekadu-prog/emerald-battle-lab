# Demo video script — target length 2:40

Record at 1080p. Keep the mouse still unless you're clicking something. Use your own voice; it will sound better than generated narration.

## 0:00–0:18 — The problem

**Screen:** Judge Demo title and proof counters.

**Say:**

“This is Pokemon Battle Solver. Difficult ROM hacks usually mean testing the same fight by hand, resetting, and changing one move at a time. My project reads the battle directly from mGBA, searches for a winning sequence, and plays that sequence back in the real game.”

## 0:18–0:42 — ROM-free evidence

**Screen:** Scroll through Judge Demo: pipeline, Chelle snapshot, eight-fight route.

**Say:**

“The judge mode needs no ROM. This sample comes from a connected Run & Bun route: eight trainers, 94 battle turns, two deathless completions, and zero state reloads after either run started. It contains only sanitized state and recorder metadata.”

## 0:42–1:10 — Strategy Lab

**Screen:** Open Strategy Lab. Click Matchup, Contingencies, then Turn Coach.

**Say:**

“Strategy Lab keeps the planning tools together. Matchup compares full lines, Contingencies shows what to do when the opponent takes another branch, and Turn Coach updates the recommendation after each observed turn. Calculator output helps rank choices, but it never gets the final word.”

## 1:10–1:38 — Live cartridge read

**Screen:** Simulator, configure the known Chelle checkpoint, click Check battle, show recognized trainer and moves.

**Say:**

“Here is a live checkpoint before Trainer Chelle. The native libmgba bridge reads both parties, HP, moves, the active matchup, and the trainer fingerprint. Search workers can restart this exact position in parallel without opening a pile of emulator windows.”

## 1:38–2:08 — Proof video

**Screen:** Gauntlet, first verified 8/8 card, then play 20–25 seconds of the uncut recording at a battle transition or final win.

**Say:**

“A proposed plan only earns this cartridge-verified label after the emulator reaches the win and the recorder passes its proof checks. This route beat Corgi through Chelle twice. The longer public recording shows the complete run, including Center visits and party preparation.”

## 2:08–2:34 — Codex and GPT-5.6

**Screen:** Brief architecture diagram in README, then test result.

**Say:**

“I built this with Codex [say the exact model name shown in your session]. Codex helped trace the memory layout, build the C bridge, debug controller timing, compare failed replays against predicted state, and turn those failures into regression tests. The project now has 150 passing tests.”

## 2:34–2:48 — Close

**Screen:** Return to Judge Demo.

**Say:**

“The current target is Pokemon Run & Bun on Emerald. It isn't a universal Pokemon solver yet. What works today is the part I cared about most: read a real position, produce a strategy, and make the cartridge prove it.”

## Recording notes

- Replace the bracketed model wording after checking the Codex task details.
- Don't show ROM filenames, Downloads paths, save-state paths, or emulator setup dialogs containing personal folders.
- Put the short demo on public YouTube. Upload the long uncut proof separately and link it in the description.
- End before 2:55 so YouTube processing or title frames cannot push the video over three minutes.

