# syntax=docker/dockerfile:1

# ── Builder stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ ./src/

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Create a non-root user
RUN useradd --create-home --shell /bin/bash agent

COPY --from=builder /app /app

RUN mkdir -p data/uploads && chown -R agent:agent data

USER agent

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
