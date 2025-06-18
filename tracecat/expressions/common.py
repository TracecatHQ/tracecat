import asyncio
import concurrent.futures
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any, TypeVar
from typing import cast as type_cast

import jsonpath_ng.ext
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat.logger import logger
from tracecat.types.exceptions import TracecatExpressionError


class TracecatEnum(StrEnum):
    def __repr__(self) -> str:
        return str(self)


class ExprContext(TracecatEnum):
    """Expression contexts."""

    # Global contexts
    ACTIONS = "ACTIONS"
    """Actions context"""

    SECRETS = "SECRETS"
    """Secrets context"""

    FN = "FN"
    """Function context"""

    INPUTS = "INPUTS"
    """Inputs context"""

    ENV = "ENV"
    """Environment context"""

    TRIGGER = "TRIGGER"
    """Trigger context"""
    # Action-local variables
    LOCAL_VARS = "var"
    """Action-local variables context"""

    TEMPLATE_ACTION_INPUTS = "inputs"
    """Template action inputs context"""

    TEMPLATE_ACTION_STEPS = "steps"
    """Template action steps context"""


class ExprType(TracecatEnum):
    GENERIC = auto()
    ACTION = auto()
    SECRET = auto()
    FUNCTION = auto()
    INPUT = auto()
    ENV = auto()
    LOCAL_VARS = auto()
    LITERAL = auto()
    TYPECAST = auto()
    ITERATOR = auto()
    TERNARY = auto()
    TRIGGER = auto()
    TEMPLATE_ACTION_STEP = auto()
    TEMPLATE_ACTION_INPUT = auto()


VISITOR_NODE_TO_EXPR_TYPE = {
    "expression": ExprType.GENERIC,
    "actions": ExprType.ACTION,
    "secrets": ExprType.SECRET,
    "function": ExprType.FUNCTION,
    "inputs": ExprType.INPUT,
    "env": ExprType.ENV,
    "local_vars": ExprType.LOCAL_VARS,
    "literal": ExprType.LITERAL,
    "typecast": ExprType.TYPECAST,
    "iterator": ExprType.ITERATOR,
    "ternary": ExprType.TERNARY,
    "trigger": ExprType.TRIGGER,
}


@dataclass
class IterableExpr[T]:
    """An expression that represents an iterable collection."""

    iterator: str
    collection: Iterable[T]

    def __iter__(self) -> Iterator[tuple[str, T]]:
        for item in self.collection:
            yield self.iterator, item


K = TypeVar("K", str, StrEnum)
ExprOperand = Mapping[K, Any]


def _expr_with_context(expr: str, context_type: ExprContext | None) -> str:
    return f"{context_type}.{expr}" if context_type else expr


def build_safe_lambda(lambda_expr: str) -> Callable[[Any], Any]:
    """Build a safe lambda function from a string expression using Deno sandbox.

    This function converts a lambda expression string into a callable that executes
    the expression in a secure WebAssembly sandbox using Pyodide/Deno.

    Args:
        lambda_expr: Lambda expression string (e.g., "lambda x: x > 2")

    Returns:
        A callable that executes the lambda in a secure sandbox

    Raises:
        ValueError: If the lambda expression is invalid
    """

    # Validate input
    if not lambda_expr or not isinstance(lambda_expr, str):
        raise ValueError("Lambda expression must be a non-empty string")

    lambda_expr = lambda_expr.strip()

    # Parse the lambda expression
    # Match patterns like "lambda: 42", "lambda x: x > 2" or "lambda x, y: x + y"
    lambda_pattern = r"^\s*lambda\s*([^:]*?):\s*(.+)$"
    match = re.match(lambda_pattern, lambda_expr)

    if not match:
        raise ValueError(
            "Invalid lambda expression format. Expected 'lambda <args>: <expression>'"
        )

    args_str, body = match.groups()

    # Parse arguments
    if args_str.strip():
        args = [arg.strip() for arg in args_str.split(",")]
    else:
        args = []  # No arguments for lambdas like "lambda: 42"

    # Create a function definition
    function_def = f"""
def main({", ".join(args)}):
    return {body}
"""

    # Add jsonpath support if needed
    if "jsonpath" in body:
        # Import the jsonpath function in the sandbox
        function_def = f"""
import json

def jsonpath(expr, data):
    # Simple jsonpath implementation for common cases
    if expr.startswith("$."):
        path_parts = expr[2:].split('.')
        result = data
        for part in path_parts:
            if '[*]' in part:
                # Handle array wildcard
                key = part.replace('[*]', '')
                if key:
                    result = result.get(key, [])
                if isinstance(result, list):
                    remaining_path = '.'.join(path_parts[path_parts.index(part)+1:])
                    if remaining_path:
                        return [jsonpath('$.' + remaining_path, item) for item in result]
                    return result
            else:
                if isinstance(result, dict):
                    result = result.get(part)
                else:
                    return None
        return result
    return None

{function_def}
"""

    # Create a callable wrapper that executes the function in the sandbox
    def lambda_wrapper(*args):
        # Import here to avoid circular imports
        from tracecat_registry.core.python import run_python

        # Build inputs dictionary from arguments
        inputs = {}
        if args_str.strip():
            arg_names = [arg.strip() for arg in args_str.split(",")]
            for _, (arg_name, arg_value) in enumerate(
                zip(arg_names, args, strict=False)
            ):
                inputs[arg_name] = arg_value
        # For lambdas with no arguments, inputs remains empty

        # Execute in sandbox with network disabled for security
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop, create one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            close_loop = True
        else:
            close_loop = False

        async def run_sandbox():
            return await run_python(
                script=function_def,
                inputs=inputs,
                dependencies=None,
                timeout_seconds=5,  # Short timeout for lambda expressions
                allow_network=False,  # Security: no network access
            )

        try:
            if close_loop:
                result = loop.run_until_complete(run_sandbox())
            else:
                # We're already in an async context
                # Use asyncio.run_coroutine_threadsafe to avoid "event loop already running" error
                def run_in_thread():
                    # Create a new event loop in this thread
                    new_loop = asyncio.new_event_loop()
                    try:
                        # Set the new loop as the current loop for this thread
                        asyncio.set_event_loop(new_loop)
                        return new_loop.run_until_complete(run_sandbox())
                    finally:
                        new_loop.close()
                        # Clear the event loop for this thread
                        asyncio.set_event_loop(None)

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(run_in_thread)
                    result = future.result(timeout=10)  # Add timeout for safety
        finally:
            if close_loop:
                loop.close()

        return result

    return type_cast(Callable[[Any], Any], lambda_wrapper)


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
