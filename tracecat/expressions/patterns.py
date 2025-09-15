import re

TEMPLATE_STRING = re.compile(
    r"(?P<template>\${{\s*(?P<expr>.+?)\s*}})", re.DOTALL
)  # Lazy match, includes newlines
"""Pattern that matches a template and its expression."""

STANDALONE_TEMPLATE = re.compile(r"^\${{\s*(?:(?!\${{).)*?\s*}}$")
"""Pattern that matches a standalone template expression."""
