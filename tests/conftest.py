"""Fixtures: sample Excel files, test client."""

import io

import openpyxl
import pytest
from openpyxl.worksheet.table import Table


@pytest.fixture
def simple_xlsx() -> bytes:
    """Single sheet with simple tabular data, no named tables."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Name", "Age", "City"])
    ws.append(["Alice", 30, "London"])
    ws.append(["Bob", 25, "Paris"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def named_table_xlsx() -> bytes:
    """Workbook with a named Excel table (ListObject)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Date", "Rep", "Amount"])
    ws.append(["2024-01-01", "Alice", 1000])
    ws.append(["2024-01-02", "Bob", 2000])
    table = Table(displayName="SalesTable", ref="A1:C3")
    ws.add_table(table)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def multi_sheet_xlsx() -> bytes:
    """Workbook with multiple sheets, one hidden."""
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Data"
    ws1.append(["ID", "Value"])
    ws1.append([1, 100])

    ws2 = wb.create_sheet("Lookup")
    ws2.append(["Code", "Name"])
    ws2.append(["A", "Alpha"])

    ws3 = wb.create_sheet("Hidden")
    ws3.sheet_state = "hidden"
    ws3.append(["Secret", "Data"])
    ws3.append(["x", "y"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def merged_cells_xlsx() -> bytes:
    """Workbook with merged cells."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Report"
    ws.merge_cells("A1:C1")
    ws["A1"] = "Report Title"
    ws.append(["Name", "Q1", "Q2"])
    ws.append(["Alice", 100, 200])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def formula_xlsx() -> bytes:
    """Workbook with formula cells (no cached values — created without Excel)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Calc"
    ws["A1"] = 10
    ws["B1"] = 20
    ws["C1"] = "=A1+B1"  # Formula with no cached value (never calculated by Excel)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def two_region_xlsx() -> bytes:
    """Workbook with two discontiguous table regions separated by a blank row."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    # First table at rows 1-3
    ws.append(["Product", "Price"])
    ws.append(["Apple", 1.5])
    ws.append(["Banana", 0.5])
    # Blank row
    ws.append([None, None])
    # Second table at rows 5-7
    ws.append(["Category", "Count"])
    ws.append(["Fruit", 2])
    ws.append(["Veg", 5])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def collision_tables_xlsx() -> bytes:
    """Workbook with two tables sharing the same column names."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Collisions"
    ws.append(["Name", "Amount"])
    ws.append(["Alice", 100])
    ws.append(["Bob", 200])
    # Use explicit empty cells so the inspector sees a fully blank separator row.
    ws.append([None, None])
    ws.append(["Name", "Amount"])
    ws.append(["Carrot", 10])
    ws.append(["Daikon", 20])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
