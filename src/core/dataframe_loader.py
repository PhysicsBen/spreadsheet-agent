"""Lazy sheet loading with optional LRU cache; returns dict[str, DataFrame]."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from core.config import settings

# ── Engine selection ──────────────────────────────────────────────────────────

_EXT_ENGINE: dict[str, str] = {
    ".xls": "xlrd",
    ".xlsb": "pyxlsb",
}


def select_engine(filepath: Path, has_merged_cells: bool = False) -> str:
    """Return the pandas engine name appropriate for the given file.

    Strategy (from implementation_plan.md):
    - ``.xls``  → xlrd   (only viable driver for legacy format)
    - ``.xlsb`` → pyxlsb (binary workbook)
    - ``.xlsx`` / ``.xlsm`` → calamine  (3–10× faster than openpyxl for raw data)
    """
    ext = filepath.suffix.lower()
    return _EXT_ENGINE.get(ext, "calamine")


# ── Core loading functions ────────────────────────────────────────────────────


def load_sheet(
    file_path: Path,
    sheet_name: str,
    has_merged_cells: bool = False,
    *,
    header: int = 0,
    skiprows: int | None = None,
    nrows: int | None = None,
) -> pd.DataFrame:
    """Load a single worksheet into a DataFrame using the appropriate engine.

    Args:
        file_path: Path to the Excel file.
        sheet_name: Name of the sheet to load.
        has_merged_cells: Hint that the sheet has merged cells. Currently forwarded
            to ``select_engine``; reserved for a future openpyxl fallback path
            when chunked loading of merged-cell sheets is required.
        header: Row (0-indexed) to use as the column header.
        skiprows: Number of rows to skip before the header.
        nrows: Maximum number of data rows to read.

    Returns:
        A pandas DataFrame with the sheet contents.
    """
    engine = select_engine(file_path, has_merged_cells)

    kwargs: dict[str, Any] = {
        "sheet_name": sheet_name,
        "engine": engine,
        "header": header,
    }
    if skiprows is not None:
        kwargs["skiprows"] = skiprows
    if nrows is not None:
        kwargs["nrows"] = nrows

    return _load_with_cache(str(file_path), sheet_name, engine, header, skiprows, nrows)


def load_sheets(
    file_path: Path,
    sheet_names: list[str],
    *,
    header: int = 0,
) -> dict[str, pd.DataFrame]:
    """Load multiple worksheets into a dict keyed by sheet name.

    Args:
        file_path: Path to the Excel file.
        sheet_names: List of sheet names to load.
        header: Row (0-indexed) to use as column headers (applied to all sheets).

    Returns:
        ``{sheet_name: DataFrame}`` for each requested sheet.
    """
    return {name: load_sheet(file_path, name, header=header) for name in sheet_names}


# ── Optional LRU cache ────────────────────────────────────────────────────────
#
# When SESSION_CACHE_SIZE > 0 each (file_path, sheet_name, engine, header,
# skiprows, nrows) tuple is cached up to that many entries.
# When SESSION_CACHE_SIZE == 0 the cache wrapper is a no-op.


def _build_loader(cache_size: int):
    """Return a (possibly cached) inner loader function."""
    if cache_size > 0:

        @lru_cache(maxsize=cache_size)
        def _cached(
            file_path_str: str,
            sheet_name: str,
            engine: str,
            header: int,
            skiprows: int | None,
            nrows: int | None,
        ) -> pd.DataFrame:
            kwargs: dict[str, Any] = {
                "sheet_name": sheet_name,
                "engine": engine,
                "header": header,
            }
            if skiprows is not None:
                kwargs["skiprows"] = skiprows
            if nrows is not None:
                kwargs["nrows"] = nrows
            return pd.read_excel(file_path_str, **kwargs)

        return _cached

    else:

        def _uncached(
            file_path_str: str,
            sheet_name: str,
            engine: str,
            header: int,
            skiprows: int | None,
            nrows: int | None,
        ) -> pd.DataFrame:
            kwargs: dict[str, Any] = {
                "sheet_name": sheet_name,
                "engine": engine,
                "header": header,
            }
            if skiprows is not None:
                kwargs["skiprows"] = skiprows
            if nrows is not None:
                kwargs["nrows"] = nrows
            return pd.read_excel(file_path_str, **kwargs)

        return _uncached


# Module-level loader — built once at import time with the configured cache size
_load_with_cache = _build_loader(settings.session_cache_size)


def get_cache_info():
    """Return lru_cache statistics, or None when the cache is disabled."""
    fn = _load_with_cache
    if hasattr(fn, "cache_info"):
        return fn.cache_info()
    return None


def clear_cache() -> None:
    """Clear the DataFrame LRU cache (no-op when cache is disabled)."""
    fn = _load_with_cache
    if hasattr(fn, "cache_clear"):
        fn.cache_clear()
