from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = r"""
const {Dex} = require('@pkmn/dex');

const generation = Number(process.argv[2] || 9);
const gen = Dex.mod(`gen${generation}`);

const species = {};
const speciesByNum = {};
for (const item of gen.species.all()) {
  if (!item.exists || item.num <= 0) continue;
  species[item.id] = {
    id: item.id,
    num: item.num,
    name: item.name,
    types: item.types,
    baseStats: item.baseStats,
    evos: item.evos || [],
  };
  if (!speciesByNum[item.num]) speciesByNum[item.num] = item.id;
}

const moves = {};
const movesByNum = {};
for (const item of gen.moves.all()) {
  if (!item.exists || !item.id) continue;
  moves[item.id] = {
    id: item.id,
    num: item.num || 0,
    name: item.name,
    type: item.type,
    category: item.category,
    basePower: item.basePower || 0,
    accuracy: item.accuracy === true ? 100 : item.accuracy || 100,
    priority: item.priority || 0,
    target: item.target || "normal",
    flags: item.flags || {},
    secondary: item.secondary || null,
    secondaries: item.secondaries || null,
    hasSecondary: item.secondaries || item.secondary ? true : false,
    recoil: item.recoil || null,
    hasCrashDamage: !!item.hasCrashDamage,
    ignoreDefensive: !!item.ignoreDefensive,
    overrideDefensiveStat: item.overrideDefensiveStat || null,
  };
  if (item.num && item.num > 0 && !movesByNum[item.num]) movesByNum[item.num] = item.id;
}

const items = {};
const itemsByNum = {};
for (const item of gen.items.all()) {
  if (!item.exists || !item.id) continue;
  items[item.id] = {
    id: item.id,
    num: item.num || 0,
    name: item.name,
  };
  if (item.num && item.num > 0 && !itemsByNum[item.num]) itemsByNum[item.num] = item.id;
}

const typeChart = {};
for (const attackingType of gen.types.all()) {
  if (!attackingType.exists) continue;
  typeChart[attackingType.name] = {};
  for (const defendingType of gen.types.all()) {
    if (!defendingType.exists || !defendingType.damageTaken) continue;
    const value = defendingType.damageTaken[attackingType.name] || 0;
    typeChart[attackingType.name][defendingType.name] =
      value === 1 ? 2 : value === 2 ? 0.5 : value === 3 ? 0 : 1;
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
