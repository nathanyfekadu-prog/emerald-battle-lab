"""Project-wide configuration defaults."""

ROM_PATH = ""
SAVE_STATE_PATH = ""

MGBA_EXECUTABLE = "mgba"
MGBA_BRIDGE_HOST = "127.0.0.1"
MGBA_BRIDGE_BASE_PORT = 55355
MGBA_STARTUP_TIMEOUT_SECONDS = 10.0

POOL_SIZE = 16
RNG_DESYNC_FRAMES = 7
INTER_INPUT_DELAY_FRAMES = 1
MIN_MENU_INPUT_DELAY_FRAMES = 8
WAIT_TIMEOUT_FRAMES = 2000
TURN_RESOLUTION_MIN_FRAMES = 240
REQUIRE_READY_STATE_AFTER_TURN = True
TRUST_PLAYER_MOVE_MEMORY = False
# Whole-fight search safety cap. The UI no longer asks for a turn limit; MCTS
# expands until the battle ends or this cap prevents runaway searches.
MAX_TURNS = 20
MCTS_ITERATIONS = 60
TRIALS_PER_NODE = 2
# Discovery is intentionally cheap; final candidates are replayed enough times
# from the identical savestate to report an empirical clear rate users can trust.
FINAL_LINE_TRIALS = 256
FINAL_LINE_CANDIDATES = 6
UCB1_C = 1.414

WIN_WEIGHT = 10.0
HP_WEIGHT = 1.0
SURVIVAL_WEIGHT = 5.0
NUZLOCKE_WEIGHT = 0.0
# This save uses Box 14 as the Nuzlocke graveyard. Pokemon stored there are dead:
# never field them and never borrow their held items. 1-based; set to None to disable.
NUZLOCKE_GRAVEYARD_BOX = 14
# The generic ROM item-table probe resolves raw id 520 as Sitrus in this hack,
# but the live Corgi save and observed 10-HP activation identify these owned
# copies as Oran Berries. Keep save ownership (the raw id) while correcting the
# user-facing/mechanics name; this run has no legal Citrus supply.
RUN_BUN_ITEM_NAME_OVERRIDES = {520: "Oran Berry"}
DEATHLESS_BONUS = 3.0
MULTI_SACK_PENALTY = 4.0
SWITCH_ACTION_PENALTY = 0.6
CONSECUTIVE_SWITCH_PENALTY = 2.0
MIN_DEATHLESS_VISITS = 10

PLAYER_PARTY_BASE = 0x02024284
ENEMY_PARTY_BASE = 0x0202402C
PARTY_STRUCT_SIZE = 100
HP_OFFSET = 0x38
MAX_HP_OFFSET = 0x3A
BATTLE_OUTCOME = 0x02022B4C
MENU_READY_FLAG = 0x02022B40
# Run & Bun's expanded battle engine keeps gBattleTypeFlags beside the live
# battler structures.  The previous vanilla-derived address always read zero,
# so real double battles were incorrectly driven as singles.  Verified by a
# same-ROM comparison: Breeder Corgi reads 0x0000000C, while Brandi/Aisha reads
# 0x0000800D (BATTLE_TYPE_DOUBLE is bit 0).
BATTLE_TYPE = 0x02023364
READ_VANILLA_PARTY_STRUCTS = False

# Run & Bun/mGBA .ss3 verification override found from the live battle screen:
# screenshot shows Sleepy at 24/60 HP, and these addresses read 24/60 before
# and after a 60-frame advance. Keep Phase 2 memory probing before relying on
# these broadly across battles.
RUN_BUN_ACTIVE_PLAYER_HP = 0x02023AEE
RUN_BUN_ACTIVE_PLAYER_MAX_HP = 0x02023AF0

# The confirmed .ss3 battle stores the visible active battler HP here. The
# vanilla party structs remain readable through StateReader as fallbacks, but
# Run & Bun's active battle structs are what match the on-screen values.
# Both party arrays are standard 100-byte Gen-3 structs laid out back to back: the player
# array starts at 0x02023A98 and the enemy array 600 bytes later at 0x02023CF0. The bases
# below are the struct start minus 2, so the historical HP offset 0x58 lands on currentHP
# (struct+0x56) and 0x5A on maxHP (struct+0x58). The enemy base was previously off by 0x5A
# (0x02023C96), which read garbage HP and mis-decoded every enemy name; 0x5A was then added
# to the enemy maxHP offset (0xB2) to paper over it. Both are corrected and symmetric now.
RUN_BUN_PLAYER_PARTY_BASE = 0x02023A96
RUN_BUN_ENEMY_PARTY_BASE = 0x02023CEE
RUN_BUN_PARTY_HP_OFFSET = 0x58
RUN_BUN_PARTY_MAX_HP_OFFSET = 0x5A
RUN_BUN_ENEMY_PARTY_MAX_HP_OFFSET = RUN_BUN_PARTY_MAX_HP_OFFSET
# Offset of the held-item u16 inside the Run & Bun in-battle battler struct. Unknown
# until probed on the ROM (the struct is custom — HP lives at 0x58, not vanilla 0x28),
# so leave None until confirmed; StateWriter.write_held_item refuses to guess. Probe by
# reading the struct on a battle whose lead's item is known, then set this offset.
RUN_BUN_PARTY_ITEM_OFFSET: int | None = None

# Live overworld player tile coordinates (gObjectEvents[0].currentCoords) — confirmed by
# walking and diffing RAM: walking changes these u16s. Used to verify movement actually
# happened (a direction press can just turn the player, or be blocked by a wall) rather than
# advancing a fixed number of frames and hoping.
RUN_BUN_PLAYER_X = 0x02036924
RUN_BUN_PLAYER_Y = 0x02036926
# Pause-menu cursor verified against the supplied overworld .ss1. 1=Pokemon,
# 2=Bag, 3=PokeNav in this Run & Bun build. Direct selection avoids wraparound.
RUN_BUN_START_MENU_CURSOR = 0x02036BCE
# Live party-screen cursor (0-5), verified by walking the real Run & Bun menu
# through 0 -> 2 -> 3 -> 2 -> 0 -> 1 -> 4.
RUN_BUN_PARTY_CURSOR = 0x0203C51D
# 1 is the first battler command prompt; 4 is the partner prompt. Keeping
# these separate prevents a half-entered doubles turn from being checkpointed
# as though the next turn had begun.
RUN_BUN_BATTLE_COMMAND_PHASE = 0x020233E0
MEMORY_OVERRIDES = {
    "PLAYER_PARTY_BASE": RUN_BUN_PLAYER_PARTY_BASE,
    "ENEMY_PARTY_BASE": RUN_BUN_ENEMY_PARTY_BASE,
    "PLAYER_HP_OFFSET": RUN_BUN_PARTY_HP_OFFSET,
    "PLAYER_MAX_HP_OFFSET": RUN_BUN_PARTY_MAX_HP_OFFSET,
    "ENEMY_HP_OFFSET": RUN_BUN_PARTY_HP_OFFSET,
    "ENEMY_MAX_HP_OFFSET": RUN_BUN_ENEMY_PARTY_MAX_HP_OFFSET,
    "PLAYER_ACTIVE_HP": RUN_BUN_ACTIVE_PLAYER_HP,
    "PLAYER_ACTIVE_MAX_HP": RUN_BUN_ACTIVE_PLAYER_MAX_HP,
    "READ_PLAYER_PARTY_STRUCTS": True,
    "READ_ENEMY_PARTY_STRUCTS": True,
    "PLAYER_ITEM_OFFSET": RUN_BUN_PARTY_ITEM_OFFSET,
}
