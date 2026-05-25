"""Application settings loaded from environment variables via pydantic-settings."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM provider — one of: "openai" | "azure" | "databricks"
    llm_provider: Literal["openai", "azure", "databricks"] = "openai"

    # OpenAI (used when llm_provider == "openai")
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4"

    # Azure OpenAI (used when llm_provider == "azure")
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-02-15-preview"

    # Databricks (used when llm_provider == "databricks")
    # databricks_host: e.g. https://adb-xxx.azuredatabricks.net
    databricks_host: str = ""
    databricks_token: str = ""
    databricks_model: str = "databricks-meta-llama-3-1-70b-instruct"

    # File handling
    max_file_size_mb: int = 50

    # Tool call limits
    max_rows_per_fetch: int = 100
    max_code_output_chars: int = 4000
    max_question_chars: int = 2000

    # Timeouts / iteration caps
    code_execution_timeout_secs: int = 10
    inspect_timeout_secs: int = 300  # max seconds for workbook inspection at upload
    query_timeout_secs: int = 90
    max_agent_iterations: int = 15

    # Workbook inspection — heuristic table detection
    table_row_gap_tolerance: int = 1  # blank rows bridged within a single table region
    table_min_cells: int = 2  # detected regions with fewer cells are discarded as noise

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

    @property
    def active_model_name(self) -> str:
        """The model identifier for the active provider, used in response metadata."""
        if self.llm_provider == "azure":
            return self.azure_openai_deployment
        if self.llm_provider == "databricks":
            return self.databricks_model
        return self.openai_model


# Module-level singleton — imported by other modules as `from core.config import settings`
settings = Settings()
