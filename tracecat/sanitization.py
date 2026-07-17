"""Shared sanitizers for text that may cross a logging boundary."""

from __future__ import annotations

import re
from collections.abc import Iterable

_URL_PATTERN = re.compile(r"\bhttps?://\S+", re.IGNORECASE)
_URL_USERINFO_PATTERN = re.compile(r"^(https?://)[^/@]*@", re.IGNORECASE)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_BEARER_TOKEN_PATTERN = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_AUTHORIZATION_VALUE_PATTERN = re.compile(r"(?im)\b(Authorization\s*:\s*)[^\r\n]+")
_COOKIE_VALUE_PATTERN = re.compile(r"(?im)\b((?:Set-)?Cookie\s*:\s*)[^\r\n]+")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(?P<prefix>['\"]?\b(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|"
    r"id[ _-]?token|oauth[ _-]?token|client[ _-]?secret|private[ _-]?key|"
    r"secret[ _-]?key|token|password|passwd|secret)\b['\"]?\s*[:=]\s*)"
    r"(?:"
    r'(?P<double_quoted>"(?:\\.|[^"\\\r\n])*")|'
    r"(?P<single_quoted>'(?:\\.|[^'\\\r\n])*')|"
    r"(?P<unquoted>[^\s,;}\]\"']+)"
    r")"
)


def sanitize_urls_in_text(text: str) -> str:
    """Remove userinfo, query strings, and fragments from HTTP URLs in text.

    Args:
        text: Free-form text that may contain one or more HTTP URLs.

    Returns:
        Text with URL credentials and parameters removed. Text outside URLs is
        unchanged.
    """

    def sanitize(match: re.Match[str]) -> str:
        url = _URL_USERINFO_PATTERN.sub(r"\1", match.group(0))
        return re.sub(r"[?#].*$", "", url)

    return _URL_PATTERN.sub(sanitize, text)


def redact_sensitive_text(
    text: str,
    *,
    sensitive_values: Iterable[str] = (),
    redact_emails: bool = False,
) -> str:
    """Redact common credentials and optionally email addresses from text.

    This function is a defense-in-depth filter for free-form text. It cannot
    identify an arbitrary opaque value as secret unless that value is supplied
    through ``sensitive_values``. Callers must still avoid passing raw request
    bodies, configuration values, workflow data, or other unrestricted content
    across a logging boundary.

    Args:
        text: Free-form text to sanitize.
        sensitive_values: Known secret values to replace exactly. Values shorter
            than four characters are ignored to avoid destructive overmatching.
        redact_emails: Whether to replace email-shaped values as PII.

    Returns:
        Sanitized text with recognized sensitive values replaced.
    """

    sanitized = sanitize_urls_in_text(text)
    for value in sensitive_values:
        if len(value) < 4:
            continue
        sanitized = sanitized.replace(value, "[redacted]")
        sanitized_value = sanitize_urls_in_text(value)
        if sanitized_value != value:
            sanitized = sanitized.replace(sanitized_value, "[redacted]")
    if redact_emails:
        sanitized = _EMAIL_PATTERN.sub("[redacted email]", sanitized)
    sanitized = _BEARER_TOKEN_PATTERN.sub(r"\1[redacted]", sanitized)
    sanitized = _AUTHORIZATION_VALUE_PATTERN.sub(r"\1[redacted]", sanitized)
    sanitized = _COOKIE_VALUE_PATTERN.sub(r"\1[redacted]", sanitized)
    sanitized = _JWT_PATTERN.sub("[redacted token]", sanitized)
    return _SENSITIVE_VALUE_PATTERN.sub(_redact_sensitive_value, sanitized)


def _redact_sensitive_value(match: re.Match[str]) -> str:
    """Replace a key-value secret while retaining readable delimiters."""

    if match.group("double_quoted") is not None:
        redacted = '"[redacted]"'
    elif match.group("single_quoted") is not None:
        redacted = "'[redacted]'"
    else:
        redacted = "[redacted]"
    return f"{match.group('prefix')}{redacted}"
