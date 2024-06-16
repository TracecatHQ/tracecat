import re


# Function to create the base pattern with optional type specifier
def _add_return_type(*patterns: str) -> str:
    combined = "|".join(patterns)
    return (
        r"^\s*"
        rf"(?P<context_expr>{combined})"
        r"(\s*->\s*(?P<context_expr_rtype>int|float|str|bool))?"
        r"\s*$"
    )


def _compile_combined_pattern(*patterns: str):
    combined = "|".join(patterns)
    # Add whitespace padding
    padded_template = rf"\s*{combined}\s*"

    return re.compile(padded_template)


TEMPLATE_STRING = re.compile(r"(?P<template>\${{\s*(?P<expr>.+?)\s*}})")  # Lazy match
"""Pattern that matches a template and its expression."""


SECRET_SCAN_TEMPLATE = re.compile(r"\${{\s*SECRETS\.(?P<secret>.+?)\s*}}")
"""Specialized pattern to scan for secrets."""

FULL_TEMPLATE = re.compile(r"^\${{\s*[^{}]*\s*}}$")


TYPE_SPECIFIERS = ("int", "float", "str", "bool")

TYPE_SPECIFIER_PATTERN = "|".join(rf"\b{type}\b" for type in TYPE_SPECIFIERS)
"""Match one of 'int', 'float', 'str', 'bool'."""

ACTION_BASE = r"ACTIONS\.(?P<action_expr>[a-zA-Z0-9_\-\.]+?)"
"""Match `ACTIONS.action`."""

SECRET_BASE = r"SECRETS\.(?P<secret_expr>[a-zA-Z0-9_\-\.]+?)"
"""Match `SECRETS.secret`."""

FUNCTION_BASE = r"FN\.(?P<fn_expr>(?P<fn_name>[a-zA-Z0-9_\.]+?)\((?P<fn_args>.*?)\))"
"""Match `FN.func(arg1, arg2)`."""

INPUTS_BASE = r"INPUTS\.(?P<input_expr>[a-zA-Z0-9_\-\.]+?)"
"""Match `INPUTS.var` or `INPUTS.my.module.items`."""

ENV_BASE = r"ENV\.(?P<env_expr>[a-zA-Z0-9_\-\.]+?)"
"""Match `ENV.var` or `ENV.my.module.items`."""

LOCAL_VARS_BASE = r"var\.(?P<vars_expr>[a-zA-Z0-9_\-\.]+?)"
"""Match the `var` action-local context. e.g.`var.some_variable` or `var.some.variable`."""

STRING_LITERAL = r"'(?P<str_literal>[^']*)'"
LIST_LITERAL = r"\[(?P<list_literal>.*?)\]"
NUMERIC_LITERAL = r"(?P<num_literal>\d+(?:\.\d+)?)"
BOOL_LITERAL = r"(?P<bool_literal>True|False)"
NONE_LITERAL = r"(?P<none_literal>None)"

LITERAL_BASE = rf"(?P<literal_expr>({STRING_LITERAL})|({BOOL_LITERAL})|({NUMERIC_LITERAL})|({LIST_LITERAL})|({NONE_LITERAL}))"
"""Match `'hello'` or `True` or `False` or `None` or `5` or `5.0` or `[1, 2, 3]` or `[]`."""


TYPECAST_BASE = (
    rf"(?P<cast_type>{TYPE_SPECIFIER_PATTERN})"  # Match one of 'int', 'float', 'str', 'bool'
    r"\("
    r"(?P<cast_expr>.+?)"
    r"\)"
)
"""Match `int(5)` or `float(5.0)` or `str('hello')` or `bool(True)`."""

TYPED_CTX_EXPR_PATTERN = _add_return_type(
    ACTION_BASE,
    SECRET_BASE,
    FUNCTION_BASE,
    INPUTS_BASE,
    ENV_BASE,
    LOCAL_VARS_BASE,
)

ITERATOR_BASE = (
    r"for"
    r"\s+(?P<iter_var_expr>[a-zA-Z_][a-zA-Z0-9_\.]*)\s+"
    r"in"
    r"\s+(?P<iter_collection_expr>[a-zA-Z_][a-zA-Z0-9_\.]*)"
)

TERNARY_PATTERN = r"(?P<ternary_true_expr>.+?)\s*if\s*(?P<ternary_cond_expr>.+?)\s*else\s*(?P<ternary_false_expr>.+?)"

ALL_EXPRESSIONS = [
    TYPED_CTX_EXPR_PATTERN,
    ITERATOR_BASE,
    TERNARY_PATTERN,
    LITERAL_BASE,
    TYPECAST_BASE,
]

EXPRESSION_PATTERN = _compile_combined_pattern(*ALL_EXPRESSIONS)
