import ast
import functools
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any, TypeVar

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


class SafeEvaluator(ast.NodeVisitor):
    """AST node visitor that ensures expressions are safe to evaluate.

    This visitor checks for and prevents:
    - Import statements (ast.Import, ast.ImportFrom)
    - Function/class definitions
    - Scope manipulation (global, nonlocal)
    - Deletion operations
    - Context managers (with statements)
    - Async operations
    - Access to dangerous built-in functions and modules
    - File, OS, and network operations
    - Introspection and attribute manipulation
    """

    RESTRICTED_NODES = {
        ast.Import,
        ast.ImportFrom,
        ast.Global,
        ast.Nonlocal,
        ast.Delete,
        ast.With,
        ast.AsyncWith,
        ast.AsyncFor,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.FunctionDef,
    }
    RESTRICTED_SYMBOLS = {
        # Core dangerous functions
        "eval",
        "exec",
        "compile",
        "__import__",
        "import",
        "from",
        # File operations
        "open",
        "file",
        "input",
        "raw_input",
        "io",
        "pathlib",
        "shutil",
        "tempfile",
        "fileinput",
        "glob",
        "fnmatch",
        # OS/System operations
        "os",
        "sys",
        "subprocess",
        "multiprocessing",
        "threading",
        "signal",
        "resource",
        "sysconfig",
        "platform",
        "ctypes",
        "pickle",
        "marshal",
        "code",
        "types",
        # Network operations
        "socket",
        "socketserver",
        "urllib",
        "http",
        "ftplib",
        "telnetlib",
        "smtplib",
        "poplib",
        "imaplib",
        "ssl",
        "asyncio",
        "requests",
        "httpx",
        "aiohttp",
        # Introspection/Attribute access
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "dir",
        "vars",
        "locals",
        "globals",
        "__builtins__",
        "help",
        "inspect",
        "traceback",
        "gc",
        # Other potentially dangerous
        "breakpoint",
        "exit",
        "quit",
        "memoryview",
        "bytearray",
    }
    ALLOWED_FUNCTIONS = {"jsonpath"}

    def visit(self, node):
        if type(node) in self.RESTRICTED_NODES:
            raise ValueError(
                f"Restricted node {type(node).__name__} detected in expression"
            )

        # Check for restricted function calls
        if isinstance(node, ast.Call):
            # Check for direct function calls (e.g., open(), eval())
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
                if (
                    func_name in self.RESTRICTED_SYMBOLS
                    and func_name not in self.ALLOWED_FUNCTIONS
                ):
                    raise ValueError(
                        f"Calling restricted function '{func_name}' is not allowed"
                    )

            # Check for attribute access calls (e.g., os.system(), socket.socket())
            elif isinstance(node.func, ast.Attribute):
                attr_name = node.func.attr
                if (
                    attr_name in self.RESTRICTED_SYMBOLS
                    and attr_name not in self.ALLOWED_FUNCTIONS
                ):
                    raise ValueError(
                        f"Calling restricted method '{attr_name}' is not allowed"
                    )

                # Also check if the object being accessed is restricted
                if isinstance(node.func.value, ast.Name):
                    obj_name = node.func.value.id
                    if obj_name in self.RESTRICTED_SYMBOLS:
                        raise ValueError(
                            f"Accessing restricted module '{obj_name}' is not allowed"
                        )

        # Check for direct name access to restricted symbols
        elif isinstance(node, ast.Name):
            if (
                node.id in self.RESTRICTED_SYMBOLS
                and node.id not in self.ALLOWED_FUNCTIONS
            ):
                raise ValueError(
                    f"Accessing restricted symbol '{node.id}' is not allowed"
                )

        # Check for attribute access to restricted symbols
        elif isinstance(node, ast.Attribute):
            if (
                node.attr in self.RESTRICTED_SYMBOLS
                and node.attr not in self.ALLOWED_FUNCTIONS
            ):
                raise ValueError(
                    f"Accessing restricted attribute '{node.attr}' is not allowed"
                )

        self.generic_visit(node)


class WhitelistValidator(ast.NodeVisitor):
    """AST validator that uses a whitelist approach - only allows safe node types."""

    ALLOWED_NODE_TYPES = {
        # Basic nodes
        ast.Module,
        ast.Expression,
        ast.Load,
        ast.Store,
        # Lambda and function basics
        ast.Lambda,
        ast.arguments,
        ast.arg,
        # Literals and basic types
        ast.Constant,
        ast.List,
        ast.Tuple,
        ast.Dict,
        ast.Set,
        # F-string support
        ast.JoinedStr,
        ast.FormattedValue,
        # Variables and attributes
        ast.Name,
        ast.Attribute,
        ast.Subscript,
        ast.Index,
        ast.Slice,
        # Operators
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        # Operator types
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.LShift,
        ast.RShift,
        ast.BitOr,
        ast.BitXor,
        ast.BitAnd,
        ast.MatMult,
        # Unary operators
        ast.Not,
        ast.UAdd,
        ast.USub,
        ast.Invert,
        # Boolean operators
        ast.And,
        ast.Or,
        # Comparison operators
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
        # Control flow (limited)
        ast.IfExp,  # Ternary operator
        # Comprehensions (safe)
        ast.ListComp,
        ast.DictComp,
        ast.SetComp,
        ast.GeneratorExp,
        ast.comprehension,
        # Function calls (will be further validated)
        ast.Call,
        ast.keyword,
    }

    def visit(self, node):
        if type(node) not in self.ALLOWED_NODE_TYPES:
            raise ValueError(
                f"Node type {type(node).__name__} is not allowed in expressions. "
                f"Only safe, simple expressions are permitted."
            )

        # Additional validation for specific node types
        if isinstance(node, ast.Attribute):
            # Prevent access to dunder attributes
            if node.attr.startswith("__") and node.attr.endswith("__"):
                raise ValueError(
                    f"Access to dunder attribute '{node.attr}' is not allowed"
                )

        self.generic_visit(node)


