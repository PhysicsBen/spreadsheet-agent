import json
from pathlib import Path

import pytest

from agent.utils.tools import (
    execute_code,
    get_column_info,
    get_sheet_sample,
    inspect_workbook,
    load_sheet,
    search_cells,
)
from core.dataframe_loader import load_sheet as load_sheet_df
from core.workbook_inspector import inspect_workbook as inspect_workbook_meta


def _write_fixture(tmp_path: Path, filename: str, file_bytes: bytes) -> Path:
    file_path = tmp_path / filename
    file_path.write_bytes(file_bytes)
    return file_path


def _make_config(
    *,
    workbook_meta: dict,
    dataframes: dict | None = None,
    file_path: Path | None = None,
) -> dict:
    configurable = {
        "workbook_meta": workbook_meta,
        "dataframes": dataframes if dataframes is not None else {},
    }
    if file_path is not None:
        configurable["file_path"] = file_path
    return {"configurable": configurable}


async def _call_tool(tool_obj, payload: dict, *, config: dict) -> dict:
    result = await tool_obj.ainvoke(payload, config=config)
    return json.loads(result)


@pytest.mark.asyncio
async def test_inspect_workbook_returns_cached_metadata(named_table_xlsx):
    workbook_meta = inspect_workbook_meta(named_table_xlsx, "named.xlsx")

    result = await _call_tool(
        inspect_workbook,
        {},
        config=_make_config(workbook_meta=workbook_meta),
    )

    assert result["ok"] is True
    assert result["workbook_meta"] == workbook_meta


@pytest.mark.asyncio
async def test_get_sheet_sample_supports_head_tail_and_column_subset(
    tmp_path, named_table_xlsx
):
    file_path = _write_fixture(tmp_path, "named.xlsx", named_table_xlsx)
    workbook_meta = inspect_workbook_meta(file_path)
    dataframes = {"Sales": load_sheet_df(file_path, "Sales")}

    head_result = await _call_tool(
        get_sheet_sample,
        {
            "table_id": "Sales.SalesTable",
            "limit": 1,
            "mode": "head",
            "columns": ["Rep", "Amount"],
        },
        config=_make_config(workbook_meta=workbook_meta, dataframes=dataframes),
    )
    tail_result = await _call_tool(
        get_sheet_sample,
        {"table_id": "Sales.SalesTable", "limit": 1, "mode": "tail"},
        config=_make_config(workbook_meta=workbook_meta, dataframes=dataframes),
    )

    assert head_result["ok"] is True
    assert head_result["rows"] == [{"Rep": "Alice", "Amount": 1000}]
    assert tail_result["rows"][0]["Rep"] == "Bob"


@pytest.mark.asyncio
async def test_get_sheet_sample_supports_slice_and_respects_max_rows(
    tmp_path, named_table_xlsx, monkeypatch
):
    monkeypatch.setattr("agent.utils.tools.settings.max_rows_per_fetch", 1)
    file_path = _write_fixture(tmp_path, "named.xlsx", named_table_xlsx)
    workbook_meta = inspect_workbook_meta(file_path)
    dataframes = {"Sales": load_sheet_df(file_path, "Sales")}

    result = await _call_tool(
        get_sheet_sample,
        {
            "table_id": "Sales.SalesTable",
            "limit": 5,
            "mode": "slice",
            "start": 1,
        },
        config=_make_config(workbook_meta=workbook_meta, dataframes=dataframes),
    )

    assert result["ok"] is True
    assert result["limit"] == 1
    assert result["rows"] == [
        {"Date": "2024-01-02", "Rep": "Bob", "Amount": 2000},
    ]


