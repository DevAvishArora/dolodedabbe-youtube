"""Entry point: handles one Telegram update forwarded by the Cloudflare Worker.

Run as:  UPDATE_JSON='{...}' python -m pipeline.handle_update
"""
import json
import os
import re
import traceback
from datetime import datetime, timezone

from . import config, research, state, tg, writer

APPROVE_WORDS = {"ok", "okay", "yes", "approve", "approved", "done", "👍"}

HELP = """Commands (or just type what you want in plain words):
/status - pending and recent clips
/pause, /resume - stop or restart processing new clips
/instruct <rule> - standing rule for all future titles and descriptions
/instructions - list saved rules
/forget <number> - delete a saved rule
/redo <clip id> - rewrite the draft for a clip
/length <seconds> - target length for videos made from #raw clips
/help - this list

On a draft: reply "ok" to approve, or reply with the change you want."""


def parse_caption(caption: str | None) -> tuple[str, str]:
    """'Paneer bhurji #raw' -> ('Paneer bhurji', 'raw'). Default mode is final."""
    caption = caption or ""
    mode = "raw" if re.search(r"#raw\b", caption, re.I) else "final"
    dish = re.sub(r"#\w+", "", caption)
    dish = re.sub(r"\s+", " ", dish).strip(" -:|")
    return dish, mode


def has_video(msg: dict) -> bool:
    if msg.get("video"):
        return True
    return (msg.get("document") or {}).get("mime_type", "").startswith("video/")


def parse_command(text: str) -> tuple[str, str]:
    """'/instruct@MyBot use Hinglish' -> ('instruct', 'use Hinglish')."""
    head, _, arg = text.partition(" ")
    return head.lstrip("/").split("@")[0].lower(), arg.strip()


def format_draft(video: dict) -> str:
    d = video["draft"]
    alts = "\n".join(f"  - {t}" for t in d.get("title_alternatives", []))
    return (
        f"Draft for clip {video['id']}: {video['dish']}\n\n"
        f"Title:\n{d['title']}\n\nOther options:\n{alts}\n\n"
        f"On-screen hook: {d.get('hook_text', '')}\n\n"
        f"Description:\n{d['description']}\n\n"
        f"Tags: {', '.join(d.get('tags', []))}\n\n"
        'Reply "ok" to approve, or reply with what to change.'
    )


def process(video: dict, change_request: str = "") -> None:
    """Edit (if raw), research, write, and send the draft to the owner."""
    try:
        if video["mode"] == "raw" and not video.get("edited"):
            from . import media
            srcs = media.download_many(video["chat"], video.get("parts") or [video["id"]])
            target = state.load_settings()["raw_seconds"]
            edited, note = media.join(srcs, target, str(video["id"]))
            media.upload(config.owner_id(), edited,
                         f"Edited clip {video['id']}: {video['dish']}\n{note}")
            video["edited"] = True
        if not video.get("research"):
            video["research"] = research.summary(research.top_videos(video["dish"]))
        video["draft"] = writer.write_draft(
            video["dish"], video["research"],
            state.load_settings()["instructions"],
            change_request, video.get("draft") if change_request else None,
        )
        video["draft_msg_id"] = tg.to_owner(format_draft(video))
        video["status"] = "awaiting_approval"
    except Exception as exc:
        video["status"] = "failed"
        video["error"] = str(exc)[:500]
        state.save_video(video)
        tg.to_owner(f"Clip {video['id']} ({video.get('dish') or 'no dish'}) failed: "
                    f"{video['error']}\nSend /redo {video['id']} to retry.")
        raise
    state.save_video(video)


