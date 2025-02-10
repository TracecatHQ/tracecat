import ast
from builtins import filter as filter_
from collections.abc import Callable, Mapping
from enum import StrEnum
from itertools import groupby
from typing import Annotated, Any
from typing import cast as type_cast

import jsonpath_ng.ext
from jsonpath_ng.exceptions import JsonPathParserError
from tracecat.expressions.common import ExprContext
from tracecat.expressions.functions import _expr_with_context
from tracecat.expressions.functions import flatten as flatten_
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatExpressionError
from typing_extensions import Doc

from tracecat_registry import registry


class SafeEvaluator(ast.NodeVisitor):
    RESTRICTED_NODES = {ast.Import, ast.ImportFrom}
    RESTRICTED_SYMBOLS = {
        "eval",
        "import",
        "from",
        "os",
        "sys",
        "exec",
        "locals",
        "globals",
    }
    # Add allowed functions that can be used in lambda expressions
    ALLOWED_FUNCTIONS = {"jsonpath"}

    def visit(self, node):
        if type(node) in self.RESTRICTED_NODES:
            raise ValueError(
                f"Restricted node {type(node).__name__} detected in expression"
            )
        if (
            isinstance(node, ast.Call)
            and (attr := getattr(node.func, "attr", None)) is not None
            and attr in self.RESTRICTED_SYMBOLS
            and attr not in self.ALLOWED_FUNCTIONS
        ):
            raise ValueError(f"Calling restricted functions are not allowed: {attr}")
        self.generic_visit(node)


def _build_safe_lambda(lambda_expr: str) -> Callable[[Any], Any]:
    """Build a safe lambda function from a string expression."""
    # Check if the string has any blacklisted symbols
    lambda_expr = lambda_expr.strip()
    if any(
        word in lambda_expr
        for word in SafeEvaluator.RESTRICTED_SYMBOLS - SafeEvaluator.ALLOWED_FUNCTIONS
    ):
        raise ValueError("Expression contains restricted symbols")
    expr_ast = ast.parse(lambda_expr, mode="eval").body

    # Ensure the parsed AST is a comparison or logical expression
    if not isinstance(expr_ast, ast.Lambda):
        raise ValueError("Expression must be a lambda function")

    # Ensure the expression complies with the SafeEvaluator
    SafeEvaluator().visit(expr_ast)

    # Compile the AST node into a code object
    code = compile(ast.Expression(expr_ast), "<string>", "eval")

    # Create a function from the code object with eval_jsonpath in globals
    lambda_func = eval(code, {"jsonpath": eval_jsonpath})
    return type_cast(Callable[[Any], Any], lambda_func)


def eval_jsonpath(
    expr: str,
    operand: Mapping[str | StrEnum, Any],
    *,
    context_type: ExprContext | None = None,
    strict: bool = False,
) -> Any | None:
    """Evaluate a jsonpath expression on the target object (operand)."""

    if operand is None or not isinstance(operand, dict | list):
        logger.error("Invalid operand for jsonpath", operand=operand)
        raise TracecatExpressionError(
            f"A dict or list operand is required as jsonpath target. Got {type(operand)}"
        )
    try:
        # Try to evaluate the expression
        jsonpath_expr = jsonpath_ng.ext.parse(expr)
    except JsonPathParserError as e:
        logger.error(
            "Invalid jsonpath expression", expr=repr(expr), context_type=context_type
        )
        formatted_expr = _expr_with_context(expr, context_type)
        raise TracecatExpressionError(f"Invalid jsonpath {formatted_expr!r}") from e
    matches = [found.value for found in jsonpath_expr.find(operand)]
    if len(matches) > 1 or "[*]" in expr:
        # If there are multiple matches or array wildcard, return the list
        return matches
    elif len(matches) == 1:
        # If there is a non-array wildcard single match, return the value
        return matches[0]
    else:
        # We should only reach this point if the jsonpath didn't match
        # If there are no matches, raise an error if strict is True

        if strict:
            # We know that if this function is called, there was a templated field.
            # Therefore, it means the jsonpath was valid but there was no match.
            logger.error("Jsonpath no match", expr=repr(expr), operand=operand)
            formatted_expr = _expr_with_context(expr, context_type)
            raise TracecatExpressionError(
                f"Couldn't resolve expression {formatted_expr!r} in the context",
                detail={"expression": formatted_expr, "operand": operand},
            )
        # Return None instead of empty list
        return None


@registry.register(
    default_title="Reshape",
    description="Reshapes the input value to the output. You can use this to reshape a JSON-like structure into another easier to manipulate JSON object.",
    display_group="Data Transform",
    namespace="core.transform",
)
def reshape(
    value: Annotated[
        Any,
        Doc("The value to reshape"),
    ],
) -> Any:
    return value


