"""Verified overworld movement.

Walking in a GBA Pokemon game is not "press a direction and you moved a tile": the first
press while facing another way only turns you, and a press into a wall does nothing. So this
helper drives movement against the live player coordinates (gObjectEvents[0].currentCoords)
and confirms the tile actually changed, retrying the turn-then-step case and reporting blocked
moves instead of silently desyncing whatever runs next.
"""

from __future__ import annotations

import config
from emulator.mgba_instance import MGBAInstance

# button -> (dx, dy) in tile space (y grows downward).
_DELTAS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}

# Frames to hold a direction for one step, and to let the step animation finish.
_STEP_FRAMES = 16
_STEP_SETTLE = 6


class OverworldMover:
    def __init__(
        self,
        instance: MGBAInstance,
        *,
        x_addr: int = config.RUN_BUN_PLAYER_X,
        y_addr: int = config.RUN_BUN_PLAYER_Y,
    ):
        self.instance = instance
        self.x_addr = x_addr
        self.y_addr = y_addr

    def position(self) -> tuple[int, int]:
        return self.instance.read_u16(self.x_addr), self.instance.read_u16(self.y_addr)

    def step(self, direction: str) -> bool:
        """Move one tile in `direction`. Returns True if the player actually moved.

        Handles the turn-then-move case (the first press may only rotate the player) by
        pressing again, and returns False if the way is blocked."""
        direction = direction.upper()
        if direction not in _DELTAS:
            raise ValueError(f"direction must be one of {sorted(_DELTAS)}")
        before = self.position()
        self._press(direction)
        if self.position() != before:
            return True
        # Either we only turned to face this way, or we're blocked — one more press settles it.
        self._press(direction)
        return self.position() != before

    def walk(self, direction: str, tiles: int) -> int:
        """Walk up to `tiles` steps in `direction`; stop early if blocked. Returns tiles moved."""
        moved = 0
        for _ in range(tiles):
            if not self.step(direction):
                break
            moved += 1
        return moved

    def interact(self, *, settle_frames: int = 45) -> None:
        """Interact with the faced tile and allow dialogue/menu scripts to settle."""
        self.instance.send_input("A", 2)
        self.instance.advance_frames(settle_frames)

    def tap(self, button: str, *, settle_frames: int = 12) -> None:
        """Press a general GBA button for menu, bag, PC, and dialogue scripts."""
        self.instance.send_input(button.upper(), 2)
        self.instance.advance_frames(settle_frames)

    def walk_path(self, directions: list[str], *, blocked_limit: int = 2) -> list[tuple[int, int]]:
        """Run a tile path, stopping before repeated blocked inputs can desync a route."""
        positions = [self.position()]
        blocked = 0
        for direction in directions:
            if self.step(direction):
                blocked = 0
                positions.append(self.position())
                continue
            blocked += 1
            if blocked >= blocked_limit:
                raise RuntimeError(
                    f"Overworld route is stuck at {self.position()} while moving {direction}."
                )
        return positions

    def face(self, direction: str) -> None:
        """Turn to face `direction` without requiring a move (a single tap turns in place)."""
        direction = direction.upper()
        if direction not in _DELTAS:
            raise ValueError(f"direction must be one of {sorted(_DELTAS)}")
        self._press(direction)

    def _press(self, direction: str) -> None:
        self.instance.send_input(direction, _STEP_FRAMES)
        self.instance.advance_frames(_STEP_SETTLE)


__all__ = ["OverworldMover"]
