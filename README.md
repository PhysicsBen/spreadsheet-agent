# Spreadsheet Agent

An AI agent that answers natural language questions about Excel spreadsheets. It intelligently traverses large, complex workbooks — multi-sheet, multi-table, thousands of rows — rather than loading everything at once. Exposed as a REST API via FastAPI, backed by a LangGraph ReAct agent, and packaged for deployment in Docker.

---

## Table of Contents

- [Purpose](#purpose)
- [Architecture](#architecture)
- [Key Design Decisions](#key-design-decisions)
- [Agent Tools](#agent-tools)
- [API Reference](#api-reference)
- [Getting Started](#getting-started)
- [Local Chat UI](#local-chat-ui)
- [Running with Docker](#running-with-docker)
- [Configuration](#configuration)
- [Development Workflow](#development-workflow)
- [Testing](#testing)
- [Linting & Formatting](#linting--formatting)
- [Project Structure](#project-structure)
- [Limitations & Known Constraints](#limitations--known-constraints)

---

## Purpose

Excel files in the real world are messy: multiple sheets, discontiguous tables, merged cells, formulas, and thousands of rows. This service lets you upload an Excel file once and then ask natural language questions against it — "What was the total revenue in Q3 for the North region?" — and get grounded, cited answers.

The agent uses a ReAct loop to decide which sheets and tables to inspect, which columns to profile, and whether to run a code snippet to compute an answer. It never loads more data than it needs.

---

## Architecture

```
                   ┌──────────────────────────────────────────────┐
                   │                FastAPI Layer                  │
                   │  POST /sessions  ·  POST /sessions/{id}/query │
                   └───────────────┬──────────────────────────────┘
                                   │
                   ┌───────────────▼──────────────────────────────┐
                   │           LangGraph ReAct Agent               │
                   │  call_model ──► call_tools ──► call_model … │
                   └───────────────┬──────────────────────────────┘
                                   │ tools
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
   inspect_workbook          get_sheet_sample           execute_code
   get_column_info            search_cells               load_sheet
        │                          │                          │
        └──────────────────────────┼──────────────────────────┘
                                   │
                   ┌───────────────▼──────────────────────────────┐
                   │              Core Layer                       │
                   │  workbook_inspector · dataframe_loader        │
                   │  session_store (SQLite) · sandbox             │
                   └──────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility |
|---|---|
| `api/routers/sessions.py` | File upload, structural inspection at ingest time, session CRUD |
| `api/routers/query.py` | Lazy-load DataFrames, build runtime config, invoke LangGraph, return answer |
| `agent/graph.py` | Compiled ReAct graph (singleton); `SqliteSaver` checkpointer for conversation history |
| `agent/utils/tools.py` | 6 async tool functions the LLM can call |
| `agent/prompts.py` | Dynamic system prompt injecting the full `WorkbookMetadataMap` |
| `core/workbook_inspector.py` | openpyxl-based structural scan at session creation — named tables, merged cells, sheet dimensions, formula detection |
| `core/dataframe_loader.py` | Lazy per-request sheet loading; selects driver (calamine / xlrd / pyxlsb / openpyxl) by format |
| `core/session_store.py` | SQLite session metadata; file path resolution; TTL cleanup |
| `core/sandbox.py` | RestrictedPython execution environment with wall-clock timeout |

### Session vs. Thread Model

| Concept | What it represents | Stored in |
|---|---|---|
| **Session** | The uploaded Excel file + its structural metadata | SQLite `sessions` table + file on disk |
| **Thread** | A single conversation (Q&A history) against that file | LangGraph `SqliteSaver` checkpointer |

One session can have many independent threads. Pass `thread_id` in a query request to continue an existing conversation; omit it to start a new one.

---

## Key Design Decisions

### Two-phase dual-driver parsing

Workbook parsing is split into two phases with different drivers:

- **Phase 1 — Structural inspection** (always openpyxl): reads named tables (Excel ListObjects), merged cells, hidden sheet state, sheet dimensions, and formula availability. This scan happens once at upload time and the result is stored as JSON in SQLite.
- **Phase 2 — Data loading** (driver selected by format): calamine (Rust, fast) for `.xlsx`/`.xlsm`; xlrd for `.xls`; pyxlsb for `.xlsb`. Falls back to openpyxl when merged cells require special handling.

### DataFrames are not in agent state

LangGraph's checkpointer is JSON-based. DataFrames cannot be serialized there. Instead, sheets are loaded lazily on each `/query` request and injected into the agent via `config["configurable"]["dataframes"]`. Nothing is held in process memory between requests (a configurable LRU cache is available for hot sessions via `SESSION_CACHE_SIZE`).

### Workbook Metadata Map

At upload time a `WorkbookMetadataMap` is built and stored in SQLite. It is injected into the system prompt verbatim so the agent understands what is available before making any tool calls:

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
          "columns": ["Date", "Rep", "Region", "Product", "Units", "Revenue"]
        }
      ]
    }
  ],
  "formula_values_available": true
}
```

Table detection precedence: Excel named tables (ListObjects) → heuristic contiguous-region detection.

### Code execution sandbox

The agent can write and run pandas snippets to answer computational questions. The sandbox uses `RestrictedPython` to block dangerous builtins (`open`, `exec`, `__import__`, `os`, `sys`, `subprocess`, etc.) and enforces a wall-clock timeout via `concurrent.futures`. DataFrames are exposed as `sheets["Sheet Name"]`.

> **Warning**: RestrictedPython is a compile-time restriction. This service is designed for trusted, local use only. Do not expose it to untrusted users without additional OS-level sandboxing.

### Blocking I/O is off the event loop

`pd.read_excel()` and openpyxl are synchronous. All file I/O is wrapped in `asyncio.to_thread()` so the FastAPI event loop is never blocked.

### Context window protection

Each tool enforces its own output limits: `get_sheet_sample` caps at `MAX_ROWS_PER_FETCH` rows; `execute_code` truncates at `MAX_CODE_OUTPUT_CHARS`; `search_cells` returns a match count alongside truncated results. LangGraph `trim_messages` prunes long threads.

---

## Agent Tools

| Tool | When the agent uses it |
|---|---|
| `inspect_workbook` | Returns the cached `WorkbookMetadataMap`. Called first if the map isn't already in the prompt. |
| `get_sheet_sample` | Fetches N rows from a named table or sheet. Supports head / tail / slice and column subsets. |
| `get_column_info` | Returns dtype, null count, unique count, min/max/mean (numeric), top value_counts (categorical). |
| `search_cells` | Filters rows where a column matches a condition (`eq`, `gt`, `lt`, `contains`, `startswith`). Returns matches up to the row limit plus a total match count. |
| `load_sheet` | Explicitly loads a sheet into the runtime DataFrames dict. Use before `execute_code` for cross-sheet operations. |
| `execute_code` | Runs a sandboxed pandas snippet. Exposes `sheets` dict. Returns result as string, truncated if needed. |

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/sessions` | Upload an Excel file. Returns `session_id` and `workbook_meta`. |
| `GET` | `/sessions` | List all sessions (id, filename, created_at, size). |
| `GET` | `/sessions/{session_id}` | Get session details including `workbook_meta`. |
| `DELETE` | `/sessions/{session_id}` | Delete session, file, and all conversation threads. |
| `POST` | `/sessions/{session_id}/query` | Submit a question. Body: `{"question": "...", "thread_id": "optional"}`. Returns `{"answer": "...", "thread_id": "...", "sources": [...]}`. |
| `GET` | `/sessions/{session_id}/threads` | List conversation threads for a session. |

Interactive API docs are available at `http://localhost:8000/docs` when the server is running.

---

## Getting Started

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (package manager)
- An OpenAI API key

### Local setup

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd spreadsheet-agent

# 2. Copy the example env file and set your API key
cp .env.example .env
# Open .env and set OPENAI_API_KEY

# 3. Install dependencies
uv sync

# 4. Start the development server (auto-reloads on file changes)
uv run fastapi dev src/api/main.py
```

The API is now available at `http://localhost:8000`. Visit `http://localhost:8000/docs` for the interactive UI.

### Quick example

```bash
# Upload a file
curl -X POST http://localhost:8000/sessions \
  -F "file=@my_spreadsheet.xlsx"
# → {"session_id": "abc123", "workbook_meta": {...}}

# Ask a question
curl -X POST http://localhost:8000/sessions/abc123/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the total revenue by region?"}'
# → {"answer": "...", "thread_id": "t1", "sources": [...]}

# Follow-up on the same thread
curl -X POST http://localhost:8000/sessions/abc123/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Which region had the highest growth?", "thread_id": "t1"}'
```

---

## Local Chat UI

A Streamlit-based chat interface is included for testing the agent locally. It is a
dev/test tool only — it calls the FastAPI backend and requires it to be running.

### Setup

```bash
# Install dev dependencies (includes streamlit)
uv sync --extra dev
```

### Start the backend

```bash
# Option A — Docker Compose (recommended)
docker compose up

# Option B — dev server with auto-reload
uv run fastapi dev src/api/main.py
```

### Run the UI

```bash
streamlit run ui/app.py
```

The UI will open at `http://localhost:8501`. By default it connects to the API at
`http://localhost:8000`. To point it at a different address, set the `API_BASE_URL`
environment variable before starting:

```bash
API_BASE_URL=http://my-server:8000 streamlit run ui/app.py
```

### Features

- **Sidebar** — upload `.xlsx`, `.xlsm`, `.xls`, or `.xlsb` files; view workbook
  metadata (sheets, dimensions, detected tables); start a new conversation thread
  or switch to a different file.
- **Chat area** — ask natural-language questions about your spreadsheet; each
  assistant reply includes a collapsed *Sources & Usage* panel showing the tables
  consulted, tool calls made, tokens consumed, and model used.

---

## Running with Docker

### Docker Compose (recommended)

```bash
# Build and start
docker compose up --build

# Run in the background
docker compose up -d --build

# View logs
docker compose logs -f

# Stop
docker compose down
```

The app runs on port `8000`. Both the SQLite database and uploaded files are stored on the `spreadsheet-data` named volume and survive container restarts.

> **Data persistence**: If you delete the `spreadsheet-data` volume (`docker volume rm spreadsheet-agent_spreadsheet-data`), all sessions and conversation history are permanently lost.

### Manual Docker build

```bash
docker build -t spreadsheet-agent .
docker run -p 8000:8000 --env-file .env spreadsheet-agent
```

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` for local development.

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required.** OpenAI API key. |
| `OPENAI_MODEL` | `gpt-4o` | Model name. |
| `MAX_FILE_SIZE_MB` | `50` | Upload size limit in megabytes. |
| `MAX_ROWS_PER_FETCH` | `100` | Maximum rows returned per tool call. |
| `MAX_CODE_OUTPUT_CHARS` | `4000` | Sandbox output truncation limit. |
| `MAX_QUESTION_CHARS` | `2000` | Maximum length of a submitted question. |
| `CODE_EXECUTION_TIMEOUT_SECS` | `10` | Kill sandbox after this many seconds. |
| `QUERY_TIMEOUT_SECS` | `90` | Overall agent query timeout. |
| `MAX_AGENT_ITERATIONS` | `15` | Maximum ReAct loop iterations before a forced stop. |
| `SESSION_CACHE_SIZE` | `0` | LRU cache size for DataFrames (0 = disabled). |
| `SESSION_TTL_HOURS` | `24` | Sessions older than this are eligible for cleanup. |
| `CLEANUP_INTERVAL_HOURS` | `6` | How often the cleanup background task runs. |
| `DB_PATH` | `data/spreadsheet_agent.db` | SQLite database path. |
| `UPLOADS_DIR` | `data/uploads` | Directory for uploaded Excel files. |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `LOG_FORMAT` | `text` | `text` for development, `json` for production log aggregators. |

---

## Development Workflow

This project uses **TDD for deterministic core modules** and **tests-after for the agent and API layer** (since agent behavior is emergent from the LLM).

```bash
# Run the dev server with auto-reload
uv run fastapi dev src/api/main.py

# Run all tests
uv run pytest

# Run a specific test file
uv run pytest tests/test_workbook_inspector.py -v

# Run tests matching a keyword
uv run pytest -k "sandbox" -v
```

For every feature or fix:

1. Create a feature branch (`git checkout -b feat/<name>`)
2. For `core/` modules: write failing tests first, then implement
3. For agent/API: implement, then write integration tests
4. Run `uv run pytest` — all tests must pass
5. Run `uv run ruff check . && uv run ruff format .` — no lint errors
6. Open a PR

---

## Testing

Tests live in `tests/` mirroring the `src/` structure. Fixtures in `conftest.py` generate real Excel files programmatically (no binary fixtures committed to the repo).

```bash
# Run all tests
uv run pytest

# With verbose output
uv run pytest -v

# With coverage (requires pytest-cov)
uv run pytest --cov=src --cov-report=term-missing
```

| Test file | What it covers |
|---|---|
| `test_workbook_inspector.py` | Named table detection, heuristic table detection, hidden sheets, merged cells, formula detection |
| `test_dataframe_loader.py` | Driver selection per format, lazy loading, LRU cache |
| `test_tools.py` | Each agent tool against fixture workbooks |
| `test_sandbox.py` | Valid code execution, blocked imports, timeout enforcement |
| `test_session_store.py` | Session CRUD, file path resolution, TTL cleanup |
| `test_sessions_api.py` | Upload, get, delete, list endpoints |
| `test_query_api.py` | Single-turn, multi-turn, cross-sheet questions, thread continuation |
| `test_graph.py` | LangGraph graph compilation and node wiring |
| `test_config.py` | Settings loading from environment |

---

## Linting & Formatting

```bash
# Check for lint errors
uv run ruff check .

# Auto-fix lint errors
uv run ruff check . --fix

# Format code
uv run ruff format .

# Check formatting without modifying
uv run ruff format . --check
```

Ruff is configured in `pyproject.toml` with rules `E`, `F`, `I`, `UP`, `B`, `SIM` and a line length of 88.

---

## Project Structure

```
spreadsheet-agent/
├── src/
│   ├── agent/
│   │   ├── utils/
│   │   │   ├── state.py          # AgentState TypedDict
│   │   │   ├── nodes.py          # LangGraph node functions (call_model, call_tools)
│   │   │   └── tools.py          # 6 tool functions with @tool decorator
│   │   ├── graph.py              # Graph construction + compile() singleton
│   │   └── prompts.py            # Dynamic system prompt builder
│   ├── api/
│   │   ├── main.py               # FastAPI app, lifespan, CORS
│   │   ├── routers/
│   │   │   ├── sessions.py       # Session CRUD + file upload
│   │   │   └── query.py          # POST /sessions/{id}/query
│   │   └── schemas.py            # Pydantic v2 request/response models
│   └── core/
│       ├── config.py             # pydantic-settings Settings class
│       ├── session_store.py      # SQLite session metadata + file path helpers
│       ├── workbook_inspector.py # WorkbookMetadataMap builder (openpyxl)
│       ├── dataframe_loader.py   # Lazy sheet loading + optional LRU cache
│       └── sandbox.py            # RestrictedPython execution environment
├── tests/                        # Mirrors src/ structure
├── data/                         # SQLite DB + uploaded files (gitignored)
├── Dockerfile                    # Multi-stage build, non-root user
├── docker-compose.yml            # Service + named volume for data/
├── langgraph.json                # LangGraph configuration (for LangSmith deployment)
├── pyproject.toml                # uv-managed dependencies + ruff + pytest config
└── .env.example                  # Environment variable template
```

---

## Limitations & Known Constraints

- **Supported formats**: `.xlsx`, `.xlsm`, `.xls`, `.xlsb`. ODS is out of scope for v1.
- **Formula values**: Only available if the file was saved after calculation. If not, the inspector sets `formula_values_available: false` and the agent is informed.
- **External links**: Formulas referencing other files (e.g., `=[Other.xlsx]Sheet1!A1`) return `None` with `data_only=True`. Surfaced in metadata.
- **Macros**: `.xlsm` files are accepted but VBA macros are never executed (openpyxl ignores them).
- **Sandbox security**: RestrictedPython is compile-time only. This service is intended for trusted, local use. Do not expose it to the public internet without additional OS-level sandboxing.
- **Long-running queries**: Complex questions involving many tool calls may take 30–90 seconds. The default `QUERY_TIMEOUT_SECS` is 90. Nginx or reverse proxy timeouts should be configured accordingly.
- **No authentication**: Designed as a personal local tool. Session IDs are UUIDs — not a substitute for real access control.
- **Memory between requests**: DataFrames are not cached by default. Set `SESSION_CACHE_SIZE` to enable an in-process LRU cache for frequently queried sessions.
