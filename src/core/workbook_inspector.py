"""Builds WorkbookMetadataMap at session creation using openpyxl."""

from io import BytesIO
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter, range_boundaries


def inspect_workbook(
    source: Path | bytes,
    filename: str | None = None,
) -> dict[str, Any]:
    """Build a WorkbookMetadataMap from an Excel file.

    Performs a structural scan of the workbook to capture:
    - Sheet names and visibility state
    - Sheet dimensions (row × col counts)
    - Named Excel tables (ListObjects) — most reliable table boundaries
    - Heuristic contiguous-region tables — fallback when no named tables exist
    - Column names (from table header rows)
    - Merged-cell presence flag per sheet
    - Whether formula cached values are available

    Args:
        source: File path or raw bytes of the Excel file.
        filename: Display filename. Inferred from the path when *source* is a Path.

    Returns:
        WorkbookMetadataMap dict.
    """
    if isinstance(source, Path):
        filename = filename or source.name
    else:
        filename = filename or "unknown.xlsx"

    formula_values_available = _check_formula_values_available(source)

    # Load without read_only so ws.tables and ws.merged_cells are accessible.
    # data_only=True so formula cells return their cached value rather than the
    # formula string (consistent with what the agent will eventually see).
    if isinstance(source, Path):
        wb = openpyxl.load_workbook(source, data_only=True)
    else:
        wb = openpyxl.load_workbook(BytesIO(source), data_only=True)

    sheets_meta: list[dict[str, Any]] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]

        state = "visible" if ws.sheet_state == "visible" else "hidden"
        rows = ws.max_row or 0
        cols = ws.max_column or 0
        has_merged = bool(ws.merged_cells.ranges)
        has_errors = _check_error_cells(ws)
        tables = _detect_tables(ws, sheet_name)

        sheets_meta.append(
            {
                "name": sheet_name,
                "state": state,
                "dimensions": {"rows": rows, "cols": cols},
                "has_merged_cells": has_merged,
                "has_error_cells": has_errors,
                "tables": tables,
            }
        )

    wb.close()

    return {
        "filename": filename,
        "sheets": sheets_meta,
        "formula_values_available": formula_values_available,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────


def _check_formula_values_available(source: Path | bytes) -> bool:
    """Return True if formula cells have cached values (or no formulas exist)."""
    # Find formula cells by loading without data_only (reads formula text)
    if isinstance(source, Path):
        wb_f = openpyxl.load_workbook(source, read_only=True, data_only=False)
    else:
        wb_f = openpyxl.load_workbook(BytesIO(source), read_only=True, data_only=False)

    formula_cells: list[tuple[str, int, int]] = []  # (sheet, row, col)

    for ws in wb_f.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formula_cells.append((ws.title, cell.row, cell.column))
                    if len(formula_cells) >= 5:
                        break
            if len(formula_cells) >= 5:
                break
        if len(formula_cells) >= 5:
            break

    wb_f.close()

    if not formula_cells:
        return True  # No formulas → vacuously available

    # Check whether data_only version has non-None values for those cells
    if isinstance(source, Path):
        wb_d = openpyxl.load_workbook(source, read_only=True, data_only=True)
    else:
        wb_d = openpyxl.load_workbook(BytesIO(source), read_only=True, data_only=True)

    has_cached = False
    for sheet_name, target_row, target_col in formula_cells:
        ws_d = wb_d[sheet_name]
        for row in ws_d.iter_rows(
            min_row=target_row,
            max_row=target_row,
            min_col=target_col,
            max_col=target_col,
        ):
            for cell in row:
                if cell.value is not None:
                    has_cached = True
                    break
        if has_cached:
            break

    wb_d.close()
    return has_cached


def _check_error_cells(ws) -> bool:
    """Return True if any cell in the first 100 rows contains an Excel error value."""
    max_row = min(ws.max_row or 0, 100)
    max_col = ws.max_column or 0
    if max_row == 0 or max_col == 0:
        return False

    for row in ws.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=max_col):
        for cell in row:
            # openpyxl represents error cells as objects whose type name contains "Error"
            if cell.value is not None and "Error" in type(cell.value).__name__:
                return True
    return False


def _detect_tables(ws, sheet_name: str) -> list[dict[str, Any]]:
    """Detect tables in a worksheet.

    Precedence:
    1. Named Excel tables (ListObjects via ws.tables) — most reliable.
    2. Heuristic contiguous-region detection — used only when no named tables exist.
    """
    named_tables = _detect_named_tables(ws, sheet_name)
    if named_tables:
        return named_tables
    return _detect_contiguous_regions(ws, sheet_name)


def _detect_named_tables(ws, sheet_name: str) -> list[dict[str, Any]]:
    """Return metadata for all named Excel tables in the worksheet."""
    tables: list[dict[str, Any]] = []

    if not ws.tables:
        return tables

    for tbl in ws.tables.values():
        ref = tbl.ref
        if not ref:
            continue

        min_col, min_row, max_col, _ = range_boundaries(ref)

        columns = []
        for col_idx in range(min_col, max_col + 1):
            cell = ws.cell(row=min_row, column=col_idx)
            val = cell.value
            columns.append(str(val) if val is not None else f"Column{col_idx}")

        display_name = tbl.displayName or tbl.name
        tables.append(
            {
                "id": f"{sheet_name}.{display_name}",
                "type": "named",
                "name": display_name,
                "range": ref,
                "header_row": min_row,
                "columns": columns,
            }
        )

    return tables


def _detect_contiguous_regions(ws, sheet_name: str) -> list[dict[str, Any]]:
    """Heuristically detect tables by finding contiguous non-empty cell blocks."""
    max_row = ws.max_row
    max_col = ws.max_column

    if not max_row or not max_col:
        return []

    # Collect coordinates of non-empty cells
    non_empty: set[tuple[int, int]] = set()
    for row in ws.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=max_col):
        for cell in row:
            if cell.value is not None:
                non_empty.add((cell.row, cell.column))

    if not non_empty:
        return []

    rows_with_data = sorted({r for r, _ in non_empty})
    row_groups = _find_contiguous_groups(rows_with_data)

    tables: list[dict[str, Any]] = []
    table_idx = 0

    for row_group in row_groups:
        min_row_g = row_group[0]
        max_row_g = row_group[-1]

        cols_in_group = sorted({c for r, c in non_empty if min_row_g <= r <= max_row_g})
        col_groups = _find_contiguous_groups(cols_in_group)

        for col_group in col_groups:
            min_col_g = col_group[0]
            max_col_g = col_group[-1]

            columns = []
            for c in range(min_col_g, max_col_g + 1):
                cell = ws.cell(row=min_row_g, column=c)
                val = cell.value
                columns.append(str(val) if val is not None else f"Column{c}")

            range_str = (
                f"{get_column_letter(min_col_g)}{min_row_g}"
                f":{get_column_letter(max_col_g)}{max_row_g}"
            )

            tables.append(
                {
                    "id": f"{sheet_name}.table_{table_idx}",
                    "type": "detected",
                    "range": range_str,
                    "header_row": min_row_g,
                    "columns": columns,
                }
            )
            table_idx += 1

    return tables


def _find_contiguous_groups(sorted_values: list[int]) -> list[list[int]]:
    """Split a sorted list of integers into groups of consecutive values."""
    if not sorted_values:
        return []

    groups: list[list[int]] = []
    current: list[int] = [sorted_values[0]]

    for v in sorted_values[1:]:
        if v == current[-1] + 1:
            current.append(v)
        else:
            groups.append(current)
            current = [v]

    groups.append(current)
    return groups
