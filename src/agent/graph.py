"""Graph construction and compile() call — singleton compiled at import time."""

import asyncio
from pathlib import Path
import sqlite3
import threading

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import tools_condition

from agent.utils.nodes import call_model, call_tools
from agent.utils.state import AgentState
from core.config import settings


def _build_checkpointer(db_path: Path | None = None) -> SqliteSaver:
    target_path = db_path or settings.db_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target_path, check_same_thread=False)
    return SqliteSaver(connection)


def _run_coroutine(coroutine):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coroutine)
        except BaseException as exc:  # noqa: BLE001
            error["value"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()

    if "value" in error:
        raise error["value"]
    return result["value"]


def _call_model_node(state: AgentState, config=None):
    return _run_coroutine(call_model(state, config))


def _call_tools_node(state: AgentState, config=None):
    return _run_coroutine(call_tools(state, config))


def build_graph(
    *,
    checkpointer: SqliteSaver | None = None,
    max_agent_iterations: int | None = None,
) -> CompiledStateGraph:
    """Build and compile the spreadsheet agent graph."""

    builder = StateGraph(AgentState)
    builder.add_node("call_model", _call_model_node)
    builder.add_node("call_tools", _call_tools_node)
    builder.add_edge(START, "call_model")
    builder.add_conditional_edges("call_model", tools_condition, {"tools": "call_tools", END: END})
    builder.add_edge("call_tools", "call_model")

    iteration_limit = max_agent_iterations or settings.max_agent_iterations
    compiled = builder.compile(checkpointer=checkpointer or _build_checkpointer())
    return compiled.with_config(recursion_limit=(iteration_limit * 2) + 1)


graph = build_graph()
