# Pokémon Emerald mode

Pokémon Emerald is the app's default mode. Run & Bun remains available from the
top-right game switch. The selected mode controls the trainer catalog, calculator
data, planner/flowchart/coach requests, simulator search, gauntlet routes, PDFs,
and saved-run filtering.

## Judge-facing captured run

`data/emerald_checkpoint_library.json` is the distributable, ROM-free battle
library generated from the completed run. The source folder currently contains
173 named checkpoints representing 158 unique trainer encounters. The web app
offers only those encounters in Emerald Simulator and Gauntlet views, while the
full 512-row catalog remains available internally as source data.

The checkpoint JSON stores decoded player parties, eligible Box 1–12 Pokémon,
trainer/map links, and boss classifications. It does not embed ROM bytes or mGBA
save-state binaries. Rebuild it locally with:

```sh
.venv/bin/python tools/build_emerald_checkpoint_library.py \
  --rom /path/to/user-owned-emerald.gba \
  --checkpoints /path/to/Emerald-Auto-Checkpoints
```

Hardcore Nuzlocke is the default ruleset. Battle items and Revives are disabled,
Hint mode starts off, and League Gauntlets use bag healing between rooms. A failed
abstract line may retry with Rare Candy levels up to the current party maximum;
the retry is a last resort and every changed level is disclosed in the run result.
Box 13 and the Box 14 graveyard are excluded.

The committed League audit is `submission/emerald-league-gauntlet.json`, with a
judge-flow recording at `submission/emerald-league-gauntlet-demo.mp4`. The current
Hardcore run accepts Sidney, Phoebe, and Glacia without a faint, then rejects the
Drake line because three party members faint. Wallace is therefore not marked as
validated. This is intentionally reported as a stopped audit rather than a clear.

## Sources

- Trainer order, parties, moves, exact displayed stats, and required-story flags:
  the `Emerald Swampert` tab (`gid=1064630895`) in the supplied Google Sheet.
- Map IDs and trainer event tile coordinates: `pret/pokeemerald` map JSON and
  scripts. The committed catalog currently matches 483 of 512 sheet battles to
  an exact event; every battle still has a route-level Hoenn mapping.
- Species, moves, items, type chart, and physical/special categories:
  generation 3 from `@pkmn/data`.

## Rebuilding data

```sh
.venv/bin/python tools/import_emerald_trainers.py \
  --decomp /path/to/pret/pokeemerald \
  --output data/emerald_trainers.json

.venv/bin/python tools/build_calc_data.py \
  --generation 3 \
  --output data/calc_data_gen3.json
```

The battle engine applies Emerald's 2x critical modifier, Gen III critical-stage
table, type-based physical/special split, 50% doubles spread modifier, exact sheet
stats, and party-order post-KO send-outs. Run & Bun cartridge playbooks and custom
switch-scoring rules are never reused in Emerald mode.

Cartridge proof requires a local vanilla Pokémon Emerald ROM and a compatible mGBA
checkpoint. ROM files are never included in or uploaded by this project.
