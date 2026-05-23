# Spreadsheet Agent

AI agent for Q&A over large Excel spreadsheets, built with LangGraph and FastAPI.

## Quick Start (Docker)

```bash
# 1. Copy the example env file and set your OpenAI API key
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...

# 2. Start the service
docker compose up
```

The API will be available at `http://localhost:8000`.

### Upload a spreadsheet

```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -F "file=@/path/to/your/spreadsheet.xlsx"
# Returns a JSON object containing "session_id"
```

### Run a query

```bash
curl -X POST http://localhost:8000/api/v1/sessions/<session_id>/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the total revenue for Q1?"}'
```

### Check service health

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## Local Development

```bash
cp .env.example .env
# Edit .env: set OPENAI_API_KEY and change storage paths to relative paths
# (e.g. DB_PATH=data/spreadsheet_agent.db, UPLOADS_DIR=data/uploads)
uv sync --extra dev
uv run fastapi dev src/api/main.py
```

## Testing

```bash
uv run pytest
```

## Linting

```bash
uv run ruff check .
uv run ruff format .
```
