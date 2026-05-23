"""AgentState definition for the spreadsheet LangGraph agent."""

from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    """Persisted graph state for one spreadsheet conversation thread."""

    messages: Annotated[list[AnyMessage], add_messages]
    session_id: str
    thread_id: str
    workbook_meta: dict[str, Any]
    loaded_sheet_names: list[str]
