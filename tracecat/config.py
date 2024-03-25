import os

HTTP_MAX_RETRIES = 6
LLM_MAX_RETRIES = 3

TRACECAT__APP_ENV = os.environ.get("TRACECAT__APP_ENV", "dev")
TRACECAT__API_URL = os.environ.get("TRACECAT__API_URL", "http://api:8000")
TRACECAT__RUNNER_URL = os.environ.get("TRACECAT__RUNNER_URL", "http://runner:8000")

TRACECAT__SERVICE_ROLES_WHITELIST = ["tracecat-runner", "tracecat-api"]
TRACECAT__SELF_HOSTED_DB_BACKEND = os.environ.get(
    "TRACECAT__SELF_HOSTED_DB_BACKEND", "postgres"
)
