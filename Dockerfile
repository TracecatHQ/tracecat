FROM ghcr.io/astral-sh/uv:0.4.20-python3.12-bookworm-slim

# Define the environment variables
ENV UV_SYSTEM_PYTHON=1
ENV HOST=0.0.0.0
ENV PORT=8000

# Expose the application port
EXPOSE $PORT

# Install necessary packages
RUN apt-get update && \
    apt-get install -y acl git xmlsec1 && \
    rm -rf /var/lib/apt/lists/*

# Copy and run the script to install additional packages
COPY scripts/install-packages.sh .
RUN chmod +x install-packages.sh && \
    ./install-packages.sh && \
    rm install-packages.sh

COPY scripts/auto-update.sh ./auto-update.sh
RUN chmod +x auto-update.sh && \
    ./auto-update.sh && \
    rm auto-update.sh

# Create the apiuser with a specific UID/GID
RUN groupadd -g 1001 apiuser && \
    useradd -m -u 1001 -g apiuser apiuser

# Set up directories for uv and pip with proper permissions
RUN mkdir -p /home/apiuser/.cache/uv /home/apiuser/.local/bin /home/apiuser/.local/lib/python3.12/site-packages && \
    chown -R apiuser:apiuser /home/apiuser/.cache /home/apiuser/.local && \
    chmod -R 755 /home/apiuser/.cache /home/apiuser/.local

# Ensure PYTHONUSERBASE and UV paths are set
ENV PYTHONUSERBASE="/home/apiuser/.local"
ENV UV_CACHE_DIR="/home/apiuser/.cache/uv"
ENV PATH="/home/apiuser/.local/bin:$PATH"
ENV PYTHONPATH="/home/apiuser/.local/lib/python3.12/site-packages:$PYTHONPATH"

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

# Install package and registry
RUN uv pip install .
RUN uv pip install ./registry

# Create symlink for uv binary to point to /home/apiuser/.local/bin/uv
RUN ln -s $(command -v uv) /home/apiuser/.local/bin/uv

# Ensure apiuser has write permissions to necessary directories
RUN chown -R apiuser:apiuser /tmp /home/apiuser

# Change to the non-root user
USER apiuser

ENTRYPOINT ["/app/entrypoint.sh"]

# Command to run the application
CMD ["sh", "-c", "python3 -m uvicorn tracecat.api.app:app --host $HOST --port $PORT"]
