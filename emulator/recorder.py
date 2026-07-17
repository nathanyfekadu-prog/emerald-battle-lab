"""Frame-by-frame capture of the headless emulator.

The libmgba bridge has no window, but screenshots are cheap (~0.5ms), so we can step the
emulator one frame at a time, grab each frame, and emit a tiny self-contained HTML player to
watch it back — scrub frame-by-frame or play it at any speed. Capturing every frame of a long
battle is a lot of PNGs, so `capture_every` lets you thin it (e.g. every 2nd frame) while the
emulator still advances one frame at a time.
"""

from __future__ import annotations

import struct
import zlib
from base64 import b64decode
from pathlib import Path

from emulator.mgba_instance import MGBAInstance


def _png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride : (y + 1) * stride]
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def _save_frame(instance: MGBAInstance, path: Path) -> None:
    shot = instance.screenshot()
    path.write_bytes(_png(int(shot["width"]), int(shot["height"]), b64decode(str(shot["rgba_base64"]))))


def record(
    instance: MGBAInstance,
    frames: int,
    out_dir: str,
    *,
    capture_every: int = 1,
    fps: int = 30,
) -> int:
    """Advance `frames` frames one at a time, saving a PNG every `capture_every` frames, and
    write an index.html player into `out_dir`. Returns the number of captured frames."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for existing in out.glob("frame_*.png"):
        existing.unlink()

    captured = 0
    _save_frame(instance, out / f"frame_{captured:05d}.png")
    captured += 1
    for index in range(1, frames + 1):
        instance.advance_frames(1)
        if index % max(1, capture_every) == 0:
            _save_frame(instance, out / f"frame_{captured:05d}.png")
            captured += 1
    _write_player(out, captured, fps)
    return captured


def _write_player(out: Path, count: int, fps: int) -> None:
    (out / "index.html").write_text(
        f"""<!doctype html><meta charset=utf-8><title>Emulator playback</title>
<style>
 body{{margin:0;background:#111;color:#ddd;font:13px system-ui;text-align:center}}
 img{{image-rendering:pixelated;width:480px;height:320px;background:#000;margin-top:12px;border:1px solid #333}}
 .bar{{margin:10px;display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap}}
 input[type=range]{{width:480px}}
</style>
<img id=f>
<div class=bar>
 <button id=play>Play</button>
 <button id=prev>◀ frame</button>
 <button id=next>frame ▶</button>
 <label>speed <input id=spd type=range min=1 max=120 value={fps}> <span id=spdv>{fps}</span> fps</label>
 <span id=lbl></span>
</div>
<input id=seek type=range min=0 max={max(0, count - 1)} value=0>
<script>
 const N={count}; let i=0, timer=null, fps={fps};
 const img=document.getElementById('f'), seek=document.getElementById('seek'), lbl=document.getElementById('lbl');
 const pad=n=>String(n).padStart(5,'0');
 function show(){{ img.src='frame_'+pad(i)+'.png'; seek.value=i; lbl.textContent=(i+1)+' / '+N; }}
 function step(d){{ i=(i+d+N)%N; show(); }}
 document.getElementById('next').onclick=()=>{{stop();step(1)}};
 document.getElementById('prev').onclick=()=>{{stop();step(-1)}};
 seek.oninput=()=>{{stop(); i=+seek.value; show()}};
 const spd=document.getElementById('spd'), spdv=document.getElementById('spdv');
 spd.oninput=()=>{{fps=+spd.value; spdv.textContent=fps; if(timer) play()}};
 function stop(){{ if(timer){{clearInterval(timer); timer=null; pbtn.textContent='Play'}} }}
 function play(){{ if(timer)clearInterval(timer); timer=setInterval(()=>step(1),1000/fps); pbtn.textContent='Pause'; }}
 const pbtn=document.getElementById('play'); pbtn.onclick=()=>timer?stop():play();
 show();
</script>""",
        encoding="utf-8",
    )


__all__ = ["record"]
