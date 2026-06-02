# ── Build stage: install deps in a throwaway layer ──
FROM python:3.14-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage: slim final image ──
FROM python:3.14-slim

WORKDIR /app

# Only runtime libs, no compiler
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Non-root user for security
RUN useradd -r -s /bin/false appuser
USER appuser

COPY --chown=appuser:appuser . .

RUN chmod +x entrypoint.sh

EXPOSE 8000

# Runs alembic upgrade head before starting gunicorn so every deploy
# automatically applies pending migrations — no manual exec needed.
CMD ["./entrypoint.sh"]
