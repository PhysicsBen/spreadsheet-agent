"""POST /sessions/{session_id}/query router."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from api.schemas import QueryRequest, QueryResponse, QuerySource
from core.config import settings
from core.dataframe_loader import load_sheets
from core.session_store import SessionNotFoundError, SessionStore

router = APIRouter(prefix="/sessions", tags=["query"])
logger = logging.getLogger(__name__)


def _get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store


SessionStoreDep = Annotated[SessionStore, Depends(_get_session_store)]


def _extract_loaded_sheet_names(agent_graph: Any, thread_id: str) -> list[str]:
    try:
        snapshot = agent_graph.get_state({"configurable": {"thread_id": thread_id}})
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        logger.debug(
            "Unable to restore loaded_sheet_names for thread %s",
            thread_id,
            exc_info=exc,
        )
        return []

    loaded = snapshot.values.get("loaded_sheet_names", [])
    if not isinstance(loaded, list):
        return []
    return [sheet for sheet in loaded if isinstance(sheet, str)]


def _extract_last_ai_message(messages: list[Any]) -> AIMessage | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message
    return None


def _extract_answer(last_ai_message: AIMessage | None) -> str:
    if last_ai_message is None:
        return ""
    if isinstance(last_ai_message.content, str):
        return last_ai_message.content
    return str(last_ai_message.content)


def _extract_token_usage(last_ai_message: AIMessage | None) -> int:
    if last_ai_message is None:
        return 0
    usage_metadata = getattr(last_ai_message, "usage_metadata", None)
    if isinstance(usage_metadata, dict):
        total = usage_metadata.get("total_tokens")
        if isinstance(total, int):
            return total
    response_metadata = getattr(last_ai_message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        token_usage = response_metadata.get("token_usage")
        if isinstance(token_usage, dict) and isinstance(
            token_usage.get("total_tokens"), int
        ):
            return token_usage["total_tokens"]
    return 0


def _extract_model(last_ai_message: AIMessage | None) -> str:
    if last_ai_message is None:
        return settings.active_model_name
    response_metadata = getattr(last_ai_message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        model_name = response_metadata.get("model_name")
        if isinstance(model_name, str) and model_name:
            return model_name
    return settings.active_model_name


def _extract_needs_clarification(last_ai_message: AIMessage | None) -> bool:
    if last_ai_message is None:
        return False
    additional_kwargs = getattr(last_ai_message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        flag = additional_kwargs.get("needs_clarification")
        if isinstance(flag, bool):
            return flag
    return False


def _extract_sources(messages: list[Any]) -> list[QuerySource]:
    deduped: dict[tuple[str, str, str], QuerySource] = {}
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if not isinstance(message.content, str):
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        table_id = payload.get("table_id")
        sheet = payload.get("sheet_name") or payload.get("sheet")
        tool_used = message.name or payload.get("tool_used")
        if not (
            isinstance(table_id, str)
            and isinstance(sheet, str)
            and isinstance(tool_used, str)
        ):
            continue
        deduped[(table_id, sheet, tool_used)] = QuerySource(
            table_id=table_id,
            sheet=sheet,
            tool_used=tool_used,
        )
    return list(deduped.values())


@router.post("/{session_id}/query", response_model=QueryResponse)
async def query_session(
    request: Request,
    session_id: str,
    payload: QueryRequest,
    session_store: SessionStoreDep,
) -> QueryResponse:
    try:
        session_record = await session_store.get(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    thread_id = payload.thread_id or uuid4()
    thread_id_str = str(thread_id)
    workbook_meta = session_record["workbook_meta"]
    file_path = Path(session_record["file_path"])
    agent_graph = request.app.state.graph

    loaded_sheet_names = await asyncio.to_thread(
        _extract_loaded_sheet_names,
        agent_graph,
        thread_id_str,
    )
    dataframes = (
        await asyncio.to_thread(load_sheets, file_path, loaded_sheet_names)
        if loaded_sheet_names
        else {}
    )

    config = {
        "configurable": {
            "thread_id": thread_id_str,
            "dataframes": dataframes,
            "workbook_meta": workbook_meta,
            "file_path": str(file_path),
        }
    }
    initial_state = {
        "messages": [HumanMessage(content=payload.question)],
        "session_id": session_id,
        "thread_id": thread_id_str,
        "workbook_meta": workbook_meta,
        "loaded_sheet_names": sorted(dataframes.keys()),
    }

    try:
        final_state = await asyncio.wait_for(
            asyncio.to_thread(agent_graph.invoke, initial_state, config=config),
            timeout=settings.query_timeout_secs,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "Query timed out before the agent returned a response",
        ) from exc

    messages = final_state.get("messages", [])
    last_ai_message = _extract_last_ai_message(messages)
    answer = _extract_answer(last_ai_message)
    sources = _extract_sources(messages)

    return QueryResponse(
        answer=answer,
        thread_id=UUID(thread_id_str),
        session_id=UUID(session_id),
        model=_extract_model(last_ai_message),
        tokens_used=_extract_token_usage(last_ai_message),
        sources=sources,
        needs_clarification=_extract_needs_clarification(last_ai_message),
    )
