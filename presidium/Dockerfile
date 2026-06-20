# ── Stage 1: Build dependencies ──
FROM python:3.12-slim AS builder

WORKDIR /app

# Install system dependencies for Qt and crypto
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install dependencies (no dev)
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main

# ── Stage 2: Runtime ──
FROM python:3.12-slim

WORKDIR /app

# Install runtime system dependencies for PySide6
RUN apt-get update && apt-get install -y --no-install-recommends \
    libegl1 \
    libgl1 \
    libglib2.0-0 \
    libxkbcommon0 \
    libdbus-1-3 \
    libfontconfig1 \
    libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY presidium/ ./presidium/

# Create data directory
RUN mkdir -p /data/presidium

# Environment
ENV PRESIDIUM_DATA_DIR=/data/presidium
ENV PYTHONUNBUFFERED=1

# Expose P2P port
EXPOSE 9478/udp 9478/tcp

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import presidium; print('ok')" || exit 1

# Entry point
ENTRYPOINT ["python", "-m", "presidium.main"]
