# в”Ђв”Ђ Stage 1: Build dependencies в”Ђв”Ђ
FROM node:24-bookworm-slim AS product-builder
WORKDIR /product/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/scripts/ ./scripts/
COPY argon/ ../argon/
RUN npm run build:argon

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS builder

WORKDIR /app

# Install system dependencies for Qt and crypto
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==2.4.1

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install dependencies (no dev)
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main --no-root

# в”Ђв”Ђ Stage 2: Runtime в”Ђв”Ђ
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

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
COPY --from=product-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=product-builder /product/presidium/features/argon-runtime.mjs ./presidium/features/argon-runtime.mjs

# Create data directory
RUN useradd --uid 10001 --create-home presidium \
    && mkdir -p /data/presidium/product \
    && chown -R presidium:presidium /data/presidium
USER presidium

# Environment
ENV PRESIDIUM_DATA_DIR=/data/presidium
ENV PRESIDIUM_PRODUCT_DATA_DIR=/data/presidium/product
ENV PYTHONUNBUFFERED=1

# Expose P2P port
EXPOSE 8000 9478/udp 9478/tcp

# Compose supplies a readiness check for the API service.
# CMD lets the API service override the desktop default.
CMD ["python", "-m", "presidium.main"]
