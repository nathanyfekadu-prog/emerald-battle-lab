"""Single mGBA process wrapper.

Integration approach
--------------------
This project treats the ROM as the battle-mechanics source of truth, so the
wrapper only drives the emulator and reads RAM. The preferred direct
``mgba-python`` bindings are attempted first, but they are not importable in
the current development environment and are not consistently packaged across
Python versions. On this machine, the working runtime path compiles the small
``emulator/mgba_bridge.c`` helper against Homebrew's ``libmgba`` and talks to
it over stdin/stdout.

The requested Lua scripting socket bridge remains as a fallback for mGBA builds
that expose command-line script loading:

1. Launch mGBA headless with ``--no-gui`` and a generated Lua script.
2. The Lua script opens a localhost TCP socket.
3. Python sends small line-oriented commands for state loading, input,
   frame advance, memory reads, and shutdown.

If your mGBA build does not support ``--script`` or LuaSocket, install a build
with scripting enabled, then set ``MGBA_EXECUTABLE`` in ``config.py`` or the
``MGBA_EXECUTABLE`` environment variable. Common manual launch equivalent:

    mgba --no-gui --script /path/to/generated_bridge.lua /path/to/rom.gba

The Lua bridge API names vary slightly across mGBA releases, so the script
tries the commonly exposed forms for memory reads, key presses, frame advance,
and savestate loading before returning an explicit error to Python.
"""

from __future__ import annotations

import argparse
import base64
import os
import socket
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Final

try:  # Documented preferred path; currently unused until bindings stabilize.
    import mgba as _mgba_python  # type: ignore
except Exception:  # pragma: no cover - environment dependent.
    _mgba_python = None

try:
    from config import (
        MGBA_BRIDGE_BASE_PORT,
        MGBA_BRIDGE_HOST,
        MGBA_EXECUTABLE,
        MGBA_STARTUP_TIMEOUT_SECONDS,
        RUN_BUN_ACTIVE_PLAYER_HP,
        RUN_BUN_ACTIVE_PLAYER_MAX_HP,
    )
except ImportError:  # Allows direct execution from this file during early setup.
    MGBA_EXECUTABLE = "mgba"
    MGBA_BRIDGE_HOST = "127.0.0.1"
    MGBA_BRIDGE_BASE_PORT = 55355
    MGBA_STARTUP_TIMEOUT_SECONDS = 10.0
    RUN_BUN_ACTIVE_PLAYER_HP = 0x02023AEE
    RUN_BUN_ACTIVE_PLAYER_MAX_HP = 0x02023AF0


BUTTONS: Final[set[str]] = {
    "A",
    "B",
    "UP",
    "DOWN",
    "LEFT",
    "RIGHT",
    "START",
    "SELECT",
    "L",
    "R",
}


class MGBAError(RuntimeError):
    """Raised when mGBA cannot be driven through the selected backend."""


