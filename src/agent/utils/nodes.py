"""LangGraph node functions for the spreadsheet agent."""

from typing import Any

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI

from agent.prompts import build_system_prompt
from agent.utils.state import AgentState
from agent.utils.tools import TOOLS
from core.config import settings

_TOOL_NODE = ToolNode(TOOLS)
_DEFAULT_MODEL: Any | None = None


def _get_configurable(config: RunnableConfig | None) -> dict[str, Any]:
    configurable = (config or {}).get("configurable", {})
    if isinstance(configurable, dict):
        return configurable
    raise ValueError("config['configurable'] must be a dictionary")


def _get_runtime_dataframes(config: RunnableConfig | None) -> dict[str, Any]:
    dataframes = _get_configurable(config).get("dataframes", {})
    if isinstance(dataframes, dict):
        return dataframes
    raise ValueError("config['configurable']['dataframes'] must be a dictionary")


def _get_model(config: RunnableConfig | None) -> Any:
    configurable = _get_configurable(config)
    llm = configurable.get("llm")
    if llm is not None:
        return llm

    global _DEFAULT_MODEL
    if _DEFAULT_MODEL is None:
        _DEFAULT_MODEL = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key or None,
        )
    return _DEFAULT_MODEL


async def call_model(
    state: AgentState,
    config: RunnableConfig | None = None,
) -> dict[str, list[Any]]:
    """Invoke the chat model with the system prompt and conversation state."""

    prompt = build_system_prompt(state.get("workbook_meta", {}))
    response = await _get_model(config).bind_tools(TOOLS).ainvoke(
        [SystemMessage(content=prompt), *state.get("messages", [])],
        config=config,
    )
    return {"messages": [response]}


async def call_tools(
    state: AgentState,
    config: RunnableConfig | None = None,
) -> dict[str, list[Any]]:
    """Execute the tool calls emitted by the last model response."""

    result = await _TOOL_NODE.ainvoke({"messages": state.get("messages", [])}, config=config)
    return {
        "messages": result["messages"],
        "loaded_sheet_names": sorted(_get_runtime_dataframes(config).keys()),
    }
