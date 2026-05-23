"""Agent tool functions with ``@tool`` decorator."""

import asyncio
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from openpyxl.utils import range_boundaries
from pandas.api.types import is_bool_dtype, is_datetime64_any_dtype, is_numeric_dtype

from core.config import settings
from core.dataframe_loader import load_sheet as load_sheet_dataframe
from core.sandbox import execute_code as run_sandboxed_code

ToolConfig = Annotated[RunnableConfig, InjectedToolArg]
logger = logging.getLogger(__name__)

_WORKSHEET_HEADER_ROW_NUMBER = 1
_DATAFRAME_FIRST_DATA_ROW_NUMBER = 2


def _ok_result(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, default=_json_default)


def _error_result(message: str, **payload: Any) -> str:
    return json.dumps({"ok": False, "error": message, **payload}, default=_json_default)


def _tool_exception(tool_name: str, exc: Exception, **payload: Any) -> str:
    logger.debug("Tool %s failed", tool_name, exc_info=exc)
    return _error_result(str(exc), **payload)


def _json_default(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Series):
        return [_clean_value(item) for item in value.tolist()]
    if pd.isna(value):
        return None
    return value


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if pd.isna(value):
        return None
    return value


def _get_configurable(config: RunnableConfig | None) -> dict[str, Any]:
    configurable = (config or {}).get("configurable", {})
    if not isinstance(configurable, dict):
        raise ValueError("Missing runtime config['configurable'] dictionary")
    return configurable


def _get_runtime_dataframes(configurable: dict[str, Any]) -> dict[str, pd.DataFrame]:
    dataframes = configurable.get("dataframes", {})
    if not isinstance(dataframes, dict):
        raise ValueError("config['configurable']['dataframes'] must be a dict")
    return dataframes


def _find_sheet_meta(
    workbook_meta: dict[str, Any], sheet_name: str
) -> dict[str, Any] | None:
    for sheet_meta in workbook_meta.get("sheets", []):
        if sheet_meta.get("name") == sheet_name:
            return sheet_meta
    return None


