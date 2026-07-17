"""Savestate-backed discovery of walkable maps, warps, and encounter boundaries."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from emulator.game_state import GameMode, WholeGameStateReader
from emulator.mgba_instance import MGBAInstance
from emulator.overworld import OverworldMover


@dataclass(frozen=True)
class MapNode:
    map_group: int
    map_number: int
    x: int
    y: int
    state_path: str

    @property
    def key(self) -> tuple[int, int, int, int]:
        return self.map_group, self.map_number, self.x, self.y


class MapExplorer:
    def __init__(self, rom_path: str, state_path: str, output_dir: str | Path):
        self.rom_path = rom_path
        self.state_path = str(Path(state_path).expanduser().resolve())
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def explore(self, *, max_nodes: int = 300) -> dict:
        instance = MGBAInstance(self.rom_path, self.state_path, 92)
        reader = WholeGameStateReader(instance)
        mover = OverworldMover(instance)
        try:
            root_snapshot = reader.read()
            if root_snapshot.mode != GameMode.OVERWORLD or root_snapshot.map_id is None:
                raise ValueError("Map exploration must start on a visible overworld frame.")
            root_path = instance.save_state(self.output_dir / "root.ss0")
            root = MapNode(*root_snapshot.map_id, root_snapshot.x, root_snapshot.y, str(root_path))
            queue = deque([root])
            nodes = {root.key: root}
            edges: list[dict] = []
            terminals: list[dict] = []
            while queue and len(nodes) < max_nodes:
                node = queue.popleft()
                for direction in ("UP", "RIGHT", "DOWN", "LEFT"):
                    instance.save_state_path = Path(node.state_path)
                    instance.load_state()
                    before = mover.position()
                    moved = mover.step(direction)
                    instance.advance_frames(24)
                    snapshot = reader.read()
                    target_key = (
                        snapshot.map_group, snapshot.map_number, snapshot.x, snapshot.y
                    ) if snapshot.map_id is not None else None
                    edge = {"from": list(node.key), "direction": direction, "moved": moved}
                    if not moved and target_key == node.key:
                        edge["kind"] = "blocked"
                        edges.append(edge)
                        continue
                    edge["to"] = list(target_key) if target_key else None
                    edge["kind"] = "walk" if target_key and target_key[:2] == node.key[:2] else "warp"
                    edge["mode"] = snapshot.mode.value
                    edge["trainer"] = snapshot.trainer_name
                    edges.append(edge)
                    if target_key is None or snapshot.mode != GameMode.OVERWORLD:
                        terminal_path = instance.save_state(
                            self.output_dir / f"terminal-{len(terminals):04d}.ss0"
                        )
                        terminals.append({**edge, "state_path": str(terminal_path)})
                        continue
                    if target_key not in nodes:
                        child_path = instance.save_state(self.output_dir / f"node-{len(nodes):04d}.ss0")
                        child = MapNode(*target_key, str(child_path))
                        nodes[target_key] = child
                        queue.append(child)
                self._write(nodes, edges, terminals)
            return self._write(nodes, edges, terminals)
        finally:
            instance.shutdown()

    def _write(self, nodes: dict, edges: list[dict], terminals: list[dict]) -> dict:
        data = {
            "source": self.state_path,
            "nodes": [asdict(node) for node in nodes.values()],
            "edges": edges,
            "terminals": terminals,
        }
        (self.output_dir / "map.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data


__all__ = ["MapExplorer", "MapNode"]
