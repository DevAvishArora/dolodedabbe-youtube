"""Environment configuration. Every secret comes from GitHub Actions secrets."""
import os


def env(name: str, default: str | None = None) -> str:
    # GitHub passes unset repo variables as "", so empty also means "use default".
    value = os.environ.get(name) or default
    if not value:
        raise RuntimeError(f"Missing required setting: {name}")
    return value


def bot_token() -> str:
    return env("TG_BOT_TOKEN")


def chat_id() -> int:
    """The group/channel where clips are dropped."""
    return int(env("TG_CHAT_ID"))


def owner_id() -> int:
    """Telegram user id of the person allowed to control the pipeline."""
    return int(env("TG_OWNER_ID"))


def gemini_key() -> str:
    return env("GEMINI_API_KEY")


def gemini_model() -> str:
    # Model names change; override with the GEMINI_MODEL repo variable.
    return env("GEMINI_MODEL", "gemini-3.8-flash")


def youtube_key() -> str:
    return env("YT_API_KEY")
