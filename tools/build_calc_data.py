from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = r"""
const {Dex} = require('@pkmn/dex');
const {Generations} = require('@pkmn/data');

// With `node -e`, the first argument after the script is argv[1].  Using argv[2]
// silently built the default generation regardless of --generation.
const generation = Number(process.argv[1] || 9);
const gen = new Generations(Dex).get(generation);

const species = {};
const speciesByNum = {};
for (const item of gen.species) {
  const num = Dex.species.get(item.id).num || 0;
  if (num <= 0) continue;
  species[item.id] = {
    id: item.id,
    num,
    name: item.name,
    types: item.types,
    baseStats: item.baseStats,
    evos: Dex.species.get(item.id).evos || [],
  };
  if (!speciesByNum[num]) speciesByNum[num] = item.id;
}

const moves = {};
const movesByNum = {};
for (const item of gen.moves) {
  if (!item.id) continue;
  const raw = Dex.moves.get(item.id);
  moves[item.id] = {
    id: item.id,
    num: raw.num || 0,
    name: item.name,
    type: item.type,
    category: item.category,
    basePower: item.basePower || 0,
    accuracy: raw.accuracy === true ? 100 : raw.accuracy || 100,
    priority: item.priority || 0,
    target: item.target || "normal",
    flags: item.flags || {},
    secondary: raw.secondary || null,
    secondaries: item.secondaries || null,
    hasSecondary: item.secondaries || item.secondary ? true : false,
    recoil: item.recoil || null,
    hasCrashDamage: !!item.hasCrashDamage,
    ignoreDefensive: !!item.ignoreDefensive,
    overrideDefensiveStat: item.overrideDefensiveStat || null,
  };
  if (raw.num && raw.num > 0 && !movesByNum[raw.num]) movesByNum[raw.num] = item.id;
}

const items = {};
const itemsByNum = {};
for (const item of gen.items) {
  if (!item.id) continue;
  const num = Dex.items.get(item.id).num || 0;
  items[item.id] = {
    id: item.id,
    num,
    name: item.name,
  };
  if (num > 0 && !itemsByNum[num]) itemsByNum[num] = item.id;
}

const typeChart = {};
for (const attackingType of gen.types) {
  typeChart[attackingType.name] = {};
  for (const defendingType of gen.types) {
    typeChart[attackingType.name][defendingType.name] =
      attackingType.effectiveness[defendingType.name] ?? 1;
  }
}

console.log(JSON.stringify({
  source: '@pkmn/data',
  generation,
  species,
  speciesByNum,
  moves,
  movesByNum,
  items,
  itemsByNum,
  typeChart,
}, null, 2));
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compact damage-calc data from @pkmn/data")
    parser.add_argument("--generation", type=int, default=9)
    parser.add_argument("--output", default="data/calc_data.json")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_build(args.generation), encoding="utf-8")
    print(f"Wrote calc data to {output}")


def _build(generation: int) -> str:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        subprocess.run(["npm", "init", "-y"], cwd=temp, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            ["npm", "install", "@pkmn/dex", "@pkmn/data"],
            cwd=temp,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        result = subprocess.run(
            ["node", "-e", SCRIPT, str(generation)],
            cwd=temp,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        return result.stdout


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        raise SystemExit("Node.js and npm are required to rebuild calc data") from exc
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
