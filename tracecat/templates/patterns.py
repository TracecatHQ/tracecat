import re

TEMPLATED_OBJ = re.compile(r"(?P<template>\${{\s*.+?\s*}})")  # Lazy match

TYPED_TEMPLATE = re.compile(
    r"""
    \${{\s*                                               # Opening curly braces and optional whitespace
    (?P<context>INPUTS|ACTIONS|SECRETS|FNS?)            # Non-greedy capture for 'expression', any chars
    \.
    (?P<expr>.+?)            # Non-greedy capture for 'expression', any chars
    (\s*->\s*(?P<type>int|float|str))?                             # Capture 'type', which must be one of 'int', 'float', 'str'
    \s*}}                                               # Optional whitespace and closing curly braces
""",
    re.VERBOSE,
)
SECRET_TEMPLATE = re.compile(
    r"""
    \${{\s*                                               # Opening curly braces and optional whitespace
    SECRETS
    \.
    (?P<secret>.+?)            # Non-greedy capture for 'expression', any chars
    \s*}}                                               # Optional whitespace and closing curly braces
""",
    re.VERBOSE,
)
EXPR_SECRET = re.compile(
    r"""
    ^\s*                          # Start of the string and optional leading whitespace
    SECRETS\.                      # Literal 'SECRETS.'
    (?P<secret>[a-zA-Z0-9_.]+?)    # Non-greedy capture for 'secret', word chars and dots
    \s*$                          # Optional trailing whitespace and end of the string
""",
    re.VERBOSE,
)
EXPR_INLINE_FN = re.compile(
    r"""
    ^\s*                          # Start of the string and optional leading whitespace
    # FNS\.                          # Literal 'FNS.'
    (?P<func>[a-zA-Z0-9_]+?)      # Non-greedy capture for 'func', restricted to word characters
    \(                            # Opening parenthesis
    (?P<args>.*?)                 # Non-greedy capture for 'args', any characters
    \)                            # Closing parenthesis
    \s*$                          # Optional trailing whitespace and end of the string
""",
    re.VERBOSE,
)
EXPR_QUALIFIED_ATTRIBUTE = re.compile(r"\b[a-zA-Z0-9]+(?:\.[a-zA-Z0-9]+)+\b")

FULL_TEMPLATE = re.compile(r"^\${{\s*[^{}]*\s*}}$")
