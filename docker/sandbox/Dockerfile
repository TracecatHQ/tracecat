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
