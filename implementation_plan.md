# Spreadsheet Agent — Implementation Plan

## Problem Statement

Build a FastAPI service wrapping a LangGraph AI agent that answers natural language questions about Excel spreadsheets. The agent intelligently traverses large, complex workbooks (multi-sheet, multi-table) rather than loading everything at once, supports persisted sessions (upload once, query many times), multi-turn conversation per session, and can execute code against the data for computed answers.

---

## Core Design Challenges

### 1. Workbook Complexity

Excel files are not simple flat tables. A real-world workbook may contain:

- **Multiple sheets (tabs)** — some may be raw data, some lookup tables, some pivots or summaries
- **Multiple discontiguous tables per sheet** — tables separated by blank rows/columns, not necessarily starting at cell A1
- **Non-standard header rows** — headers might be at row 3, or preceded by a title block
- **Excel named tables (ListObjects)** — formal Excel table objects with their own names and ranges (most reliable way to detect table boundaries)
- **Merged cells** — break standard pandas parsing; require special handling
- **Hidden sheets** — may contain relevant data; should be surfaced but not assumed visible
- **Formula cells** — values are only available if the file was saved with computed values (`data_only=True` in openpyxl)
- **Mixed data types within columns** — e.g., a column that is mostly numeric but has text flags

**Strategy:** At session creation time, perform a full structural scan of the workbook to build a **Workbook Metadata Map**. This map is stored in the session record (as JSON in SQLite) and injected into the agent's system prompt so it can reason about what is available before deciding what to fetch.

### 2. DataFrame Management at Runtime

DataFrames are not JSON-serializable and cannot be stored in LangGraph's checkpointer. They must be managed separately:

