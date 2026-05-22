# Spreadsheet Agent — Project Guidelines

## Agent: Read This First

If you are an autonomous or delegated agent working on this project:

1. **Read `implementation_plan.md` in full before writing any code.** It contains the authoritative design decisions, phase breakdown, risk mitigations, driver strategy, data type gotchas, and API contracts. All implementation choices must align with it.
2. **Follow the Development Workflow section below** — feature branch, plan, implement, test, lint, docs, PR.
3. **Work one phase at a time.** Each phase builds on the previous. Check that prior phase artifacts exist before starting.
4. **When in doubt about a design decision, consult `implementation_plan.md` first.** Do not invent architecture that contradicts it.
5. **Never hardcode secrets.** All config comes from environment variables via `core/config.py`.

## Project Overview

This project is an AI agent that handles Q&A over large Excel spreadsheets. The agent intelligently decides how to traverse and iterate through spreadsheet data to answer questions accurately. It is exposed as a REST API via FastAPI and packaged for deployment in Docker.

## Tech Stack

- **Language**: Python 3.12+
- **Package manager**: `uv` (not pip, not poetry)
- **Agent framework**: LangGraph (stateful, graph-based agent)
- **LLM**: OpenAI API (via `openai` SDK)
- **API layer**: FastAPI with async handlers
- **Spreadsheet parsing**: Two-phase dual-driver strategy — **openpyxl** for structural inspection (named tables, merged cells, hidden sheets, formula detection); **calamine** (Rust, fast) as the default data-loading engine for `.xlsx`/`.xlsm`; **xlrd** for `.xls`; **pyxlsb** for `.xlsb`
- **Containerization**: Docker + Docker Compose

## Architecture

See `implementation_plan.md` for full design rationale, risk analysis, and phase breakdown.

```
spreadsheet-agent/
├── src/
│   ├── agent/                  # LangGraph agent package
│   │   ├── utils/
│   │   │   ├── __init__.py
│   │   │   ├── nodes.py        # Node functions (call_model, call_tools)
│   │   │   ├── state.py        # AgentState TypedDict definition
│   │   │   └── tools.py        # 6 tool functions with @tool decorator
│   │   ├── __init__.py
│   │   ├── graph.py            # Graph construction and compile() call
│   │   └── prompts.py          # Dynamic system prompt builder (injects workbook meta)
│   ├── api/                    # FastAPI app, routers, request/response schemas
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI app entry point
│   │   ├── routers/
│   │   │   ├── sessions.py     # Session CRUD + file upload
│   │   │   └── query.py        # POST /sessions/{id}/query
│   │   └── schemas.py          # Pydantic v2 request/response models
│   └── core/                   # Config, logging, shared utilities
│       ├── __init__.py
│       ├── config.py           # Settings loaded from env vars
│       ├── session_store.py    # SQLite session metadata + file path helpers
│       ├── workbook_inspector.py # Builds WorkbookMetadataMap at session creation
│       ├── dataframe_loader.py # Lazy sheet loading + optional LRU cache
│       └── sandbox.py          # RestrictedPython execution environment
├── tests/                      # Mirrors src/ structure
├── Dockerfile
├── docker-compose.yml
├── langgraph.json              # LangGraph configuration (graphs, env, deps)
└── pyproject.toml              # uv-managed, single source of truth for deps
```

Key design decisions:
- The LangGraph agent owns all spreadsheet traversal logic — the API is a thin wrapper
- **Session vs. Thread**: a Session = the spreadsheet file; a Thread = one conversation. One session can have many threads.
- **DataFrames are NOT in agent state** — they're injected per-request via `config["configurable"]["dataframes"]`
- Agent state holds: messages, session_id, thread_id, workbook_meta (the structural map), loaded_sheet_names
- The compiled graph is a module-level singleton in `graph.py` — compiled once at import time, not per request
- All secrets come from environment variables — never hardcoded
- `langgraph.json` points to the compiled graph and is required for LangSmith deployment
- See `implementation_plan.md` for full rationale on multi-table detection, sandbox design, and context window management

## Code Style

- Follow PEP 8; use `ruff` for linting and formatting (configured in `pyproject.toml`)
- Use type hints on all function signatures
- Prefer `async def` for FastAPI route handlers and any I/O-bound operations
- Use Pydantic v2 models for all API request/response schemas and agent state
- Keep functions small and single-purpose — agent nodes especially should do one thing

## LangGraph Conventions

- Define agent state as a `TypedDict` with `Annotated` fields using LangGraph reducers
- Each graph node is a plain `async def` function: `async def node_name(state: AgentState) -> dict`
- Nodes return only the state keys they modify (partial updates)
- Use `Command` objects for dynamic routing; use `add_conditional_edges` for static branching
- Name nodes with verbs: `analyze_question`, `inspect_sheet`, `fetch_rows`, `generate_answer`
- Compile the graph once at startup; do not recompile per request

## Development Workflow

Follow this workflow for every feature or fix:

### For `core/` modules and tool functions (deterministic layer) — use TDD:
1. **Create feature branch** (`git checkout -b feat/<name>`)
2. **Plan the change** — understand inputs, outputs, and edge cases before writing anything
3. **Write tests first** — define expected behavior via failing tests (`pytest`)
4. **Implement** until tests pass
5. **Update inline docs** — docstrings and comments alongside the implementation
6. **Run full test suite** — `uv run pytest`
7. **Fix anything surfaced**, re-run until clean
8. **Lint and format** — `uv run ruff check . && uv run ruff format .`
9. **Update higher-level docs** — `implementation_plan.md`, README, or API docs if affected
10. **Commit and open PR**

### For the agent graph, prompts, and API layer — tests after:
1. **Create feature branch**
2. **Plan the change**
3. **Implement**
4. **Write tests** — integration and behavioral tests after contracts are established
5. **Update inline docs** alongside implementation
6. **Run full test suite** — `uv run pytest`
7. **Fix anything surfaced**, re-run until clean
8. **Lint and format** — `uv run ruff check . && uv run ruff format .`
9. **Update higher-level docs** if affected
10. **Commit and open PR**

> **Why the split?** TDD pays off on deterministic logic (inspectors, loaders, sandbox, tools) where you can enumerate correct behavior upfront. Agent graph behavior is emergent — the LLM decides tool call order — so tests are better written against observed contracts after the shape is known.


# Install dependencies
uv sync

# Run dev server
uv run fastapi dev src/api/main.py

# Run tests
uv run pytest

# Lint / format
uv run ruff check .
uv run ruff format .

# Build Docker image
docker build -t spreadsheet-agent .

# Run with Docker Compose
docker compose up
```

## Environment Variables

All configuration via environment variables (use `python-dotenv` for local dev):

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENAI_MODEL` | Model name (default: `gpt-4o`) |
| `LOG_LEVEL` | Logging level (default: `INFO`) |
| `MAX_ROWS_PER_FETCH` | Limit rows returned per tool call (default: `100`) |

## Conventions

- Never commit `.env` files; provide `.env.example` with placeholder values
- All file uploads are handled in memory (use `UploadFile` + `BytesIO`) — no temp files on disk
- Log at `INFO` for request lifecycle, `DEBUG` for agent node transitions
- Tests live in `tests/` mirroring `src/` structure; use `pytest` with `pytest-asyncio`
- Use `httpx.AsyncClient` for API integration tests (not `requests`)
