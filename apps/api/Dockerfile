FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN python -m venv /opt/venv && \
    addgroup --system app && adduser --system --ingroup app --home /app app

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN pip install --no-cache-dir .

USER app
EXPOSE 8000
CMD ["uvicorn", "rail_waitlist.main:app", "--host", "0.0.0.0", "--port", "8000"]
