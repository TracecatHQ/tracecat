"""Redact secrets that requests carry in their URL.

Incoming webhooks, SNS ingest endpoints, agent channel endpoints, channel
links and invitation links authenticate the caller with a secret path
segment, and OAuth-style callbacks carry codes and signed state in the query
string. Anything that logs a request path or request line passes it through
``redact_url_secrets`` first, and a bare query string through
``redact_query_string``. A new route with a secret in its path adds one
pattern to ``_URL_SECRET_PATTERNS``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from urllib.parse import unquote_plus

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

# Query parameters that carry a credential: OAuth codes and state (the
# channel-link state embeds the link token), and bearer tokens. Names are
# compared after URL-decoding, as the app binds them.
_SECRET_QUERY_KEYS = frozenset(
    {"code", "state", "token", "access_token", "id_token", "refresh_token"}
)
# The query part of a URL or request line: up to a fragment, quote or space.
_QUERY = re.compile(r"\?([^#\s\"']*)")

UVICORN_LOGGER_NAMES = ("uvicorn.access", "uvicorn.error")


def redact_url_secrets(text: str) -> str:
    """Replace every URL secret in ``text`` with ``[redacted]``.

    Args:
        text: A request path, a query string, a full URL, or a log line
            containing one of them.

    Returns:
        The text with each secret segment replaced; other text is unchanged.
    """
    for pattern in _URL_SECRET_PATTERNS:
        text = pattern.sub(rf"\g<1>{REDACTED_URL_SECRET}", text)
    return _QUERY.sub(lambda match: f"?{redact_query_string(match[1])}", text)


def redact_query_string(query: str) -> str:
    """Replace the value of every credential query parameter with ``[redacted]``.

    Args:
        query: A query string without the leading ``?``, raw or decoded.

    Returns:
        The query string with credential values replaced, in the same order.
    """
    pairs: list[str] = []
    for pair in query.split("&"):
        name, sep, _value = pair.partition("=")
        if sep and unquote_plus(name) in _SECRET_QUERY_KEYS:
            pair = f"{name}={REDACTED_URL_SECRET}"
        pairs.append(pair)
    return "&".join(pairs)


class URLSecretRedactionFilter(logging.Filter):
    """Redact URL secrets from stdlib log records, such as uvicorn's access log.

    uvicorn formats its access line from ``record.args`` (client, method, path
    with query, HTTP version, status), so the arguments and the message are
    both rewritten before any handler formats the record. A traceback is
    rendered once into ``exc_text``, which formatters reuse, and redacted too.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_url_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_url_secrets(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        elif isinstance(record.args, Mapping):
            # A single mapping argument is kept as the mapping itself.
            record.args = {
                key: redact_url_secrets(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_url_secrets(record.exc_text)
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
