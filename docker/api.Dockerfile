# syntax=docker/dockerfile:1.7
# OpsPilot API image. Works on Apple Silicon (arm64) and amd64.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv

WORKDIR /app

# 1) Dependencies first, so this layer is cached until pyproject.toml changes.
# uv.lock* is optional: the glob means the build works before you've run `uv lock`.
COPY pyproject.toml README.md uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project

# 2) Then our own code.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

# 3) Bake the embedding/reranker models into the image so containers start
#    without downloading anything (important for Kubernetes and Cloud Run).
ARG PRELOAD_MODELS=true
ENV MODELS_CACHE_DIR=/app/.cache/fastembed
RUN if [ "$PRELOAD_MODELS" = "true" ]; then \
      python -c "from fastembed import TextEmbedding, SparseTextEmbedding; \
from fastembed.rerank.cross_encoder import TextCrossEncoder; \
c='/app/.cache/fastembed'; \
TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir=c); \
SparseTextEmbedding('Qdrant/bm25', cache_dir=c); \
TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2', cache_dir=c)"; \
    fi

COPY data ./data
COPY scripts ./scripts

# Don't run as root.
RUN useradd --create-home --uid 1000 opspilot \
    && mkdir -p /app/.cache \
    && chown -R opspilot:opspilot /app/.cache
USER opspilot

ENV KNOWLEDGE_BASE_DIR=/app/data/knowledge_base
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"

# Cloud Run injects $PORT; everywhere else it defaults to 8000.
CMD ["sh", "-c", "uvicorn opspilot.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
