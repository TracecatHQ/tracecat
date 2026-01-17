# Based on https://github.com/anthropics/claude-code/blob/main/.devcontainer/Dockerfile
FROM ghcr.io/anthropics/claude-code:latest

ARG AST_GREP_VERSION=0.31.1
ARG FD_VERSION=10.2.0

USER root

# Install fd-find
RUN ARCH=$(dpkg --print-architecture) && \
    wget "https://github.com/sharkdp/fd/releases/download/v${FD_VERSION}/fd_${FD_VERSION}_${ARCH}.deb" && \
    dpkg -i "fd_${FD_VERSION}_${ARCH}.deb" && \
    rm "fd_${FD_VERSION}_${ARCH}.deb"

# Install ast-grep
RUN ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64) ARCH_NAME="x86_64-unknown-linux-gnu" ;; \
        aarch64) ARCH_NAME="aarch64-unknown-linux-gnu" ;; \
        *) echo "Unsupported architecture: $ARCH" && exit 1 ;; \
    esac && \
    wget "https://github.com/ast-grep/ast-grep/releases/download/${AST_GREP_VERSION}/ast-grep-${ARCH_NAME}.zip" && \
    unzip "ast-grep-${ARCH_NAME}.zip" && \
    mv sg /usr/local/bin/ast-grep && \
    ln -s /usr/local/bin/ast-grep /usr/local/bin/sg && \
    rm "ast-grep-${ARCH_NAME}.zip"

USER node
