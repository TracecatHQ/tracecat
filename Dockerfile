# ====================
# Stage 1: Build nsjail from source
# ====================
FROM debian:bookworm-slim AS nsjail-builder

ENV DEBIAN_FRONTEND=noninteractive
# Build from specific commit that includes pasta/user_net support
ENV NSJAIL_COMMIT=b24be32d38a26656568491c2c5fcffa6e77341d6

RUN apt-get update && apt-get install -y --no-install-recommends \
    git gcc g++ make pkg-config bison flex \
    libprotobuf-dev protobuf-compiler libnl-route-3-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/google/nsjail.git /tmp/nsjail && \
    cd /tmp/nsjail && git checkout "${NSJAIL_COMMIT}" && \
    git submodule update --init --recursive && \
    make -j"$(nproc)" && \
    install -m 0755 nsjail /usr/local/bin/nsjail && \
    rm -rf /tmp/nsjail

# ====================
# Stage 2: Create minimal sandbox rootfs
# ====================
FROM node:22.13.1-slim AS node-bin
FROM python:3.12-slim-bookworm AS sandbox-rootfs

ARG TARGETARCH
ARG DUCKDB_VERSION=1.4.3

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget jq iputils-ping && rm -rf /var/lib/apt/lists/*

# This rootfs is shared by run_python and agent sandboxes; CLI additions here
# are intentionally available to both. DuckDB is not available from Bookworm
# apt, so install the official release binary, verify it, preinstall extensions,
# and wrap the CLI to load those extensions on each invocation.
#
# Both the CLI binary and every extension are pinned by sha256: extensions are
# downloaded from the version-pinned repository, verified, and installed from
# local files (DuckDB still validates the signed extensions on load) instead of
# resolving "latest for this version" via a bare INSTALL.
RUN set -eu; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "${arch}" in \
        amd64) \
            platform="linux_amd64"; \
            duckdb_sha256="c479794045d094058d3092e404e696508d6310b5d234a8c1945b745678f09d8d"; \
            ext_shas="json:35144e27f6635f4934a715fc721bd26e74520852cf92d920c4e7a0c8729c3e8b httpfs:786b8e1aea9b49bb3072dba9f553d84c1a9aa042e9351124bb3ec2753b221227 inet:07430f6c1ad5a03e6fcbf153c2690765bfd445c9cab40927011105b75bef4a31 fts:ed5e639aee5070dee0b58f60322393c1861c5c82d57164192065ef758f1371b5"; \
            ;; \
        arm64) \
            platform="linux_arm64"; \
            duckdb_sha256="c709eb3efc74a609af4b92bc885c509a1bd21ddfa71ea1e717420d4dd9fc121b"; \
            ext_shas="json:0dbe43e05f23bd9c9e4df30a80a201b05cac5c7a0a1709143195451a526fe132 httpfs:662e96fe6c39724be7977649314b92eadf3a9a0c6127c3a3e96611480d6ad04b inet:c48932d9060053e570c76a99169709ad4e52f1ba5cb1a1468e8b1eb00899361a fts:0966c1c9cfb798845b53117ea7f3fd7210fb545d63b1273963075a4b56311a5a"; \
            ;; \
        *) echo "Unsupported DuckDB CLI architecture: ${arch}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/duckdb/duckdb/releases/download/v${DUCKDB_VERSION}/duckdb_cli-linux-${arch}.gz" -o /tmp/duckdb.gz; \
    echo "${duckdb_sha256}  /tmp/duckdb.gz" | sha256sum -c -; \
    gunzip /tmp/duckdb.gz; \
    install -m 0755 /tmp/duckdb /usr/local/bin/duckdb.real; \
    rm -f /tmp/duckdb; \
    mkdir -p /usr/local/lib/duckdb/extensions /usr/local/share/duckdb /tmp/ddbext; \
    install_args=""; \
    for spec in ${ext_shas}; do \
        name="${spec%%:*}"; sha="${spec##*:}"; \
        curl -fsSL "https://extensions.duckdb.org/v${DUCKDB_VERSION}/${platform}/${name}.duckdb_extension.gz" -o "/tmp/ddbext/${name}.duckdb_extension.gz"; \
        echo "${sha}  /tmp/ddbext/${name}.duckdb_extension.gz" | sha256sum -c -; \
        gunzip "/tmp/ddbext/${name}.duckdb_extension.gz"; \
        install_args="${install_args} INSTALL '/tmp/ddbext/${name}.duckdb_extension';"; \
    done; \
    /usr/local/bin/duckdb.real -c "SET extension_directory = '/usr/local/lib/duckdb/extensions';${install_args}"; \
    rm -rf /tmp/ddbext; \
    printf '%s\n' \
        "SET extension_directory = '/usr/local/lib/duckdb/extensions';" \
        "LOAD json;" \
        "LOAD httpfs;" \
        "LOAD inet;" \
        "LOAD fts;" \
        > /usr/local/share/duckdb/tracecat-init.sql; \
    printf '%s\n' \
        '#!/bin/sh' \
        'exec /usr/local/bin/duckdb.real -init /usr/local/share/duckdb/tracecat-init.sql "$@"' \
        > /usr/local/bin/duckdb; \
    chmod 0755 /usr/local/bin/duckdb; \
    jq --version; \
    duckdb --version; \
    test "$(duckdb -csv -noheader -c "SELECT count(*) FROM duckdb_extensions() WHERE extension_name IN ('json', 'httpfs', 'inet', 'fts') AND installed AND loaded;")" = "4"

COPY --from=ghcr.io/astral-sh/uv:0.9.15 /uv /usr/local/bin/uv
COPY --from=ghcr.io/astral-sh/uv:0.9.15 /uvx /usr/local/bin/uvx
COPY --from=node-bin /usr/local/bin/node /usr/local/bin/node
COPY --from=node-bin /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN useradd -m -u 1000 sandbox && \
    mkdir -p /workspace /work /cache /packages /home/sandbox && \
    chown sandbox:sandbox /workspace /work /cache /packages /home/sandbox

ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# ====================
# Stage 3: Shared base with runtime dependencies
# ====================
FROM ghcr.io/astral-sh/uv:0.9.15-python3.12-bookworm-slim AS base

ENV HOST=0.0.0.0 PORT=8000

# Copy nsjail binary
COPY --from=nsjail-builder /usr/local/bin/nsjail /usr/local/bin/nsjail

# Copy Node.js + npx for in-process MCP command servers (stdio)
COPY --from=node-bin /usr/local/bin/node /usr/local/bin/node
COPY --from=node-bin /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Install runtime packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    acl git openssh-client xmlsec1 libmagic1 curl ca-certificates jq \
    libnl-route-3-200 libprotobuf32 libcap2-bin util-linux \
    passt squashfs-tools \
    && apt-get -y upgrade \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# DuckDB (CLI binary + extensions) is stored as a single physical copy inside
# the sandbox rootfs (copied in below). The executor host — including the
# in-process DuckDB Python package used by core.duckdb.execute_sql — reaches it
# through symlinks created right after that copy, so the large extension
# binaries are not stored twice in the image. The Python package loads
# extensions from this directory instead of autoinstalling over the network.
ENV TRACECAT__DUCKDB_EXTENSION_DIRECTORY=/usr/local/lib/duckdb/extensions

# Allow the non-root executor process to invoke mount/umount when the container
# runtime grants the needed bounding capabilities. Without these file caps,
# privileged Docker containers still run apiuser with no effective capabilities.
RUN chmod u-s /usr/bin/mount /usr/bin/umount && \
    setcap cap_sys_admin,cap_dac_override+ep /usr/bin/mount && \
    setcap cap_sys_admin,cap_dac_override+ep /usr/bin/umount

# Copy sandbox rootfs
COPY --from=sandbox-rootfs /usr /var/lib/tracecat/sandbox-rootfs/usr

# Expose the single rootfs DuckDB copy to the executor host via symlinks, so the
# host CLI and the in-process DuckDB Python package work without a second copy
# of the extension binaries. The nsjail sandbox uses its own (physical) rootfs
# copy and is unaffected by these host-side links.
RUN ln -s /var/lib/tracecat/sandbox-rootfs/usr/local/lib/duckdb /usr/local/lib/duckdb && \
    ln -s /var/lib/tracecat/sandbox-rootfs/usr/local/share/duckdb /usr/local/share/duckdb && \
    ln -s /var/lib/tracecat/sandbox-rootfs/usr/local/bin/duckdb.real /usr/local/bin/duckdb.real && \
    ln -s /var/lib/tracecat/sandbox-rootfs/usr/local/bin/duckdb /usr/local/bin/duckdb && \
    jq --version && duckdb --version

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

# Create apiuser for non-root runtime (required for pasta userspace networking)
RUN groupadd -g 1001 apiuser && useradd -m -u 1001 -g apiuser apiuser && \
    mkdir -p /home/apiuser/.cache/uv /home/apiuser/.cache/s3 /home/apiuser/.cache/tmp /home/apiuser/.local/bin && \
    chown -R apiuser:apiuser /home/apiuser

# Create MCP socket directory for apiuser
RUN mkdir -p /var/run/tracecat && chown 1001:1001 /var/run/tracecat

WORKDIR /app

# ====================
# Stage 4: Development app
# ====================
FROM base AS development-app

ENV TMPDIR="/home/apiuser/.cache/tmp" TEMP="/home/apiuser/.cache/tmp" TMP="/home/apiuser/.cache/tmp"

# Set sandbox cache permissions for apiuser
RUN chown -R 1001:1001 /var/lib/tracecat/sandbox-cache && \
    chmod -R 755 /var/lib/tracecat/sandbox-cache

# Prime uv cache (as root, before switching user)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages,target=packages \
    uv sync --locked --no-install-project --no-dev --no-editable

COPY --chown=apiuser:apiuser . /app/

RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# Fix ownership of /app (uv sync creates .venv as root)
RUN chown -R apiuser:apiuser /app

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/home/apiuser/.local"

RUN mkdir -p /home/apiuser/.local/bin && ln -s $(which uv) /home/apiuser/.local/bin/uv

COPY docker/scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Switch to non-root user (matches production, required for pasta userspace networking)
USER apiuser

ENTRYPOINT ["/app/entrypoint.sh"]
EXPOSE $PORT
CMD ["sh", "-c", "python3 -m uvicorn tracecat.api.app:app --host $HOST --port $PORT --reload"]

# ====================
# Stage 5: Development registry manifest
# ====================
FROM development-app AS development-registry-manifest

RUN /app/.venv/bin/python -m tracecat.registry.sync.prebuild

# ====================
# Stage 6: Development target
# ====================
FROM development-app AS development

# Carry only the generated builtin registry metadata in dev images too, so local
# cluster startup can update the DB without rediscovering actions on the hot path.
COPY --from=development-registry-manifest --chown=apiuser:apiuser /app/.registry-artifacts /app/.registry-artifacts

# ====================
# Stage 7: Test target (development + pytest)
# ====================
FROM development AS test

# Install test dependencies
RUN --mount=type=cache,target=/home/apiuser/.cache/uv,uid=1001,gid=1001 uv sync --frozen --group dev

CMD ["python", "-m", "pytest"]

# ====================
# Stage 8: Production app
# ====================
FROM base AS production-app

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Set sandbox cache permissions for apiuser
RUN chown -R 1001:1001 /var/lib/tracecat/sandbox-cache && \
    chmod -R 755 /var/lib/tracecat/sandbox-cache

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

# ====================
# Stage 9: Production registry manifest
# ====================
FROM production-app AS registry-manifest

RUN /app/.venv/bin/python -m tracecat.registry.sync.prebuild

# ====================
# Stage 10: Production target
# ====================
FROM production-app AS production

# Carry only the generated builtin registry metadata in the image so platform
# registry startup can update the DB without rediscovering actions on the hot path.
COPY --from=registry-manifest --chown=apiuser:apiuser /app/.registry-artifacts /app/.registry-artifacts

# Verification
RUN nsjail --help > /dev/null 2>&1 && echo "nsjail available"

EXPOSE $PORT
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["sh", "-c", "python3 -m uvicorn tracecat.api.app:app --host $HOST --port $PORT"]