@pytest.mark.asyncio
async def test_get_column_info_reports_numeric_and_categorical_stats(
    tmp_path, named_table_xlsx
):
    file_path = _write_fixture(tmp_path, "named.xlsx", named_table_xlsx)
    workbook_meta = inspect_workbook_meta(file_path)
    dataframes = {"Sales": load_sheet_df(file_path, "Sales")}

    numeric_result = await _call_tool(
        get_column_info,
        {"table_id": "Sales.SalesTable", "column_name": "Amount"},
        config=_make_config(workbook_meta=workbook_meta, dataframes=dataframes),
    )
    categorical_result = await _call_tool(
        get_column_info,
        {"table_id": "Sales.SalesTable", "column_name": "Rep"},
        config=_make_config(workbook_meta=workbook_meta, dataframes=dataframes),
    )

    assert numeric_result["ok"] is True
    assert numeric_result["column"]["dtype"] in {"int64", "Int64"}
    assert numeric_result["column"]["mean"] == 1500.0
    assert numeric_result["column"]["min"] == 1000
    assert numeric_result["column"]["max"] == 2000
    assert categorical_result["column"]["top_values"][0] == {
        "value": "Alice",
        "count": 1,
    }


@pytest.mark.asyncio
async def test_search_cells_uses_table_id_to_handle_column_collisions(
    tmp_path, collision_tables_xlsx
):
    file_path = _write_fixture(tmp_path, "collisions.xlsx", collision_tables_xlsx)
    workbook_meta = inspect_workbook_meta(file_path)
    dataframes = {"Collisions": load_sheet_df(file_path, "Collisions")}

    result = await _call_tool(
        search_cells,
        {
            "table_id": "Collisions.table_1",
            "column_name": "Name",
            "condition": "startswith",
            "value": "C",
            "limit": 5,
        },
        config=_make_config(workbook_meta=workbook_meta, dataframes=dataframes),
    )

    assert result["ok"] is True
    assert result["total_matches"] == 1
    assert result["rows"] == [{"Name": "Carrot", "Amount": 10}]


@pytest.mark.asyncio
async def test_search_cells_supports_numeric_comparisons(tmp_path, named_table_xlsx):
    file_path = _write_fixture(tmp_path, "named.xlsx", named_table_xlsx)
    workbook_meta = inspect_workbook_meta(file_path)
    dataframes = {"Sales": load_sheet_df(file_path, "Sales")}

    result = await _call_tool(
        search_cells,
        {
            "table_id": "Sales.SalesTable",
            "column_name": "Amount",
            "condition": "gt",
            "value": 1500,
            "limit": 5,
        },
        config=_make_config(workbook_meta=workbook_meta, dataframes=dataframes),
    )

    assert result["ok"] is True
    assert result["total_matches"] == 1
    assert result["rows"][0]["Rep"] == "Bob"


@pytest.mark.asyncio
async def test_execute_code_runs_against_loaded_sheets(tmp_path, multi_sheet_xlsx):
    file_path = _write_fixture(tmp_path, "multi.xlsx", multi_sheet_xlsx)
    workbook_meta = inspect_workbook_meta(file_path)
    dataframes = {"Data": load_sheet_df(file_path, "Data")}

    result = await _call_tool(
        execute_code,
        {"code": "result = sheets['Data']['Value'].sum()"},
        config=_make_config(workbook_meta=workbook_meta, dataframes=dataframes),
    )

    assert result["ok"] is True
    assert result["sandbox"]["result"] == "100"
    assert result["sandbox"]["error"] is None


@pytest.mark.asyncio
async def test_execute_code_returns_errors_as_strings():
    result = await _call_tool(
        execute_code,
        {"code": "result = 1 / 0"},
        config=_make_config(workbook_meta={}),
    )

    assert result["ok"] is False
    assert "ZeroDivision" in result["error"]


@pytest.mark.asyncio
async def test_load_sheet_adds_dataframe_for_execute_code(tmp_path, multi_sheet_xlsx):
    file_path = _write_fixture(tmp_path, "multi.xlsx", multi_sheet_xlsx)
    workbook_meta = inspect_workbook_meta(file_path)
    dataframes = {"Data": load_sheet_df(file_path, "Data")}
    config = _make_config(
        workbook_meta=workbook_meta,
        dataframes=dataframes,
        file_path=file_path,
    )

    load_result = await _call_tool(
        load_sheet,
        {"sheet_name": "Lookup"},
        config=config,
    )
    exec_result = await _call_tool(
        execute_code,
        {"code": "result = sheets['Lookup']['Name'].iloc[0]"},
        config=config,
    )

    assert load_result["ok"] is True
    assert "Lookup" in dataframes
    assert exec_result["sandbox"]["result"] == "Alpha"
