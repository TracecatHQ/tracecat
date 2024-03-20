import os
from pathlib import Path

HTTP_MAX_RETRIES = 6
LLM_MAX_RETRIES = 3

TRACECAT__OAUTH2_GMAIL_PATH = (
    Path("~/tracecat-runner-client-secret.json").expanduser().resolve()
)
TRACECAT__API_URL = os.environ.get("TRACECAT__API_URL", "http://api:8000")
TRACECAT__RUNNER_URL = os.environ.get("TRACECAT__RUNNER_URL", "http://runner:8000")

TRACECAT__SERVICE_ROLES_WHITELIST = ["tracecat-runner", "tracecat-api"]
