# Sandbox rootfs image for nsjail Python execution
# This image provides a minimal Python 3.12 + uv environment for sandboxed script execution

FROM python:3.12-slim-bookworm

# Install essential packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast package management
COPY --from=ghcr.io/astral-sh/uv:0.9.15 /uv /usr/local/bin/uv

# Create sandbox user (UID 1000) - matches common container user mappings
RUN useradd -m -u 1000 sandbox

# Create required directories with proper ownership
# /workspace - for mounting user scripts and data
# /work - working directory for script execution
# /cache - for package cache during installation phase
# /packages - for mounted site-packages during execution phase
RUN mkdir -p /workspace /work /cache /packages && \
    chown sandbox:sandbox /workspace /work /cache /packages

USER sandbox
WORKDIR /work

# Set minimal environment for Python execution
ENV HOME=/tmp
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
