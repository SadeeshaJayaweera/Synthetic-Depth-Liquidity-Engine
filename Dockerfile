# syntax=docker/dockerfile:1
FROM python:3.11-slim as base

# Python runtime configuration
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_CREATE=false \
    PATH="/opt/poetry/bin:$PATH"

WORKDIR /app

# Install system build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    g++ \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -

# Install project dependencies
COPY pyproject.toml poetry.lock* /app/
RUN poetry install --no-root --no-interaction --no-ansi

# Copy project source code
COPY . /app/
RUN poetry install --no-interaction --no-ansi

# Ensure data directory exists
RUN mkdir -p /app/data

# Default port exposure
EXPOSE 8000 8050

# Default command: FastAPI API
CMD ["poetry", "run", "uvicorn", "synthetic_depth.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
