"""Large-file download/upload and ffmpeg editing for #raw clips.

The Bot API cannot download files over 20 MB, so files move through Telethon
(MTProto), logged in as the same bot.

Raw clips for one dish arrive as a Telegram album. They are joined in the
order sent, cropped to 9:16, and fitted to a target length.
"""
import atexit
import json
import subprocess
from pathlib import Path

from . import config

WORK = Path("/tmp/dolladabbe")
LOGO = Path(__file__).resolve().parent.parent / "assets" / "logo.png"
MAX_SPEED = 4.0
ALBUM_SPAN = 10  # Telegram albums hold at most 10 items
_CLIENT = None


def _client():  # -> telethon.sync.TelegramClient (imported lazily)
    """Log in once per run and reuse the connection.

    Telegram makes bots wait if they log in many times in a short period, and a
    #raw album used to log in three times (find album, download, send back).
    """
    global _CLIENT
    if _CLIENT is None:
        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient

        client = TelegramClient(
            StringSession(), int(config.env("TG_API_ID")), config.env("TG_API_HASH")
        )
        client.start(bot_token=config.bot_token())
        atexit.register(client.disconnect)
        _CLIENT = client
    return _CLIENT


def fetch_album(chat: int, message_id: int) -> dict:
    """Return {'ids': [...], 'caption': str} for the album containing message_id."""
    nearby = list(range(max(1, message_id - ALBUM_SPAN), message_id + ALBUM_SPAN + 1))
    msgs = [m for m in _client().get_messages(chat, ids=nearby) if m]
    me = next((m for m in msgs if m.id == message_id), None)
    if me is None or not me.grouped_id:
        return {"ids": [message_id], "caption": (me.message if me else "") or ""}
    group = sorted((m for m in msgs if m.grouped_id == me.grouped_id), key=lambda m: m.id)
    caption = next((m.message for m in group if m.message), "")
    return {"ids": [m.id for m in group], "caption": caption}


def download_many(chat: int, message_ids: list[int]) -> list[Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    paths = []
    client = _client()
    for mid in message_ids:
        msg = client.get_messages(chat, ids=mid)
        if msg is None or not msg.media:
            raise RuntimeError(f"Message {mid} has no video to download")
        paths.append(Path(client.download_media(msg, file=str(WORK / f"{mid}_raw"))))
    return paths


def upload(chat: int, path: Path, caption: str) -> None:
    _client().send_file(chat, str(path), caption=caption, supports_streaming=True)


def probe(path: Path) -> tuple[float, bool]:
    """Return (duration in seconds, has an audio stream)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type", "-of", "json", str(path)],
        check=True, capture_output=True, text=True).stdout
    data = json.loads(out)
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return float(data["format"]["duration"]), has_audio


def plan_join(durations: list[float], target: float,
              max_speed: float = MAX_SPEED) -> tuple[float, list[tuple[float, float]]]:
    """Decide how to fit clips into `target` seconds.

    Returns (speed, [(start, length) per clip]) in source time. Clips that fit
    are left alone. Otherwise they are sped up (to max_speed at most), and if
    that is still too long the middle of each clip is kept in proportion.
    """
    total = sum(durations)
    if total <= target:
        return 1.0, [(0.0, round(d, 3)) for d in durations]
    speed = min(total / target, max_speed)
    keep = min(1.0, target / (total / speed))
    return round(speed, 3), [
        (round(d * (1 - keep) / 2, 3), round(d * keep, 3)) for d in durations
    ]


def join_args(srcs: list[Path], dst: Path, speed: float,
              segments: list[tuple[float, float]], audio: bool,
              logo: Path | None) -> list[str]:
    args = ["ffmpeg", "-y"]
    for s in srcs:
        args += ["-i", str(s)]
    if logo:
        args += ["-i", str(logo)]
    crop = "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920,setsar=1,fps=30"
    parts, labels = [], ""
    for i, (start, length) in enumerate(segments):
        parts.append(f"[{i}:v]trim=start={start}:duration={length},"
                     f"setpts=(PTS-STARTPTS)/{speed},{crop}[v{i}]")
        labels += f"[v{i}]"
        if audio:
            parts.append(f"[{i}:a]atrim=start={start}:duration={length},"
                         f"asetpts=PTS-STARTPTS,aresample=44100[a{i}]")
            labels += f"[a{i}]"
    n = len(srcs)
    parts.append(f"{labels}concat=n={n}:v=1:a={1 if audio else 0}"
                 + ("[vj][aout]" if audio else "[vj]"))
    if logo:
        parts.append(f"[{n}:v]scale=160:-1[l];[vj][l]overlay=W-w-40:60[vout]")
        vout = "[vout]"
    else:
        vout = "[vj]"
    args += ["-filter_complex", ";".join(parts), "-map", vout]
    if audio:
        args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "160k"]
    args += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-movflags", "+faststart", str(dst)]
    return args


def join(srcs: list[Path], target: float, name: str) -> tuple[Path, str]:
    """Join raw clips into one 9:16 video. Returns (path, note for the owner)."""
    WORK.mkdir(parents=True, exist_ok=True)
    probes = [probe(s) for s in srcs]
    durations = [p[0] for p in probes]
    speed, segments = plan_join(durations, target)
    # Sped-up audio sounds wrong, so sound is kept only at normal speed.
    audio = speed == 1.0 and all(p[1] for p in probes)
    dst = WORK / f"{name}_edited.mp4"
    logo = LOGO if LOGO.exists() else None
    done = subprocess.run(join_args(srcs, dst, speed, segments, audio, logo),
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + done.stderr[-400:])
    total = sum(durations)
    note = f"{len(srcs)} clip(s), {total:.0f}s of footage."
    if speed > 1.0:
        note += f" Sped up {speed:g}x to fit {target:.0f}s, so there is no sound."
        if segments[0][0] > 0:
            note += " Still too long, so only the middle of each clip was kept."
    elif not audio:
        note += " No sound (at least one clip had none)."
    return dst, note