- On each `/query` request, load only the sheets the agent requests (lazy loading) from disk
- Maintain a **per-request DataFrames dict** (`{sheet_name: DataFrame}`) keyed by session
- Inject this dict into the LangGraph graph via `config["configurable"]` (LangGraph's runtime injection mechanism) — node and tool functions access it via the `config` parameter
- Do **not** hold DataFrames in process memory between requests (prevents memory bloat for large files)
- Optional: configurable LRU in-memory cache for hot sessions (env var `SESSION_CACHE_SIZE`, default 0 = disabled)

### 3. Session vs. Thread Model

There are two distinct concepts:

| Concept | What it is | Stored in |
|---|---|---|
| **Session** | The spreadsheet + its metadata | SQLite `sessions` table + file on disk |
| **Thread** | A conversation (Q&A history) on that spreadsheet | LangGraph checkpointer (`SqliteSaver`) |

A single session can have **multiple threads** (independent conversations on the same file). The API accepts an optional `thread_id` on `/query` — if omitted, a new thread is created; if provided, the conversation continues.

### 4. Large File Handling

- Use `openpyxl` in **read-only mode** (`read_only=True`) for the initial structural scan — orders of magnitude faster and lower memory than full load
- Load individual sheets into pandas via `pd.read_excel(..., sheet_name=name)` only when the agent requests them
- Implement configurable limits: `MAX_FILE_SIZE_MB`, `MAX_ROWS_PER_FETCH`, `MAX_CODE_OUTPUT_CHARS`
- For sheets too large for pandas to load fully: implement chunked reading via `skiprows` + `nrows`

### 5. Code Execution Sandbox

The agent can generate pandas code snippets to answer computational questions (averages, sums, filters, cross-sheet joins). Key considerations:

- Use `RestrictedPython` to block dangerous builtins (`open`, `exec`, `import`, `__import__`, `os`, `sys`, etc.)
- Allowed: `pandas`, `numpy`, and safe Python builtins only
- DataFrames are exposed to the sandbox as a `sheets` dict: `sheets["Sheet1"]`, `sheets["Sales Data"]`, etc.
- Enforce a **wall-clock timeout** (via `concurrent.futures.ThreadPoolExecutor` + `Future.result(timeout=N)`)
- Truncate output if it exceeds `MAX_CODE_OUTPUT_CHARS` — the agent gets a truncation notice
- All errors are caught and returned to the agent (not raised) so it can retry or adjust its approach

### 6. Workbook Metadata Map Structure

Built once at session creation, stored as JSON, injected into the system prompt:

```json
{
  "filename": "sales_report_2024.xlsx",
  "sheets": [
    {
      "name": "Q1 Sales",
      "state": "visible",
      "dimensions": {"rows": 5200, "cols": 12},
      "tables": [
        {
          "id": "Q1 Sales.SalesTable",
          "type": "named",
          "name": "SalesTable",
          "range": "A1:L5200",
          "header_row": 1,
          "columns": ["Date", "Rep", "Region", "Product", "Units", "Revenue", ...]
        }
      ]
    },
    {
      "name": "Lookup",
      "state": "visible",
      "dimensions": {"rows": 50, "cols": 3},
      "tables": [
        {
          "id": "Lookup.table_0",
          "type": "detected",
          "range": "A1:C15",
          "header_row": 1,
          "columns": ["Region", "Manager", "Target"]
        },
        {
          "id": "Lookup.table_1",
          "type": "detected",
          "range": "E1:F10",
          "header_row": 1,
          "columns": ["Product", "Category"]
        }
      ]
    }
  ],
  "formula_values_available": true
}
```

**Table detection precedence:**
1. Excel named tables (ListObjects via `ws.tables`) — most reliable
2. Heuristic contiguous region detection — scan for blank row/column separators

### 7. Context Window Management

Tool results can be very large. Protect the context window:

- `get_sheet_sample`: max `MAX_ROWS_PER_FETCH` rows (default 100)
- `get_column_info`: always returns aggregates, not raw values
- `search_cells`: truncated to max rows with a count of total matches
- `execute_code`: output truncated to `MAX_CODE_OUTPUT_CHARS` (default 4000)
- Message history: use LangGraph's `trim_messages` to prune old messages if the thread grows long

---

## Additional Design Challenges

### 7. Security

#### File Upload Security
- **XLSX is a ZIP of XML files** — malformed XLSX can trigger XXE (XML External Entity) attacks. openpyxl has some protections but large nested XMLs can cause memory issues.
- **Zip bombs** — a malicious XLSX could decompress to gigabytes. Enforce `MAX_FILE_SIZE_MB` check on the raw upload AND on decompressed size via openpyxl's sheet dimension check during inspection.
- **Macros** — `.xlsm` files contain VBA macros. openpyxl never executes them, but we should refuse `.xlsm` files with active macros or at minimum document this clearly. For v1: accept `.xlsm` but strip/ignore macros.
- **External links in formulas** — `=[OtherFile.xlsx]Sheet1!A1` returns `None` with `data_only=True`. Surface this in metadata.
- **Path traversal** — upload directory paths must use UUID session IDs only, never user-provided names. Filenames from uploads are sanitized and stored separately (display only).

#### API Security
- **No authentication required** — this is a local personal tool, not exposed to a network.
- **Input validation**: Max question length (`MAX_QUESTION_CHARS`, default 2000). Reject empty questions.
- **Session isolation**: Session IDs are UUIDs — acceptable for local use.

#### Sandbox Security (Revised Assessment)
- `RestrictedPython` is compile-time restriction only — a sufficiently motivated attacker can bypass it.
- For v1 (internal/trusted users): RestrictedPython + timeout is acceptable.
- Document clearly: **this service should NOT be exposed to untrusted users without additional sandboxing** (e.g., subprocess isolation with `resource.setrlimit`, or a dedicated execution microservice).
- Block list must explicitly include: `open`, `exec`, `eval`, `compile`, `__import__`, `importlib`, `os`, `sys`, `subprocess`, `socket`, `requests`, `urllib`.

### 8. Excel Data Type Gotchas

These are real-world data quality issues the agent tools must handle:

| Issue | Detail | Mitigation |
|---|---|---|
| **Dates stored as floats** | Excel stores dates as days-since-1900. Pandas usually handles this but timezone and 1900 leap year bugs exist. | Use `pd.to_datetime` with `unit="D"` origin; flag date columns in column metadata. |
| **Error cells** (`#DIV/0!`, `#REF!`, `#N/A`, `#VALUE!`) | openpyxl returns these as `None` or `CellErrorValue` objects. pandas turns them to `NaN`. | Inspector detects error cells in sample rows; metadata flags `has_error_cells: true` per sheet. |
| **Leading zeros lost** | ZIP codes, phone numbers, employee IDs stored as numbers lose leading zeros. | Inspector flags columns where dtype is numeric but values look like identifiers (all same length integers). Agent is warned. |
| **Mixed-type columns** | A mostly-numeric column with text annotations. Pandas infers `object` dtype. | Surfaced in column info as `dtype: object` with `mixed_types: true`; agent uses `execute_code` for careful handling. |
| **Boolean values** | Excel TRUE/FALSE vs Python True/False — usually fine but locale-specific Excel files may use localized strings. | Cast to bool explicitly in column info tool. |
| **Numeric precision** | Excel float vs. Python float rounding differences. | Document; use `decimal` module in sandbox if precision matters. |
| **Multi-row headers** | Some tables have a header spanning rows 1–2 (merged header groups). | Inspector detects this via openpyxl merged cells in the header region; surfaces as `header_rows: [1, 2]`. |

### 9. API Design & Long-Running Queries

The `/query` endpoint may take 30–90 seconds for complex questions (10+ tool calls, each requiring an LLM round-trip + file I/O). This creates two problems:

#### Async File I/O
- `pd.read_excel()` is **synchronous and blocking**. Running it in an `async` FastAPI route will block the event loop.
- **Fix**: Wrap all pandas/openpyxl I/O in `asyncio.to_thread()` to run in a thread pool.
- This applies to: file upload + inspection, DataFrame loading, sandbox execution.

#### Request Timeout Strategy
- Default nginx/Docker proxy timeout is 60s — not enough.
- **v1 strategy**: Increase proxy timeout to 120s; add `QUERY_TIMEOUT_SECS` env var (default 90). If the agent hasn't answered within this window, return a structured timeout response.
- **Future**: Async job pattern — `POST /query` returns a `job_id` immediately; client polls `GET /jobs/{job_id}`. Mention this in plan but don't build for v1.

### 10. Observability & Debugging

#### Observability & Debugging
- Use Python's `logging` module in structured format. `LOG_FORMAT` env var controls `text` (dev) vs `json` (production).
- Log at every: request received, session created, query invoked, tool call made, agent completed, error occurred.
- Include: `session_id`, `thread_id`, `tool_name`, `duration_ms` in log context.

#### Token Usage Tracking
- OpenAI responses include token usage. Capture and log this per query.
- Add `tokens_used` and `model` to the query response schema — useful for monitoring costs over time.

### 11. Agent Answer Quality

#### Citations / Grounding
- The agent must cite which data it used. Without this, it's impossible to verify answers.
- The query response schema should include a `sources` field: list of `{table_id, sheet, range, tool_used}` entries corresponding to tool calls that produced facts in the answer.
- System prompt must instruct: "Only state facts that you retrieved via tool calls. Do not infer data you have not fetched."

#### Clarifying Questions
- When the question is ambiguous (e.g., "total revenue" but there are 3 revenue columns across 2 sheets), the agent should ask rather than guess.
- LangGraph supports `interrupt()` for human-in-the-loop pauses. For v1: the agent returns a clarifying question as the `answer` field with `needs_clarification: true` in the response. The client re-submits with additional context on the same `thread_id`.

#### Column Name Collisions
- Two tables (same or different sheets) may share a column name (e.g., "Name", "ID", "Amount").
- Tools must reference columns by `table_id` + `column_name`, not just `column_name`.
- The system prompt must instruct the agent to always qualify column references with their table ID.

#### Answer Format
- For factual questions ("what is the total?") → plain text.
- For tabular questions ("show me the top 10 sales reps") → the agent should return a markdown table in the answer.
- System prompt should instruct the agent to use markdown tables for multi-row results.

### 12. Storage & Operations

#### Docker Volume Persistence
- Both `data/uploads/` (Excel files) and `data/spreadsheet_agent.db` (SQLite) must survive container restarts.
- These should be on the **same named Docker volume** (`spreadsheet-data`). The `UPLOADS_DIR` and `DB_PATH` env vars both point into this volume.
- Document: if the volume is deleted, all sessions and conversation history are lost.

#### Session Cleanup
- TTL-based cleanup must handle both the SQLite row AND the file on disk atomically.
- Implement as a FastAPI lifespan background task that runs once at startup and then every `CLEANUP_INTERVAL_HOURS` hours.
- Cleanup is soft-delete friendly: mark session as `deleted_at` in SQLite, then delete file, then hard-delete row.

#### File Deduplication (Nice-to-have, not v1)
- Multiple uploads of the same file (same SHA-256 hash) could share a single copy on disk.
- Not in scope for v1 but worth noting.

### 13. Dependency Versioning

LangGraph and the OpenAI SDK both have rapidly changing APIs. Pin **minor** versions (not just major):
- `langgraph>=0.2,<0.3`
- `openai>=1.30,<2.0`
- `fastapi>=0.111,<0.112`

Commit the `uv.lock` file so all developers and the Docker image use identical resolved dependencies.

---

## Architecture

### Directory Structure

```
spreadsheet-agent/
├── src/
│   ├── agent/
│   │   ├── utils/
│   │   │   ├── __init__.py
│   │   │   ├── state.py          # AgentState TypedDict
│   │   │   ├── nodes.py          # LangGraph node functions
│   │   │   └── tools.py          # 6 tool functions with @tool decorator
│   │   ├── __init__.py
│   │   ├── graph.py              # Graph construction + compile() singleton
│   │   └── prompts.py            # System prompt templates
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py               # FastAPI app, lifespan, CORS
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── sessions.py       # Session CRUD + file upload
│   │   │   └── query.py          # POST /sessions/{id}/query
│   │   └── schemas.py            # Pydantic v2 request/response models
│   └── core/
│       ├── __init__.py
│       ├── config.py             # pydantic-settings Settings
│       ├── session_store.py      # SQLite session metadata + file path helpers
│       ├── workbook_inspector.py # Build WorkbookMetadataMap at session creation
│       ├── dataframe_loader.py   # Lazy sheet loading + optional LRU cache
│       └── sandbox.py            # RestrictedPython execution environment
├── data/
│   └── uploads/                  # Excel files per session (gitignored)
├── tests/
│   ├── conftest.py               # Fixtures: sample Excel files, test client
│   ├── test_workbook_inspector.py
│   ├── test_tools.py
│   ├── test_sandbox.py
│   ├── test_sessions_api.py
│   └── test_query_api.py
├── Dockerfile
├── docker-compose.yml
├── langgraph.json
├── .env.example
└── pyproject.toml
```

### Agent Tools

| Tool | Description |
|---|---|
| `inspect_workbook` | Returns the cached WorkbookMetadataMap for the session. The agent calls this first if the map isn't in the system prompt yet. |
| `get_sheet_sample` | Fetches N rows from a named table or sheet (by range/table ID). Supports head/tail/slice and column subset. |
| `get_column_info` | Returns column statistics: dtype, null count, unique count, min/max/mean (numeric), top value_counts (categorical). |
| `search_cells` | Filters rows from a sheet/table where a column matches a condition (eq, gt, lt, contains, startswith). Returns matching rows up to max limit + total match count. |
| `execute_code` | Runs a sandboxed pandas code snippet. Exposes `sheets` dict with loaded DataFrames. Returns result as string, truncated if needed. |
| `load_sheet` | Explicitly loads a sheet into the runtime DataFrames dict and confirms its availability. Agent uses this before `execute_code` for cross-sheet operations. |

### Agent State

```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # Full conversation history
    session_id: str
    thread_id: str
    workbook_meta: dict          # WorkbookMetadataMap (from session store)
    loaded_sheet_names: list[str] # Which sheets are currently loaded in runtime
```

> Note: Actual DataFrames are NOT in state. They live in `config["configurable"]["dataframes"]` (injected per-request).

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/sessions` | Upload Excel file. Returns `session_id` + `workbook_meta`. |
| `GET` | `/sessions` | List all sessions (id, filename, created_at, size). |
| `GET` | `/sessions/{session_id}` | Get session details including `workbook_meta`. |
| `DELETE` | `/sessions/{session_id}` | Delete session, file, and all conversation threads. |
| `POST` | `/sessions/{session_id}/query` | Submit a question. Body: `{question, thread_id?}`. Returns `{answer, thread_id, sources}`. |
| `GET` | `/sessions/{session_id}/threads` | List conversation threads for a session. |

### System Prompt Design

The system prompt is dynamically constructed per invocation and includes:

1. Agent role and behavior instructions
2. The full `WorkbookMetadataMap` serialized as JSON/YAML so the agent knows what's available
3. Tool usage guidance (when to use which tool, how to reference table IDs)
4. Constraint reminders (never assume all data is loaded, always inspect first)

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Required |
| `OPENAI_MODEL` | `gpt-4o` | Model name |
| `MAX_FILE_SIZE_MB` | `50` | Upload size limit |
| `MAX_ROWS_PER_FETCH` | `100` | Rows returned per tool call |
| `MAX_CODE_OUTPUT_CHARS` | `4000` | Truncate sandbox output at this length |
| `MAX_QUESTION_CHARS` | `2000` | Max length of a submitted question |
| `CODE_EXECUTION_TIMEOUT_SECS` | `10` | Kill sandbox after this many seconds |
| `QUERY_TIMEOUT_SECS` | `90` | Overall agent query timeout |
| `MAX_AGENT_ITERATIONS` | `15` | Max ReAct loop iterations before forced stop |
| `SESSION_CACHE_SIZE` | `0` | LRU cache size for DataFrames (0 = disabled) |
| `SESSION_TTL_HOURS` | `24` | Sessions older than this are eligible for cleanup |
| `CLEANUP_INTERVAL_HOURS` | `6` | How often the cleanup background task runs |
| `DB_PATH` | `data/spreadsheet_agent.db` | SQLite database path |
| `UPLOADS_DIR` | `data/uploads` | Directory for uploaded Excel files |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FORMAT` | `text` | `text` (dev) or `json` (production) |

---

## Supported File Formats & Driver Strategy

Excel parsing uses a **two-phase, dual-driver approach**. The driver used depends on the phase and file format.

### Phase 1 — Structural Inspection (`workbook_inspector.py`)

Always uses **openpyxl** — it is the only library that exposes the metadata we need:
- Named tables (Excel ListObjects) with their cell ranges
- Hidden sheet state
- Merged cell maps (to warn the agent)
- Formula vs. cached value availability (`data_only=True`)
- Sheet dimensions without loading full data (`read_only=True`)

No other driver can substitute for openpyxl in this phase.

### Phase 2 — Data Loading (`dataframe_loader.py`)

Driver is selected per file format:

| Format | Primary Driver | Fallback | Notes |
|---|---|---|---|
| `.xlsx`, `.xlsm` | **calamine** (Rust, fast) | openpyxl | Calamine is 3–10x faster for raw data. Fall back to openpyxl if sheet has merged cells that need special handling. |
| `.xls` | **xlrd** | — | Only viable driver for legacy format since xlrd v2 dropped .xlsx support |
| `.xlsb` | **pyxlsb** | — | Excel Binary Workbook; rare but real-world files exist |
| `.ods` | **odf** | — | Out of scope for v1 but driver is available |

### Driver Trade-offs

| Concern | Detail |
|---|---|
| **Named tables** | Only openpyxl exposes them — calamine/xlrd/pyxlsb load raw cell ranges only. The inspector builds the table map using openpyxl; loaders use the map's `range` to slice the DataFrame correctly. |
| **Merged cells** | Calamine forward-fills merged values (same behavior as openpyxl default). The inspector flags sheets with merged cells in metadata so the agent is aware. |
| **Formula values** | Only openpyxl with `data_only=True` reads cached computed values. If a cell is a formula but was never saved after calc, openpyxl returns `None`. The inspector sets `formula_values_available: false` when this is detected. Calamine reads only stored cell values (no formula distinction). |
| **Memory** | openpyxl read-only mode (`read_only=True`) streams the file without loading the full workbook into memory — critical for inspection of large files. Calamine loads the sheet fully but much faster. |
| **Large file chunking** | Only openpyxl (via `skiprows` + `nrows` in pandas) supports row-range chunking. Calamine loads the whole sheet — use openpyxl for sheets where chunked access is needed. |

### Driver Selection Logic (pseudocode)

```python
def select_engine(filepath: Path, sheet_meta: SheetMeta) -> str:
    ext = filepath.suffix.lower()
    if ext == ".xls":
        return "xlrd"
    if ext == ".xlsb":
        return "pyxlsb"
    # .xlsx / .xlsm
    if sheet_meta.has_merged_cells and requires_chunked_load:
        return "openpyxl"
    return "calamine"  # default fast path for .xlsx/.xlsm
```

---

## Implementation Phases

### Phase 1: Project Scaffolding
- `uv init` with `pyproject.toml`
- All dependencies declared
- Full directory structure + `__init__.py` files
- `.env.example`
- `ruff` configured in `pyproject.toml`
- `pytest` + `pytest-asyncio` configured

### Phase 2: Core Infrastructure
- `core/config.py` — `Settings` class via `pydantic-settings`
- `core/session_store.py` — SQLite-backed session metadata (CRUD + file path resolution)
- `core/workbook_inspector.py` — Build `WorkbookMetadataMap`: detect sheets, named tables, heuristic tables, column names/dtypes, hidden sheets, formula availability
- `core/dataframe_loader.py` — Lazy sheet loader with optional LRU cache; returns `dict[str, DataFrame]`
- `core/sandbox.py` — `RestrictedPython` sandbox with timeout, `sheets` dict injection, output capture and truncation

### Phase 3: Agent Tools
- All 6 tools in `agent/utils/tools.py` using `@tool` decorator (LangChain tool format for LangGraph compatibility)
- Each tool is an `async def` that reads DataFrames from `config["configurable"]["dataframes"]`
- Structured return types with error handling — errors returned as tool result strings, not raised

### Phase 4: LangGraph Graph
- `agent/utils/state.py` — `AgentState` TypedDict
- `agent/prompts.py` — System prompt builder (injects workbook meta)
- `agent/utils/nodes.py` — `call_model` node (binds tools to LLM), `call_tools` node (tool executor)
- `agent/graph.py` — Standard ReAct loop: `call_model → call_tools → call_model → ...` until no tool calls; compiled with `SqliteSaver` checkpointer
- `langgraph.json`

### Phase 5: FastAPI Layer
- `api/schemas.py` — all Pydantic v2 models
- `api/routers/sessions.py` — upload (validate file, scan workbook, create session), list, get, delete
- `api/routers/query.py` — load DataFrames, build runtime config, invoke graph, return answer
- `api/main.py` — app factory, lifespan (init SQLite), CORS, router registration

### Phase 6: Docker
- `Dockerfile` — multi-stage (builder + runtime), non-root user, `data/` as volume mount point
- `docker-compose.yml` — service + named volume for `data/` + `env_file: .env`

### Phase 7: Tests
- `conftest.py` — programmatically generated Excel fixtures (single sheet, multi-sheet, multi-table, merged cells, named tables)
- `test_workbook_inspector.py` — table detection logic
- `test_tools.py` — each tool against fixture workbooks
- `test_sandbox.py` — valid code execution, blocked imports, timeout enforcement
- `test_sessions_api.py` — upload, get, delete, list
- `test_query_api.py` — single-turn, multi-turn, cross-sheet question, new vs. continued thread

---

## Key Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Multi-table detection false positives | Use Excel named tables first; fall back to heuristics; surface both with confidence labels |
| DataFrames not matching formula-computed values | Use `data_only=True`; surface `formula_values_available: false` when detected; document limitation |
| Sandbox bypass via RestrictedPython | Explicit block list; timeout enforced; acceptable risk for local personal tool |
| Very large files exceeding memory | Chunked loading; max file size; sheet-level lazy loading; openpyxl read-only mode for inspection |
| Agent ReAct loop not terminating | `MAX_AGENT_ITERATIONS` limit; structured timeout response |
| Concurrent queries on same session | SQLite WAL mode; LangGraph `SqliteSaver` serializes writes |
| Context window overflow from tool results | Per-tool output truncation; message trimming; workbook meta in system prompt not messages |
| Stale session files consuming disk | TTL background cleanup task; explicit DELETE endpoint |
| Agent hallucinating data it never fetched | System prompt grounding rules; `sources` field in response requiring citation of tool calls used |
| Column name collisions across tables | Tools accept `table_id` + `column_name`; system prompt enforces qualified references |
| Blocking event loop with pandas I/O | All file I/O wrapped in `asyncio.to_thread()` |
| Long-running queries exceeding proxy timeout | `QUERY_TIMEOUT_SECS`; document Docker proxy timeout config; async job pattern as future work |
| ZIP bomb / malicious XLSX | File size limit on raw upload + decompressed size check via openpyxl dimension scan |
| Path traversal via uploaded filenames | Filenames are sanitized; upload paths are UUID-only; display names stored separately |
| No user authentication | Not required — local personal tool only |
| Date/float/error cell data type issues | Inspector flags problematic columns; column info tool surfaces dtype + anomalies |
| Container restart losing data | Both DB and uploads on same named Docker volume; documented clearly |
| Dependency API breakage (LangGraph, OpenAI) | Pin minor versions; commit `uv.lock` |