def _find_table_meta(
    table_id: str, workbook_meta: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    for sheet_meta in workbook_meta.get("sheets", []):
        for table_meta in sheet_meta.get("tables", []):
            if table_meta.get("id") == table_id:
                return sheet_meta, table_meta
    raise ValueError(f"Unknown table_id: {table_id}")


def _get_table_dataframe(
    table_id: str,
    workbook_meta: dict[str, Any],
    dataframes: dict[str, pd.DataFrame],
) -> tuple[str, pd.DataFrame]:
    sheet_meta, table_meta = _find_table_meta(table_id, workbook_meta)
    sheet_name = sheet_meta["name"]
    dataframe = dataframes.get(sheet_name)
    if dataframe is None:
        raise ValueError(
            f"Sheet '{sheet_name}' is not loaded. Call load_sheet first or include it in runtime dataframes."
        )

    min_col, _min_row, max_col, max_row = range_boundaries(table_meta["range"])
    header_row = int(table_meta.get("header_row", 1))
    data_start = max(header_row - _WORKSHEET_HEADER_ROW_NUMBER, 0)
    # The runtime sheet DataFrame is loaded with worksheet row 1 as the header,
    # so DataFrame row 0 corresponds to worksheet row 2.
    data_end = max(max_row - _DATAFRAME_FIRST_DATA_ROW_NUMBER, data_start - 1)
    subset = dataframe.iloc[data_start : data_end + 1, min_col - 1 : max_col].copy()
    subset.columns = table_meta.get("columns", list(subset.columns))
    subset = subset.reset_index(drop=True)
    return sheet_name, subset


def _select_columns(dataframe: pd.DataFrame, columns: list[str] | None) -> pd.DataFrame:
    if not columns:
        return dataframe

    missing = [column for column in columns if column not in dataframe.columns]
    if missing:
        raise ValueError(f"Unknown columns requested: {', '.join(missing)}")
    return dataframe.loc[:, columns]


def _records(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in dataframe.to_dict(orient="records"):
        records.append({key: _clean_value(value) for key, value in row.items()})
    return records


def _coerce_value(series: pd.Series, value: str | int | float | bool) -> Any:
    if is_numeric_dtype(series):
        coerced = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.isna(coerced):
            raise ValueError(f"Value {value!r} is not comparable to numeric column")
        return coerced

    if is_bool_dtype(series):
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        raise ValueError(f"Value {value!r} is not comparable to boolean column")

    if is_datetime64_any_dtype(series):
        coerced = pd.to_datetime(value, errors="coerce")
        if pd.isna(coerced):
            raise ValueError(f"Value {value!r} is not comparable to datetime column")
        return coerced

    return value


@tool
async def inspect_workbook(config: ToolConfig) -> str:
    """Return the cached workbook metadata map for the active session."""
    try:
        workbook_meta = _get_configurable(config).get("workbook_meta")
        if workbook_meta is None:
            return _error_result("Workbook metadata is not available in runtime config")
        return _ok_result(workbook_meta=workbook_meta)
    except Exception as exc:  # noqa: BLE001
        return _tool_exception("inspect_workbook", exc)


@tool
async def get_sheet_sample(
    table_id: str,
    limit: int = 5,
    mode: Literal["head", "tail", "slice"] = "head",
    start: int = 0,
    columns: list[str] | None = None,
    config: ToolConfig = None,
) -> str:
    """Return a small row sample from a table identified by table_id."""
    try:
        if limit < 1:
            return _error_result("limit must be at least 1")
        if start < 0:
            return _error_result("start must be at least 0")

        configurable = _get_configurable(config)
        workbook_meta = configurable.get("workbook_meta", {})
        dataframes = _get_runtime_dataframes(configurable)
        sheet_name, table_df = _get_table_dataframe(table_id, workbook_meta, dataframes)
        table_df = _select_columns(table_df, columns)

        clamped_limit = min(limit, settings.max_rows_per_fetch)
        if mode == "head":
            sample = table_df.head(clamped_limit)
        elif mode == "tail":
            sample = table_df.tail(clamped_limit)
        else:
            sample = table_df.iloc[start : start + clamped_limit]

        return _ok_result(
            table_id=table_id,
            sheet_name=sheet_name,
            mode=mode,
            start=start,
            limit=clamped_limit,
            total_rows=len(table_df),
            rows=_records(sample),
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_exception("get_sheet_sample", exc, table_id=table_id)


@tool
async def get_column_info(
    table_id: str,
    column_name: str,
    config: ToolConfig = None,
) -> str:
    """Return aggregate information for one qualified table column."""
    try:
        configurable = _get_configurable(config)
        workbook_meta = configurable.get("workbook_meta", {})
        dataframes = _get_runtime_dataframes(configurable)
        sheet_name, table_df = _get_table_dataframe(table_id, workbook_meta, dataframes)
        if column_name not in table_df.columns:
            return _error_result(
                f"Column '{column_name}' was not found in table '{table_id}'",
                table_id=table_id,
                column_name=column_name,
            )

        series = table_df[column_name]
        stats: dict[str, Any] = {
            "dtype": str(series.dtype),
            "null_count": int(series.isna().sum()),
            "unique_count": int(series.nunique(dropna=True)),
        }

        if is_numeric_dtype(series):
            numeric = pd.to_numeric(series, errors="coerce").dropna()
            stats["min"] = _clean_value(numeric.min()) if not numeric.empty else None
            stats["max"] = _clean_value(numeric.max()) if not numeric.empty else None
            stats["mean"] = (
                _clean_value(float(numeric.mean())) if not numeric.empty else None
            )
        else:
            stats["top_values"] = [
                {"value": _clean_value(index), "count": int(count)}
                for index, count in series.dropna().value_counts().head(5).items()
            ]

        return _ok_result(
            table_id=table_id,
            sheet_name=sheet_name,
            column_name=column_name,
            column=stats,
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_exception(
            "get_column_info",
            exc,
            table_id=table_id,
            column_name=column_name,
        )


@tool
async def search_cells(
    table_id: str,
    column_name: str,
    condition: Literal["eq", "gt", "lt", "contains", "startswith"],
    value: str | int | float | bool,
    limit: int = 10,
    config: ToolConfig = None,
) -> str:
    """Filter rows in a table by a single column condition."""
    try:
        if limit < 1:
            return _error_result("limit must be at least 1")

        configurable = _get_configurable(config)
        workbook_meta = configurable.get("workbook_meta", {})
        dataframes = _get_runtime_dataframes(configurable)
        sheet_name, table_df = _get_table_dataframe(table_id, workbook_meta, dataframes)
        if column_name not in table_df.columns:
            return _error_result(
                f"Column '{column_name}' was not found in table '{table_id}'",
                table_id=table_id,
                column_name=column_name,
            )

        series = table_df[column_name]
        if condition == "contains":
            mask = series.astype("string").str.contains(str(value), na=False)
        elif condition == "startswith":
            mask = series.astype("string").str.startswith(str(value), na=False)
        else:
            comparable_value = _coerce_value(series, value)
            if condition == "eq":
                mask = series == comparable_value
            elif condition == "gt":
                mask = series > comparable_value
            else:
                mask = series < comparable_value

        matches = table_df.loc[mask].reset_index(drop=True)
        clamped_limit = min(limit, settings.max_rows_per_fetch)
        return _ok_result(
            table_id=table_id,
            sheet_name=sheet_name,
            column_name=column_name,
            condition=condition,
            value=_clean_value(value),
            total_matches=len(matches),
            limit=clamped_limit,
            rows=_records(matches.head(clamped_limit)),
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_exception(
            "search_cells",
            exc,
            table_id=table_id,
            column_name=column_name,
        )


@tool
async def execute_code(code: str, config: ToolConfig = None) -> str:
    """Execute a sandboxed pandas snippet against the loaded runtime sheets."""
    try:
        dataframes = _get_runtime_dataframes(_get_configurable(config))
        sandbox_result = await asyncio.to_thread(run_sandboxed_code, code, dataframes)
        if sandbox_result["error"] is not None:
            return _error_result(
                sandbox_result["error"],
                sandbox=sandbox_result,
                available_sheets=sorted(dataframes.keys()),
            )

        return _ok_result(
            sandbox=sandbox_result,
            available_sheets=sorted(dataframes.keys()),
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_exception("execute_code", exc)


@tool
async def load_sheet(sheet_name: str, config: ToolConfig = None) -> str:
    """Load one workbook sheet into the runtime dataframes dict."""
    try:
        configurable = _get_configurable(config)
        workbook_meta = configurable.get("workbook_meta", {})
        file_path = configurable.get("file_path")
        if not file_path:
            return _error_result(
                "Runtime config is missing file_path for sheet loading"
            )

        dataframes = _get_runtime_dataframes(configurable)
        sheet_meta = _find_sheet_meta(workbook_meta, sheet_name)
        if sheet_meta is None:
            return _error_result(f"Unknown sheet: {sheet_name}", sheet_name=sheet_name)

        dataframe = await asyncio.to_thread(
            load_sheet_dataframe,
            Path(file_path),
            sheet_name,
            bool(sheet_meta.get("has_merged_cells", False)),
        )
        dataframes[sheet_name] = dataframe

        return _ok_result(
            sheet_name=sheet_name,
            row_count=len(dataframe),
            columns=list(dataframe.columns),
            loaded_sheet_names=sorted(dataframes.keys()),
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_exception("load_sheet", exc, sheet_name=sheet_name)


TOOLS = [
    inspect_workbook,
    get_sheet_sample,
    get_column_info,
    search_cells,
    execute_code,
    load_sheet,
]

__all__ = [tool.name for tool in TOOLS] + ["TOOLS"]
