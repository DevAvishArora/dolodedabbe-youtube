"""Find what is working for a dish on YouTube (read-only, API key only).

Ranks recent short videos by views per subscriber, so a small channel's
breakout counts for more than a big channel's ordinary upload.
"""
from datetime import datetime, timedelta, timezone

import requests

from . import config

API = "https://www.googleapis.com/youtube/v3"


def _get(path: str, **params) -> dict:
    params["key"] = config.youtube_key()
    r = requests.get(f"{API}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def rank(videos: list[dict], top: int = 8) -> list[dict]:
    """Sort by views per subscriber; channels under 100 subs are skipped."""
    scored = []
    for v in videos:
        if v["subs"] < 100 or v["views"] <= 0:
            continue
        scored.append({**v, "ratio": round(v["views"] / v["subs"], 2)})
    return sorted(scored, key=lambda v: v["ratio"], reverse=True)[:top]


def top_videos(dish: str, days: int = 180) -> list[dict]:
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    found = _get("search", part="id", q=f"{dish} recipe", type="video",
                 videoDuration="short", order="viewCount", regionCode="IN",
                 publishedAfter=after, maxResults=25)
    ids = [i["id"]["videoId"] for i in found.get("items", [])]
    if not ids:
        return []
    details = _get("videos", part="snippet,statistics", id=",".join(ids))["items"]
    channel_ids = sorted({d["snippet"]["channelId"] for d in details})
    channels = _get("channels", part="statistics", id=",".join(channel_ids))
    subs = {c["id"]: int(c["statistics"].get("subscriberCount", 0))
            for c in channels.get("items", [])}
    videos = [{
        "title": d["snippet"]["title"],
        "description": d["snippet"].get("description", "")[:300],
        "tags": d["snippet"].get("tags", [])[:15],
        "channel": d["snippet"]["channelTitle"],
        "views": int(d["statistics"].get("viewCount", 0)),
        "subs": subs.get(d["snippet"]["channelId"], 0),
    } for d in details]
    return rank(videos)


def summary(videos: list[dict]) -> str:
    if not videos:
        return "No comparable videos found."
    lines = []
    for v in videos:
        lines.append(
            f"- {v['title']} | {v['views']:,} views | {v['ratio']}x views per "
            f"subscriber | tags: {', '.join(v['tags']) or 'none'}"
        )
    return "\n".join(lines)
