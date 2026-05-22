"""Application settings loaded from environment variables via pydantic-settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # File handling
    max_file_size_mb: int = 50

    # Tool call limits
    max_rows_per_fetch: int = 100
    max_code_output_chars: int = 4000
    max_question_chars: int = 2000

    # Timeouts / iteration caps
    code_execution_timeout_secs: int = 10
    query_timeout_secs: int = 90
    max_agent_iterations: int = 15

    # Session management
    session_cache_size: int = 0
    session_ttl_hours: int = 24
    cleanup_interval_hours: int = 6

    # Storage paths
    db_path: Path = Path("data/spreadsheet_agent.db")
    uploads_dir: Path = Path("data/uploads")

    # Logging
    log_level: str = "INFO"
    log_format: str = "text"


# Module-level singleton — imported by other modules as `from core.config import settings`
settings = Settings()
