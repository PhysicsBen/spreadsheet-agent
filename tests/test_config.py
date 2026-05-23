"""Tests for core/config.py — Settings class."""


def test_default_settings():
    from core.config import Settings

    s = Settings()
    assert s.openai_model == "gpt-5.4"
    assert s.max_file_size_mb == 50
    assert s.max_rows_per_fetch == 100
    assert s.max_code_output_chars == 4000
    assert s.max_question_chars == 2000
    assert s.code_execution_timeout_secs == 10
    assert s.query_timeout_secs == 90
    assert s.max_agent_iterations == 15
    assert s.session_cache_size == 0
    assert s.session_ttl_hours == 24
    assert s.cleanup_interval_hours == 6
    assert s.log_level == "INFO"
    assert s.log_format == "text"


def test_settings_override_via_env(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4-turbo")
    monkeypatch.setenv("MAX_ROWS_PER_FETCH", "200")
    monkeypatch.setenv("SESSION_CACHE_SIZE", "10")

    from core.config import Settings

    s = Settings()
    assert s.openai_model == "gpt-4-turbo"
    assert s.max_rows_per_fetch == 200
    assert s.session_cache_size == 10


def test_db_path_and_uploads_dir_are_paths():
    from pathlib import Path

    from core.config import Settings

    s = Settings()
    assert isinstance(s.db_path, Path)
    assert isinstance(s.uploads_dir, Path)


def test_settings_singleton_exists():
    from core.config import settings

    assert settings is not None
    assert settings.openai_model == "gpt-5.4"


def test_default_provider_is_openai():
    from core.config import Settings

    s = Settings()
    assert s.llm_provider == "openai"


def test_active_model_name_openai():
    from core.config import Settings

    s = Settings()
    assert s.active_model_name == s.openai_model


def test_active_model_name_azure(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "my-gpt4-deployment")

    from core.config import Settings

    s = Settings()
    assert s.active_model_name == "my-gpt4-deployment"


def test_active_model_name_databricks(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "databricks")
    monkeypatch.setenv("DATABRICKS_MODEL", "databricks-mixtral-8x7b-instruct")

    from core.config import Settings

    s = Settings()
    assert s.active_model_name == "databricks-mixtral-8x7b-instruct"


def test_azure_provider_settings(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my-resource.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-deploy")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

    from core.config import Settings

    s = Settings()
    assert s.llm_provider == "azure"
    assert s.azure_openai_api_key == "az-key"
    assert s.azure_openai_endpoint == "https://my-resource.openai.azure.com"
    assert s.azure_openai_deployment == "gpt-4o-deploy"
    assert s.azure_openai_api_version == "2025-01-01-preview"


def test_databricks_provider_settings(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "databricks")
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb-123.azuredatabricks.net")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-abc123")
    monkeypatch.setenv("DATABRICKS_MODEL", "databricks-dbrx-instruct")

    from core.config import Settings

    s = Settings()
    assert s.llm_provider == "databricks"
    assert s.databricks_host == "https://adb-123.azuredatabricks.net"
    assert s.databricks_token == "dapi-abc123"
    assert s.databricks_model == "databricks-dbrx-instruct"
