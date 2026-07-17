"""Checkpointed overworld execution primitives for the long-running game agent.

Routes are deliberately data, not Python callbacks: they can be logged, replayed, edited in
the browser, and resumed from the last successful savestate after a blocked tile or bad menu.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from emulator.mgba_instance import MGBAInstance
from emulator.overworld import OverworldMover
import config


@dataclass(frozen=True)
class RouteAction:
    kind: str
    value: str | None = None
    count: int = 1
    settle_frames: int = 12


@dataclass
class RouteRun:
    run_id: str
    route_name: str
    source_state: str
    status: str = "running"
    action_index: int = 0
    position: tuple[int, int] | None = None
    checkpoints: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class CheckpointedGameRunner:
    """Execute overworld/menu actions with a savestate and manifest after every milestone."""

    def __init__(self, instance: MGBAInstance, output_dir: str | Path):
        self.instance = instance
        self.mover = OverworldMover(instance)
        self.output_dir = Path(output_dir).expanduser().resolve()

    def run(
        self,
        route_name: str,
        actions: list[RouteAction],
        *,
        checkpoint_every: int = 1,
    ) -> RouteRun:
        now = datetime.now(timezone.utc)
        safe_name = re.sub(r"[^a-z0-9]+", "-", route_name.casefold()).strip("-") or "route"
        run = RouteRun(
            run_id=f"{now.strftime('%Y%m%dT%H%M%SZ')}-{safe_name}",
            route_name=route_name,
            source_state=str(self.instance.save_state_path),
        )
        run_dir = self.output_dir / run.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            run.position = self.mover.position()
            self._checkpoint(run, run_dir, "start")
            for index, action in enumerate(actions):
                before = self.mover.position()
                self._execute(action)
                after = self.mover.position()
                run.action_index = index + 1
                run.position = after
                run.events.append({
                    "index": index,
                    "action": asdict(action),
                    "position_before": list(before),
                    "position_after": list(after),
                    "at": datetime.now(timezone.utc).isoformat(),
                })
                if action.kind == "checkpoint" or (
                    checkpoint_every > 0 and run.action_index % checkpoint_every == 0
                ):
                    self._checkpoint(run, run_dir, f"step-{run.action_index:04d}")
            run.status = "complete"
        except Exception as exc:
            run.status = "blocked"
            run.error = str(exc)
            self._checkpoint(run, run_dir, f"blocked-{run.action_index:04d}")
            raise
        finally:
            self._write_manifest(run, run_dir)
        return run

    def _execute(self, action: RouteAction) -> None:
        if action.kind == "walk":
            self.mover.walk_path([action.value or ""] * max(1, action.count))
        elif action.kind == "path":
            directions = [part.strip() for part in (action.value or "").split(",") if part.strip()]
            self.mover.walk_path(directions)
        elif action.kind == "face":
            self.mover.face(action.value or "")
        elif action.kind == "interact":
            self.mover.interact(settle_frames=action.settle_frames)
        elif action.kind == "button":
            for _ in range(max(1, action.count)):
                self.mover.tap(action.value or "", settle_frames=action.settle_frames)
        elif action.kind == "wait":
            self.instance.advance_frames(max(0, action.count))
        elif action.kind == "open_bag":
            # Select the verified BAG row directly. Directional "clamping" is unsafe here:
            # Emerald's pause menu wraps from top to bottom.
            self.mover.tap("START", settle_frames=30)
            self.instance.write_u8(config.RUN_BUN_START_MENU_CURSOR, 2)
            self.mover.tap("A", settle_frames=150)
        elif action.kind == "close_menu":
            for _ in range(max(1, action.count)):
                self.mover.tap("B", settle_frames=20)
        elif action.kind != "checkpoint":
            raise ValueError(f"Unknown route action {action.kind!r}")

    def _checkpoint(self, run: RouteRun, run_dir: Path, label: str) -> None:
        path = self.instance.save_state(run_dir / f"{label}.ss0")
        run.checkpoints.append(str(path))
        self._write_manifest(run, run_dir)

    @staticmethod
    def _write_manifest(run: RouteRun, run_dir: Path) -> None:
        path = run_dir / "run.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(run), indent=2), encoding="utf-8")
        temporary.replace(path)


__all__ = ["CheckpointedGameRunner", "RouteAction", "RouteRun"]
