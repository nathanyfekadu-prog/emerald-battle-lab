"""Attach verified emulator evidence to an existing saved gauntlet log."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "output" / "gauntlet_runs"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--fixed-validation", required=True)
    parser.add_argument("--sampled-validation")
    args = parser.parse_args()

    if not args.run_id.replace("-", "").isalnum():
        raise ValueError("invalid run id")
    record_path = RUNS / f"{args.run_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    result = record.setdefault("result", {})
    video = Path(args.video).expanduser().resolve()
    manifest = Path(args.manifest).expanduser().resolve()
    fixed_path = Path(args.fixed_validation).expanduser().resolve()
    sampled_path = Path(args.sampled_validation).expanduser().resolve() if args.sampled_validation else None
    for path in (video, manifest, fixed_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    if sampled_path and not sampled_path.is_file():
        raise FileNotFoundError(sampled_path)

    fixed = json.loads(fixed_path.read_text(encoding="utf-8"))
    sampled = json.loads(sampled_path.read_text(encoding="utf-8")) if sampled_path else {}
    relative_video = video.relative_to(RUNS)
    result.update({
        "emulator_result": "chelle-verified",
        "emulator_verified_at": datetime.now(timezone.utc).isoformat(),
        "emulator_validation": {
            "fixed_replays": fixed.get("replays", 0),
            "fixed_deathless_wins": fixed.get("deathless_wins", 0),
            "sampled_replays": sampled.get("replays", 0),
            "sampled_deathless_wins": sampled.get("deathless_wins", 0),
            "sampled_faints": sampled.get("faints", 0),
            "sampled_errors": sampled.get("errors", 0),
        },
        "videos": [{
            "kind": "emulator_verified_chelle",
            "label": "Uncut normal-speed Chelle victory — legal Box 1 team",
            "video_url": "/gauntlet-artifacts/" + relative_video.as_posix(),
            "video_ready": video.stat().st_size > 0,
        }],
        "emulator_artifacts": {
            "manifest": str(manifest.relative_to(ROOT)),
            "fixed_validation": str(fixed_path.relative_to(ROOT)),
            "sampled_validation": str(sampled_path.relative_to(ROOT)) if sampled_path else None,
        },
    })
    temporary = record_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temporary.replace(record_path)
    print(json.dumps({
        "run_id": args.run_id,
        "emulator_result": result["emulator_result"],
        "video_url": result["videos"][0]["video_url"],
        "validation": result["emulator_validation"],
    }, indent=2))


if __name__ == "__main__":
    main()
