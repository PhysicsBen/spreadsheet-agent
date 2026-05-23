"""RestrictedPython sandbox with timeout and output capture."""

import io
import multiprocessing as mp
import operator
import warnings
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
    - A wall-clock timeout is enforced by running the snippet in a subprocess.
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

    ctx = mp.get_context("spawn")
    recv_conn, send_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_sandbox_worker, args=(send_conn, code, sheets))
    process.start()
    send_conn.close()

    try:
        process.join(timeout_secs)
        if process.is_alive():
            process.terminate()
            process.join(1)
            if process.is_alive():
                process.kill()
                process.join()
            return _result(
                output="",
                result_val=None,
                error=f"Execution timed out after {timeout_secs} seconds",
                truncated=False,
            )

        payload = (
            recv_conn.recv()
            if recv_conn.poll()
            else _result(
                output="",
                result_val=None,
                error="Sandbox worker exited without returning a result",
                truncated=False,
            )
        )
    finally:
        recv_conn.close()

    raw_output = payload["output"]
    truncated = len(raw_output) > max_output_chars
    payload["output"] = raw_output[:max_output_chars] if truncated else raw_output
    payload["truncated"] = truncated
    return payload


def _sandbox_worker(send_conn: Any, code: str, sheets: dict[str, Any]) -> None:
    try:
        payload = _execute_code_in_process(code=code, sheets=sheets)
        send_conn.send(payload)
    finally:
        send_conn.close()


def _execute_code_in_process(code: str, sheets: dict[str, Any]) -> dict[str, Any]:
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

    restricted_builtins = {
        k: v for k, v in safe_builtins.items() if k not in _BLOCKED_NAMES
    }

    exec_globals: dict[str, Any] = {
        "__builtins__": restricted_builtins,
        "_print_": PrintCollector,
        "_getattr_": safer_getattr,
        "_getitem_": _guarded_getitem,
        "_getiter_": _guarded_iter,
        "_write_": full_write_guard,
        "_inplacevar_": _guarded_inplacevar,
        "pd": pd,
        "pandas": pd,
        "np": np,
        "numpy": np,
        "sheets": sheets,
    }

    for name in _BLOCKED_NAMES:
        exec_globals.pop(name, None)

    stdout_buf = io.StringIO()
    local_vars: dict[str, Any] = {}
    error: str | None = None
    result_val: Any = None
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stdout_buf):
            exec(byte_code, exec_globals, local_vars)  # noqa: S102
        result_val = local_vars.get("result")
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    print_collector = local_vars.get("_print")
    print_out = print_collector() if print_collector is not None else ""
    return _result(
        output=print_out + stdout_buf.getvalue(),
        result_val=None if result_val is None else str(result_val),
        error=error,
        truncated=False,
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
