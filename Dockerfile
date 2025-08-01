FROM ghcr.io/astral-sh/uv:0.8.4-python3.12-bookworm-slim

ENV UV_SYSTEM_PYTHON=1
ENV HOST=0.0.0.0
ENV PORT=8000
# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1
# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Install required apt packages (this now creates ALL cache directories)
COPY scripts/install-packages.sh .
RUN chmod +x install-packages.sh && \
    ./install-packages.sh && \
    rm install-packages.sh

COPY scripts/auto-update.sh ./auto-update.sh
RUN chmod +x auto-update.sh && \
    ./auto-update.sh && \
    rm auto-update.sh

# Create the apiuser with specific UID/GID
RUN groupadd -g 1001 apiuser && \
    useradd -m -u 1001 -g apiuser apiuser

# Ensure all required directories exist and copy pre-cached files
RUN mkdir -p /home/apiuser/.cache/uv /home/apiuser/.cache/deno /home/apiuser/.cache/s3 /home/apiuser/.cache/tmp && \
    mkdir -p /home/apiuser/.local/lib/node_modules && \
    cp -r /opt/deno-cache/* /home/apiuser/.cache/deno/ 2>/dev/null || true && \
    cp -r /opt/node_modules/* /home/apiuser/.local/lib/node_modules/ 2>/dev/null || true && \
    rm -rf /opt/deno-cache /opt/node_modules

# Set environment variables for Python and package managers
ENV PYTHONUSERBASE="/home/apiuser/.local"
ENV UV_CACHE_DIR="/home/apiuser/.cache/uv"
ENV PYTHONPATH=/home/apiuser/.local:$PYTHONPATH
ENV PATH=/home/apiuser/.local/bin:$PATH

# Set deno environment variables to use user-owned directories
ENV DENO_DIR="/home/apiuser/.cache/deno"
ENV NODE_MODULES_DIR="/home/apiuser/.local/lib/node_modules"

# Set temporary directory environment variables for apiuser
ENV TMPDIR="/home/apiuser/.cache/tmp"
ENV TEMP="/home/apiuser/.cache/tmp"
ENV TMP="/home/apiuser/.cache/tmp"

# Temp directory is now created above with other cache directories

# Set the working directory inside the container
WORKDIR /app

# Install the project's dependencies using the lockfile and settings
# WIHTOUT installing the project for better caching
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages,target=packages \
    uv sync --locked --no-install-project --no-dev --no-editable

# Then, add the rest of the project source code and install it
# Installing separately from its dependencies allows optimal layer caching
# Copy the application files into the container and set ownership
COPY --chown=apiuser:apiuser ./tracecat /app/tracecat
COPY --chown=apiuser:apiuser ./packages/tracecat-registry /app/packages/tracecat-registry
COPY --chown=apiuser:apiuser ./pyproject.toml /app/pyproject.toml
COPY --chown=apiuser:apiuser ./uv.lock /app/uv.lock
COPY --chown=apiuser:apiuser ./.python-version /app/.python-version
COPY --chown=apiuser:apiuser ./README.md /app/README.md
COPY --chown=apiuser:apiuser ./LICENSE /app/LICENSE
COPY --chown=apiuser:apiuser ./alembic.ini /app/alembic.ini
COPY --chown=apiuser:apiuser ./alembic /app/alembic

# Copy the entrypoint script
COPY --chown=apiuser:apiuser scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Install the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Ensure uv binary is available where Ray expects it
RUN mkdir -p /home/apiuser/.local/bin && \
    ln -s $(which uv) /home/apiuser/.local/bin/uv && \
    chown -R apiuser:apiuser /home/apiuser/.local/bin

# Fix ownership of all apiuser directories after root operations
# This ensures apiuser can access all necessary files and directories
RUN chown -R apiuser:apiuser /home/apiuser /app/.scripts

# Verify permissions are correctly set before switching users
RUN ls -la /home/apiuser/ && \
    ls -la /home/apiuser/.cache/ && \
    ls -ld /home/apiuser/.cache/uv && \
    echo "UV cache directory permissions: $(stat -c '%a %U:%G' /home/apiuser/.cache/uv)" && \
    echo "Permission verification complete"

# Change to the non-root user
USER apiuser

# Verify apiuser can access required directories and binaries
RUN deno --version && \
    rg --version && \
    python3 -c "import os; print(f'DENO_DIR accessible: {os.access(os.environ[\"DENO_DIR\"], os.R_OK | os.W_OK)}')" && \
    python3 -c "import os; print(f'UV_CACHE_DIR accessible: {os.access(os.environ[\"UV_CACHE_DIR\"], os.R_OK | os.W_OK)}')" && \
    python3 -c "import os, tempfile; f=tempfile.NamedTemporaryFile(dir=os.environ['UV_CACHE_DIR'], delete=True); print(f'UV_CACHE_DIR write test: SUCCESS - {f.name}')" && \
    python3 -c "import os; print(f'NODE_MODULES_DIR accessible: {os.access(os.environ[\"NODE_MODULES_DIR\"], os.R_OK | os.W_OK)}')" && \
    python3 -c "import os; print(f'/app/.scripts accessible: {os.access(\"/app/.scripts\", os.R_OK | os.W_OK)}')" && \
    python3 -c "import os; print(f'S3 cache accessible: {os.access(\"/home/apiuser/.cache/s3\", os.R_OK | os.W_OK)}')" && \
    python3 -c "import tempfile; print(f'Temp dir: {tempfile.gettempdir()}')" && \
    python3 -c "import os; print(f'Entrypoint executable: {os.access(\"/app/entrypoint.sh\", os.R_OK | os.X_OK)}')" && \
    ls -la /app/entrypoint.sh && \
    echo "User access verification complete"

EXPOSE $PORT

ENTRYPOINT ["/app/entrypoint.sh"]

CMD ["sh", "-c", "python3 -m uvicorn tracecat.api.app:app --host $HOST --port $PORT"]
