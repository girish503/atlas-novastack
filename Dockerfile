# ATLAS Enterprise Search & Answering Service
# Base Image: Python 3.11 slim (official, stable Debian-based OCI image)
FROM python:3.11-slim

# Set environment variables for clean, non-buffering execution
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    HOST=0.0.0.0

# Set container working directory
WORKDIR /app

# Install minimal system dependencies required for runtime/health checks
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Create dedicated non-root user and group for container security hardening
RUN groupadd -r atlas && useradd -r -g atlas -d /app -s /sbin/nologin atlas

# Copy package metadata first for dependency layer caching
COPY pyproject.toml README.md ./

# Copy source code and runtime data assets
COPY src/ ./src/
COPY data/ ./data/

# Install atlas-novastack package with all dependencies
RUN pip install --no-cache-dir .

# Change ownership of the application directory to the non-root atlas user
RUN chown -R atlas:atlas /app

# Switch to non-root user
USER atlas

# Expose HTTP service port
EXPOSE 8000

# Container healthcheck using liveness probe (/healthz)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Production ASGI server entrypoint
CMD ["uvicorn", "novastack.service.api:app", "--host", "0.0.0.0", "--port", "8000"]
