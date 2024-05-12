"""A module for templated futures.


Motivation
----------
- Formalize the templated expression syntax and vocabulary.
- By doing so, in TypeScript we can infer the final type of the future to improve type checking and validation.
    - This helps with type checking in the UI at form submission time

Template
--------
A string with one or more templated expressions, e.g. "{{ $.webhook.result: int }}"

Expression
----------
The constituients of the template, "{{ <expression>: <type> }}" "$.webhook.result: int"
The expression and type together are referred to as typed/annotated expression


Resolved Type
-------------
The type to cast the result to, e.g. "int" or "str" or "float"

Context
-------
The root level namespace of the expression, e.g. "SECRETS" or "$" or "FNS".
Anything that isn't a global context like SECRETS/FNS is assumed to be a jsonpath, e.g. "$.webhook.result"

Operand
-------
The data structure to evaluate the expression on, e.g. {"webhook": {"result": 42}}



"""

import re
from typing import Any, TypeVar

import jsonpath_ng
from jsonpath_ng.exceptions import JsonPathParserError

T = TypeVar("T")
token_pattern = r""
_BUILTIN_TYPE_NAP = {
    "int": int,
    "float": float,
    "str": str,
    # TODO: Perhaps support for URLs for files?
}
_TYPED_TEMPLATE_PATTERN = re.compile(
    r"""
    {{\s*                           # Opening curly braces and optional whitespace
    (?P<expression>.+?)              # Non-greedy capture for 'expression', any chars
    :\s*                            # Colon followed by optional whitespace
    (?P<type>int|float|str)         # Capture 'type', which must be one of 'int', 'float', 'str'
    \s*}}                           # Optional whitespace and closing curly braces
""",
    re.VERBOSE,
)
_EXPR_SECRET_PATTERN = re.compile(
    r"""
    ^\s*                          # Start of the string and optional leading whitespace
    SECRETS\.                      # Literal 'SECRETS.'
    (?P<secret>[a-zA-Z0-9_.]+?)    # Non-greedy capture for 'secret', word chars and dots
    \s*$                          # Optional trailing whitespace and end of the string
""",
    re.VERBOSE,
)
_EXPR_INLINE_FN_PATTERN = re.compile(
    r"""
    ^\s*                          # Start of the string and optional leading whitespace
    FNS\.                          # Literal 'FNS.'
    (?P<func>[a-zA-Z0-9_]+?)      # Non-greedy capture for 'func', restricted to word characters
    \(                            # Opening parenthesis
    (?P<args>.*?)                 # Non-greedy capture for 'args', any characters
    \)                            # Closing parenthesis
    \s*$                          # Optional trailing whitespace and end of the string
""",
    re.VERBOSE,
)
_EXPR_QUALIFIED_ATTRIBUTE_PATTERN = re.compile(r"\b[a-zA-Z0-9]+(?:\.[a-zA-Z0-9]+)+\b")

TemplateStr = str
"""An annotated template string that can be resolved into a type T."""


class TemplatedFuture:
    """A future that resolves into a type T.

    Args:
        Generic (_type_): _description_
    """

    def __init__(self, template: TemplateStr, *, operand: Any | None = None) -> None:
        self._template = template
        match = _TYPED_TEMPLATE_PATTERN.match(template)
        if not match:
            raise ValueError(f"Invalid template: {template!r}")

        # Top level types
        self._expr = match.group("expression")
        self._resolve_typename = match.group("type")
        self._resolve_type = _BUILTIN_TYPE_NAP[self._resolve_typename]

        # Operand
        self._operand = operand

    def result(self) -> Any:
        """Evaluate the templated future and return the result.

        Raises:
            ValueError: _description_

        Returns:
            Any: _description_
        """
        ret: Any
        if matched_secret := _EXPR_SECRET_PATTERN.match(self._expr):
            print("Got a secret")
            ret = matched_secret.group("secret")
            # Get the secret from the secret manager
        elif matched_fn := _EXPR_INLINE_FN_PATTERN.match(self._expr):
            print("Got an inline function")
            fn_name = matched_fn.group("func")
            fn_args = re.split(r",\s*", matched_fn.group("args"))
            # Get the function, likely from some regsitry or module and call it

            print(fn_name, fn_args)
            # raise NotImplementedError("Inline functions are not yet supported.")
            ret = fn_name
        else:
            print("Got a jsonpath")
            ret = self._evaluate_jsonpath()

        try:
            # Attempt to cast the result into the desired type
            return self._resolve_type(ret)
        except Exception as e:
            raise ValueError(f"Could not cast {ret!r} to {self._resolve_type!r}") from e

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return (
            "TemplatedFuture("
            f"template={self._template},"
            f" expression={self._expr},"
            f" typename={self._resolve_typename},"
            f" type={self._resolve_type}),"
            f" operand={self._operand})"
        )

    def _evaluate_jsonpath(self) -> Any:
        if self._operand is None or not isinstance(self._operand, dict):
            raise ValueError("A dict-type operand is required for templated jsonpath.")
        try:
            # Try to evaluate the expression
            jsonpath_expr = jsonpath_ng.parse(self._expr)
        except JsonPathParserError as e:
            raise ValueError(f"Invalid jsonpath {self._expr!r}") from e
        matches = [found.value for found in jsonpath_expr.find(self._operand)]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            return matches
        else:
            # We know that if this function is called, there was a templated field.
            # Therefore, it means the jsonpath was valid but there was no match.
            raise ValueError(
                f"Operand has no path {self._expr!r}. Operand: {self._operand}."
            )


if __name__ == "__main__":
    fut = TemplatedFuture(
        "{{ $.webhook.result: int }}", operand={"webhook": {"result": 42}}
    )
    print(fut)
    res = fut.result()
    print(type(res), repr(res))

    fn_fut = TemplatedFuture(
        "{{ FNS.get_my_car(x.y.z, a.b.c, $.webhook.result): str }}"
    )
    print(fn_fut)
    print(fn_fut.result())

    sec_fut = TemplatedFuture("{{ SECRETS.path.to.secret.VALUE: str }}")
    print(sec_fut)
    print(sec_fut.result())

    text = "{{ a.b.c(1.2.3, x.y.z): str }}"

    # Regex to match dot-delimited alphanumeric groups
    pattern = _EXPR_QUALIFIED_ATTRIBUTE_PATTERN

    # Find all matches
    matches = re.findall(pattern, text)
    print(matches)  # Outputs: ['a.b.c', '1.2.3']
