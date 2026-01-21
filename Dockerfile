# ====================
# Stage 1: Build nsjail from source
# ====================
FROM debian:bookworm-slim AS nsjail-builder

ENV DEBIAN_FRONTEND=noninteractive
ENV NSJAIL_VERSION=3.4

RUN apt-get update && apt-get install -y --no-install-recommends \
    git gcc g++ make pkg-config bison flex \
    libprotobuf-dev protobuf-compiler libnl-route-3-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --recurse-submodules --branch "${NSJAIL_VERSION}" \
    https://github.com/google/nsjail.git /tmp/nsjail && \
    cd /tmp/nsjail && make -j"$(nproc)" && \
    install -m 0755 nsjail /usr/local/bin/nsjail && \
    rm -rf /tmp/nsjail

# ====================
# Stage 2: Create minimal sandbox rootfs
# ====================
FROM python:3.12-slim-bookworm AS sandbox-rootfs

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.15 /uv /usr/local/bin/uv

# Install duckdb globally for sandbox Python tool usage
RUN uv pip install --system duckdb==1.4.3

RUN useradd -m -u 1000 sandbox && \
    mkdir -p /workspace /work /cache /packages /home/sandbox && \
    chown sandbox:sandbox /workspace /work /cache /packages /home/sndbox

ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# ====================
# Stage 3: Shared base with runtime dependencies
# ====================
FROM ghcr.io/astral-sh/uv:0.9.15-python3.12-bookworm-slim AS base

ENV HOST=0.0.0.0 PORT=8000

# Copy nsjail binary
COPY --from=nsjail-builder /usr/local/bin/nsjail /usr/local/bin/nsjail

# Install runtime packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    acl git openssh-client xmlsec1 libmagic1 curl ca-certificates \
    libnl-route-3-200 libprotobuf32 \
    && apt-get -y upgrade \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy sandbox rootfs
COPY --from=sandbox-rootfs /usr /var/lib/tracecat/sandbox-rootfs/usr
COPY --from=sandbox-rootfs /lib /var/lib/tracecat/sandbox-rootfs/lib
COPY --from=sandbox-rootfs /bin /var/lib/tracecat/sandbox-rootfs/bin
COPY --from=sandbox-rootfs /sbin /var/lib/tracecat/sandbox-rootfs/sbin
COPY --from=sandbox-rootfs /etc/passwd /var/lib/tracecat/sandbox-rootfs/etc/passwd
COPY --from=sandbox-rootfs /etc/group /var/lib/tracecat/sandbox-rootfs/etc/group
COPY --from=sandbox-rootfs /etc/ssl /var/lib/tracecat/sandbox-rootfs/etc/ssl
COPY --from=sandbox-rootfs /etc/ca-certificates /var/lib/tracecat/sandbox-rootfs/etc/ca-certificates
RUN install -m 0644 /dev/null /var/lib/tracecat/sandbox-rootfs/etc/resolv.conf && \
    install -m 0644 /dev/null /var/lib/tracecat/sandbox-rootfs/etc/hosts && \
    install -m 0644 /dev/null /var/lib/tracecat/sandbox-rootfs/etc/nsswitch.conf

# Handle lib64 for amd64
RUN if [ -d /var/lib/tracecat/sandbox-rootfs/lib64 ] || [ "$(uname -m)" = "x86_64" ]; then \
        mkdir -p /var/lib/tracecat/sandbox-rootfs/lib64 && \
        cp -a /lib64/. /var/lib/tracecat/sandbox-rootfs/lib64/ 2>/dev/null || true; \
    fi

