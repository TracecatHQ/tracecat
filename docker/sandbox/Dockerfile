# Sandbox rootfs image for nsjail Python execution
# This image provides a minimal Python 3.12 + uv environment for sandboxed script execution

FROM python:3.12-slim-bookworm

ARG TARGETARCH
ARG DUCKDB_VERSION=1.4.3

# Install essential packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

RUN arch="${TARGETARCH:-$(dpkg --print-architecture)}" && \
    case "${arch}" in \
        amd64) duckdb_sha256="c479794045d094058d3092e404e696508d6310b5d234a8c1945b745678f09d8d" ;; \
        arm64) duckdb_sha256="c709eb3efc74a609af4b92bc885c509a1bd21ddfa71ea1e717420d4dd9fc121b" ;; \
        *) echo "Unsupported DuckDB CLI architecture: ${arch}" >&2; exit 1 ;; \
    esac && \
    curl -fsSL "https://github.com/duckdb/duckdb/releases/download/v${DUCKDB_VERSION}/duckdb_cli-linux-${arch}.gz" -o /tmp/duckdb.gz && \
    echo "${duckdb_sha256}  /tmp/duckdb.gz" | sha256sum -c - && \
    gunzip /tmp/duckdb.gz && \
    install -m 0755 /tmp/duckdb /usr/local/bin/duckdb.real && \
    rm -f /tmp/duckdb && \
    mkdir -p /usr/local/lib/duckdb/extensions /usr/local/share/duckdb && \
    /usr/local/bin/duckdb.real -c "SET extension_directory = '/usr/local/lib/duckdb/extensions'; INSTALL json; INSTALL postgres; INSTALL httpfs; INSTALL sqlite; INSTALL inet;" && \
    printf '%s\n' \
        "SET extension_directory = '/usr/local/lib/duckdb/extensions';" \
        "LOAD json;" \
        "LOAD postgres;" \
        "LOAD httpfs;" \
        "LOAD sqlite;" \
        "LOAD inet;" \
        > /usr/local/share/duckdb/tracecat-init.sql && \
    printf '%s\n' \
        '#!/bin/sh' \
        'exec /usr/local/bin/duckdb.real -init /usr/local/share/duckdb/tracecat-init.sql "$@"' \
        > /usr/local/bin/duckdb && \
    chmod 0755 /usr/local/bin/duckdb && \
    jq --version && \
    duckdb --version && \
    test "$(duckdb -csv -noheader -c "SELECT count(*) FROM duckdb_extensions() WHERE extension_name IN ('json', 'postgres_scanner', 'httpfs', 'sqlite_scanner', 'inet') AND installed AND loaded;")" = "5"

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
