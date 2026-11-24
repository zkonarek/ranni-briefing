"""Sestaví briefing-latest.mp3, což je nahrávka přehrávaná na stránce.

Zdroj je celý briefing-script.txt. Nahrávka nevznikne, pokud na závěru
chybí aktuální pasáž o D-Wave Quantum a o IonQ, nebo pokud text vrací
přehled asijských trhů.

Nejdřív zkusí český hlas Microsoftu. Když ten hlas nevrátí zvuk, použije
český hlas Google.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

import edge_tts
import imageio_ffmpeg
from gtts import gTTS

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "briefing-script.txt"
OUTPUT = ROOT / "briefing-latest.mp3"
EDGE_VOICE = "cs-CZ-AntoninNeural"
REQUIRED = ("D-Wave Quantum", "IonQ")
CLOSING = "Na závěr dvě kvantové firmy."
ASIA = ("Nikkei", "Kospi", "Kosdaq", "Hang Seng", "Šanghaj", "Šen-čen", "Asie", "asijsk")
MAX_CHARS = 2800
MAX_SECONDS = 20 * 60


def chunks(text: str) -> list[str]:
    parts: list[str] = []
    buffer = ""
    for paragraph in text.split("\n\n"):
        piece = paragraph.strip()
        if not piece:
            continue
        candidate = f"{buffer}\n\n{piece}".strip() if buffer else piece
        if buffer and len(candidate) > MAX_CHARS:
            parts.append(buffer)
            buffer = piece
        else:
            buffer = candidate
    if buffer:
        parts.append(buffer)
    return parts


def strip_id3(blob: bytes) -> bytes:
    if not blob.startswith(b"ID3") or len(blob) < 10:
        return blob
    size = (
        ((blob[6] & 0x7F) << 21)
        | ((blob[7] & 0x7F) << 14)
        | ((blob[8] & 0x7F) << 7)
        | (blob[9] & 0x7F)
    )
    return blob[10 + size :]


async def edge_available() -> bool:
    probe = ROOT / ".briefing-voice-probe.mp3"
    try:
        await edge_tts.Communicate("Zkouška hlasu.", EDGE_VOICE).save(str(probe))
        return probe.stat().st_size > 1000
    except Exception:
        return False
    finally:
        probe.unlink(missing_ok=True)


async def synth_edge(piece: str, path: Path) -> None:
    await edge_tts.Communicate(piece, EDGE_VOICE, rate="-5%").save(str(path))


def synth_google(piece: str, path: Path) -> None:
    gTTS(piece, lang="cs").save(str(path))


def require_closing_quantum(text: str) -> None:
    asia = [name for name in ASIA if name.lower() in text.lower()]
    if asia:
        sys.exit(
            "Nahrávka se nesestaví. Skript vrací asijské trhy: "
            + ", ".join(asia)
        )
    missing = [name for name in REQUIRED if name not in text]
    if CLOSING not in text:
        missing.append(CLOSING)
    if missing:
        sys.exit(
            "Nahrávka se nesestaví. Skript nemá povinnou pasáž o: "
            + ", ".join(missing)
        )
    tail = text.split(CLOSING, 1)[1]
    buried = [name for name in REQUIRED if name not in tail]
    if buried:
        sys.exit(
            "Nahrávka se nesestaví. D-Wave Quantum a IonQ mají být až na závěr, chybí: "
            + ", ".join(buried)
        )


def media_duration(path: Path) -> float:
    probe = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
        probe.stderr,
    )
    if not match:
        sys.exit("Nahrávka se nesestaví. Nelze zjistit délku zvuku.")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


async def main() -> None:
    text = SCRIPT.read_text(encoding="utf-8").strip()
    require_closing_quantum(text)

    use_edge = await edge_available()
    engine = "microsoft" if use_edge else "google"
    pieces = chunks(text)
    part_dir = ROOT / ".briefing-audio-parts"
    part_dir.mkdir(exist_ok=True)
    paths: list[Path] = []
    try:
        for index, piece in enumerate(pieces, start=1):
            path = part_dir / f"part-{index:02d}.mp3"
            if use_edge:
                await synth_edge(piece, path)
            else:
                await asyncio.to_thread(synth_google, piece, path)
            paths.append(path)
            print(f"{index}/{len(pieces)} {engine}", flush=True)
        raw = ROOT / ".briefing-raw.mp3"
        encoded = ROOT / ".briefing-encoded.mp3"
        raw.write_bytes(b"".join(strip_id3(path.read_bytes()) for path in paths))
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-y",
                "-i",
                str(raw),
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "48k",
                "-ar",
                "24000",
                "-ac",
                "1",
                str(encoded),
            ],
            check=True,
            capture_output=True,
        )
        raw.unlink(missing_ok=True)
        length = media_duration(encoded)
        if length > MAX_SECONDS:
            encoded.unlink(missing_ok=True)
            sys.exit(
                "Nahrávka se nesestaví. Skript by trval "
                f"{length / 60:.1f} min, maximum je {MAX_SECONDS // 60} min."
            )
        encoded.replace(OUTPUT)
    finally:
        for path in part_dir.glob("part-*.mp3"):
            path.unlink(missing_ok=True)
        if part_dir.exists():
            part_dir.rmdir()

    length = media_duration(OUTPUT)
    print(
        f"{OUTPUT.name} {OUTPUT.stat().st_size} bytes, "
        f"{length / 60:.1f} min, via {engine}"
    )


if __name__ == "__main__":
    asyncio.run(main())
