"""Thin Telegram Bot API client for text messages (files go through media.py)."""
import requests

from . import config

LIMIT = 4000  # Telegram caps messages at 4096 characters


def _call(method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{config.bot_token()}/{method}"
    r = requests.post(url, json=params, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
    return data["result"]


def send(chat: int, text: str, reply_to: int | None = None) -> int:
    """Send plain text; returns the id of the (last) message sent."""
    msg_id = 0
    for start in range(0, max(len(text), 1), LIMIT):
        params = {"chat_id": chat, "text": text[start:start + LIMIT]}
        if reply_to:
            params["reply_to_message_id"] = reply_to
            params["allow_sending_without_reply"] = True
        msg_id = _call("sendMessage", **params)["message_id"]
    return msg_id


def to_owner(text: str, reply_to: int | None = None) -> int:
    return send(config.owner_id(), text, reply_to)
