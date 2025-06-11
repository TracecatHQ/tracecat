FROM ghcr.io/astral-sh/uv:0.6.14-python3.12-bookworm-slim

ENV UV_SYSTEM_PYTHON=1
ENV HOST=0.0.0.0
ENV PORT=8000

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
    chown -R apiuser:apiuser /home/apiuser /app/.scripts && \
    chmod -R 755 /home/apiuser/.cache && \
    chmod -R 755 /home/apiuser/.local && \
    chmod -R 700 /app/.scripts && \
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

# Copy the application files into the container and set ownership
COPY --chown=apiuser:apiuser ./tracecat /app/tracecat
COPY --chown=apiuser:apiuser ./registry /app/registry
COPY --chown=apiuser:apiuser ./pyproject.toml /app/pyproject.toml
COPY --chown=apiuser:apiuser ./README.md /app/README.md
COPY --chown=apiuser:apiuser ./LICENSE /app/LICENSE
COPY --chown=apiuser:apiuser ./alembic.ini /app/alembic.ini
COPY --chown=apiuser:apiuser ./alembic /app/alembic

# Copy the entrypoint script
COPY --chown=apiuser:apiuser scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Install package and registry as root for better caching
RUN uv pip install .
RUN uv pip install ./registry

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
