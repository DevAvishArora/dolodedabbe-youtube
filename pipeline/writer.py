"""Gemini calls: write the title/description/tags, and route free-text messages."""
import json
import os
import re
import time
from pathlib import Path

import requests

from . import config

VOICE = Path(__file__).resolve().parent.parent / "voice.md"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT = 180          # seconds to wait for one Gemini reply
ATTEMPTS = 3           # tries per model before moving to the fallback
RETRY_STATUS = {429, 500, 502, 503, 504}


class GeminiError(RuntimeError):
    pass


def models() -> list[str]:
    """Primary model, then a lighter fallback (repo variable GEMINI_FALLBACK_MODEL)."""
    primary = config.gemini_model()
    fallback = os.environ.get("GEMINI_FALLBACK_MODEL") or "gemini-3.5-flash-lite"
    return [primary] if fallback == primary else [primary, fallback]


def extract_json(data: dict) -> dict:
    """Pull the JSON answer out of a Gemini response, skipping thought parts."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        reason = (data.get("promptFeedback") or {}).get("blockReason") \
            or (data.get("candidates") or [{}])[0].get("finishReason")
        raise GeminiError(f"Gemini returned no answer ({reason or 'empty response'})")
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def _post(model: str, body: dict) -> requests.Response:
    return requests.post(API.format(model=model), json=body, timeout=TIMEOUT,
                         headers={"x-goog-api-key": config.gemini_key()})


def ask_json(prompt: str) -> dict:
    """Call Gemini with low thinking (fast), retrying slow or busy replies,
    then falling back to a lighter model."""
    errors = []
    for model in models():
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingLevel": "low"},
            },
        }
        for attempt in range(ATTEMPTS):
            try:
                r = _post(model, body)
            except (requests.Timeout, requests.ConnectionError) as exc:
                errors.append(f"{model}: {type(exc).__name__}")
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 400 and "thinkingConfig" in body["generationConfig"]:
                # Model does not accept a thinking level: drop it and retry now,
                # through the same loop so timeouts are still caught.
                body["generationConfig"].pop("thinkingConfig")
                errors.append(f"{model}: HTTP 400, retrying without thinkingLevel")
                continue
            if r.status_code in RETRY_STATUS:
                errors.append(f"{model}: HTTP {r.status_code}")
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code != 200:
                errors.append(f"{model}: HTTP {r.status_code} {r.text[:200]}")
                break  # not worth retrying this model
            try:
                return extract_json(r.json())
            except (GeminiError, ValueError) as exc:
                errors.append(f"{model}: {exc}")
    raise GeminiError("Gemini failed after retries: " + "; ".join(errors[-4:]))


def draft_prompt(dish: str, research: str, instructions: list[str],
                 change_request: str = "", previous: dict | None = None) -> str:
    voice = VOICE.read_text() if VOICE.exists() else ""
    rules = "\n".join(f"- {i}" for i in instructions) or "- none"
    prompt = f"""You write YouTube Shorts packaging for Dolladabbe, a cloud kitchen in India
that posts 30-40 second recipe clips with no voiceover.

Channel voice:
{voice}

Standing instructions from the owner (always follow):
{rules}

Dish in this clip: {dish}

Recent videos that beat their channel's usual numbers for this dish:
{research}

Learn from the patterns above; never copy a title. Do not invent facts about
the recipe that the dish name does not support. Tags matter little, so keep
them few and specific.

Return JSON with exactly these keys:
"title": string, max 70 characters, specific and curiosity-driven
"title_alternatives": array of 2 different-style titles
"hook_text": string, max 8 words, on-screen text for the first second
"description": string, 2-4 short lines, ends with 3 hashtags including #shorts
"tags": array of 8-12 strings
"""
    if previous and change_request:
        prompt += (f"\nPrevious draft:\n{json.dumps(previous, ensure_ascii=False)}"
                   f"\nThe owner asked for this change: {change_request}\n")
    return prompt


def write_draft(dish, research, instructions, change_request="", previous=None) -> dict:
    return ask_json(draft_prompt(dish, research, instructions, change_request, previous))


COMMANDS = ["status", "pause", "resume", "instruct", "instructions",
            "forget", "redo", "length", "help", "unknown"]


def route(text: str) -> dict:
    """Turn a free-text message into one of the known commands."""
    return ask_json(f"""Map the owner's Telegram message to one command for a YouTube
upload pipeline. Commands:
status - what is pending or recently done
pause / resume - stop or restart processing new clips
instruct - save a standing rule for all future titles/descriptions (arg = the rule)
instructions - list saved rules
forget - delete a saved rule (arg = its number)
redo - rewrite the draft for a clip (arg = clip id)
length - target length for videos made from raw clips (arg = seconds, digits only)
help - list commands
unknown - anything else

Message: {text!r}

Return JSON: {{"command": one of {COMMANDS}, "arg": string}}""")
