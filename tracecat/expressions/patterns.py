import re

TEMPLATE_STRING = re.compile(r"(?P<template>\${{\s*(?P<expr>.+?)\s*}})")  # Lazy match
"""Pattern that matches a template and its expression."""


# Match any occurrence of SECRETS.<name>.<key> within a single template expression
# This allows nested expressions like:
#   ${{ FN.to_base64(SECRETS.zendesk.ZENDESK_EMAIL + "/token:" + SECRETS.zendesk.ZENDESK_API_TOKEN) }}
# Previously, we only matched when the entire template was a bare SECRETS reference.
# We now scan inside the template, but still require being within ${{ ... }} to avoid false positives.
SECRET_SCAN_TEMPLATE = re.compile(
    r"\${{[^}]*SECRETS\.(?P<secret>[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)[^}]*}}"
)
"""Specialized pattern to scan for secrets within template expressions."""

STANDALONE_TEMPLATE = re.compile(r"^\${{\s*(?:(?!\${{).)*?\s*}}$")
"""Pattern that matches a standalone template expression."""
