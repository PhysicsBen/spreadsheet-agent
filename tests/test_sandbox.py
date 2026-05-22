# Tests for sandbox: valid code execution, blocked imports, timeout enforcement


from core.sandbox import execute_code

# ── Successful execution ──────────────────────────────────────────────────────


def test_simple_arithmetic():
    result = execute_code("result = 2 + 2", sheets={})
    assert result["error"] is None
    assert result["result"] == "4"


def test_output_capture():
    result = execute_code("print('hello world')", sheets={})
    assert "hello world" in result["output"]
    assert result["error"] is None


def test_sheets_dict_accessible():
    import pandas as pd

    df = pd.DataFrame({"A": [1, 2, 3]})
    result = execute_code("result = sheets['Sheet1']['A'].sum()", sheets={"Sheet1": df})
    assert result["error"] is None
    assert result["result"] == "6"


def test_pandas_operations():
    import pandas as pd

    df = pd.DataFrame({"val": [10, 20, 30]})
    code = "result = sheets['data']['val'].mean()"
    result = execute_code(code, sheets={"data": df})
    assert result["error"] is None
    assert result["result"] == "20.0"


def test_numpy_available():
    code = "result = np.sqrt(16)"
    result = execute_code(code, sheets={})
    assert result["error"] is None
    assert result["result"] == "4.0"


def test_returns_none_result_when_no_result_var():
    result = execute_code("x = 1 + 1", sheets={})
    assert result["result"] is None
    assert result["error"] is None


# ── Error handling ────────────────────────────────────────────────────────────


def test_syntax_error_returned_not_raised():
    result = execute_code("def (", sheets={})
    assert result["error"] is not None
    assert "SyntaxError" in result["error"]


def test_runtime_error_returned_not_raised():
    result = execute_code("result = 1 / 0", sheets={})
    assert result["error"] is not None
    assert (
        "ZeroDivision" in result["error"]
        or "division by zero" in result["error"].lower()
    )


def test_name_error_returned_not_raised():
    result = execute_code("result = undefined_variable", sheets={})
    assert result["error"] is not None


# ── Security: blocked imports ─────────────────────────────────────────────────


def test_import_os_blocked():
    result = execute_code("import os", sheets={})
    assert result["error"] is not None


def test_import_sys_blocked():
    result = execute_code("import sys", sheets={})
    assert result["error"] is not None


def test_import_subprocess_blocked():
    result = execute_code("import subprocess", sheets={})
    assert result["error"] is not None


def test_open_builtin_blocked():
    result = execute_code("open('/etc/passwd')", sheets={})
    assert result["error"] is not None


def test_exec_blocked():
    result = execute_code("exec('x=1')", sheets={})
    assert result["error"] is not None


def test_dunder_import_blocked():
    result = execute_code("__import__('os')", sheets={})
    assert result["error"] is not None


# ── Timeout ───────────────────────────────────────────────────────────────────


def test_timeout_enforced():
    result = execute_code(
        "while True: pass",
        sheets={},
        timeout_secs=1,
    )
    assert result["error"] is not None
    assert (
        "timeout" in result["error"].lower() or "timed out" in result["error"].lower()
    )


# ── Output truncation ─────────────────────────────────────────────────────────


def test_output_truncation():
    code = "print('x' * 10000)"
    result = execute_code(code, sheets={}, max_output_chars=100)
    assert len(result["output"]) <= 100
    assert result["truncated"] is True


def test_no_truncation_when_under_limit():
    result = execute_code("print('hi')", sheets={}, max_output_chars=4000)
    assert result["truncated"] is False


# ── Return structure ──────────────────────────────────────────────────────────


def test_result_keys_always_present():
    result = execute_code("x = 1", sheets={})
    assert "output" in result
    assert "result" in result
    assert "error" in result
    assert "truncated" in result
