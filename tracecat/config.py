import os
from pathlib import Path

MAX_RETRIES = 3

TRACECAT__OAUTH2_GMAIL_PATH = (
    Path("~/tracecat-runner-client-secret.json").expanduser().resolve()
)
TRACECAT__API_URL = os.environ.get("TRACECAT__API_URL", "http://api:8000")

TRACECAT__SERVICE_ROLES_WHITELIST = ["tracecat-runner"]
