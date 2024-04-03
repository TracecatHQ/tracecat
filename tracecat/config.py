import os

HTTP_MAX_RETRIES = 10
LLM_MAX_RETRIES = 3

TRACECAT__SCHEDULE_INTERVAL_SECONDS = os.environ.get(
    "TRACECAT__SCHEDULE_INTERVAL_SECONDS", 60
)
TRACECAT__SCHEDULE_MAX_CONNECTIONS = 6
TRACECAT__APP_ENV = os.environ.get("TRACECAT__APP_ENV", "dev")
TRACECAT__API_URL = os.environ.get("TRACECAT__API_URL", "http://api:8000")
TRACECAT__RUNNER_URL = os.environ.get("TRACECAT__RUNNER_URL", "http://runner:8000")

TRACECAT__SERVICE_ROLES_WHITELIST = [
    "tracecat-runner",
    "tracecat-api",
    "tracecat-scheduler",
]
