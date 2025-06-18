import ast
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


class SafeEvaluator(ast.NodeVisitor):
    # Restricted AST node types that can lead to code injection
    RESTRICTED_NODES = {
        ast.Import,
        ast.ImportFrom,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Global,
        ast.Nonlocal,
        ast.With,
        ast.AsyncWith,
        ast.Raise,
        ast.Try,
        ast.ExceptHandler,
        ast.Assert,
        ast.Delete,
        ast.AugAssign,  # +=, -=, etc.
        ast.AnnAssign,  # type annotations with assignment
    }

    # Restricted symbols that should never appear in lambda expressions
    RESTRICTED_SYMBOLS = {
        "eval",
        "exec",
        "compile",
        "open",
        "file",
        "input",
        "raw_input",
        "import",
        "from",
        "__import__",
        "reload",
        "os",
        "sys",
        "subprocess",
        "platform",
        "socket",
        "urllib",
        "locals",
        "globals",
        "vars",
        "dir",
        "help",
        "memoryview",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "callable",
        "isinstance",
        "issubclass",
        "super",
        "__builtins__",
        "__builtin__",
        "builtins",
        "type",
        "object",
        "property",
        "staticmethod",
        "classmethod",
        "exit",
        "quit",
        "license",
        "credits",
        "copyright",
    }

    # Restricted magic methods and attributes
    RESTRICTED_ATTRIBUTES = {
        "__class__",
        "__bases__",
        "__subclasses__",
        "__mro__",
        "__globals__",
        "__locals__",
        "__code__",
        "__func__",
        "__self__",
        "__module__",
        "__dict__",
        "__doc__",
        "__name__",
        "__qualname__",
        "__annotations__",
        "__builtins__",
        "__import__",
        "__build_class__",
        "__metaclass__",
        "__prepare__",
        "__instancecheck__",
        "__subclasscheck__",
        "__call__",
        "__new__",
        "__init__",
        "__del__",
        "__repr__",
        "__str__",
        "__bytes__",
        "__format__",
        "__lt__",
        "__le__",
        "__eq__",
        "__ne__",
        "__gt__",
        "__ge__",
        "__hash__",
        "__bool__",
        "__sizeof__",
        "__getattr__",
        "__getattribute__",
        "__setattr__",
        "__delattr__",
        "__dir__",
        "__get__",
        "__set__",
        "__delete__",
        "__set_name__",
        "__init_subclass__",
        "__class_getitem__",
    }

    ALLOWED_FUNCTIONS = {"jsonpath"}

    # Allowed built-in functions for data manipulation
    SAFE_BUILTINS = {
        "abs",
        "all",
        "any",
        "bin",
        "bool",
        "chr",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "format",
        "frozenset",
        "hex",
        "int",
        "len",
        "list",
        "map",
        "max",
        "min",
        "oct",
        "ord",
        "pow",
        "range",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
    }

    def visit(self, node):
        # Check for restricted node types
        if type(node) in self.RESTRICTED_NODES:
            raise ValueError(
                f"Restricted AST node {type(node).__name__} detected in lambda expression"
            )

        # Check for assignment operations (even simple ones)
        if isinstance(node, ast.Assign):
            raise ValueError(
                "Assignment operations are not allowed in lambda expressions"
            )

        # Check for attribute access to restricted attributes
        if isinstance(node, ast.Attribute):
            attr_name = node.attr
            if attr_name in self.RESTRICTED_ATTRIBUTES:
                raise ValueError(
                    f"Access to restricted attribute '{attr_name}' is not allowed"
                )

            # Block access to any dunder methods not explicitly allowed
            if attr_name.startswith("__") and attr_name.endswith("__"):
                raise ValueError(f"Access to magic method '{attr_name}' is not allowed")

        # Check for function calls
        if isinstance(node, ast.Call):
            # Handle direct function calls by name
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
                if func_name in self.RESTRICTED_SYMBOLS:
                    if func_name not in self.ALLOWED_FUNCTIONS:
                        raise ValueError(
                            f"Calling restricted function '{func_name}' is not allowed"
                        )
                # Only allow safe built-ins
                elif (
                    func_name not in self.SAFE_BUILTINS
                    and func_name not in self.ALLOWED_FUNCTIONS
                ):
                    # Allow if it's a method call on the lambda parameter
                    pass

            # Handle method calls (obj.method())
            elif isinstance(node.func, ast.Attribute):
                attr_name = node.func.attr
                if attr_name in self.RESTRICTED_SYMBOLS:
                    raise ValueError(
                        f"Calling restricted method '{attr_name}' is not allowed"
                    )
                if attr_name in self.RESTRICTED_ATTRIBUTES:
                    raise ValueError(
                        f"Calling restricted attribute '{attr_name}' is not allowed"
                    )

        # Check for name access to restricted symbols
        if isinstance(node, ast.Name):
            name = node.id
            if name in self.RESTRICTED_SYMBOLS and name not in self.ALLOWED_FUNCTIONS:
                raise ValueError(f"Access to restricted name '{name}' is not allowed")

        # Check for subscript operations that might access dangerous items
        if isinstance(node, ast.Subscript):
            # Allow normal list/dict access but be cautious about complex expressions
            pass

        # Recursively visit child nodes
        self.generic_visit(node)


