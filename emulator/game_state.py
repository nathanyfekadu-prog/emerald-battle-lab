"""Whole-game state recognition above the battle-only memory reader."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import config
from battle.damage_calc import DamageCalculator
from emulator.input_controller import InputController
from emulator.mgba_instance import MGBAInstance
from emulator.state_reader import StateReader
from optimizer.gen3_save import RomNameResolver, SavePointers, discover_save_pointers


class GameMode(StrEnum):
    OVERWORLD = "overworld"
    BATTLE_COMMAND = "battle-command"
    BATTLE_TRANSITION = "battle-transition"
    MENU_OR_DIALOGUE = "menu-or-dialogue"


@dataclass(frozen=True)
class GameSnapshot:
    mode: GameMode
    map_group: int | None
    map_number: int | None
    x: int
    y: int
    trainer_name: str | None
    trainer_location: str | None
    trainer_confidence: str | None
    is_known_trainer: bool
    is_wild_battle: bool
    player_hp: tuple[int, ...]
    player_max_hp: tuple[int, ...]
    player_fainted: tuple[bool, ...]
    enemy_hp: tuple[int, ...]
    enemy_max_hp: tuple[int, ...]

    @property
    def map_id(self) -> tuple[int, int] | None:
        if self.map_group is None or self.map_number is None:
            return None
        return self.map_group, self.map_number


class WholeGameStateReader:
    def __init__(self, instance: MGBAInstance, calculator: DamageCalculator | None = None):
        self.instance = instance
        self.battle_reader = StateReader(instance)
        self.calculator = calculator or DamageCalculator()
        self._pointers: SavePointers | None = None

    def read(self) -> GameSnapshot:
        battle = self.battle_reader.read()
        command = InputController(self.instance, self.battle_reader)._screen_looks_battle_command()
        enemy_present = any(value > 0 for value in battle.enemy_max_hp)
        match = self.calculator.matched_trainer(battle) if command and enemy_present else None
        if command:
            mode = GameMode.BATTLE_COMMAND
        elif self._screen_looks_overworld():
            mode = GameMode.OVERWORLD
        elif enemy_present and any(battle.enemy_hp):
            mode = GameMode.BATTLE_TRANSITION
        else:
            mode = GameMode.MENU_OR_DIALOGUE
        map_group, map_number = self._map_id()
        return GameSnapshot(
            mode=mode,
            map_group=map_group,
            map_number=map_number,
            x=self.instance.read_u16(config.RUN_BUN_PLAYER_X),
            y=self.instance.read_u16(config.RUN_BUN_PLAYER_Y),
            trainer_name=match.battle.trainer_name if match else None,
            trainer_location=(match.battle.location or match.battle.section) if match else None,
            trainer_confidence=("exact" if match.hp_error == 0 else "close") if match else None,
            is_known_trainer=match is not None,
            is_wild_battle=command and enemy_present and match is None,
            player_hp=tuple(battle.player_hp),
            player_max_hp=tuple(battle.player_max_hp),
            player_fainted=tuple(battle.player_fainted),
            enemy_hp=tuple(battle.enemy_hp),
            enemy_max_hp=tuple(battle.enemy_max_hp),
        )

    def _map_id(self) -> tuple[int | None, int | None]:
        if self._pointers is None:
            self._pointers = discover_save_pointers(
                self.instance,
                RomNameResolver(self.instance.rom_path),
                self.calculator.species_by_num,
                self.calculator.moves_by_num,
                self.calculator.moves,
            )
        base = self._pointers.save_block_1
        if base is None:
            return None, None
        return self.instance.read_u8(base + 4), self.instance.read_u8(base + 5)

    def _screen_looks_overworld(self) -> bool:
        """Recognize the visible map by rejecting battle/menu-sized light panels."""
        try:
            screen = self.instance.screenshot()
            rgba = __import__("base64").b64decode(str(screen["rgba_base64"]))
            width = int(screen["width"])
            height = int(screen["height"])
        except Exception:
            return False
        if width < 240 or height < 160:
            return False
        # Overworld frames normally have varied pixels in both the top and bottom thirds;
        # dialogue/menu frames have a large near-white panel across the bottom.
        def light_ratio(y0: int, y1: int) -> float:
            light = total = 0
            for y in range(y0, y1, 4):
                for x in range(4, width - 4, 4):
                    offset = (y * width + x) * 4
                    r, g, b = rgba[offset : offset + 3]
                    total += 1
                    light += int(r > 220 and g > 220 and b > 220)
            return light / max(1, total)
        return light_ratio(112, 156) < 0.55 and light_ratio(8, 52) < 0.75


__all__ = ["GameMode", "GameSnapshot", "WholeGameStateReader"]
