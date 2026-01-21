FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Create non-root user
RUN groupadd --gid 1000 operator && \
    useradd --uid 1000 --gid operator --shell /bin/bash --create-home operator

WORKDIR /app

# Install dependencies
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy source code
COPY src/ src/

# Set ownership
RUN chown -R operator:operator /app

# Switch to non-root user
USER operator

ENTRYPOINT ["uv", "run", "python", "-m", "src"]
