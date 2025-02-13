import re

TEMPLATE_STRING = re.compile(r"(?P<template>\${{\s*(?P<expr>.+?)\s*}})")  # Lazy match
"""Pattern that matches a template and its expression."""


SECRET_SCAN_TEMPLATE = re.compile(r"\${{\s*SECRETS\.(?P<secret>.+?)\s*}}")
"""Specialized pattern to scan for secrets."""

STANDALONE_TEMPLATE = re.compile(r"^\${{\s*(?:(?!\${{).)*?\s*}}$")
"""Pattern that matches a standalone template expression."""