# Create sandbox directories
RUN mkdir -p /var/lib/tracecat/sandbox-rootfs/tmp \
    /var/lib/tracecat/sandbox-rootfs/proc \
    /var/lib/tracecat/sandbox-rootfs/dev \
    /var/lib/tracecat/sandbox-rootfs/work \
    /var/lib/tracecat/sandbox-rootfs/cache \
    /var/lib/tracecat/sandbox-rootfs/packages \
    /var/lib/tracecat/sandbox-rootfs/home/sandbox \
    /var/lib/tracecat/sandbox-cache/packages \
    /var/lib/tracecat/sandbox-cache/uv-cache && \
    chmod -R 755 /var/lib/tracecat/sandbox-rootfs && \
    chown -R 1000:1000 /var/lib/tracecat/sandbox-rootfs/work \
        /var/lib/tracecat/sandbox-rootfs/cache \
        /var/lib/tracecat/sandbox-rootfs/packages \
        /var/lib/tracecat/sandbox-rootfs/home/sandbox && \
    chmod 1777 /var/lib/tracecat/sandbox-rootfs/tmp

WORKDIR /app

# ====================
# Stage 4: Development target
# ====================
FROM base AS development

ENV TMPDIR=/tmp TEMP=/tmp TMP=/tmp

# Prime uv cache
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages,target=packages \
    uv sync --locked --no-install-project --no-dev --no-editable

COPY . /app/

RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /root/.local/bin && ln -s $(which uv) /root/.local/bin/uv
RUN mkdir -p /app/.scripts

COPY docker/scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
EXPOSE $PORT
CMD ["sh", "-c", "python3 -m uvicorn tracecat.api.app:app --host $HOST --port $PORT --reload"]

# ====================
# Stage 5: Test target (development + pytest)
# ====================
FROM development AS test

# Install test dependencies
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --group dev

CMD ["python", "-m", "pytest"]

# ====================
# Stage 6: Production target
# ====================
FROM base AS production

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Create apiuser
RUN groupadd -g 1001 apiuser && useradd -m -u 1001 -g apiuser apiuser
RUN mkdir -p /home/apiuser/.cache/uv /home/apiuser/.cache/s3 /home/apiuser/.cache/tmp /home/apiuser/.local/bin && \
    chown -R apiuser:apiuser /home/apiuser

# Set sandbox cache permissions for apiuser
RUN chown -R 1001:1001 /var/lib/tracecat/sandbox-cache && \
    chmod -R 755 /var/lib/tracecat/sandbox-cache

COPY docker/scripts/auto-update.sh ./auto-update.sh
RUN chmod +x auto-update.sh && ./auto-update.sh && rm auto-update.sh

ENV PYTHONUSERBASE="/home/apiuser/.local"
ENV UV_CACHE_DIR="/home/apiuser/.cache/uv"
ENV PYTHONPATH="/home/apiuser/.local"
ENV PATH="/home/apiuser/.local/bin:/usr/local/bin:/usr/bin:/bin"
ENV TMPDIR="/home/apiuser/.cache/tmp" TEMP="/home/apiuser/.cache/tmp" TMP="/home/apiuser/.cache/tmp"

RUN mkdir -p /app/.scripts && chown -R apiuser:apiuser /app

# Switch to non-root user
USER apiuser

# Install dependencies as apiuser
RUN --mount=type=cache,target=/home/apiuser/.cache/uv,uid=1001,gid=1001 \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages,target=packages \
    uv sync --locked --no-install-project --no-dev --no-editable

COPY --chown=apiuser:apiuser ./tracecat /app/tracecat
COPY --chown=apiuser:apiuser ./packages /app/packages
COPY --chown=apiuser:apiuser ./pyproject.toml ./uv.lock ./.python-version ./README.md ./LICENSE ./alembic.ini /app/
COPY --chown=apiuser:apiuser ./alembic /app/alembic
COPY --chown=apiuser:apiuser docker/scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

RUN --mount=type=cache,target=/home/apiuser/.cache/uv,uid=1001,gid=1001 \
    uv sync --locked --no-dev --no-editable

ENV PATH="/app/.venv/bin:/home/apiuser/.local/bin:/usr/local/bin:/usr/bin:/bin"

RUN ln -sf $(which uv) /home/apiuser/.local/bin/uv

# Verification
RUN nsjail --help > /dev/null 2>&1 && echo "nsjail available"

EXPOSE $PORT
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["sh", "-c", "python3 -m uvicorn tracecat.api.app:app --host $HOST --port $PORT"]