def _expr_with_context(expr: str, context_type: ExprContext | None) -> str:
    return f"{context_type}.{expr}" if context_type else expr


def build_safe_lambda(lambda_expr: str) -> Callable[[Any], Any]:
    """Build a safe lambda function from a string expression with enhanced security."""

    # Input validation
    if not lambda_expr or not isinstance(lambda_expr, str):
        raise ValueError("Lambda expression must be a non-empty string")

    lambda_expr = lambda_expr.strip()

    # Length limit to prevent DoS
    if len(lambda_expr) > 1000:
        raise ValueError("Lambda expression too long (max 1000 characters)")

    # Check for null bytes
    if "\x00" in lambda_expr:
        raise ValueError("Lambda expression contains null bytes")

    # Basic pattern matching for dangerous constructs
    dangerous_patterns = [
        r"__\w+__",  # Most dunder methods
        r"getattr\s*\(",
        r"setattr\s*\(",
        r"hasattr\s*\(",
        r"delattr\s*\(",
        r"__import__\s*\(",
        r"exec\s*\(",
        r"eval\s*\(",
        r"compile\s*\(",
        r"open\s*\(",
        r"file\s*\(",
        r"input\s*\(",
        r"\.system\s*\(",
        r"\.popen\s*\(",
        r"\.call\s*\(",
        r"\.run\s*\(",
        r"\.mro\s*\(",  # Method resolution order - dangerous for escapes
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, lambda_expr, re.IGNORECASE):
            raise ValueError(f"Lambda expression contains dangerous pattern: {pattern}")

    # Check for restricted symbols using simple string matching
    lambda_lower = lambda_expr.lower()
    for symbol in SafeEvaluator.RESTRICTED_SYMBOLS - SafeEvaluator.ALLOWED_FUNCTIONS:
        # Use word boundaries to avoid false positives
        if re.search(r"\b" + re.escape(symbol.lower()) + r"\b", lambda_lower):
            raise ValueError(f"Lambda expression contains restricted symbol: {symbol}")

    # Parse the AST
    try:
        expr_ast = ast.parse(lambda_expr, mode="eval").body
    except SyntaxError as e:
        raise ValueError(f"Invalid lambda syntax: {e}") from e

    # Ensure the parsed AST is a lambda expression
    if not isinstance(expr_ast, ast.Lambda):
        raise ValueError("Expression must be a lambda function")

    # Additional lambda-specific validations
    if len(expr_ast.args.args) == 0:
        raise ValueError("Lambda must have at least one parameter")

    if len(expr_ast.args.args) > 3:
        raise ValueError("Lambda cannot have more than 3 parameters")

    # Check for complex argument patterns that might be dangerous
    for arg in expr_ast.args.args:
        if not isinstance(arg, ast.arg):
            raise ValueError("Lambda arguments must be simple names")

    # Ensure the expression complies with the enhanced SafeEvaluator
    try:
        SafeEvaluator().visit(expr_ast)
    except ValueError as e:
        raise ValueError(f"Security validation failed: {e}") from e

    # Create a wrapper for jsonpath that matches expected lambda usage
    def safe_jsonpath_wrapper(expr: str, operand: Any) -> Any:
        return eval_jsonpath(expr, operand)

    # Create a restricted execution environment
    safe_globals = {
        # Only allow specific safe functions
        "jsonpath": safe_jsonpath_wrapper,
        # Safe built-ins
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "chr": chr,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "range": range,
        "reversed": reversed,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
        # Explicitly block dangerous built-ins
        "__builtins__": {},
        "__name__": None,
        "__file__": None,
        "__package__": None,
    }

    # Compile the AST node into a code object
    try:
        code = compile(ast.Expression(expr_ast), "<lambda>", "eval")
    except Exception as e:
        raise ValueError(f"Failed to compile lambda: {e}") from e

    # Create a function from the code object with restricted globals
    try:
        lambda_func = eval(code, safe_globals, {})
    except Exception as e:
        raise ValueError(f"Failed to create lambda function: {e}") from e

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
