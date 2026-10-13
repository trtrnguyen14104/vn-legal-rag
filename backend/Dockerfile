FROM python:3.11-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home app && mkdir -p /data && chown app:app /data
USER app
CMD ["uvicorn", "legal_rag.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM base AS embedding
ARG TORCH_EXTRA=cpu
ENV JAVA_HOME=/usr/lib/jvm/default-java
USER root
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra embedding --extra ${TORCH_EXTRA} && mkdir -p /models/vncorenlp && chown -R app:app /models
USER app
CMD ["uvicorn", "legal_rag.embedding.service:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]

