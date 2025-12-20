"""Configuration for tracecat-registry package."""

import os

# Maximum number of rows that can be returned from client-facing queries
MAX_ROWS_CLIENT_POSTGRES = int(
    os.environ.get("TRACECAT__MAX_ROWS_CLIENT_POSTGRES", 1000)
)
