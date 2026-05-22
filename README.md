# Spreadsheet Agent

AI agent for Q&A over large Excel spreadsheets, built with LangGraph and FastAPI.

## Setup

```bash
cp .env.example .env
# Edit .env and set OPENAI_API_KEY
uv sync
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
