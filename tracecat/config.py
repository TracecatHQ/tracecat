import os
from pathlib import Path

HTTP_MAX_RETRIES = 10
LLM_MAX_RETRIES = 3

TRACECAT_DIR = Path(os.environ["TRACECAT_DIR"]).expanduser().resolve()
TRACECAT__SCHEDULE_INTERVAL_SECONDS = os.environ.get(
    "TRACECAT__SCHEDULE_INTERVAL_SECONDS", 60
)
TRACECAT__SCHEDULE_MAX_CONNECTIONS = 6
TRACECAT__APP_ENV = os.environ.get("TRACECAT__APP_ENV", "dev")
TRACECAT__API_URL = os.environ.get("TRACECAT__API_URL", "http://api:8000")
TRACECAT__RUNNER_URL = os.environ.get("TRACECAT__RUNNER_URL", "http://runner:8000")

TRACECAT__TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"
TRACECAT__TRIAGE_DIR = TRACECAT_DIR / "triage"
TRACECAT__TRIAGE_DIR.mkdir(parents=True, exist_ok=True)

TRACECAT__SERVICE_ROLES_WHITELIST = [
    "tracecat-runner",
    "tracecat-api",
    "tracecat-scheduler",
]
