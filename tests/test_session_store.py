"""Tests for core/session_store.py — SQLite session CRUD + file path helpers."""

from pathlib import Path

import pytest

from core.session_store import SessionNotFoundError, SessionStore


@pytest.fixture
async def store(tmp_path):
    s = SessionStore(
        db_path=tmp_path / "test.db",
        uploads_dir=tmp_path / "uploads",
    )
    await s.initialize()
    return s


async def test_create_returns_session_dict(store):
    session = await store.create("test.xlsx", b"fakecontent", {"filename": "test.xlsx"})
    assert session["id"] is not None
    assert session["filename"] == "test.xlsx"
    assert session["workbook_meta"] == {"filename": "test.xlsx"}
    assert session["created_at"] is not None
    assert session["updated_at"] is not None


async def test_create_writes_file_to_disk(store, tmp_path):
    await store.create("test.xlsx", b"filedata", {})
    # uploads_dir / session_id / filename should exist
    uploads = tmp_path / "uploads"
    session_dirs = list(uploads.iterdir())
    assert len(session_dirs) == 1
    files = list(session_dirs[0].iterdir())
    assert len(files) == 1
    assert files[0].name == "test.xlsx"
    assert files[0].read_bytes() == b"filedata"


async def test_get_returns_correct_session(store):
    session = await store.create("test.xlsx", b"content", {"key": "value"})
    fetched = await store.get(session["id"])
    assert fetched["id"] == session["id"]
    assert fetched["filename"] == "test.xlsx"
    assert fetched["workbook_meta"] == {"key": "value"}


async def test_get_nonexistent_raises(store):
    with pytest.raises(SessionNotFoundError):
        await store.get("00000000-0000-0000-0000-000000000000")


async def test_list_all_sessions(store):
    await store.create("a.xlsx", b"content_a", {})
    await store.create("b.xlsx", b"content_b", {})
    sessions = await store.list()
    assert len(sessions) == 2
    filenames = {s["filename"] for s in sessions}
    assert filenames == {"a.xlsx", "b.xlsx"}


async def test_list_empty(store):
    sessions = await store.list()
    assert sessions == []


async def test_delete_removes_session(store):
    session = await store.create("test.xlsx", b"content", {})
    session_id = session["id"]
    await store.delete(session_id)
    with pytest.raises(SessionNotFoundError):
        await store.get(session_id)


async def test_delete_removes_file_from_disk(store, tmp_path):
    session = await store.create("test.xlsx", b"content", {})
    file_path = session["file_path"]
    assert Path(file_path).exists()
    await store.delete(session["id"])
    assert not Path(file_path).exists()


async def test_delete_nonexistent_raises(store):
    with pytest.raises(SessionNotFoundError):
        await store.delete("00000000-0000-0000-0000-000000000000")


async def test_wal_mode_enabled(store, tmp_path):
    import aiosqlite

    async with (
        aiosqlite.connect(tmp_path / "test.db") as db,
        db.execute("PRAGMA journal_mode") as cursor,
    ):
        row = await cursor.fetchone()
    assert row[0].lower() == "wal"


async def test_initialize_creates_sessions_table(store, tmp_path):
    import aiosqlite

    async with (
        aiosqlite.connect(tmp_path / "test.db") as db,
        db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        ) as cursor,
    ):
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "sessions"


def test_get_file_path_helper(tmp_path):
    store = SessionStore(
        db_path=tmp_path / "test.db",
        uploads_dir=tmp_path / "uploads",
    )
    path = store.get_file_path("abc-123", "sales.xlsx")
    assert path == tmp_path / "uploads" / "abc-123" / "sales.xlsx"
