# syntax=docker/dockerfile:1

# ── Builder stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Bring uv binary and installed virtualenv from builder
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
COPY --from=builder /app/uv.lock /app/uv.lock

# Tell uv not to re-sync on every `uv run` — the venv was built at image build time
ENV UV_NO_SYNC=1

# Expose the application source so `src.api.main` resolves and internal
# imports (from agent / api / core) find their modules
ENV PYTHONPATH=/app/src

# Copy application source
COPY src/ ./src/

# Create non-root user, set up data directory, and fix ownership
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p data/uploads \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
