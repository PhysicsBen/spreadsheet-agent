"""Tests for core/config.py — Settings class."""


def test_default_settings():
    from core.config import Settings

    s = Settings()
    assert s.openai_model == "gpt-4o"
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
    assert settings.openai_model == "gpt-4o"
