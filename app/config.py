from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    session_dir: Path = Path("./sessions")
    database_path: Path = Path("./data/tgbt.db")
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False

    lines_per_chunk: int = 15
    antispam_wait_seconds: int = 3
    bot_response_timeout: int = 300
    max_retries: int = 10


settings = Settings()
