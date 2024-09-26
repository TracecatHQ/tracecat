#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Check if AWS_DEFAULT_REGION is set
if [ -z "$AWS_DEFAULT_REGION" ]; then
    echo "Error: AWS_DEFAULT_REGION is not set."
    exit 1
fi

# Create service key and signing secret
SERVICE_KEY=$(openssl rand -hex 32)
SIGNING_SECRET=$(openssl rand -hex 32)

# Create database encryption key
DB_ENCRYPTION_KEY=$(docker run --rm python:3.12-slim-bookworm /bin/bash -c "\
    pip install cryptography >/dev/null 2>&1; \
    python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")

# Set default KEY_NAMES if not provided
DB_ENCRYPTION_KEY_NAME=${DB_ENCRYPTION_KEY_NAME:-"${APP_ENV:-production}/tracecat/db-encryption-key"}
SERVICE_KEY_NAME=${SERVICE_KEY_NAME:-"${APP_ENV:-production}/tracecat/service-key"}
SIGNING_SECRET_NAME=${SIGNING_SECRET_NAME:-"${APP_ENV:-production}/tracecat/signing-secret"}

# Create AWS Secrets
aws secretsmanager create-secret --name "$DB_ENCRYPTION_KEY_NAME" --secret-string "$DB_ENCRYPTION_KEY" --region "$AWS_DEFAULT_REGION"
aws secretsmanager create-secret --name "$SERVICE_KEY_NAME" --secret-string "$SERVICE_KEY" --region "$AWS_DEFAULT_REGION"
aws secretsmanager create-secret --name "$SIGNING_SECRET_NAME" --secret-string "$SIGNING_SECRET" --region "$AWS_DEFAULT_REGION"

echo "AWS Secrets created successfully."
echo "DB_ENCRYPTION_KEY_NAME: $DB_ENCRYPTION_KEY_NAME"
echo "SERVICE_KEY_NAME: $SERVICE_KEY_NAME"
echo "SIGNING_SECRET_NAME: $SIGNING_SECRET_NAME"
