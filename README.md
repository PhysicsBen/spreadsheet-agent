# Spreadsheet Agent

An AI-powered REST API that answers natural-language questions about Excel spreadsheets. Upload a workbook, ask questions, and get accurate, cited answers — the agent intelligently traverses large, complex workbooks (multi-sheet, multi-table, merged cells, named tables) so you don't have to.

Built with **LangGraph** (stateful ReAct agent), **FastAPI**, and **OpenAI**.

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
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
- [Deployment & Operations Runbook](#deployment--operations-runbook)
- [Security Considerations](#security-considerations)
- [Troubleshooting](#troubleshooting)
- [Supported File Formats](#supported-file-formats)
- [Project Structure](#project-structure)
- [License](#license)

---

## Features

- **Intelligent traversal** — the agent inspects workbook structure first, then fetches only the data it needs
- **Multi-sheet & multi-table** — handles workbooks with many sheets, discontiguous tables, named Excel tables, and merged cells
- **Code execution** — generates and runs sandboxed pandas code for computed answers (sums, filters, joins)
- **Multi-turn conversations** — maintains conversation history per thread; ask follow-up questions
- **Cited answers** — responses include source references so you can verify the data used
- **Session persistence** — upload once, query many times; sessions survive container restarts

---

## Quick Start

### Docker (recommended)

```bash
# 1. Clone and configure
git clone https://github.com/PhysicsBen/spreadsheet-agent.git
cd spreadsheet-agent
cp .env.example .env
# Open .env and set OPENAI_API_KEY

# 3. Install dependencies
uv sync

# 2. Start the service
docker compose up --build
```

The API is available at `http://localhost:8000`.

### Quick example

```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -F "file=@/path/to/your/spreadsheet.xlsx"
# Returns: {"session_id": "abc123...", "workbook_meta": {...}}
```

### Ask a question

```bash
curl -X POST http://localhost:8000/api/v1/sessions/<session_id>/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the total revenue for Q1?"}'
# Returns: {"answer": "...", "thread_id": "...", "sources": [...]}
```

### Continue the conversation

```bash
curl -X POST http://localhost:8000/api/v1/sessions/<session_id>/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Break that down by region", "thread_id": "<thread_id>"}'
```

### Health check

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/sessions` | Upload an Excel file. Returns `session_id` + `workbook_meta`. |
| `GET` | `/api/v1/sessions` | List all sessions (id, filename, created_at, size). |
| `GET` | `/api/v1/sessions/{session_id}` | Get session details including `workbook_meta`. |
| `DELETE` | `/api/v1/sessions/{session_id}` | Delete session, file, and all conversation threads. |
| `POST` | `/api/v1/sessions/{session_id}/query` | Submit a question. Body: `{"question": "...", "thread_id": "..."}`. |
| `GET` | `/api/v1/sessions/{session_id}/threads` | List conversation threads for a session. |
| `GET` | `/health` | Service health check. |

### Query Response Schema

```json
{
  "answer": "The total revenue for Q1 is $1,234,567.",
  "thread_id": "thread-uuid",
  "sources": [
    {"table_id": "Q1 Sales.SalesTable", "sheet": "Q1 Sales", "tool_used": "get_column_info"}
  ],
  "needs_clarification": false,
  "tokens_used": 1523,
  "model": "gpt-4o"
}
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    FastAPI Layer                      │
│  /sessions (upload, CRUD)    /query (invoke agent)   │
└──────────────┬──────────────────────┬───────────────┘
               │                      │
        ┌──────▼──────┐        ┌──────▼──────┐
        │   Session    │        │  LangGraph   │
        │   Store      │        │  Agent       │
        │  (SQLite)    │        │  (ReAct)     │
        └─────────────┘        └──────┬───────┘
                                      │
                          ┌───────────┼───────────┐
                          │           │           │
                    ┌─────▼───┐ ┌─────▼───┐ ┌────▼────┐
                    │Workbook  │ │DataFrame│ │Sandbox  │
                    │Inspector │ │ Loader  │ │(Restrict│
                    │(openpyxl)│ │(calamine)│ │edPython)│
                    └──────────┘ └─────────┘ └─────────┘
```

### Request Flow

1. **Upload** — file is validated, saved to disk, and structurally inspected to build a `WorkbookMetadataMap` (stored in SQLite)
2. **Query** — metadata is injected into the agent's system prompt; the agent uses tools to selectively load and analyze data
3. **Agent loop** — ReAct cycle: the LLM decides which tool to call → tool executes → result returned → LLM decides next step (or answers)
4. **Response** — final answer with citations is returned to the client

### Agent Tools

| Tool | Purpose |
|------|---------|
| `inspect_workbook` | Returns the cached workbook metadata map |
| `get_sheet_sample` | Fetches N rows from a table/sheet (head, tail, or slice) |
| `get_column_info` | Column statistics: dtype, nulls, unique count, min/max/mean |
| `search_cells` | Filter rows by column condition (eq, gt, lt, contains) |
| `execute_code` | Run sandboxed pandas code against loaded DataFrames |
| `load_sheet` | Explicitly load a sheet into the runtime DataFrames dict |

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

| Decision | Rationale |
|----------|-----------|
| **DataFrames NOT in agent state** | DataFrames aren't JSON-serializable and can't be checkpointed. They're injected per-request via `config["configurable"]["dataframes"]`. |
| **Session ≠ Thread** | A Session = the spreadsheet file. A Thread = one conversation. One session supports many independent threads. |
| **Two-phase driver strategy** | openpyxl for structural inspection (only library exposing named tables, merged cells, hidden sheets); calamine (Rust) for fast data loading. |
| **Lazy sheet loading** | Only sheets the agent requests are loaded — avoids memory bloat on large workbooks. |
| **Graph compiled once** | The LangGraph graph is a module-level singleton compiled at import time, not per-request. |
| **RestrictedPython sandbox** | Code execution uses compile-time restrictions + wall-clock timeout. Acceptable for trusted local use. |
| **Workbook metadata in system prompt** | The full structural map is injected so the agent can reason about what's available before fetching data. |
| **Output truncation** | Every tool enforces output size limits to protect the LLM context window. |

---

## Environment Variables

All configuration is via environment variables. See `.env.example` for a complete template.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | **Required.** Your OpenAI API key. |
| `OPENAI_MODEL` | `gpt-4o` | LLM model name |
| `MAX_FILE_SIZE_MB` | `50` | Maximum upload file size |
| `MAX_ROWS_PER_FETCH` | `100` | Rows returned per tool call |
| `MAX_CODE_OUTPUT_CHARS` | `4000` | Truncation limit for sandbox output |
| `MAX_QUESTION_CHARS` | `2000` | Maximum question length |
| `CODE_EXECUTION_TIMEOUT_SECS` | `10` | Sandbox execution timeout |
| `QUERY_TIMEOUT_SECS` | `90` | Overall agent query timeout |
| `MAX_AGENT_ITERATIONS` | `15` | Max ReAct loop iterations |
| `SESSION_CACHE_SIZE` | `0` | LRU DataFrame cache size (0 = disabled) |
| `SESSION_TTL_HOURS` | `24` | Session expiry for cleanup |
| `CLEANUP_INTERVAL_HOURS` | `6` | Background cleanup interval |
| `DB_PATH` | `data/spreadsheet_agent.db` | SQLite database path |
| `UPLOADS_DIR` | `data/uploads` | Uploaded file storage directory |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FORMAT` | `text` | `text` (dev) or `json` (production) |

---

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

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) package manager

### Setup

```bash
# Clone and configure
git clone https://github.com/PhysicsBen/spreadsheet-agent.git
cd spreadsheet-agent
cp .env.example .env
# Edit .env: set OPENAI_API_KEY and use relative storage paths:
#   DB_PATH=data/spreadsheet_agent.db
#   UPLOADS_DIR=data/uploads

# Install dependencies
uv sync --extra dev

# Start the dev server (auto-reload)
uv run fastapi dev src/api/main.py

# Run all tests
uv run pytest

# Run a specific test file
uv run pytest tests/test_workbook_inspector.py -v

# Run tests matching a keyword
uv run pytest -k "sandbox" -v
```

### Testing

Tests live in `tests/` mirroring the `src/` structure. Fixtures in `conftest.py` generate real Excel files programmatically (no binary fixtures committed to the repo).

```bash
# Run the full test suite
uv run pytest

# Run with verbose output
uv run pytest -v

# Run a specific test file
uv run pytest tests/test_sandbox.py
```

### Linting & Formatting

```bash
# Check for lint errors
uv run ruff check .

# Auto-format code
uv run ruff format .

# Check formatting without modifying
uv run ruff format . --check
```

---

## Deployment & Operations Runbook

### Docker Deployment

```bash
# Build and start
docker compose up --build -d

# View logs
docker compose logs -f spreadsheet-agent

# Stop
docker compose down

# Stop and remove data volume (WARNING: deletes all sessions)
docker compose down -v
```

### Data Persistence

Both the SQLite database and uploaded files are stored on a single named Docker volume (`spreadsheet-data`) mounted at `/app/data`. This ensures data survives container restarts.

> ⚠️ **If the volume is deleted, all sessions and conversation history are permanently lost.**

### Session Cleanup

Sessions older than `SESSION_TTL_HOURS` are automatically cleaned up by a background task that runs every `CLEANUP_INTERVAL_HOURS`. Cleanup removes the SQLite record and the associated file on disk.

To manually delete a session:

```bash
curl -X DELETE http://localhost:8000/api/v1/sessions/<session_id>
```

### Monitoring

- **Logs**: Structured logging is enabled. Set `LOG_FORMAT=json` for production log aggregators.
- **Log context**: Each log entry includes `session_id`, `thread_id`, `tool_name`, and `duration_ms` where applicable.
- **Token usage**: Query responses include `tokens_used` and `model` fields for cost tracking.
- **Health endpoint**: `GET /health` returns `{"status": "ok"}` for load balancer health checks.

### Scaling Considerations

- This is designed as a **single-instance local tool**, not a horizontally-scaled service.
- SQLite is the persistence layer — it does not support concurrent writes from multiple processes.
- For multi-user deployments, replace SQLite with PostgreSQL and add authentication.

---

## Security Considerations

This service is designed for **local, trusted use only**. It should NOT be exposed to the public internet without additional hardening.

| Concern | Mitigation |
|---------|-----------|
| **No authentication** | Acceptable for local personal tool; add auth before network exposure |
| **Sandbox (RestrictedPython)** | Compile-time restrictions + timeout; not escape-proof against motivated attackers |
| **File upload (XXE/ZIP bombs)** | Max file size enforced on upload; openpyxl dimension check limits decompressed size |
| **Path traversal** | Upload paths use UUID session IDs only; user-supplied filenames are sanitized |
| **Macros (.xlsm)** | Accepted but macros are never executed (openpyxl ignores VBA) |
| **Input validation** | Max question length enforced; empty questions rejected |

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `OPENAI_API_KEY not set` error on startup | Missing or empty `.env` | Copy `.env.example` to `.env` and set your key |
| Query timeout (no answer) | Complex question hitting `QUERY_TIMEOUT_SECS` | Increase `QUERY_TIMEOUT_SECS` or simplify the question |
| `formula_values_available: false` in metadata | Excel file was saved without computing formulas | Re-save the file in Excel (triggers formula recalc) before uploading |
| Agent says "I don't have access to that data" | Sheet not loaded | The agent should call `load_sheet` automatically; check logs for errors |
| Large file upload fails | Exceeds `MAX_FILE_SIZE_MB` | Increase the limit or split the workbook |
| Container data lost after restart | Volume not mounted | Ensure `docker compose up` (not `docker run`) is used with the compose file |
| `ModuleNotFoundError` in dev | Dependencies not installed | Run `uv sync --extra dev` |

---

## Supported File Formats

| Format | Extension | Inspection Driver | Data Loading Driver |
|--------|-----------|-------------------|---------------------|
| Excel Workbook | `.xlsx` | openpyxl | calamine (fast, Rust-based) |
| Excel Macro-Enabled | `.xlsm` | openpyxl | calamine |
| Excel 97-2003 | `.xls` | — | xlrd |
| Excel Binary | `.xlsb` | — | pyxlsb |

The two-phase approach uses **openpyxl** for structural inspection (named tables, merged cells, hidden sheets, formula detection) and **calamine** as the default fast data-loading engine for `.xlsx`/`.xlsm` files.

---

## Project Structure

```
spreadsheet-agent/
├── src/
│   ├── agent/                  # LangGraph agent
│   │   ├── utils/
│   │   │   ├── nodes.py        # Node functions (call_model, call_tools)
│   │   │   ├── state.py        # AgentState TypedDict
│   │   │   └── tools.py        # 6 tool functions with @tool decorator
│   │   ├── graph.py            # Graph construction + compile() singleton
│   │   └── prompts.py          # Dynamic system prompt builder
│   ├── api/                    # FastAPI application
│   │   ├── main.py             # App entry point, lifespan, CORS
│   │   ├── routers/
│   │   │   ├── sessions.py     # Session CRUD + file upload
│   │   │   └── query.py        # POST /sessions/{id}/query
│   │   └── schemas.py          # Pydantic v2 request/response models
│   └── core/                   # Shared infrastructure
│       ├── config.py           # Settings from environment variables
│       ├── session_store.py    # SQLite session metadata
│       ├── workbook_inspector.py # Workbook structural scanning
│       ├── dataframe_loader.py # Lazy sheet loading + LRU cache
│       └── sandbox.py          # RestrictedPython execution
├── tests/                      # pytest test suite
├── data/                       # Runtime data (gitignored)
├── Dockerfile                  # Multi-stage build
├── docker-compose.yml          # Service + named volume
├── langgraph.json              # LangGraph deployment config
├── implementation_plan.md      # Detailed design document
├── .env.example                # Environment variable template
└── pyproject.toml              # Dependencies and tool config (uv)
```

---

## License

This project is for personal/internal use. See repository for license details.