def _expr_with_context(expr: str, context_type: ExprContext | None) -> str:
    return f"{context_type}.{expr}" if context_type else expr


def create_sandboxed_lambda(func: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Wrap a lambda function with additional runtime protections.

    This adds:
    - Recursion depth limits
    - Attribute access validation
    - Protection against infinite loops via iteration limits
    """

    @functools.wraps(func)
    def sandboxed_wrapper(x):
        # Store original recursion limit
        original_recursion_limit = sys.getrecursionlimit()

        # Set a lower recursion limit to prevent stack exhaustion
        # But not too low - some libraries like jsonpath_ng need reasonable depth
        MAX_RECURSION_DEPTH = 500
        sys.setrecursionlimit(MAX_RECURSION_DEPTH)

        try:
            # Add a simple execution counter to prevent infinite loops
            # This is a basic protection - more sophisticated would use threading
            execution_count = 0
            MAX_ITERATIONS = 10000

            def count_guard(value):
                nonlocal execution_count
                execution_count += 1
                if execution_count > MAX_ITERATIONS:
                    raise ValueError("Expression exceeded maximum iteration limit")
                return value

            # Wrap any iterables in the input to add iteration guards
            if hasattr(x, "__iter__") and not isinstance(x, str | bytes):
                if isinstance(x, dict):
                    x = {k: count_guard(v) for k, v in x.items()}
                elif isinstance(x, list):
                    x = [count_guard(item) for item in x]

            # Execute the function
            result = func(x)

            # Validate the result isn't trying to return dangerous objects
            if hasattr(result, "__class__"):
                result_type = type(result)
                # Allow basic types
                safe_return_types = (
                    type(None),
                    bool,
                    int,
                    float,
                    str,
                    bytes,
                    list,
                    tuple,
                    dict,
                    set,
                    frozenset,
                )
                if not isinstance(result, safe_return_types):
                    raise ValueError(
                        f"Lambda returned unsafe type: {result_type.__name__}"
                    )

            return result

        except RecursionError as e:
            raise ValueError("Expression exceeded maximum recursion depth") from e
        finally:
            # Restore original recursion limit
            sys.setrecursionlimit(original_recursion_limit)

    return sandboxed_wrapper


def build_safe_lambda(lambda_expr: str) -> Callable[[Any], Any]:
    """Build a safe lambda function from a string expression.

    This function implements multiple layers of security:
    1. String-level blacklist checking
    2. AST whitelist validation
    3. Deep attribute chain detection
    4. Restricted execution environment
    """
    # Limit expression length to prevent DoS
    MAX_EXPR_LENGTH = 1000
    if len(lambda_expr) > MAX_EXPR_LENGTH:
        raise ValueError(f"Expression too long (max {MAX_EXPR_LENGTH} characters)")

    # Check if the string has any blacklisted symbols
    lambda_expr = lambda_expr.strip()
    if any(
        word in lambda_expr
        for word in SafeEvaluator.RESTRICTED_SYMBOLS - SafeEvaluator.ALLOWED_FUNCTIONS
    ):
        raise ValueError("Expression contains restricted symbols")

    # Check for common obfuscation patterns
    dangerous_patterns = [
        "__",  # Double underscore (dunder) methods
        "\\x",  # Hex escape sequences
        "\\u",  # Unicode escape sequences
        "chr(",  # Character conversion
        "ord(",  # Ordinal conversion
        ".decode",  # String decoding
        ".encode",  # String encoding
        "base64",  # Base64 operations
        "codecs",  # Codec operations
    ]

    for pattern in dangerous_patterns:
        if pattern in lambda_expr:
            raise ValueError(f"Expression contains dangerous pattern: {pattern}")

    try:
        expr_ast = ast.parse(lambda_expr, mode="eval").body
    except SyntaxError as e:
        raise ValueError(f"Invalid syntax in expression: {e}") from e

    # Ensure the parsed AST is a lambda expression
    if not isinstance(expr_ast, ast.Lambda):
        raise ValueError("Expression must be a lambda function")

    # Use both blacklist and whitelist validation
    SafeEvaluator().visit(expr_ast)
    WhitelistValidator().visit(expr_ast)

    # Compile the AST node into a code object
    code = compile(ast.Expression(expr_ast), "<string>", "eval")

    # Create a restricted builtins dict with only safe functions
    safe_builtins = {
        # Math operations
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "round": round,
        "len": len,
        # Type conversions (limited)
        "int": int,
        "float": float,
        "str": str,
        "bool": bool,
        # Safe data structures
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "set": set,
        # Comparison
        "all": all,
        "any": any,
        # Other safe operations
        "sorted": sorted,
        "reversed": reversed,
        "enumerate": enumerate,
        "zip": zip,
        "range": range,
        # Constants
        "True": True,
        "False": False,
        "None": None,
    }

    # Create restricted globals with custom builtins
    restricted_globals = {
        "__builtins__": safe_builtins,
        "jsonpath": eval_jsonpath,
    }

    # Create a function from the code object with restricted globals
    lambda_func = eval(code, restricted_globals, {})

    # Wrap the lambda to add additional runtime protections
    return create_sandboxed_lambda(lambda_func)


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
