"""Session CRUD + file upload router."""

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from api.schemas import SessionListItem, SessionResponse
from core.config import settings
from core.session_store import SessionNotFoundError, SessionStore
from core.workbook_inspector import inspect_workbook

router = APIRouter(prefix="/sessions", tags=["sessions"])

_ZIP_SIGNATURE = b"PK\x03\x04"
_OLE_SIGNATURE = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
_ZIP_EXTENSIONS = {".xlsx", ".xlsm"}
_OLE_EXTENSIONS = {".xls", ".xlsb"}


def _get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def _safe_filename(filename: str | None) -> str:
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing upload filename")
    sanitized = Path(filename).name
    if not sanitized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid upload filename")
    return sanitized


def _validate_excel_magic_bytes(file_bytes: bytes, filename: str) -> None:
    extension = Path(filename).suffix.lower()
    if file_bytes.startswith(_ZIP_SIGNATURE) and extension in _ZIP_EXTENSIONS:
        return
    if file_bytes.startswith(_OLE_SIGNATURE) and extension in _OLE_EXTENSIONS:
        return
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "Unsupported or invalid Excel file. Allowed types: .xlsx, .xlsm, .xls, .xlsb",
    )


async def _file_size_bytes(file_path: str) -> int:
    return await asyncio.to_thread(lambda: Path(file_path).stat().st_size)


def _collect_thread_ids_for_session(checkpointer: Any, session_id: str) -> list[str]:
    thread_ids: set[str] = set()
    for checkpoint in checkpointer.list(None):
        values = checkpoint.checkpoint.get("channel_values", {})
        if values.get("session_id") != session_id:
            continue
        thread_id = values.get("thread_id")
        if isinstance(thread_id, str):
            thread_ids.add(thread_id)
            continue
        configurable = checkpoint.config.get("configurable", {})
        configured_thread_id = configurable.get("thread_id")
        if isinstance(configured_thread_id, str):
            thread_ids.add(configured_thread_id)
    return sorted(thread_ids)


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    file: UploadFile = File(...),
    session_store: SessionStore = Depends(_get_session_store),
) -> SessionResponse:
    filename = _safe_filename(file.filename)
    file_bytes = await file.read()
    await file.close()

    max_file_size_bytes = settings.max_file_size_mb * 1024 * 1024
    if len(file_bytes) > max_file_size_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Uploaded file exceeds MAX_FILE_SIZE_MB ({settings.max_file_size_mb} MB)",
        )

    _validate_excel_magic_bytes(file_bytes, filename)
    workbook_meta = await asyncio.to_thread(inspect_workbook, file_bytes, filename)
    record = await session_store.create(filename, file_bytes, workbook_meta)

    return SessionResponse(
        session_id=record["id"],
        filename=record["filename"],
        created_at=record["created_at"],
        file_size_bytes=len(file_bytes),
        workbook_meta=record["workbook_meta"],
    )


@router.get("", response_model=list[SessionListItem])
async def list_sessions(
    session_store: SessionStore = Depends(_get_session_store),
) -> list[SessionListItem]:
    sessions = await session_store.list()
    response: list[SessionListItem] = []
    for record in sessions:
        response.append(
            SessionListItem(
                session_id=record["id"],
                filename=record["filename"],
                created_at=record["created_at"],
                file_size_bytes=await _file_size_bytes(record["file_path"]),
            )
        )
    return response


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    session_store: SessionStore = Depends(_get_session_store),
) -> SessionResponse:
    try:
        record = await session_store.get(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return SessionResponse(
        session_id=record["id"],
        filename=record["filename"],
        created_at=record["created_at"],
        file_size_bytes=await _file_size_bytes(record["file_path"]),
        workbook_meta=record["workbook_meta"],
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    request: Request,
    session_id: str,
    session_store: SessionStore = Depends(_get_session_store),
) -> None:
    try:
        thread_ids = await asyncio.to_thread(
            _collect_thread_ids_for_session, request.app.state.graph.checkpointer, session_id
        )
        for thread_id in thread_ids:
            await asyncio.to_thread(request.app.state.graph.checkpointer.delete_thread, thread_id)
        await session_store.delete(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/{session_id}/threads", response_model=list[str])
async def list_session_threads(
    request: Request,
    session_id: str,
    session_store: SessionStore = Depends(_get_session_store),
) -> list[str]:
    try:
        await session_store.get(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return await asyncio.to_thread(
        _collect_thread_ids_for_session, request.app.state.graph.checkpointer, session_id
    )
