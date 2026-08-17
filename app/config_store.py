"""Persist and load application settings from database and .env."""

from pathlib import Path

from app import database as db
from app.config import settings

ENV_PATH = Path(".env")

SETTING_KEYS = (
    "telegram_api_id",
    "telegram_api_hash",
    "lines_per_chunk",
    "antispam_wait_seconds",
    "bot_response_timeout",
    "max_retries",
)


async def load_settings() -> None:
    """Load saved settings from DB into the live settings object."""
    for key in SETTING_KEYS:
        val = await db.get_config(key, "")
        if val == "":
            continue
        if key == "telegram_api_id":
            settings.telegram_api_id = int(val)
        elif key in ("lines_per_chunk", "antispam_wait_seconds", "bot_response_timeout", "max_retries"):
            setattr(settings, key, int(val))
        else:
            setattr(settings, key, val)


async def save_settings(updates: dict) -> None:
    """Save settings to DB, memory, and .env file."""
    for key, val in updates.items():
        if val is None or key not in SETTING_KEYS:
            continue
        await db.set_config(key, str(val))
        if key == "telegram_api_id":
            settings.telegram_api_id = int(val)
        elif key in ("lines_per_chunk", "antispam_wait_seconds", "bot_response_timeout", "max_retries"):
            setattr(settings, key, int(val))
        else:
            setattr(settings, key, str(val))
    write_env_file()


def write_env_file() -> None:
    ENV_PATH.write_text(
        "\n".join(
            [
                f"TELEGRAM_API_ID={settings.telegram_api_id}",
                f"TELEGRAM_API_HASH={settings.telegram_api_hash}",
                f"SESSION_DIR=./sessions",
                f"HOST={settings.host}",
                f"PORT={settings.port}",
                f"DEBUG={'true' if settings.debug else 'false'}",
                "",
            ]
        ),
        encoding="utf-8",
    )
