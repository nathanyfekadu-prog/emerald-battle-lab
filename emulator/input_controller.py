from __future__ import annotations

from base64 import b64decode
from dataclasses import replace

import config
from battle.action import Action
from battle.battle_state import BattleState
from emulator.mgba_instance import MGBAInstance
from emulator.state_reader import StateReader


class TurnDidNotResolveError(RuntimeError):
    """Raised when inputs leave the emulator outside a battle-ready state."""


class InputController:
    def __init__(
        self,
        instance: MGBAInstance,
        reader: StateReader,
        inter_input_delay_frames: int = config.INTER_INPUT_DELAY_FRAMES,
        stop_on_player_faint: bool = False,
    ):
        self.instance = instance
        self.reader = reader
        self.inter_input_delay_frames = max(
            inter_input_delay_frames,
            config.MIN_MENU_INPUT_DELAY_FRAMES,
        )
        self.stop_on_player_faint = stop_on_player_faint

    def select_move(self, move_slot: int, target_slot: int | None = None) -> None:
        if move_slot not in range(4):
            raise ValueError("move_slot must be 0-3")
        if target_slot is not None and target_slot not in range(2):
            raise ValueError("target_slot must be 0-1")

        self._prepare_for_battle_menu()
        is_doubles = self.reader.read().is_doubles
        # In doubles, the partner's command box is visible a few frames before
        # it begins accepting input. Let that prompt finish sliding in.
        self._delay(16)
        self._tap("A")  # Main menu starts on FIGHT in the verified .ss3 state.
        self._delay(12)
        self._move_cursor_2x2(move_slot)
        if move_slot:
            # The move cursor has the same short animation window as the
            # doubles target cursor. An immediate A can be dropped, leaving the
            # second battler stranded in its move list while the turn waiter
            # mistakenly backs out to the first battler's menu.
            self._delay(16)
        self._tap("A")

        if target_slot is not None:
            self._delay(12)
            if target_slot == 1:
                self._tap("RIGHT")
            # Run & Bun animates the red doubles-target cursor even when the
            # default left target is kept. Confirming target zero after only the
            # old 12-frame wait was discarded, leaving the target picker open;
            # the following partner action then backed out to battler one's menu
            # and the search saved a no-op "turn". Both target directions need
            # the full cursor-animation delay.
            self._delay(16)
            self._tap("A")
        elif is_doubles:
            # Spread/field moves show a short target-summary transition even
            # though there is no directional choice. They still need the same
            # final confirmation that a single-target move receives.
            self._delay(32)
            self._tap("A")

    def select_switch(self, party_slot: int) -> None:
        if party_slot not in range(6):
            raise ValueError("party_slot must be 0-5")

        self._prepare_for_battle_menu()
        before_party = self.reader.read()
        target_name = (
            before_party.player_names[party_slot]
            if party_slot < len(before_party.player_names) else None
        )
        self._tap("DOWN")  # POKEMON is below FIGHT in Emerald's main battle menu.
        self._tap("A")
        # Later doubles turns can retain the previous party cursor while the
        # party screen is still sliding in. Wait until the menu accepts input;
        # 60 frames was intermittently too short on the real cartridge state.
        self._delay(150)

        self._move_party_cursor(party_slot, target_name=target_name)
        self._party_tap("A")
        self._delay(50)
        # The SHIFT / SUMMARY submenu remembers its previous cursor. Normalize
        # to SHIFT so a requested pivot cannot silently open Summary or Cancel
        # and return the same sleeping active Pokemon as a fake next turn.
        self._party_tap("UP")
        self._party_tap("UP")
        self._party_tap("A")
        self._delay(50)

    def execute_action(self, action: Action) -> None:
        if action.is_move:
            if action.move_slot is None:
                raise ValueError("move action requires move_slot")
            self.select_move(action.move_slot, action.target_slot)
        elif action.is_switch:
            if action.switch_target is None:
                raise ValueError("switch action requires switch_target")
            self.select_switch(action.switch_target)
        else:
            raise ValueError(f"unknown action kind: {action.kind!r}")

    def execute_turn(self, actions: list[Action]) -> BattleState:
        if not actions:
            raise ValueError("execute_turn requires at least one action")
        is_doubles = self.reader.read().is_doubles
        for action in actions:
            self.execute_action(action)
        if is_doubles and len(actions) == 2:
            # A target confirmation can be dropped while the partner command
            # box is animating. Phase 4 means we are still choosing the second
            # battler, not looking at the next turn. Retry that second action
            # instead of saving this half-entered menu as a search checkpoint.
            self.instance.advance_frames(60)
            if self._screen_looks_battle_command() and self._battle_command_phase() == 4:
                self.execute_action(actions[-1])
        return self.wait_for_turn_end()

    def wait_for_turn_end(self, timeout_frames: int = config.WAIT_TIMEOUT_FRAMES) -> BattleState:
        is_doubles = self.reader.read().is_doubles
        self.instance.advance_frames(config.TURN_RESOLUTION_MIN_FRAMES)
        if self._battle_over_quick():
            return self.reader.read()
        if self._screen_looks_battle_command() and (
            not is_doubles or self._battle_command_phase() == 1
        ):
            return replace(self.reader.read(), menu_ready=True)

        # Poll in small frame batches rather than one frame at a time, and use only the cheap
        # signals each step (a single battle-outcome read + the screenshot heuristics, both
        # well under a millisecond). The old loop ran a FULL reader.read() — which decodes
        # every party name/species/move from RAM and ROM — on every single frame, which was
        # the dominant cost of every trial. The full decode now happens only when we return.
        step = 6
        party_select_hits = 0
        active_faint_hits = 0
        elapsed = 0
        remaining = max(0, timeout_frames - config.TURN_RESOLUTION_MIN_FRAMES)
        while elapsed < remaining:
            if self._battle_over_quick():
                return self.reader.read()
            if self._screen_looks_battle_command() and (
                not is_doubles or self._battle_command_phase() == 1
            ):
                return replace(self.reader.read(), menu_ready=True)
            if self._screen_looks_party_select():
                # Active Pokemon fainted -> the game forces a replacement. Confirm it's stable
                # (not a transient frame), then send the next mon in and let that send (plus the
                # enemy's free move) fully resolve before the next check, so we don't double-fire
                # and corrupt the cursor.
                party_select_hits += 1
                if party_select_hits >= 2:
                    if self.stop_on_player_faint:
                        return self.reader.read_live_battle()
                    self._send_forced_replacement(self.reader.read())
                    self.instance.advance_frames(150)
                    elapsed += 150
                    party_select_hits = 0
                    active_faint_hits = 0
                    continue
            else:
                party_select_hits = 0
                # Run & Bun's party screen palette varies with the selected mon,
                # so the visual detector is not sufficient on every RNG branch.
                # The battle-mon HP address is stable; after it remains zero long
                # enough for the faint dialogue to clear, choose a replacement.
                try:
                    active_faint_hits = active_faint_hits + 1 if self.reader.active_player_fainted() else 0
                except Exception:
                    active_faint_hits = 0
                if active_faint_hits >= 10:
                    if self.stop_on_player_faint:
                        return self.reader.read_live_battle()
                    self._tap("B")
                    self.instance.advance_frames(24)
                    replacement_state = self.reader.read()
                    if replacement_state.battle_over:
                        return replacement_state
                    if self._send_forced_replacement(replacement_state):
                        self.instance.advance_frames(150)
                        elapsed += 174
                        active_faint_hits = 0
                        continue
                # Advance "X used Y" / "X fainted!" dialogue toward the next menu.
                self._tap("B")
            self.instance.advance_frames(step)
            elapsed += step

        last_state = self.reader.read()
        if self._screen_looks_battle_command() and (
            not is_doubles or self._battle_command_phase() == 1
        ):
            return replace(last_state, menu_ready=True)
        if (
            config.REQUIRE_READY_STATE_AFTER_TURN
            and not last_state.battle_over
            and not last_state.menu_ready
        ):
            raise TurnDidNotResolveError(
                "Turn inputs did not return to a battle-ready state. "
                "The emulator may still be in a party, summary, or action menu."
            )
        return last_state

    def _battle_command_phase(self) -> int:
        try:
            return self.instance.read_u8(config.RUN_BUN_BATTLE_COMMAND_PHASE)
        except Exception:
            return 0

    def _battle_over_quick(self) -> bool:
        """Cheap battle-over probe (one memory read) for the turn-resolution poll loop."""
        try:
            return self.instance.read_u16(self.reader.memory.battle_outcome) in (1, 2)
        except Exception:
            return False

    def _send_forced_replacement(self, state: BattleState) -> bool:
        """On a faint-forced switch, send the first healthy party member in."""
        occupied = {slot for slot in state.player_active_slots if slot is not None}
        actually_fainted = any(
            slot < len(state.player_hp)
            and (
                state.player_hp[slot] <= 0
                or (slot < len(state.player_fainted) and state.player_fainted[slot])
            )
            for slot in occupied
        )
        if not actually_fainted:
            # gBattleMons briefly reports 0 HP while a voluntary switch sprite
            # is off-screen. Do not turn that animation into a fake forced send.
            return False
        target = next(
            (
                slot
                for slot in range(6)
                if slot < len(state.player_hp)
                and state.player_hp[slot] > 0
                and slot not in occupied
                and not (slot < len(state.player_fainted) and state.player_fainted[slot])
            ),
            None,
        )
        if target is None:
            return False
        self._delay(90)
        # The cursor byte is the underlying party slot in both singles and
        # doubles. Navigate to the chosen healthy slot by reading that byte;
        # treating it as a visual card index is wrong whenever the active mon
        # has moved the party display order around after a faint.
        self._move_party_cursor(target)
        self._party_tap("A")  # open the SEND OUT / SUMMARY submenu
        self._delay(30)
        self._party_tap("A")  # confirm SEND OUT
        self._delay(40)
        return True

    def _move_party_cursor(self, party_slot: int, *, target_name: str | None = None) -> None:
        """Navigate Emerald's 1+5 party layout from the large active slot.

        Emerald lays the party out as two cards on the left (slots 0-1) and
        the remaining cards on the right (slots 2-5). The cursor persists
        between turns, so read the verified cursor byte, normalize through
        known menu edges to slot 0, then navigate to the requested slot.
        """
        state = self.reader.read()
        present = [
            slot for slot, max_hp in enumerate(state.player_max_hp)
            if max_hp > 0
        ]
        active = [
            slot for slot in state.player_active_slots
            if slot is not None and slot in present
        ]
        if active:
            first = active[0]
            cyclic = [
                (first + offset) % 6 for offset in range(1, 7)
                if (first + offset) % 6 in present
            ]
            display_order = active + [slot for slot in cyclic if slot not in active]
        else:
            display_order = present
        if party_slot not in display_order:
            raise TurnDidNotResolveError(
                f"Party slot {party_slot} is absent from menu order {display_order}."
            )

        read_u8 = getattr(self.instance, "read_u8", None)
        current = read_u8(config.RUN_BUN_PARTY_CURSOR) if callable(read_u8) else 0
        if not state.is_doubles:
            # On this ROM the cursor byte initially contains the active party
            # index, then changes to a visual card index while navigating. It is
            # therefore not a stable coordinate system. Normalize physically to
            # the large active card, then walk the right-hand bench column.
            menu_slot = next(
                (
                    index for index, name in enumerate(state.player_names)
                    if target_name and name == target_name
                ),
                display_order.index(party_slot),
            )
            for button in (("LEFT",) + ("UP",) * 6 + ("LEFT",)):
                self._party_tap(button)
            if menu_slot > 0:
                self._party_tap("RIGHT")
                for _ in range(menu_slot - 1):
                    self._party_tap("DOWN")
            return
        if callable(read_u8):
            # In doubles the party array is visually reordered around both
            # active battlers while the menu is open. The cursor byte is that
            # visual index, not the canonical pre-menu party slot (for example,
            # canonical Pole slot 1 is cursor 3 when Rah/Ethan occupy the two
            # field cards). Resolve by the name captured before opening the
            # menu; forced replacements already pass an index from the open
            # menu and therefore use the fallback directly.
            target_cursor = next(
                (
                    index for index, name in enumerate(state.player_names)
                    if target_name and name == target_name
                ),
                party_slot,
            )
            traversal = (
                ("UP",) * 6 + ("LEFT",) + ("UP",) * 2
                + ("DOWN", "UP", "RIGHT") + ("DOWN",) * 6
            )
            selected = current
            for button in traversal:
                if selected == target_cursor:
                    return
                self._party_tap(button)
                selected = read_u8(config.RUN_BUN_PARTY_CURSOR)
            if selected != target_cursor:
                raise TurnDidNotResolveError(
                    f"Could not select party slot {party_slot}; cursor stopped on {selected}."
                )
            return
        if state.is_doubles:
            to_zero = {
                0: (), 1: ("UP",), 2: ("LEFT",), 3: ("UP", "LEFT"),
                4: ("UP", "UP", "LEFT"), 5: ("UP", "UP", "UP", "LEFT"),
            }
            from_zero = {
                0: (), 1: ("DOWN",), 2: ("RIGHT",),
                3: ("RIGHT", "DOWN"), 4: ("RIGHT", "DOWN", "DOWN"),
                5: ("RIGHT", "DOWN", "DOWN", "DOWN"),
            }
        else:
            # Singles uses one large active card on the left and every bench
            # member in one vertical column on the right.
            to_zero = {slot: (() if slot == 0 else ("LEFT",)) for slot in range(6)}
            from_zero = {
                slot: (() if slot == 0 else ("RIGHT",) + ("DOWN",) * (slot - 1))
                for slot in range(6)
            }
        menu_slot = display_order.index(party_slot)
        for button in to_zero.get(current, ()) + from_zero[menu_slot]:
            self._party_tap(button)
        if callable(read_u8):
            selected = read_u8(config.RUN_BUN_PARTY_CURSOR)
            # Menu animation occasionally drops one directional input. Correct
            # from the cursor byte instead of accepting the adjacent card or
            # failing the whole replay. In singles, 0 is the large left card
            # and 1..5 are the right column from top to bottom.
            for _ in range(12):
                if selected == menu_slot:
                    break
                if menu_slot == 0:
                    button = "LEFT"
                elif selected == 0:
                    button = "RIGHT"
                elif selected < menu_slot:
                    button = "DOWN"
                else:
                    button = "UP"
                before = selected
                self._party_tap(button)
                selected = read_u8(config.RUN_BUN_PARTY_CURSOR)
                if selected == before:
                    self._delay(24)
            if selected != menu_slot:
                raise TurnDidNotResolveError(
                    f"Party cursor selected menu slot {selected}, expected {menu_slot} "
                    f"for party slot {party_slot} in {display_order}."
                )

    def _prepare_for_battle_menu(self) -> None:
        if self.reader.wait_for_menu(timeout_frames=60) or self._screen_looks_battle_command():
            return
        self._escape_to_battle_menu()
        if self.reader.wait_for_menu(timeout_frames=180) or self._screen_looks_battle_command():
            return
        raise TurnDidNotResolveError("Could not reach the battle command menu before input.")

    def _escape_to_battle_menu(self) -> None:
        for _ in range(5):
            self._tap("B")
            self._delay(10)

    def _screen_looks_battle_command(self) -> bool:
        try:
            screen = self.instance.screenshot()
            width = int(screen["width"])
            height = int(screen["height"])
            rgba = b64decode(str(screen["rgba_base64"]))
        except Exception:
            return False
        if width < 240 or height < 160:
            return False

        # The bottom-right command box (Fight/Bag/Pokemon/Run) is a light panel with
        # dark menu text. Require both a light background AND dark text pixels: a blank
        # white transition screen is light but has no text, so this rejects it. The old
        # heuristic checked a single top-screen pixel as "battle scene", but Run & Bun's
        # UI has white info boxes up top, which broke detection for every battle.
        command_ratio = self._light_ratio(rgba, width, 122, 112, 236, 156)
        move_list_ratio = self._light_ratio(rgba, width, 4, 112, 118, 156)
        left_command_edge = self._pixel_is_light(rgba, width, 130, 120)
        text_ratio = self._dark_ratio(rgba, width, 122, 112, 236, 156)
        # FIGHT's four-move list is light across both halves of the bottom UI.
        # Only the main command menu has a dark/coloured prompt pane on the left.
        return (
            command_ratio > 0.35
            and move_list_ratio < 0.25
            and left_command_edge
            and text_ratio > 0.03
        )

    def _screen_looks_party_select(self) -> bool:
        """The 'Choose a Pokemon' party screen shown on a forced (faint) switch. Unlike
        the command menu, it has no white command box in the bottom-right, but the party
        panels fill the right column with content."""
        try:
            screen = self.instance.screenshot()
            width = int(screen["width"])
            height = int(screen["height"])
            rgba = b64decode(str(screen["rgba_base64"]))
        except Exception:
            return False
        if width < 240 or height < 160:
            return False
        command_box = self._light_ratio(rgba, width, 122, 112, 236, 156)
        party_panels = self._dark_ratio(rgba, width, 150, 40, 238, 150)
        # The real party menu has a wide white "Choose a Pokemon." prompt along
        # the lower-left edge. Battle dialogue uses the same dark teal text and
        # can temporarily black out the upper-right during move animations, so
        # checking only for dark party panels classified ordinary attack text as
        # a forced-switch screen and fired cursor inputs in the middle of a turn.
        choose_prompt = self._light_ratio(rgba, width, 4, 136, 150, 156, step=2)
        return command_box < 0.12 and party_panels > 0.12 and choose_prompt > 0.28

    @staticmethod
    def _light_ratio(
        rgba: bytes,
        width: int,
        left: int,
        top: int,
        right: int,
        bottom: int,
        step: int = 3,
    ) -> float:
        total = 0
        light = 0
        for y in range(top, bottom, step):
            for x in range(left, right, step):
                total += 1
                if InputController._pixel_is_light(rgba, width, x, y):
                    light += 1
        return light / max(1, total)

    @staticmethod
    def _pixel_is_light(rgba: bytes, width: int, x: int, y: int) -> bool:
        offset = (y * width + x) * 4
        if offset + 2 >= len(rgba):
            return False
        red, green, blue = rgba[offset], rgba[offset + 1], rgba[offset + 2]
        return red > 230 and green > 230 and blue > 230

    @staticmethod
    def _dark_ratio(
        rgba: bytes,
        width: int,
        left: int,
        top: int,
        right: int,
        bottom: int,
        step: int = 3,
    ) -> float:
        total = 0
        dark = 0
        for y in range(top, bottom, step):
            for x in range(left, right, step):
                total += 1
                if InputController._pixel_is_dark(rgba, width, x, y):
                    dark += 1
        return dark / max(1, total)

    @staticmethod
    def _pixel_is_dark(rgba: bytes, width: int, x: int, y: int) -> bool:
        offset = (y * width + x) * 4
        if offset + 2 >= len(rgba):
            return False
        red, green, blue = rgba[offset], rgba[offset + 1], rgba[offset + 2]
        return red < 90 and green < 90 and blue < 90

    def _move_cursor_2x2(self, slot: int) -> None:
        if slot in (1, 3):
            self._tap("RIGHT")
        if slot in (2, 3):
            self._tap("DOWN")

    def _tap(self, button: str, frames: int = 3) -> None:
        self.instance.send_input(button, frames)
        self._delay()

    def _party_tap(self, button: str) -> None:
        self.instance.send_input(button, 3)
        self._delay(24)

    def _delay(self, frames: int | None = None) -> None:
        self.instance.advance_frames(frames if frames is not None else self.inter_input_delay_frames)
