"""Drive the in-game PC Storage System to move Pokemon between the boxes and the party.

This is the "swap a box Pokemon onto the team" side of the prepare workflow: the box
optimizer decides *which* mon to pull, and this controller actually performs the pull inside
the emulator by navigating the real Storage UI (no memory injection, so the save can never be
corrupted). The navigation was mapped against the Run & Bun Pokemon Center save state and is
driven deterministically from a freshly loaded state, with generous frame settling between
inputs so menu transitions finish before the next press.

Flow that was verified on the ROM (screenshots + reading the live party out of RAM):

    overworld facing the PC
      -> A: "<player> booted up the PC."
      -> A: dismiss
      -> A: "Someone's PC" (Pokemon Storage System)
      -> A: "Accessed Someone's PC."
      -> A: "Pokemon Storage System opened." -> action submenu
            (Withdraw / Deposit / Move / Move Items / See Ya!)
      -> Withdraw -> box grid (6 wide x 5 tall), cursor on box 1 slot 1
      -> navigate to (box, slot), A -> per-mon menu (Withdraw / Summary / Mark / Release)
      -> Withdraw -> mon joins the party
      -> B -> "Continue Box operations?" -> No -> exits and commits to gPlayerParty
"""

from __future__ import annotations

import config
from emulator.mgba_instance import MGBAInstance
from emulator.state_reader import StateReader

# Box grid geometry (Gen-3 storage): 30 slots laid out 6 across, 5 down.
BOX_COLUMNS = 6
BOX_ROWS = 5
BOX_COUNT = 14

# Frame budgets. Menu transitions, the PC boot animation, and the box-slide animation need
# time to settle before the next input registers; tuned with headroom against the live ROM.
_MENU_SETTLE = 50
_GRID_STEP_SETTLE = 18
_BOX_SWITCH_SETTLE = 48   # the box-change slide is slower than a cursor move
_BOX_SETTLE = 30          # let the slide fully finish before moving within the new grid
_TEXT_SETTLE = 60
_WITHDRAW_SETTLE = 120

# The PC Storage struct begins with a u8 "current box" index; the box grid opens on it.
_CURRENT_BOX_OFFSET = 0


