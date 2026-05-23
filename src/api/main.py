"""FastAPI app entry point — lifespan, CORS, and router registration."""

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.graph import graph
from api.routers import query, sessions
from core.config import settings
from core.session_store import SessionNotFoundError, SessionStore


async def _cleanup_expired_sessions(session_store: SessionStore) -> None:
    sessions_to_check = await session_store.list()
    cutoff = datetime.now(UTC) - timedelta(hours=settings.session_ttl_hours)

    for record in sessions_to_check:
        created_at = datetime.fromisoformat(record["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if created_at >= cutoff:
            continue
        try:
            await session_store.delete(record["id"])
        except SessionNotFoundError:
            continue


async def _cleanup_loop(session_store: SessionStore) -> None:
    await _cleanup_expired_sessions(session_store)
    interval_seconds = max(settings.cleanup_interval_hours, 1) * 3600
    while True:
        await asyncio.sleep(interval_seconds)
        await _cleanup_expired_sessions(session_store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    session_store = SessionStore()
    await session_store.initialize()
    app.state.session_store = session_store
    app.state.graph = graph

    cleanup_task = asyncio.create_task(_cleanup_loop(session_store))
    app.state.cleanup_task = cleanup_task
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(title="Spreadsheet Agent API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(sessions.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")
