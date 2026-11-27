"""Redact secrets that some public routes carry in their URL path.

Incoming webhooks, SNS ingest endpoints, agent channel endpoints, channel
links and invitation links authenticate the caller with a secret path segment. Anything that logs a request path or a
request line passes it through ``redact_url_secrets`` first. A new route with a
secret in its path adds one pattern to ``_URL_SECRET_PATTERNS``.
"""

from __future__ import annotations

import logging
import re

REDACTED_URL_SECRET = "[redacted]"

# One path segment: stops at the next slash, query, fragment, quote or space,
# so the patterns also work inside an access-log request line.
_SEGMENT = r"[^/?#\s\"']+"

# Group 1 is the part kept; the segment after it is the secret.
_URL_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # /webhooks/{workflow_id}/{secret}
    re.compile(rf"(/webhooks/{_SEGMENT}/){_SEGMENT}"),
    # /ingest/sns/{endpoint_token}
    re.compile(rf"(/ingest/sns/){_SEGMENT}"),
    # /invitations/token/{token}
    re.compile(rf"(/invitations/token/){_SEGMENT}"),
    # /agent/channels/{channel_type}/{token}, but not /slack/oauth/callback
    re.compile(rf"(/agent/channels/{_SEGMENT}/)(?!oauth/callback\b){_SEGMENT}"),
    # /agent/channel-links/{token}/sign-in
    re.compile(rf"(/agent/channel-links/){_SEGMENT}(?=/sign-in\b)"),
)

UVICORN_LOGGER_NAMES = ("uvicorn.access", "uvicorn.error")


def redact_url_secrets(text: str) -> str:
    """Replace every secret path segment in ``text`` with ``[redacted]``.

    Args:
        text: A request path, a full URL, or a log line containing either.

    Returns:
        The text with each secret segment replaced; other text is unchanged.
    """
    for pattern in _URL_SECRET_PATTERNS:
        text = pattern.sub(rf"\g<1>{REDACTED_URL_SECRET}", text)
    return text


class URLSecretRedactionFilter(logging.Filter):
    """Redact URL secrets from stdlib log records, such as uvicorn's access log.

    uvicorn formats its access line from ``record.args`` (client, method, path
    with query, HTTP version, status), so the arguments and the message are
    both rewritten before any handler formats the record.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_url_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_url_secrets(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


def install_uvicorn_url_secret_redaction() -> None:
    """Attach the redaction filter to uvicorn's loggers, once per process.

    Logger filters survive uvicorn's ``dictConfig`` call, so this holds whether
    uvicorn configures logging before importing the app (the CLI) or after
    (``uvicorn.run(app)``).
    """
    for name in UVICORN_LOGGER_NAMES:
        uvicorn_logger = logging.getLogger(name)
        if not any(
            isinstance(existing, URLSecretRedactionFilter)
            for existing in uvicorn_logger.filters
        ):
            uvicorn_logger.addFilter(URLSecretRedactionFilter())
