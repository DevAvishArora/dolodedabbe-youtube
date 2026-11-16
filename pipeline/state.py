"""State lives in the repo as JSON: one file per clip plus one settings file.

One file per clip keeps concurrent workflow runs from conflicting in git.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
VIDEOS = STATE / "videos"
SETTINGS = STATE / "settings.json"

DEFAULT_SETTINGS = {
    "paused": False,
    "slots": ["Tue 19:00", "Thu 19:00", "Sat 19:00", "Sun 11:00"],
    "instructions": [],
    "raw_seconds": 40,
}


def load_settings() -> dict:
    if SETTINGS.exists():
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS.read_text())}
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    STATE.mkdir(exist_ok=True)
    SETTINGS.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")


def _path(clip_id: int | str) -> Path:
    return VIDEOS / f"{clip_id}.json"


def load_video(clip_id: int | str) -> dict | None:
    p = _path(clip_id)
    return json.loads(p.read_text()) if p.exists() else None


def save_video(video: dict) -> None:
    VIDEOS.mkdir(parents=True, exist_ok=True)
    _path(video["id"]).write_text(
        json.dumps(video, indent=2, ensure_ascii=False) + "\n"
    )


def all_videos() -> list[dict]:
    if not VIDEOS.exists():
        return []
    vids = [json.loads(p.read_text()) for p in VIDEOS.glob("*.json")]
    return sorted(vids, key=lambda v: int(v["id"]))


def find_video(field: str, value) -> dict | None:
    """Find the clip whose `field` (e.g. draft_msg_id) equals `value`."""
    for v in all_videos():
        if v.get(field) == value:
            return v
    return None