def new_clip(msg: dict) -> None:
    if state.load_video(msg["message_id"]):
        return  # Telegram re-delivered the same update
    dish, mode = parse_caption(msg.get("caption"))
    parts = [msg["message_id"]]
    if msg.get("media_group_id"):
        # Album: only its first item carries the caption, and each item arrives
        # as its own update. A #raw album becomes one video, handled once by
        # the run for the first item; the other runs stop here.
        from . import media
        album = media.fetch_album(msg["chat"]["id"], msg["message_id"])
        album_dish, album_mode = parse_caption(album["caption"])
        if album_mode == "raw":
            if msg["message_id"] != min(album["ids"]):
                return
            dish, mode, parts = album_dish, "raw", album["ids"]
    video = {
        "id": msg["message_id"], "chat": msg["chat"]["id"], "dish": dish,
        "mode": mode, "parts": parts, "status": "new",
        "received": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if state.load_settings()["paused"]:
        video["status"] = "held"
        state.save_video(video)
        tg.to_owner(f"Paused: clip {video['id']} is held. /resume, then /redo {video['id']}.")
        return
    if not dish:
        video["ask_msg_id"] = tg.send(
            video["chat"], "Which dish is this? Reply to this message with the name.",
            reply_to=video["id"])
        video["status"] = "awaiting_dish"
        state.save_video(video)
        return
    process(video)


def dish_reply(msg: dict) -> None:
    video = state.find_video("ask_msg_id", msg["reply_to_message"]["message_id"])
    if video and video["status"] == "awaiting_dish":
        video["dish"], _ = parse_caption(msg["text"])
        process(video)


def approve(video: dict) -> None:
    video["status"] = "approved"
    state.save_video(video)
    d = video["draft"]
    tg.to_owner(f"Approved clip {video['id']}. Auto-upload is not switched on yet, "
                "so post it from the YouTube app. Copy-ready text follows.")
    tg.to_owner(d["title"])
    tg.to_owner(d["description"])
    tg.to_owner(", ".join(d.get("tags", [])))


def run_command(command: str, arg: str) -> None:
    settings = state.load_settings()
    if command == "status":
        vids = [v for v in state.all_videos() if v["status"] != "posted"][-10:]
        lines = [f"{v['id']}: {v.get('dish') or '(no dish)'} - {v['status']}" for v in vids]
        paused = "PAUSED\n" if settings["paused"] else ""
        tg.to_owner(paused + ("\n".join(lines) or "Nothing pending."))
    elif command in ("pause", "resume"):
        settings["paused"] = command == "pause"
        state.save_settings(settings)
        tg.to_owner("Paused. New clips will be held." if settings["paused"]
                    else "Resumed. Use /redo <clip id> for any held clips.")
    elif command == "instruct" and arg:
        settings["instructions"].append(arg)
        state.save_settings(settings)
        tg.to_owner(f"Saved rule {len(settings['instructions'])}: {arg}")
    elif command == "instructions":
        rules = [f"{i}. {r}" for i, r in enumerate(settings["instructions"], 1)]
        tg.to_owner("\n".join(rules) or "No saved rules.")
    elif command == "forget" and arg.isdigit():
        index = int(arg) - 1
        if 0 <= index < len(settings["instructions"]):
            removed = settings["instructions"].pop(index)
            state.save_settings(settings)
            tg.to_owner(f"Deleted rule: {removed}")
        else:
            tg.to_owner("No rule with that number. See /instructions.")
    elif command == "redo" and arg.isdigit():
        video = state.load_video(arg)
        if not video:
            tg.to_owner(f"No clip with id {arg}. See /status.")
        elif not video.get("dish"):
            tg.to_owner(f"Clip {arg} has no dish name yet.")
        else:
            video.pop("research", None)
            process(video)
    elif command == "length" and arg.isdigit():
        seconds = int(arg)
        if 10 <= seconds <= 180:
            settings["raw_seconds"] = seconds
            state.save_settings(settings)
            tg.to_owner(f"Videos made from #raw clips will now target {seconds}s.")
        else:
            tg.to_owner("Pick a length between 10 and 180 seconds.")
    elif command == "help" or command == "start":
        tg.to_owner(HELP)
    else:
        tg.to_owner("I didn't understand that.\n\n" + HELP)


def owner_message(msg: dict) -> None:
    text = (msg.get("text") or "").strip()
    if not text:
        return
    replied = (msg.get("reply_to_message") or {}).get("message_id")
    video = state.find_video("draft_msg_id", replied) if replied else None
    if video:
        if text.lower().strip(" .!") in APPROVE_WORDS:
            approve(video)
        else:
            process(video, change_request=text)
        return
    if text.startswith("/"):
        command, arg = parse_command(text)
    else:
        routed = writer.route(text)
        command, arg = routed.get("command", "unknown"), str(routed.get("arg", ""))
    run_command(command, arg)


def handle(update: dict) -> None:
    msg = update.get("message") or update.get("channel_post")
    if not msg:
        return
    chat = msg["chat"]["id"]
    sender = (msg.get("from") or {}).get("id")
    if chat == config.chat_id() and has_video(msg):
        new_clip(msg)
    elif chat == config.chat_id() and msg.get("reply_to_message") and msg.get("text"):
        dish_reply(msg)
    elif msg["chat"]["type"] == "private" and sender == config.owner_id():
        owner_message(msg)


if __name__ == "__main__":
    try:
        handle(json.loads(os.environ["UPDATE_JSON"]))
    except Exception:
        traceback.print_exc()
        try:
            tg.to_owner("Pipeline error: " + traceback.format_exc()[-600:])
        finally:
            raise SystemExit(1)
