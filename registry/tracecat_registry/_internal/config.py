import os

# === Actions Config === #
HTTP_MAX_RETRIES = 10
LLM_MAX_RETRIES = 3

# === Internal Services === #
TRACECAT__APP_ENV = os.environ.get("TRACECAT__APP_ENV", "development")

# SMTP Settings
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = os.environ.get("SMTP_PORT", 25)
SMTP_STARTTLS_ENABLED = os.environ.get("SMTP_STARTTLS_ENABLED", "0").lower() in (
    "1",
    "true",
)
SMTP_SSL_ENABLED = os.environ.get("SMTP_SSL_ENABLED", "0").lower() in ("1", "true")
SMTP_IGNORE_CERT_ERRORS = os.environ.get("SMTP_IGNORE_CERT_ERRORS", "0").lower() in (
    "1",
    "true",
)
SMTP_AUTH_ENABLED = os.environ.get("SMTP_AUTH_ENABLED", "0").lower() in ("1", "true")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")


# === Remote registry === #
# If you wish to use a remote registry, set the URL here
# If the url is unset, this will be set to None
TRACECAT__REMOTE_REGISTRY_URL = os.environ.get("TRACECAT__REMOTE_REGISTRY_URL") or None
