FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install ffmpeg for yt-dlp video/audio merging and curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies without installing the project yet
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source code
COPY src/ src/
COPY README.md ./

# Install the project into the virtual environment
RUN uv sync --frozen --no-dev && chmod -R 755 /app

# Ensure default mount directories exist
RUN mkdir -p /data /media

# Set runtime environment
ENV PATH="/app/.venv/bin:$PATH"
ENV VAULT_HOST=0.0.0.0
ENV VAULT_PORT=8000
ENV VAULT_DATA_DIR=/data
ENV MEDIA_DIR=/media
ENV DATABASE_URL="sqlite+aiosqlite:////data/vault.db"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "vault.main:app", "--host", "0.0.0.0", "--port", "8000"]
