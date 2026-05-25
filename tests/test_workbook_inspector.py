# Tests for WorkbookMetadataMap construction and table detection logic

import io

import openpyxl

from core.workbook_inspector import inspect_workbook


def test_metadata_contains_filename(simple_xlsx):
    meta = inspect_workbook(simple_xlsx, "simple.xlsx")
    assert meta["filename"] == "simple.xlsx"


def test_single_sheet_detected(simple_xlsx):
    meta = inspect_workbook(simple_xlsx, "simple.xlsx")
    assert len(meta["sheets"]) == 1
    assert meta["sheets"][0]["name"] == "Sheet1"
    assert meta["sheets"][0]["state"] == "visible"


def test_dimensions_correct(simple_xlsx):
    meta = inspect_workbook(simple_xlsx, "simple.xlsx")
    sheet = meta["sheets"][0]
    assert sheet["dimensions"]["rows"] == 3
    assert sheet["dimensions"]["cols"] == 3


def test_heuristic_table_detection(simple_xlsx):
    meta = inspect_workbook(simple_xlsx, "simple.xlsx")
    tables = meta["sheets"][0]["tables"]
    assert len(tables) == 1
    assert tables[0]["type"] == "detected"
    assert tables[0]["columns"] == ["Name", "Age", "City"]
    assert tables[0]["header_row"] == 1


def test_named_table_detected(named_table_xlsx):
    meta = inspect_workbook(named_table_xlsx, "named.xlsx")
    tables = meta["sheets"][0]["tables"]
    named = [t for t in tables if t["type"] == "named"]
    assert len(named) == 1
    assert named[0]["name"] == "SalesTable"
    assert named[0]["columns"] == ["Date", "Rep", "Amount"]
    assert named[0]["range"] == "A1:C3"


def test_named_table_id_format(named_table_xlsx):
    meta = inspect_workbook(named_table_xlsx, "named.xlsx")
    table = meta["sheets"][0]["tables"][0]
    assert table["id"] == "Sales.SalesTable"


def test_multi_sheet_detected(multi_sheet_xlsx):
    meta = inspect_workbook(multi_sheet_xlsx, "multi.xlsx")
    assert len(meta["sheets"]) == 3
    names = [s["name"] for s in meta["sheets"]]
    assert "Data" in names
    assert "Lookup" in names
    assert "Hidden" in names


def test_hidden_sheet_state(multi_sheet_xlsx):
    meta = inspect_workbook(multi_sheet_xlsx, "multi.xlsx")
    states = {s["name"]: s["state"] for s in meta["sheets"]}
    assert states["Data"] == "visible"
    assert states["Lookup"] == "visible"
    assert states["Hidden"] == "hidden"


def test_merged_cells_flag_true(merged_cells_xlsx):
    meta = inspect_workbook(merged_cells_xlsx, "merged.xlsx")
    assert meta["sheets"][0]["has_merged_cells"] is True


def test_merged_cells_flag_false(simple_xlsx):
    meta = inspect_workbook(simple_xlsx, "simple.xlsx")
    assert meta["sheets"][0]["has_merged_cells"] is False


def test_formula_values_not_available(formula_xlsx):
    meta = inspect_workbook(formula_xlsx, "formula.xlsx")
    # Formulas without cached values → False
    assert meta["formula_values_available"] is False


def test_formula_values_available_when_no_formulas(simple_xlsx):
    meta = inspect_workbook(simple_xlsx, "simple.xlsx")
    # No formulas → considered available (vacuously true)
    assert meta["formula_values_available"] is True


def test_two_region_detection(two_region_xlsx):
    meta = inspect_workbook(two_region_xlsx, "two_region.xlsx")
    tables = meta["sheets"][0]["tables"]
    assert len(tables) == 2
    col_sets = [tuple(t["columns"]) for t in tables]
    assert ("Product", "Price") in col_sets
    assert ("Category", "Count") in col_sets


def test_sparse_row_merged_into_one_table(sparse_row_xlsx):
    """A single blank row within a data region should not split the table."""
    meta = inspect_workbook(sparse_row_xlsx, "sparse.xlsx")
    tables = meta["sheets"][0]["tables"]
    assert len(tables) == 1
    assert tables[0]["columns"] == ["ProjectCode", "Owner", "Budget"]


def test_single_cell_annotation_filtered(annotated_table_xlsx):
    """Single-cell annotation rows (e.g. confidentiality notices) should be
    excluded from the returned table list."""
    meta = inspect_workbook(annotated_table_xlsx, "annotated.xlsx")
    tables = meta["sheets"][0]["tables"]
    assert len(tables) == 1
    assert tables[0]["columns"] == ["Name", "Value"]


def test_accepts_path(tmp_path, simple_xlsx):
    """inspect_workbook also accepts a file Path."""
    xlsx_file = tmp_path / "test.xlsx"
    xlsx_file.write_bytes(simple_xlsx)
    meta = inspect_workbook(xlsx_file)
    assert meta["filename"] == "test.xlsx"
    assert len(meta["sheets"]) == 1


def test_table_range_string_format(simple_xlsx):
    meta = inspect_workbook(simple_xlsx, "simple.xlsx")
    table = meta["sheets"][0]["tables"][0]
    # Range should look like "A1:C3"
    assert ":" in table["range"]
    parts = table["range"].split(":")
    assert len(parts) == 2


# ── Precedence: named tables suppress heuristic detection ────────────────────


def test_named_table_takes_precedence_over_heuristic(named_table_xlsx):
    """When named Excel tables exist, heuristic detection is not used."""
    meta = inspect_workbook(named_table_xlsx, "named.xlsx")
    tables = meta["sheets"][0]["tables"]
    types = [t["type"] for t in tables]
    # All returned tables must be named — no heuristically detected tables
    assert "detected" not in types
    assert "named" in types


# ── formula_values_available = True when cached values are present ────────────


def test_formula_values_available_with_cached_values(formula_xlsx_with_cached):
    """formula_values_available should be True when formulas have cached values."""
    meta = inspect_workbook(formula_xlsx_with_cached, "calc_cached.xlsx")
    assert meta["formula_values_available"] is True


# ── Multi-row header handling ────────────────────────────────────────────────


def test_multi_row_header_sheet_detected_from_first_row(tmp_path):
    """A sheet whose first two rows are both header-like should be detected at row 1."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MultiHeader"
    # Row 1: primary headers, Row 2: sub-headers, Rows 3-5: data
    ws.append(["Category", "Q1", "Q2"])
    ws.append(["(label)", "(jan-mar)", "(apr-jun)"])
    ws.append(["Widgets", 100, 200])
    ws.append(["Gadgets", 150, 250])
    buf = io.BytesIO()
    wb.save(buf)
    multi_header_bytes = buf.getvalue()

    meta = inspect_workbook(multi_header_bytes, "multi_header.xlsx")
    tables = meta["sheets"][0]["tables"]
    assert len(tables) >= 1
    # header_row should point at the first row of the region (row 1)
    assert tables[0]["header_row"] == 1
    # Columns come from the first row
    assert tables[0]["columns"] == ["Category", "Q1", "Q2"]
