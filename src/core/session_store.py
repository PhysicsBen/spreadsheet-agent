"""SQLite-backed session metadata store + file path helpers."""

import contextlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import asyncio

from core.config import settings


class SessionNotFoundError(Exception):
    """Raised when a requested session does not exist."""


class SessionStore:
    """Async session store backed by SQLite with WAL journal mode."""

    def __init__(
        self,
        db_path: Path | None = None,
        uploads_dir: Path | None = None,
    ) -> None:
        self.db_path = db_path or settings.db_path
        self.uploads_dir = uploads_dir or settings.uploads_dir

    async def initialize(self) -> None:
        """Create the sessions table and enable WAL mode.

        Must be called once before any other operations (typically at app startup).
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id          TEXT PRIMARY KEY,
                    filename    TEXT NOT NULL,
                    file_path   TEXT NOT NULL,
                    workbook_meta TEXT NOT NULL DEFAULT '{}',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
                """
            )
            await db.commit()

    async def create(
        self,
        filename: str,
        file_bytes: bytes,
        workbook_meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a new session, persist the uploaded file, and return the record.

        Args:
            filename: Original file name (used for display and extension detection).
            file_bytes: Raw file content to write to disk.
            workbook_meta: Pre-built WorkbookMetadataMap dict to store as JSON.

        Returns:
            Session record dict.
        """
        session_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        # Persist file
        session_dir = self.uploads_dir / session_id
        await asyncio.to_thread(session_dir.mkdir, parents=True, exist_ok=True)
        file_path = session_dir / filename
        await asyncio.to_thread(file_path.write_bytes, file_bytes)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sessions (id, filename, file_path, workbook_meta, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    filename,
                    str(file_path),
                    json.dumps(workbook_meta),
                    now,
                    now,
                ),
            )
            await db.commit()

        return {
            "id": session_id,
            "filename": filename,
            "file_path": str(file_path),
            "workbook_meta": workbook_meta,
            "created_at": now,
            "updated_at": now,
        }

    async def get(self, session_id: str) -> dict[str, Any]:
        """Retrieve a session by ID.

        Raises:
            SessionNotFoundError: If no session with the given ID exists.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            raise SessionNotFoundError(f"Session '{session_id}' not found")

        return self._row_to_dict(row)

    async def list(self) -> list[dict[str, Any]]:
        """Return all sessions ordered by creation time (newest first)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()

        return [self._row_to_dict(row) for row in rows]

    async def delete(self, session_id: str) -> None:
        """Delete a session and its associated file from disk.

        Raises:
            SessionNotFoundError: If the session does not exist.
        """
        session = await self.get(session_id)  # Raises if not found

        # Remove file from disk
        file_path = Path(session["file_path"])
        if await asyncio.to_thread(file_path.exists):
            await asyncio.to_thread(file_path.unlink)

        # Try to remove the now-empty session directory
        session_dir = file_path.parent
        if await asyncio.to_thread(session_dir.exists) and session_dir != self.uploads_dir:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(session_dir.rmdir)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            await db.commit()

    def get_file_path(self, session_id: str, filename: str) -> Path:
        """Return the expected on-disk path for a session file without hitting the DB."""
        return self.uploads_dir / session_id / filename

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        d["workbook_meta"] = json.loads(d["workbook_meta"])
        return d