class MGBAInstance:
    def __init__(self, rom_path: str, save_state_path: str, instance_id: int):
        self.rom_path = Path(rom_path).expanduser().resolve()
        self.save_state_path = Path(save_state_path).expanduser().resolve()
        self.instance_id = instance_id
        self.port = MGBA_BRIDGE_BASE_PORT + instance_id
        self.host = MGBA_BRIDGE_HOST
        self.process: subprocess.Popen[str] | None = None
        self._socket: socket.socket | None = None
        self._socket_file = None
        self._bridge_file: tempfile.NamedTemporaryFile[str] | None = None

        self._validate_paths()
        if _libmgba_bridge_available():
            self._start_libmgba_bridge()
        else:
            self._start_lua_bridge()
        self.load_state()
        self._set_max_speed()

    def load_state(self) -> None:
        self._request_ok(f"LOADSTATE {self.save_state_path}")

    def save_state(self, path: str | Path) -> Path:
        """Write a resumable emulator checkpoint and return its absolute path."""
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._request_ok(f"SAVESTATE {destination}")
        return destination

    def send_input(self, button: str, frames: int) -> None:
        normalized = button.upper()
        if normalized not in BUTTONS:
            valid = ", ".join(sorted(BUTTONS))
            raise ValueError(f"Unknown GBA button {button!r}. Expected one of: {valid}")
        if frames < 1:
            raise ValueError("frames must be >= 1")
        self._request_ok(f"KEY {normalized} {frames}")

    def read_u8(self, address: int) -> int:
        return self._read_int("READ8", address, 0xFF)

    def read_u16(self, address: int) -> int:
        return self._read_int("READ16", address, 0xFFFF)

    def read_u32(self, address: int) -> int:
        return self._read_int("READ32", address, 0xFFFFFFFF)

    def read_block(self, address: int, length: int) -> bytes:
        if length < 0:
            raise ValueError("length must be >= 0")
        response = self._request(f"READBLOCK {address} {length}")
        if not response.startswith("OK "):
            raise MGBAError(response)
        return base64.b64decode(response.split(" ", 1)[1])

    def write_u8(self, address: int, value: int) -> None:
        self._write_int("WRITE8", address, value, 0xFF)

    def write_u16(self, address: int, value: int) -> None:
        self._write_int("WRITE16", address, value, 0xFFFF)

    def write_u32(self, address: int, value: int) -> None:
        self._write_int("WRITE32", address, value, 0xFFFFFFFF)

    def write_block(self, address: int, data: bytes) -> None:
        if address < 0:
            raise ValueError("address must be >= 0")
        if not data:
            return
        # The bridge protocol uses one 4096-byte command line.  Base64 expands
        # large party/storage writes by 4/3; sending a whole 33 KB PC blob split
        # the command across fgets() calls and left the protocol desynchronized.
        # Chunk below the line limit and advance the destination address.
        chunk_size = 2048
        payload = bytes(data)
        for offset in range(0, len(payload), chunk_size):
            chunk = payload[offset : offset + chunk_size]
            encoded = base64.b64encode(chunk).decode("ascii")
            response = self._request(f"WRITEBLOCK {address + offset} {encoded}")
            if not response.startswith("OK"):
                raise MGBAError(response)

    def advance_frames(self, n: int) -> None:
        if n < 0:
            raise ValueError("n must be >= 0")
        if n == 0:
            return
        self._request_ok(f"ADVANCE {n}")

    def screenshot(self) -> dict[str, int | str]:
        response = self._request("SCREENSHOT")
        if not response.startswith("OK "):
            raise MGBAError(response)
        parts = response.split(" ", 3)
        if len(parts) != 4:
            raise MGBAError(f"Malformed screenshot response: {response[:80]!r}")
        return {
            "width": int(parts[1]),
            "height": int(parts[2]),
            "rgba_base64": parts[3],
        }

    def start_recording(self, video_raw_path: str | Path, audio_raw_path: str | Path) -> None:
        """Capture every emulated video frame and stereo audio sample to raw files."""
        video = Path(video_raw_path).expanduser().resolve()
        audio = Path(audio_raw_path).expanduser().resolve()
        if " " in str(video) or " " in str(audio):
            raise ValueError("Raw recording paths cannot contain spaces")
        video.parent.mkdir(parents=True, exist_ok=True)
        audio.parent.mkdir(parents=True, exist_ok=True)
        self._request_ok(f"STARTRECORD {video} {audio}")

    def stop_recording(self) -> dict[str, int]:
        response = self._request("STOPRECORD")
        if not response.startswith("OK "):
            raise MGBAError(response)
        parts = response.split()
        if len(parts) != 4:
            raise MGBAError(f"Malformed recording response: {response!r}")
        return {
            "audio_rate": int(parts[1]),
            "video_frames": int(parts[2]),
            "audio_frames": int(parts[3]),
        }

    def shutdown(self) -> None:
        try:
            if self._socket_file is not None:
                try:
                    self._request_ok("QUIT")
                except Exception:
                    pass
        finally:
            if self._socket_file is not None:
                self._socket_file.close()
                self._socket_file = None
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=3)
                self.process = None
            if self._bridge_file is not None:
                try:
                    Path(self._bridge_file.name).unlink(missing_ok=True)
                finally:
                    self._bridge_file = None

    def __enter__(self) -> "MGBAInstance":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.shutdown()

    def _validate_paths(self) -> None:
        if not self.rom_path.is_file():
            raise FileNotFoundError(f"ROM not found: {self.rom_path}")
        if not self.save_state_path.is_file():
            raise FileNotFoundError(f"Save state not found: {self.save_state_path}")

    def _start_libmgba_bridge(self) -> None:
        helper = _ensure_libmgba_bridge_compiled()
        self.process = subprocess.Popen(
            [str(helper), str(self.rom_path), str(self.save_state_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise MGBAError("failed to open libmgba bridge pipes")
        self._socket_file = _ProcessLineIO(self.process.stdin, self.process.stdout)
        hello = self._socket_file.readline().strip()
        if hello != "OK READY":
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise MGBAError(
                f"libmgba bridge failed to start. greeting={hello!r}\nstderr:\n{stderr}"
            )

    def _start_lua_bridge(self) -> None:
        self._bridge_file = tempfile.NamedTemporaryFile(
            "w", suffix=f"-mgba-bridge-{self.instance_id}.lua", delete=False
        )
        self._bridge_file.write(_lua_bridge_source(self.host, self.port))
        self._bridge_file.flush()
        self._bridge_file.close()

        executable = os.environ.get("MGBA_EXECUTABLE", MGBA_EXECUTABLE)
        command = [
            executable,
            "--no-gui",
            "--script",
            self._bridge_file.name,
            str(self.rom_path),
        ]
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            raise MGBAError(
                f"mGBA executable {executable!r} was not found. Install mGBA or set "
                "MGBA_EXECUTABLE to a binary that supports --no-gui and --script."
            ) from exc

        self._connect_bridge()

    def _connect_bridge(self) -> None:
        deadline = time.monotonic() + MGBA_STARTUP_TIMEOUT_SECONDS
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                stderr = self.process.stderr.read() if self.process.stderr else ""
                raise MGBAError(f"mGBA exited before bridge connection. stderr:\n{stderr}")
            try:
                self._socket = socket.create_connection((self.host, self.port), timeout=0.5)
                self._socket.settimeout(30.0)
                self._socket_file = self._socket.makefile("rw", newline="\n")
                hello = self._socket_file.readline().strip()
                if hello != "OK READY":
                    raise MGBAError(f"Unexpected bridge greeting: {hello!r}")
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.1)
        raise MGBAError(
            f"Timed out connecting to mGBA Lua bridge on {self.host}:{self.port}. "
            "Confirm this mGBA build supports Lua scripting and LuaSocket."
        ) from last_error

    def _set_max_speed(self) -> None:
        self._request_ok("MAXSPEED")

    def _read_int(self, command: str, address: int, mask: int) -> int:
        if address < 0:
            raise ValueError("address must be >= 0")
        response = self._request(f"{command} {address}")
        if not response.startswith("OK "):
            raise MGBAError(response)
        return int(response.split(" ", 1)[1], 0) & mask

    def _write_int(self, command: str, address: int, value: int, mask: int) -> None:
        if address < 0:
            raise ValueError("address must be >= 0")
        response = self._request(f"{command} {address} {value & mask}")
        if not response.startswith("OK"):
            raise MGBAError(response)

    def _request_ok(self, command: str) -> None:
        response = self._request(command)
        if response != "OK":
            raise MGBAError(response)

    def _request(self, command: str) -> str:
        if self._socket_file is None:
            raise MGBAError("mGBA bridge is not connected")
        self._socket_file.write(command + "\n")
        self._socket_file.flush()
        response = self._socket_file.readline()
        if response == "":
            raise MGBAError("mGBA bridge closed the connection")
        return response.strip()


def _lua_bridge_source(host: str, port: int) -> str:
    return textwrap.dedent(
        f"""
        local socket = require("socket")
        local server = assert(socket.bind("{host}", {port}))
        server:settimeout(nil)
        local client = assert(server:accept())
        client:settimeout(nil)
        client:send("OK READY\\n")

        local running = true
        local key_names = {{
          A = "A", B = "B", UP = "Up", DOWN = "Down", LEFT = "Left",
          RIGHT = "Right", START = "Start", SELECT = "Select", L = "L", R = "R"
        }}

        local function call_first(names, ...)
          for _, name in ipairs(names) do
            local fn = emu[name]
            if type(fn) == "function" then
              local ok, result = pcall(fn, emu, ...)
              if ok then return true, result end
            end
          end
          return false, nil
        end

        local function advance_one_frame()
          local ok = call_first({{"runFrame", "frameAdvance", "advanceFrame"}})
          if not ok then
            emu:yield()
          end
        end

        local function advance_frames(n)
          for _ = 1, n do
            advance_one_frame()
          end
        end

        local function read8(address)
          local ok, result = call_first({{"read8", "readU8", "readByte"}}, address)
          if ok and result ~= nil then return result end
          if emu.memory and emu.memory.read8 then return emu.memory:read8(address) end
          error("no supported mGBA Lua u8 read API found")
        end

        local function read16(address)
          local lo = read8(address)
          local hi = read8(address + 1)
          return lo + hi * 0x100
        end

        local function write8(address, value)
          local ok = call_first({{"write8", "writeU8", "writeByte"}}, address, value)
          if ok then return end
          if emu.memory and emu.memory.write8 then emu.memory:write8(address, value); return end
          error("no supported mGBA Lua u8 write API found")
        end

        local function write16(address, value)
          write8(address, value % 0x100)
          write8(address + 1, math.floor(value / 0x100) % 0x100)
        end

        local function write32(address, value)
          write8(address, value % 0x100)
          write8(address + 1, math.floor(value / 0x100) % 0x100)
          write8(address + 2, math.floor(value / 0x10000) % 0x100)
          write8(address + 3, math.floor(value / 0x1000000) % 0x100)
        end

        local function read32(address)
          local b0 = read8(address)
          local b1 = read8(address + 1)
          local b2 = read8(address + 2)
          local b3 = read8(address + 3)
          return b0 + b1 * 0x100 + b2 * 0x10000 + b3 * 0x1000000
        end

        local function set_key(button, pressed)
          local key = key_names[button]
          if key == nil then error("unknown key " .. tostring(button)) end

          local ok = call_first({{"setKey", "setButton", "keypadSet"}}, key, pressed)
          if ok then return end
          if emu.input and emu.input.setKey then
            emu.input:setKey(key, pressed)
            return
          end
          error("no supported mGBA Lua input API found")
        end

        local function load_state(path)
          local ok = call_first({{"loadState", "loadSaveState", "loadStateFile"}}, path)
          if ok then return end
          error("no supported mGBA Lua savestate load API found")
        end

        local function save_state(path)
          local ok = call_first({{"saveState", "saveSaveState", "saveStateFile"}}, path)
          if ok then return end
          error("no supported mGBA Lua savestate save API found")
        end

        local function max_speed()
          call_first({{"setTurbo", "setFastForward"}}, true)
          call_first({{"setFrameLimiter", "setVideoSync"}}, false)
        end

        while running do
          local line = client:receive("*l")
          if line == nil then break end
          local parts = {{}}
          for part in string.gmatch(line, "%S+") do table.insert(parts, part) end
          local command = parts[1]
          local ok, result = pcall(function()
            if command == "LOADSTATE" then
              load_state(string.sub(line, string.len(command) + 2))
              return "OK"
            elseif command == "SAVESTATE" then
              save_state(string.sub(line, string.len(command) + 2))
              return "OK"
            elseif command == "KEY" then
              local button = parts[2]
              local frames = tonumber(parts[3])
              set_key(button, true)
              advance_frames(frames)
              set_key(button, false)
              advance_frames(1)
              return "OK"
            elseif command == "ADVANCE" then
              advance_frames(tonumber(parts[2]))
              return "OK"
            elseif command == "READ8" then
              return "OK " .. tostring(read8(tonumber(parts[2])))
            elseif command == "READ16" then
              return "OK " .. tostring(read16(tonumber(parts[2])))
            elseif command == "READ32" then
              return "OK " .. tostring(read32(tonumber(parts[2])))
            elseif command == "WRITE8" then
              write8(tonumber(parts[2]), tonumber(parts[3]))
              return "OK"
            elseif command == "WRITE16" then
              write16(tonumber(parts[2]), tonumber(parts[3]))
              return "OK"
            elseif command == "WRITE32" then
              write32(tonumber(parts[2]), tonumber(parts[3]))
              return "OK"
            elseif command == "WRITEBLOCK" then
              local b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
              local payload = parts[3] or ""
              local address = tonumber(parts[2])
              local bytes = {{}}
              local buffer, bits = 0, 0
              for ch in string.gmatch(payload, ".") do
                if ch ~= "=" then
                  local idx = string.find(b64, ch, 1, true)
                  if idx then
                    buffer = buffer * 64 + (idx - 1)
                    bits = bits + 6
                    if bits >= 8 then
                      bits = bits - 8
                      local byte = math.floor(buffer / (2 ^ bits)) % 256
                      table.insert(bytes, byte)
                    end
                  end
                end
              end
              for i, byte in ipairs(bytes) do
                write8(address + (i - 1), byte)
              end
              return "OK " .. tostring(#bytes)
            elseif command == "MAXSPEED" then
              max_speed()
              return "OK"
            elseif command == "QUIT" then
              running = false
              return "OK"
            else
              error("unknown command " .. tostring(command))
            end
          end)

          if ok then
            client:send(result .. "\\n")
          else
            client:send("ERR " .. tostring(result) .. "\\n")
          end
        end

        client:close()
        server:close()
        """
    ).strip()


class _ProcessLineIO:
    def __init__(self, stdin, stdout):
        self._stdin = stdin
        self._stdout = stdout

    def write(self, value: str) -> None:
        self._stdin.write(value)

    def flush(self) -> None:
        self._stdin.flush()

    def readline(self) -> str:
        return self._stdout.readline()

    def close(self) -> None:
        try:
            self._stdin.close()
        finally:
            self._stdout.close()


def _libmgba_paths() -> tuple[list[Path], Path] | None:
    project_root = Path(__file__).resolve().parents[1]
    vendor_source = project_root.parent / "vendor" / "mgba-0.10.3"
    vendor_build = project_root.parent / "vendor" / "mgba-0.10.3-build"
    if (vendor_build / "libmgba.dylib").is_file():
        return [vendor_source / "include", vendor_build / "include"], vendor_build

    for prefix in (
        Path("/opt/homebrew/Cellar/mgba/0.10.5_2"),
        Path("/usr/local/Cellar/mgba/0.10.5_2"),
    ):
        include = prefix / "include"
        lib = prefix / "lib"
        if include.is_dir() and (lib / "libmgba.0.10.5.dylib").is_file():
            return [include], lib
    return None


def _libmgba_bridge_available() -> bool:
    return _libmgba_paths() is not None


def _ensure_libmgba_bridge_compiled() -> Path:
    paths = _libmgba_paths()
    if paths is None:
        raise MGBAError("Homebrew libmgba was not found")
    includes, lib = paths
    source = Path(__file__).with_name("mgba_bridge.c")
    output = Path(__file__).with_name("mgba_bridge")
    if output.exists() and output.stat().st_mtime >= source.stat().st_mtime:
        return output

    command = [
        "cc",
        "-std=c11",
        "-Wall",
        "-Wextra",
        *(f"-I{include}" for include in includes),
        f"-L{lib}",
        f"-Wl,-rpath,{lib}",
        str(source),
        "-lmgba",
        "-o",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise MGBAError(
            "Failed to compile libmgba bridge.\n"
            f"Command: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return output


def _run_acceptance_test() -> None:
    parser = argparse.ArgumentParser(description="Phase 1 mGBA integration acceptance test")
    parser.add_argument("--rom", required=True, help="Path to Run & Bun/Emerald ROM")
    parser.add_argument("--state", required=True, help="Path to .ss0/.sgm save state")
    parser.add_argument("--instance-id", type=int, default=0)
    args = parser.parse_args()

    vanilla_hp_address = 0x02024284
    hp_address = RUN_BUN_ACTIVE_PLAYER_HP
    max_hp_address = RUN_BUN_ACTIVE_PLAYER_MAX_HP
    with MGBAInstance(args.rom, args.state, args.instance_id) as instance:
        vanilla_hp = instance.read_u16(vanilla_hp_address)
        hp_before = instance.read_u16(hp_address)
        max_hp_before = instance.read_u16(max_hp_address)
        print(f"Vanilla prompt HP address @ {vanilla_hp_address:#010x}: {vanilla_hp}")
        print(f"Run & Bun active HP before inputs @ {hp_address:#010x}: {hp_before}/{max_hp_before}")
        instance.advance_frames(60)
        hp_after_advance = instance.read_u16(hp_address)
        print(f"Run & Bun active HP after 60 frames @ {hp_address:#010x}: {hp_after_advance}/{max_hp_before}")
        for _ in range(5):
            instance.send_input("A", 1)
            instance.advance_frames(3)
        hp_after = instance.read_u16(hp_address)
        print(f"Run & Bun active HP after 5x A @ {hp_address:#010x}: {hp_after}/{max_hp_before}")


if __name__ == "__main__":
    _run_acceptance_test()
