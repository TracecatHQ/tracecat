FROM python:3.12-slim-bookworm

ARG LANCEDB_CONFIG_DIR=/var/lib/tracecat/lancedb
# Define the environment variables
ENV API_MODULE=tracecat.api.app:app
ENV HOST=0.0.0.0
ENV PORT=8000
ENV TRACECAT_DIR=/var/lib/tracecat
ENV LANCEDB_CONFIG_DIR=/var/lib/tracecat/lancedb

# Expose the application port
EXPOSE $PORT

# Install necessary packages, including acl
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

# Create the apiuser with a specific UID/GID,
# pre-create required directories, and set the correct permissions
RUN groupadd -g 1001 apiuser && \
    useradd -m -u 1001 -g apiuser apiuser && \
    mkdir -p $TRACECAT_DIR && \
    chown -R apiuser:apiuser $TRACECAT_DIR && \
    chmod -R 755 $TRACECAT_DIR && \
    setfacl -d -m u:apiuser:rwx $TRACECAT_DIR

# Set the working directory inside the container
WORKDIR /app

# Change to the non-root user
USER apiuser

# Copy the application files into the container and set ownership
COPY --chown=apiuser:apiuser ./tracecat /app/tracecat
COPY --chown=apiuser:apiuser ./pyproject.toml /app/pyproject.toml
COPY --chown=apiuser:apiuser ./requirements.txt /app/requirements.txt
COPY --chown=apiuser:apiuser ./README.md /app/README.md
COPY --chown=apiuser:apiuser ./LICENSE /app/LICENSE

# Install the Python dependencies
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Command to run the application
CMD ["sh", "-c", "python3 -m uvicorn $API_MODULE --host $HOST --port $PORT --reload"]
