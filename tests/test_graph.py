import json
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError

from agent.graph import build_graph
from core.workbook_inspector import inspect_workbook


def _write_fixture(tmp_path: Path, filename: str, file_bytes: bytes) -> Path:
    file_path = tmp_path / filename
    file_path.write_bytes(file_bytes)
    return file_path


class MockLLM:
    def __init__(
        self,
        responder: Callable[[list[object]], Awaitable[AIMessage]],
    ) -> None:
        self._responder = responder
        self.bound_tool_names: list[str] = []
        self.system_prompts: list[str] = []

    def bind_tools(self, tools, **kwargs):
        self.bound_tool_names = [tool.name for tool in tools]

        async def arun(messages, config=None):
            self.system_prompts.append(messages[0].content)
            return await self._responder(messages)

        return RunnableLambda(lambda *_args, **_kwargs: None, afunc=arun)


def _build_test_graph(
    tmp_path: Path,
    *,
    max_agent_iterations: int = 5,
):
    """Return a compiled test graph plus its open SQLite connection."""

    connection = sqlite3.connect(tmp_path / "graph.db", check_same_thread=False)
    saver = SqliteSaver(connection)
    return build_graph(
        checkpointer=saver,
        max_agent_iterations=max_agent_iterations,
    ), connection


def test_graph_react_loop_routes_tool_calls_and_tracks_loaded_sheets(
    tmp_path,
    named_table_xlsx,
):
    file_path = _write_fixture(tmp_path, "named.xlsx", named_table_xlsx)
    workbook_meta = inspect_workbook(file_path)
    dataframes: dict[str, object] = {}

    async def responder(messages):
        tool_messages = [
            message for message in messages if isinstance(message, ToolMessage)
        ]
        if not tool_messages:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_sheet",
                        "args": {"sheet_name": "Sales"},
                        "id": "call-load-sheet",
                        "type": "tool_call",
                    }
                ],
            )

        last_tool = tool_messages[-1]
        if last_tool.name == "load_sheet":
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_sheet_sample",
                        "args": {"table_id": "Sales.SalesTable", "limit": 2},
                        "id": "call-sample",
                        "type": "tool_call",
                    }
                ],
            )

        rows = json.loads(last_tool.content)["rows"]
        return AIMessage(content=f"Loaded {len(rows)} rows from Sales.SalesTable.")

    llm = MockLLM(responder)
    graph, connection = _build_test_graph(tmp_path)

    try:
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="Show me a sales sample")],
                "session_id": "session-1",
                "thread_id": "thread-react",
                "workbook_meta": workbook_meta,
                "loaded_sheet_names": [],
            },
            config={
                "configurable": {
                    "thread_id": "thread-react",
                    "llm": llm,
                    "workbook_meta": workbook_meta,
                    "dataframes": dataframes,
                    "file_path": file_path,
                }
            },
        )
    finally:
        connection.close()

    tool_message_names = [
        message.name
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]
    assert llm.bound_tool_names == [
        "inspect_workbook",
        "get_sheet_sample",
        "get_column_info",
        "search_cells",
        "execute_code",
        "load_sheet",
    ]
    assert '"filename": "named.xlsx"' in llm.system_prompts[0]
    assert "Only state facts that you retrieved via tool calls" in llm.system_prompts[0]
    assert "table_id" in llm.system_prompts[0]
    assert tool_message_names == ["load_sheet", "get_sheet_sample"]
    assert result["messages"][-1].content == "Loaded 2 rows from Sales.SalesTable."
    assert sorted(dataframes) == ["Sales"]
    assert result["loaded_sheet_names"] == ["Sales"]


def test_graph_persists_conversation_state_by_thread_id(tmp_path):
    workbook_meta = {
        "filename": "memory.xlsx",
        "sheets": [],
        "formula_values_available": True,
    }

    async def responder(messages):
        human_messages = [
            message.content for message in messages if isinstance(message, HumanMessage)
        ]
        if "What did I ask first?" in human_messages:
            return AIMessage(content=f"You first asked: {human_messages[0]}")
        return AIMessage(content="I can remember this thread.")

    llm = MockLLM(responder)
    graph, connection = _build_test_graph(tmp_path)

    try:
        graph.invoke(
            {
                "messages": [HumanMessage(content="Remember my first question")],
                "session_id": "session-2",
                "thread_id": "thread-memory",
                "workbook_meta": workbook_meta,
                "loaded_sheet_names": [],
            },
            config={
                "configurable": {
                    "thread_id": "thread-memory",
                    "llm": llm,
                    "workbook_meta": workbook_meta,
                    "dataframes": {},
                }
            },
        )
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="What did I ask first?")],
                "session_id": "session-2",
                "thread_id": "thread-memory",
                "workbook_meta": workbook_meta,
                "loaded_sheet_names": [],
            },
            config={
                "configurable": {
                    "thread_id": "thread-memory",
                    "llm": llm,
                    "workbook_meta": workbook_meta,
                    "dataframes": {},
                }
            },
        )
    finally:
        connection.close()

    ai_contents = [
        message.content
        for message in result["messages"]
        if isinstance(message, AIMessage)
    ]
    assert "I can remember this thread." in ai_contents
    assert (
        result["messages"][-1].content == "You first asked: Remember my first question"
    )


def test_graph_stops_runaway_loops_at_iteration_limit(tmp_path):
    workbook_meta = {
        "filename": "loop.xlsx",
        "sheets": [],
        "formula_values_available": True,
    }

    async def responder(messages):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "inspect_workbook",
                    "args": {},
                    "id": f"loop-{len(messages)}",
                    "type": "tool_call",
                }
            ],
        )

    llm = MockLLM(responder)
    graph, connection = _build_test_graph(tmp_path, max_agent_iterations=2)

    try:
        with pytest.raises(GraphRecursionError):
            graph.invoke(
                {
                    "messages": [HumanMessage(content="Loop forever")],
                    "session_id": "session-3",
                    "thread_id": "thread-loop",
                    "workbook_meta": workbook_meta,
                    "loaded_sheet_names": [],
                },
                config={
                    "configurable": {
                        "thread_id": "thread-loop",
                        "llm": llm,
                        "workbook_meta": workbook_meta,
                        "dataframes": {},
                    }
                },
            )
    finally:
        connection.close()
