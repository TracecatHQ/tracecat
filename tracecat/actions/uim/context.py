"""Unified API for context / data extraction.

Supported Capabilities:
- `extract_emails`: extract emails from a list of strings.
- `extract_secrets`: optional flag (only_verified).
"""

from tracecat.actions.integrations import get_capability


def extract_emails(texts: list[str]) -> list[str]:
    extract = get_capability(category="extraction", capability="analyze_email")
    emails = extract(texts)
    return emails
