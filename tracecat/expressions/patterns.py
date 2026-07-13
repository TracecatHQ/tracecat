import re

TEMPLATE_STRING = re.compile(
    r"(?P<template>\${{\s*(?P<expr>.+?)\s*}})", re.DOTALL
)  # Lazy match, includes newlines
"""Pattern that matches a template and its expression."""

STANDALONE_TEMPLATE = re.compile(r"^\${{\s*(?:(?!\${{).)*?\s*}}$")
"""Pattern that matches a standalone template expression."""

STANDALONE_SAFE_REFERENCE = re.compile(
    r"^\$\{\{\s*(?:SECRETS|VARS)\.[a-zA-Z0-9_.]+\s*\}\}$"
)
"""Pattern that matches a standalone ``SECRETS.*`` or ``VARS.*`` reference.

Unlike :data:`STANDALONE_TEMPLATE`, this only matches a single template whose
entire inner expression is a plain ``SECRETS``/``VARS`` attribute path (e.g.
``${{ SECRETS.example.TOKEN }}`` or ``${{ VARS.foo }}``). It rejects anything
that could embed a literal, such as function calls, arithmetic, string
literals, or concatenations, so it is safe to echo the raw value back to the
client. Attribute-path segments follow the expression grammar's ``CNAME``
tokens (``[a-zA-Z_][a-zA-Z0-9_]*``), which also covers the ``[a-z0-9_]``
secret-name and ``[a-zA-Z0-9_]`` secret-key alphabets.
"""