@registry.register(
    default_title="Filter",
    description="Filter a collection using a Python lambda function.",
    display_group="Data Transform",
    namespace="core.transform",
)
def filter(
    items: Annotated[
        list[Any],
        Doc("Items to filter."),
    ],
    python_lambda: Annotated[
        str,
        Doc(
            'Filter condition as a Python lambda expression (e.g. `"lambda x: x > 2"`).'
        ),
    ],
) -> Any:
    fn = _build_safe_lambda(python_lambda)
    return list(filter_(fn, items))


@registry.register(
    default_title="Is in",
    description="Filters items in a list based on whether they are in a collection.",
    display_group="Data Transform",
    namespace="core.transform",
)
def is_in(
    items: Annotated[
        list[Any],
        Doc("Items to filter."),
    ],
    collection: Annotated[
        list[Any],
        Doc("Collection to check against."),
    ],
    python_lambda: Annotated[
        str | None,
        Doc(
            "Python lambda applied to each item before checking membership (e.g. `\"lambda x: x.get('name')\"`). Similar to `key` in the Python `sorted` function."
        ),
    ] = None,
    unique: Annotated[
        bool,
        Doc("Drop duplicate items by the Python lambda key."),
    ] = False,
) -> list[Any]:
    col_set = set(collection)
    if python_lambda:
        fn = _build_safe_lambda(python_lambda)
        result = [item for item in items if fn(item) in col_set]
    else:
        result = [item for item in items if item in col_set]

    if unique:
        result = deduplicate(result, python_lambda=python_lambda)
    return result


@registry.register(
    default_title="Is not in",
    description="Filters items in a list based on whether they are not in a collection.",
    display_group="Data Transform",
    namespace="core.transform",
)
def is_not_in(
    items: Annotated[
        list[Any],
        Doc("Items to filter."),
    ],
    collection: Annotated[
        list[Any],
        Doc("Collection to check against."),
    ],
    python_lambda: Annotated[
        str | None,
        Doc(
            "Python lambda applied to each item before checking membership (e.g. `\"lambda x: x.get('name')\"`). Similar to `key` in the Python `sorted` function."
        ),
    ] = None,
    unique: Annotated[
        bool,
        Doc("Drop duplicate items by the Python lambda key."),
    ] = False,
) -> list[Any]:
    col_set = set(collection)
    if python_lambda:
        fn = _build_safe_lambda(python_lambda)
        result = [item for item in items if fn(item) not in col_set]
    else:
        result = [item for item in items if item not in col_set]

    if unique:
        result = deduplicate(result, python_lambda=python_lambda)
    return result


@registry.register(
    default_title="Flatten",
    description="Flatten a list of lists into a single list.",
    display_group="Data Transform",
    namespace="core.transform",
)
def flatten(
    items: Annotated[
        list[list[Any]],
        Doc("List of lists to flatten."),
    ],
) -> list[Any]:
    return flatten_(items)


@registry.register(
    default_title="Deduplicate",
    description="Deduplicate items in a list. Similar to uniq command in unix.",
    display_group="Data Transform",
    namespace="core.transform",
)
def deduplicate(
    items: Annotated[list[Any], Doc("Items to deduplicate.")],
    python_lambda: Annotated[
        str | None,
        Doc(
            "Python lambda applied to each item to extract key to deduplicate by (e.g. `\"lambda x: x.get('name')\"`). Defaults to identity function."
        ),
    ] = None,
) -> list[Any]:
    if python_lambda:
        fn = _build_safe_lambda(python_lambda)
        items = sorted(items, key=fn)
    return [item for item, _ in groupby(items, key=fn)]


@registry.register(
    default_title="Apply",
    description="Apply a Python lambda function to each item in a list.",
    display_group="Data Transform",
    namespace="core.transform",
)
def apply(
    items: Annotated[
        list[Any],
        Doc("Items to apply the lambda function to."),
    ],
    python_lambda: Annotated[
        str,
        Doc("Python lambda function as a string (e.g. `\"lambda x: x.get('name')\"`)."),
    ],
) -> list[Any]:
    fn = _build_safe_lambda(python_lambda)
    return list(map(fn, items))


@registry.register(
    default_title="Merge JSON objects",
    description="Merge two JSON objects into a single JSON object.",
    display_group="Data Transform",
    namespace="core.transform",
)
def merge(
    left: Annotated[dict[str, Any], Doc("Left JSON object")],
    right: Annotated[dict[str, Any], Doc("Right JSON object")],
) -> dict[str, Any]:
    """Merge two JSON objects into a single JSON object."""
    return {**left, **right}
