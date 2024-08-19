FROM python:3.12-slim-bookworm

# Define the environment variables
ENV HOST=0.0.0.0
ENV PORT=8000

# Expose the application port
EXPOSE $PORT

# Install necessary packages
RUN apt-get update && \
    apt-get install -y acl && \
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

# Set the working directory inside the container
WORKDIR /app

# Copy the application files into the container and set ownership
COPY --chown=apiuser:apiuser ./tracecat /app/tracecat
COPY --chown=apiuser:apiuser ./pyproject.toml /app/pyproject.toml
COPY --chown=apiuser:apiuser ./README.md /app/README.md
COPY --chown=apiuser:apiuser ./LICENSE /app/LICENSE
COPY --chown=apiuser:apiuser ./alembic.ini /app/alembic.ini
COPY --chown=apiuser:apiuser ./alembic /app/alembic

# Copy the entrypoint script
COPY --chown=apiuser:apiuser scripts/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Change to the non-root user
USER apiuser

# Install package
RUN pip install --upgrade pip && pip install .

ENTRYPOINT ["/app/entrypoint.sh"]

# Command to run the application
CMD ["sh", "-c", "python3 -m uvicorn tracecat.api.app:app --host $HOST --port $PORT"]
