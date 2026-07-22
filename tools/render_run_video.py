"""Render deterministic MP4 evidence for text-planned simulator and Gauntlet runs."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 1280, 720
BACKGROUND = (6, 24, 18)
PANEL = (10, 45, 33)
MINT = (151, 255, 204)
WHITE = (239, 247, 241)
MUTED = (171, 195, 182)
GOLD = (248, 207, 92)
RED = (255, 132, 119)


def _ffmpeg_executable() -> str:
    """Use system FFmpeg first, then the wheel-bundled binary."""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        bundled_ffmpeg = get_ffmpeg_exe()
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "FFmpeg is required because every Simulator and Gauntlet result includes an MP4"
        ) from exc
    if not bundled_ffmpeg or not Path(bundled_ffmpeg).is_file():
        raise RuntimeError(
            "FFmpeg is required because every Simulator and Gauntlet result includes an MP4"
        )
    return bundled_ffmpeg


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _wrapped(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], *, width: int = 72,
             font: ImageFont.ImageFont, fill: tuple[int, int, int], spacing: int = 8) -> int:
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        lines.extend(textwrap.wrap(paragraph, width=width) or [""])
    x, y = xy
    draw.multiline_text((x, y), "\n".join(lines), font=font, fill=fill, spacing=spacing)
    bbox = draw.multiline_textbbox((x, y), "\n".join(lines), font=font, spacing=spacing)
    return bbox[3]


def _slide(title: str, kicker: str, lines: list[tuple[str, tuple[int, int, int]]], *,
           counter: str = "") -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((38, 34, WIDTH - 38, HEIGHT - 34), radius=24, fill=PANEL, outline=(31, 102, 74), width=2)
    draw.text((72, 66), kicker.upper(), font=_font(20, bold=True), fill=GOLD)
    if counter:
        right = draw.textbbox((0, 0), counter, font=_font(19, bold=True))[2]
        draw.text((WIDTH - 72 - right, 68), counter, font=_font(19, bold=True), fill=MINT)
    y = _wrapped(draw, title, (72, 108), width=42, font=_font(43, bold=True), fill=WHITE, spacing=5) + 30
    for line, color in lines:
        y = _wrapped(draw, line, (76, y), width=82, font=_font(24), fill=color, spacing=6) + 17
        if y > HEIGHT - 72:
            break
    draw.text((72, HEIGHT - 62), "Emerald Battle Atlas · generated from the saved run log",
              font=_font(17), fill=MUTED)
    return image


def _gauntlet_slides(result: dict[str, Any]) -> list[Image.Image]:
    fights = list(result.get("fights") or [])
    route = " → ".join(str(fight.get("trainer") or "Trainer") for fight in fights)
    selection = result.get("route_team_selection") or {}
    slides = [_slide(
        "Gauntlet video record",
        "ROM-free deterministic replay",
        [
            (route, WHITE),
            (f"Rules: {result.get('ruleset_label') or 'configured rules'} · faint budget {result.get('max_total_faints', 0)}", MINT),
            (f"Fixed party: {' · '.join(selection.get('chosen') or []) or 'captured party'}", MUTED),
            (str(selection.get("reproducibility_key") or "The saved request and result are replayable."), GOLD),
        ],
        counter=f"0/{len(fights)}",
    )]
    for index, fight in enumerate(fights, 1):
        ending = fight.get("ending_team") or []
        fainted = [str(member.get("name")) for member in ending if int(member.get("hp") or 0) <= 0]
        status = "CLEARED" if fight.get("result") == "win-line" else str(fight.get("result") or "STOPPED").upper()
        lines = [
            (f"{status} · {len(fight.get('turns') or [])} modeled turns", MINT if status == "CLEARED" else RED),
            ("Party out: " + " · ".join(
                f"{member.get('name')} {member.get('hp', 0)}/{member.get('max_hp', '?')}"
                for member in ending
            ), WHITE),
            (f"Faints this fight: {', '.join(fainted) if fainted else 'none'}", RED if fainted else MINT),
        ]
        leveling = (fight.get("preparation") or {}).get("leveling") or []
        if leveling:
            lines.append(("Rare Candy disclosure: " + " · ".join(
                f"{change.get('pokemon')} Lv.{change.get('from')}→{change.get('to')}"
                for change in leveling
            ), GOLD))
        first_actions = [str(turn.get("action") or "") for turn in (fight.get("turns") or [])[:3]]
        if first_actions:
            lines.append(("Opening line: " + " / ".join(first_actions), MUTED))
        slides.append(_slide(str(fight.get("trainer") or "Trainer"), "Gauntlet fight", lines, counter=f"{index}/{len(fights)}"))
    accepted = sum(1 for fight in fights if fight.get("result") == "win-line")
    slides.append(_slide(
        "Route complete" if result.get("result") == "route-complete" else "Route audit stopped",
        "Saved verdict",
        [
            (f"Accepted fights: {accepted}/{result.get('queued', len(fights))}", MINT if result.get("result") == "route-complete" else GOLD),
            (f"Total faints: {result.get('total_faints', 0)} / budget {result.get('max_total_faints', 0)}", WHITE),
            (str(result.get("stopped_reason") or "Every queued trainer was completed."), MUTED),
            ("This video and the full text log are generated together from the same deterministic result.", MINT),
        ],
    ))
    return slides


def _simulator_slides(result: dict[str, Any]) -> list[Image.Image]:
    turns = list(result.get("turns") or [])
    team = [str(member.get("name") or member.get("species")) for member in result.get("team") or []]
    try:
        confidence = float(result.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    slides = [_slide(
        str(result.get("trainer") or "Battle simulation"),
        "Simulator video record",
        [
            (f"Result: {result.get('result', 'complete')} · confidence {confidence:.1%}", MINT),
            ("Party: " + " · ".join(team), WHITE),
            (f"Location: {result.get('location') or 'captured checkpoint'}", MUTED),
        ],
    )]
    for offset in range(0, len(turns), 4):
        chunk = turns[offset:offset + 4]
        slides.append(_slide(
            f"Turns {offset + 1}–{offset + len(chunk)}",
            "Turn-by-turn replay",
            [(f"{turn.get('turn', offset + index + 1)}. {turn.get('action') or turn.get('answer') or 'Continue'}", WHITE)
             for index, turn in enumerate(chunk)],
            counter=f"{min(offset + len(chunk), len(turns))}/{len(turns)}",
        ))
    slides.append(_slide(
        "Replay saved with its analysis",
        "Evidence complete",
        [("The MP4 and full text result share the same saved-run identifier.", MINT)],
    ))
    return slides


def render_run_video(result: dict[str, Any], output_path: Path, *, kind: str) -> dict[str, Any]:
    """Create a deterministic MP4. Raises if evidence cannot be produced."""
    ffmpeg = _ffmpeg_executable()
    slides = _gauntlet_slides(result) if kind == "gauntlet" else _simulator_slides(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="emerald-run-video-") as raw_tmp:
        temp_dir = Path(raw_tmp)
        concat_lines: list[str] = []
        for index, slide in enumerate(slides):
            frame = temp_dir / f"frame-{index:03d}.png"
            slide.save(frame)
            concat_lines.extend((f"file '{frame}'", "duration 2.4"))
        concat_lines.append(f"file '{frame}'")
        concat = temp_dir / "slides.txt"
        concat.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        rendered = temp_dir / "rendered.mp4"
        errors: list[str] = []
        for codec_args in (
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"],
            ["-c:v", "mpeg4", "-q:v", "4"],
        ):
            rendered.unlink(missing_ok=True)
            completed = subprocess.run([
                ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                "-vf", "fps=30,format=yuv420p", *codec_args,
                "-movflags", "+faststart", str(rendered),
            ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if completed.returncode == 0 and rendered.is_file() and rendered.stat().st_size > 0:
                break
            errors.append((completed.stderr or "unknown FFmpeg error").strip().splitlines()[-1])
        else:
            raise RuntimeError(f"Could not encode required MP4: {'; '.join(errors)}")
        # Decode the full file before publishing it. A half-written MP4 must never be
        # returned with video_ready=true.
        verified = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(rendered), "-f", "null", "-"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        if verified.returncode != 0:
            raise RuntimeError(f"Required MP4 failed decode verification: {verified.stderr.strip()}")
        rendered.replace(output_path)
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("video renderer completed without producing an MP4")
    return {
        "kind": f"{kind}-text-replay",
        "label": "Gauntlet planner replay" if kind == "gauntlet" else "Simulator turn replay",
        "video_url": "",
        "video_ready": True,
        "text_log_included": True,
        "size_bytes": output_path.stat().st_size,
        "duration_seconds": round(len(slides) * 2.4, 1),
    }