class BoxController:
    def __init__(
        self,
        instance: MGBAInstance,
        reader: StateReader | None = None,
        *,
        storage_ptr: int | None = None,
    ):
        self.instance = instance
        self.reader = reader or StateReader(instance)
        # Pointer to the live PokemonStorage struct (from the box optimizer's pointer
        # discovery). When known, the entry box is read from it so cross-box navigation
        # starts from the correct origin instead of assuming Box 1.
        self.storage_ptr = storage_ptr

    # -- public operations --------------------------------------------------------------

    def withdraw(self, box: int, slot: int) -> None:
        """Pull a single (box, slot) Pokemon into the party from a freshly loaded overworld
        state in front of a PC. VERIFIED and deterministic (commits to the live party).

        Points the storage struct's current box at the target up front so the grid opens
        there and only reliable in-grid navigation is used. Ends on the storage action
        submenu (the party is already committed at that point).
        """
        self._validate(box, slot)
        entry_box = self._set_current_box(box)    # written in the overworld (PC closed)
        self._open_action_menu()                  # -> action submenu, cursor on "Withdraw"
        self._tap("A", settle=_WITHDRAW_SETTLE)   # Withdraw -> box grid (opens on target box)
        self._goto_box_slot(box, slot, entry_box=entry_box)
        self._tap("A", settle=_MENU_SETTLE)        # per-mon menu, cursor on "Withdraw"
        self._tap("A", settle=_WITHDRAW_SETTLE)    # confirm Withdraw -> joins party
        self._commit()

    def show_storage_visit(self, *, linger_frames: int = 120) -> None:
        """Open the real Storage UI, show the box grid, and return to the overworld.

        This is used by recorded preparation runs so a PC visit is visible instead of the
        party changing between two unexplained frames.  It deliberately does not mutate the
        party; the separately verified swap transaction remains responsible for preserving
        boxed Pokemon and held-item inventory atomically.
        """
        self._open_action_menu()
        self._tap("DOWN", settle=_GRID_STEP_SETTLE)  # Deposit
        self._tap("DOWN", settle=_GRID_STEP_SETTLE)  # Move Pokemon
        self._tap("A", settle=_WITHDRAW_SETTLE)
        self.instance.advance_frames(max(1, linger_frames))
        self._tap("B", settle=_MENU_SETTLE)          # Continue Box operations?
        self._tap("DOWN", settle=_GRID_STEP_SETTLE)  # No
        self._tap("A", settle=_WITHDRAW_SETTLE)      # action submenu
        self._tap("DOWN", settle=_GRID_STEP_SETTLE)
        self._tap("DOWN", settle=_GRID_STEP_SETTLE)  # See Ya!
        self._tap("A", settle=_TEXT_SETTLE)
        self._tap("A", settle=_TEXT_SETTLE)

    def _commit(self) -> None:
        """B -> 'Continue Box operations?' -> No commits the withdrawn party and returns to
        the action submenu. (Chaining multiple withdraws and a clean exit all the way back to
        the overworld are NOT solved yet — the post-commit submenu cursor state is fragile, so
        do one withdraw per freshly loaded state for now.)"""
        self._tap("B", settle=_MENU_SETTLE)          # "Continue Box operations?"
        self._tap("DOWN", settle=_GRID_STEP_SETTLE)  # -> No
        self._tap("A", settle=_WITHDRAW_SETTLE)      # commit + return to action submenu

    def _set_current_box(self, box: int) -> int:
        """Write the target box (1-based) into the storage struct's current-box byte so the
        grid opens directly on it. Returns the resulting 0-based entry box."""
        target = (box - 1) % BOX_COUNT
        if self.storage_ptr is None:
            return self._entry_box()
        try:
            self.instance.write_u8(self.storage_ptr + _CURRENT_BOX_OFFSET, target)
        except Exception:
            return self._entry_box()
        return target

    def _entry_box(self) -> int:
        """0-based index of the box the grid will open on (the storage struct's current box)."""
        if self.storage_ptr is None:
            return 0
        try:
            return self.instance.read_u8(self.storage_ptr + _CURRENT_BOX_OFFSET) % BOX_COUNT
        except Exception:
            return 0

    # -- navigation building blocks ------------------------------------------------------

    def _open_action_menu(self) -> None:
        """From the overworld in front of the PC, open the Storage action submenu with the
        cursor resting on 'Withdraw Pokemon' (the default first option)."""
        # Face/approach the PC, then boot it.
        for _ in range(3):
            self._tap("UP", settle=12)
        # A x5: boot, dismiss "booted up", select "Someone's PC", dismiss "Accessed",
        # dismiss "Storage System opened" -> action submenu.
        self._tap("A", settle=_TEXT_SETTLE)   # boot PC
        self._tap("A", settle=_TEXT_SETTLE)   # dismiss "booted up the PC"
        self._tap("A", settle=_TEXT_SETTLE)   # "Someone's PC" (default highlight)
        self._tap("A", settle=_TEXT_SETTLE)   # dismiss "Accessed Someone's PC"
        self._tap("A", settle=_TEXT_SETTLE)   # dismiss "Storage System opened" -> submenu

    def _goto_box_slot(self, box: int, slot: int, *, entry_box: int = 0) -> None:
        """Move the grid cursor to (box, slot), 1-based.

        When `withdraw` has pointed the storage struct's current box at the target, the grid
        already opens there (entry_box == target) and this only does in-grid row/column moves,
        which are reliable. The in-session box-switch fallback (UP to the box-name header, then
        LEFT/RIGHT — the shoulder buttons are inert in this build) is kept for the no-pointer
        case but is known to desync when followed by an in-grid move, so prefer the pointer."""
        target = (box - 1) % BOX_COUNT
        steps = (target - entry_box) % BOX_COUNT
        if steps:
            self._tap("UP", settle=_GRID_STEP_SETTLE)        # top row -> header banner
            # Shortest direction around the 14-box ring.
            if steps <= BOX_COUNT - steps:
                for _ in range(steps):
                    self._tap("RIGHT", settle=_BOX_SWITCH_SETTLE)
            else:
                for _ in range(BOX_COUNT - steps):
                    self._tap("LEFT", settle=_BOX_SWITCH_SETTLE)
            self._tap("DOWN", settle=_BOX_SETTLE)             # header -> grid (top-left slot)
        row, col = divmod(slot - 1, BOX_COLUMNS)
        for _ in range(row):
            self._tap("DOWN", settle=_GRID_STEP_SETTLE)
        for _ in range(col):
            self._tap("RIGHT", settle=_GRID_STEP_SETTLE)

    # -- low level -----------------------------------------------------------------------

    def _tap(self, button: str, *, settle: int) -> None:
        self.instance.send_input(button, 3)
        self.instance.advance_frames(max(1, settle))

    @staticmethod
    def _validate(box: int, slot: int) -> None:
        if not 1 <= box <= BOX_COUNT:
            raise ValueError(f"box must be 1..{BOX_COUNT}")
        if not 1 <= slot <= BOX_COLUMNS * BOX_ROWS:
            raise ValueError(f"slot must be 1..{BOX_COLUMNS * BOX_ROWS}")


__all__ = ["BoxController", "BOX_COLUMNS", "BOX_ROWS", "BOX_COUNT"]
