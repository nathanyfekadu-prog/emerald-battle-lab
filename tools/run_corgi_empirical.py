"""Long empirical search for the supplied Breeder Corgi battle state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from search.mcts import MCTS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--iterations", type=int, default=120)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", default="output/autonomy/corgi-mcts.json")
    args = parser.parse_args()
    last = -1

    def progress(data: dict) -> None:
        nonlocal last
        item = data.get("progress", {})
        done = int(item.get("iterations_done", 0))
        message = str(item.get("message") or "")
        if done != last and (done % 10 == 0 or "Confidence" in message):
            last = done
            print({
                "done": done, "total": item.get("iterations_total"),
                "trials": item.get("trials_run"), "best": item.get("best_win_rate"),
                "deathless": item.get("has_deathless"), "message": message,
            }, flush=True)

    search = MCTS(
        args.rom, args.state, pool_size=args.workers, max_turns=24,
        trials_per_node=args.workers, final_line_trials=30, final_line_candidates=3,
        on_node_visited=progress,
    )
    try:
        result = search.search(iterations=args.iterations)
        data = result.to_dict()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print({
            "win_probability": result.win_probability,
            "faint_probability": result.faint_probability,
            "nuzlocke_safe": result.nuzlocke_safe,
            "trials": result.total_trials_run,
            "seconds": result.search_time_seconds,
            "output": str(output.resolve()),
        }, flush=True)
        print(json.dumps(data["recommended_line"], indent=2), flush=True)
    finally:
        search.shutdown()


if __name__ == "__main__":
    main()
