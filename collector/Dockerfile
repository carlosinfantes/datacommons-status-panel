FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim

RUN useradd --uid 1001 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build --chown=1001:1001 /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080

USER 1001
EXPOSE 8080

# Two workers with threads: every request fans out to ten probes internally.
CMD exec gunicorn --bind ":$PORT" --workers 2 --threads 8 --timeout 60 \
    --access-logfile - dc_status.app:application
