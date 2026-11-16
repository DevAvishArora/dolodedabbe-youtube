"""Gemini calls: write the title/description/tags, and route free-text messages."""
import json
from pathlib import Path

import requests

from . import config

VOICE = Path(__file__).resolve().parent.parent / "voice.md"


def ask_json(prompt: str) -> dict:
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.gemini_model()}:generateContent")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    r = requests.post(url, json=body, timeout=90,
                      headers={"x-goog-api-key": config.gemini_key()})
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


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
