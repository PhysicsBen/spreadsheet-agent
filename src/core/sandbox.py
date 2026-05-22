"""RestrictedPython sandbox with timeout and output capture."""

import io
import operator
import warnings
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

import numpy as np
import pandas as pd
from RestrictedPython import PrintCollector, compile_restricted, safe_builtins
from RestrictedPython.Guards import full_write_guard, safer_getattr

from core.config import settings

# ---------------------------------------------------------------------------
# Guard helpers
# ---------------------------------------------------------------------------


def _guarded_getitem(obj: Any, index: Any) -> Any:
    return obj[index]


def _guarded_iter(obj: Any):
    return iter(obj)


_INPLACE_OPS = {
    "+=": operator.iadd,
    "-=": operator.isub,
    "*=": operator.imul,
    "/=": operator.itruediv,
    "//=": operator.ifloordiv,
    "%=": operator.imod,
    "**=": operator.ipow,
    "&=": operator.iand,
    "|=": operator.ior,
    "^=": operator.ixor,
}


def _guarded_inplacevar(op: str, x: Any, y: Any) -> Any:
    fn = _INPLACE_OPS.get(op)
    if fn is None:
        raise ValueError(f"Unsupported in-place operator: {op!r}")
    return fn(x, y)


# ---------------------------------------------------------------------------
# Block list
# ---------------------------------------------------------------------------

# Names that must never be reachable inside the sandbox.
_BLOCKED_NAMES: frozenset[str] = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "importlib",
        "os",
        "sys",
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "input",
        "raw_input",
    }
)


def execute_code(
    code: str,
    sheets: dict[str, Any],
    timeout_secs: int | None = None,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    """Execute a sandboxed pandas code snippet against the provided DataFrames.

    The snippet runs inside a RestrictedPython environment:
    - Dangerous builtins (``open``, ``exec``, ``eval``, ``__import__``, …) are removed.
    - ``pd`` / ``pandas`` and ``np`` / ``numpy`` are available.
    - The ``sheets`` dict exposes DataFrames as ``sheets["SheetName"]``.
    - A wall-clock timeout is enforced via ``ThreadPoolExecutor``.
    - All print() and stdout/stderr output is captured and optionally truncated.

    The function never raises — all errors are returned in the result dict.

    Args:
        code: Python source code to execute.
        sheets: Mapping of sheet name → DataFrame exposed to the snippet.
        timeout_secs: Wall-clock execution limit (defaults to ``CODE_EXECUTION_TIMEOUT_SECS``).
        max_output_chars: Truncation limit for captured output (defaults to ``MAX_CODE_OUTPUT_CHARS``).

    Returns:
        Dict with keys:
        - ``"output"`` (str): Captured stdout/stderr + print() output.
        - ``"result"`` (str | None): ``str(result)`` if a variable named ``result``
          was assigned in the snippet, else ``None``.
        - ``"error"`` (str | None): Error description, or ``None`` on success.
        - ``"truncated"`` (bool): Whether output was truncated.
    """
    timeout_secs = (
        timeout_secs
        if timeout_secs is not None
        else settings.code_execution_timeout_secs
    )
    max_output_chars = (
        max_output_chars
        if max_output_chars is not None
        else settings.max_code_output_chars
    )

    # ── Compile-time restrictions ─────────────────────────────────────────────
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        try:
            byte_code = compile_restricted(code, filename="<sandbox>", mode="exec")
        except SyntaxError as exc:
            return _result(
                output="", result_val=None, error=f"SyntaxError: {exc}", truncated=False
            )

    if byte_code is None:
        return _result(
            output="",
            result_val=None,
            error="Code compilation failed",
            truncated=False,
        )

    # ── Build restricted globals ──────────────────────────────────────────────
    restricted_builtins = {
        k: v for k, v in safe_builtins.items() if k not in _BLOCKED_NAMES
    }

    exec_globals: dict[str, Any] = {
        "__builtins__": restricted_builtins,
        # RestrictedPython guards (required by compiled bytecode)
        "_print_": PrintCollector,
        "_getattr_": safer_getattr,
        "_getitem_": _guarded_getitem,
        "_getiter_": _guarded_iter,
        "_write_": full_write_guard,
        "_inplacevar_": _guarded_inplacevar,
        # Data science libraries
        "pd": pd,
        "pandas": pd,
        "np": np,
        "numpy": np,
        # Spreadsheet data
        "sheets": sheets,
    }

    # Ensure no blocked names leak in at the top level
    for name in _BLOCKED_NAMES:
        exec_globals.pop(name, None)

    # ── Run in worker thread with timeout ─────────────────────────────────────
    output_buf: list[str] = []
    error_buf: list[str | None] = [None]
    result_buf: list[Any] = [None]

    def _run() -> None:
        stdout_buf = io.StringIO()
        local_vars: dict[str, Any] = {}
        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stdout_buf):
                exec(byte_code, exec_globals, local_vars)  # noqa: S102
            result_buf[0] = local_vars.get("result")
        except Exception as exc:  # noqa: BLE001
            error_buf[0] = f"{type(exc).__name__}: {exc}"
        finally:
            # Combine PrintCollector output (from print() calls) with any
            # direct sys.stdout writes (e.g. pandas/numpy warnings)
            print_collector = local_vars.get("_print")
            print_out = print_collector() if print_collector is not None else ""
            output_buf.append(print_out + stdout_buf.getvalue())

    # Do NOT use the executor as a context manager — its __exit__ calls
    # shutdown(wait=True) which would block forever if the code times out.
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_run)
    timed_out = False
    try:
        future.result(timeout=timeout_secs)
    except FuturesTimeoutError:
        timed_out = True
    finally:
        # wait=False: don't block on a timed-out (unkillable) thread.
        executor.shutdown(wait=False, cancel_futures=True)

    if timed_out:
        return _result(
            output="",
            result_val=None,
            error=f"Execution timed out after {timeout_secs} seconds",
            truncated=False,
        )

    raw_output = output_buf[0] if output_buf else ""
    truncated = len(raw_output) > max_output_chars
    output = raw_output[:max_output_chars] if truncated else raw_output

    result_val = result_buf[0]
    error = error_buf[0]

    return _result(
        output=output,
        result_val=None if result_val is None else str(result_val),
        error=error,
        truncated=truncated,
    )


def _result(
    *,
    output: str,
    result_val: str | None,
    error: str | None,
    truncated: bool,
) -> dict[str, Any]:
    return {
        "output": output,
        "result": result_val,
        "error": error,
        "truncated": truncated,
    }
