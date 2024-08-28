#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Function to clean up .env file
cleanup() {
    if [ -f .env ]; then
        echo "Cleaning up .env file"
        rm .env
        rm .env.example
    fi
}

# Set up trap to ensure cleanup happens on exit
trap cleanup EXIT

# Download .env file from tracecat repo
curl -o env.sh https://raw.githubusercontent.com/TracecatHQ/tracecat/0.7.2/env.sh
curl -o .env.example https://raw.githubusercontent.com/TracecatHQ/tracecat/0.7.2/.env.example
chmod +x env.sh && ./env.sh

# Extract keys from .env file
DB_ENCRYPTION_KEY=$(grep TRACECAT__DB_ENCRYPTION_KEY .env | cut -d '=' -f2)
SERVICE_KEY=$(grep TRACECAT__SERVICE_KEY .env | cut -d '=' -f2)
SIGNING_SECRET=$(grep TRACECAT__SIGNING_SECRET .env | cut -d '=' -f2)

# Set default KEY_NAMES if not provided
DB_ENCRYPTION_KEY_NAME=${DB_ENCRYPTION_KEY_NAME:-"${APP_ENV:-production}/tracecat/db-encryption-key"}
SERVICE_KEY_NAME=${SERVICE_KEY_NAME:-"${APP_ENV:-production}/tracecat/service-key"}
SIGNING_SECRET_NAME=${SIGNING_SECRET_NAME:-"${APP_ENV:-production}/tracecat/signing-secret"}

# Check if AWS_DEFAULT_REGION is set
if [ -z "$AWS_DEFAULT_REGION" ]; then
    echo "Error: AWS_DEFAULT_REGION is not set."
    exit 1
fi

# Create AWS Secrets
aws secretsmanager create-secret --name "$DB_ENCRYPTION_KEY_NAME" --secret-string "$DB_ENCRYPTION_KEY" --region "$AWS_DEFAULT_REGION"
aws secretsmanager create-secret --name "$SERVICE_KEY_NAME" --secret-string "$SERVICE_KEY" --region "$AWS_DEFAULT_REGION"
aws secretsmanager create-secret --name "$SIGNING_SECRET_NAME" --secret-string "$SIGNING_SECRET" --region "$AWS_DEFAULT_REGION"

echo "AWS Secrets created successfully."
echo "DB_ENCRYPTION_KEY_NAME: $DB_ENCRYPTION_KEY_NAME"
echo "SERVICE_KEY_NAME: $SERVICE_KEY_NAME"
echo "SIGNING_SECRET_NAME: $SIGNING_SECRET_NAME"