# =============================================================================
# HD SKYE Agent v2 - Multi-stage Dockerfile
# Builds React frontend with Bun, serves with Python FastAPI
# =============================================================================

# Stage 1: Build frontend with Bun
FROM oven/bun:latest as builder
WORKDIR /usr/src/app

# Build-time arguments for VITE environment variables
ARG VITE_API_BASE_URL=
ARG VITE_SKYE_AGENT_NAME=HD SKYE

# Set as environment variables for Vite build
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_SKYE_AGENT_NAME=$VITE_SKYE_AGENT_NAME

# Copy frontend package files
COPY frontend/package.json frontend/bun.lock* ./

# Install dependencies
RUN bun install --frozen-lockfile || bun install

# Copy frontend source
COPY frontend/ .

# Build the frontend application
RUN bun run build

# Stage 2: Production image with Python
FROM python:3.11-slim as production
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt ./requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy built frontend from builder stage
COPY --from=builder /usr/src/app/dist ./dist

# Copy Python backend source code
COPY . .

# Copy BigQuery service account credentials
COPY hd-onedata-prod.json ./hd-onedata-prod.json

# Create a non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port 8000
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Set Python path to include app directory
ENV PYTHONPATH=/app

# Start the application using uvicorn
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
