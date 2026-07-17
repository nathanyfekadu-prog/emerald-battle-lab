from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emulator.map_explorer import MapExplorer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--nodes", type=int, default=300)
    args = parser.parse_args()
    result = MapExplorer(args.rom, args.state, args.output).explore(max_nodes=args.nodes)
    print({"nodes": len(result["nodes"]), "edges": len(result["edges"]), "terminals": len(result["terminals"])})


if __name__ == "__main__":
    main()
