FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python:3.12-slim AS runtime

COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

RUN python -m venv /opt/venv && \
    addgroup --system app && adduser --system --ingroup app --home /app app

WORKDIR /app
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
# Resolve runtime packages from the same lockfile used by CI, without an editable
# source install or an implicit lock update during an image build.
RUN uv sync --locked --no-dev --no-editable --no-cache

USER app
EXPOSE 8000
CMD ["uvicorn", "rail_waitlist.main:app", "--host", "0.0.0.0", "--port", "8000"]
