"""Safe Python executor for environments without nsjail.

This executor provides security through:
1. AST-based script validation (deny-by-default imports)
2. Subprocess isolation via Python subprocess
3. Import hook injection to block unauthorized modules at runtime
4. No access to os.environ from executed scripts

This is the fallback executor used when nsjail is not available (e.g., when
running without privileged Docker mode).
"""

from __future__ import annotations

import ast
import asyncio
import functools
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tracecat.config import (
    TRACECAT__SANDBOX_CACHE_DIR,
    TRACECAT__SANDBOX_DEFAULT_TIMEOUT,
    TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS,
    TRACECAT__SANDBOX_PYPI_INDEX_URL,
)
from tracecat.expressions.common import eval_jsonpath
from tracecat.logger import logger
from tracecat.sandbox.exceptions import (
    PackageInstallError,
    SandboxExecutionError,
    SandboxTimeoutError,
    SandboxValidationError,
)
from tracecat.sandbox.types import SandboxResult

# =============================================================================
# AST Validators for Safe Expression/Script Evaluation
# =============================================================================


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
        "object",
        "type",
        "__build_class__",
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


# Safe stdlib modules allowed in sandboxed script execution (deny-by-default allowlist)
SAFE_STDLIB_MODULES = frozenset(
    {
        # Data handling
        "json",
        "csv",
        "base64",
        "binascii",
        "hashlib",
        "hmac",
        "secrets",
        # Text processing
        "re",
        "string",
        "textwrap",
        "unicodedata",
        # Date/time
        "datetime",
        "time",
        "calendar",
        "zoneinfo",
        # Math/numbers
        "math",
        "decimal",
        "fractions",
        "random",
        "statistics",
        # Collections/iteration
        "collections",
        "itertools",
        "functools",
        "operator",
        # Data structures
        "copy",
        "pprint",
        "dataclasses",
        "enum",
        "typing",
        # Parsing
        "html",
        "xml",
        "urllib.parse",
        # Compression (read-only operations)
        "gzip",
        "zipfile",
        "zlib",
        # Other safe modules
        "uuid",
        "contextlib",
        "warnings",
        "logging",
        "traceback",
        "abc",
        "numbers",
        "difflib",
        "fnmatch",
        "struct",
        "io",
        "inspect",
    }
)

# Network modules that should be blocked in sandboxed execution
NETWORK_MODULES = frozenset(
    {
        "socket",
        "socketserver",
        "http",
        "http.client",
        "http.server",
        "urllib.request",
        "urllib.error",
        "ftplib",
        "poplib",
        "imaplib",
        "smtplib",
        "telnetlib",
        "ssl",
        "asyncio",
        "aiohttp",
        "requests",
        "httpx",
        "websocket",
        "websockets",
    }
)

# Modules that provide access to os.environ or system operations
SYSTEM_ACCESS_MODULES = frozenset(
    {
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
        "pty",
        "tty",
        "termios",
        "fcntl",
        "pipes",
        "posix",
        "pwd",
        "grp",
        "spwd",
        "crypt",
        "select",
        "selectors",
        "mmap",
        "shutil",
        "tempfile",
        "pathlib",
        "glob",
        "fileinput",
    }
)


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


class ScriptValidator(ast.NodeVisitor):
    """AST validator for full Python scripts using deny-by-default approach.

    This validator checks imports and ensures only safe modules are used:
    - Only allows imports from SAFE_STDLIB_MODULES + user-specified dependencies
    - Blocks all modules in SYSTEM_ACCESS_MODULES (os, sys, subprocess, etc.)
    - Blocks all modules in NETWORK_MODULES unless allow_network=True
    - Blocks access to os.environ

    Unlike SafeEvaluator which uses a blacklist, this uses a strict allowlist
    for imports while still allowing general Python code execution.
    """

    def __init__(
        self,
        allowed_dependencies: set[str] | None = None,
        allow_network: bool = False,
    ):
        """Initialize the validator.

        Args:
            allowed_dependencies: Set of package names that are allowed to be imported.
                These should be base package names (e.g., "requests" not "requests==2.28.0").
            allow_network: If True, network modules are allowed. Note that without
                OS-level isolation, this only controls import validation.
        """
        self.allowed_dependencies = allowed_dependencies or set()
        self.allow_network = allow_network
        self.errors: list[str] = []

    def _get_module_base(self, module_name: str) -> str:
        """Get the base module name (e.g., 'urllib.parse' -> 'urllib')."""
        return module_name.split(".")[0]

    def _is_module_allowed(self, module_name: str) -> bool:
        """Check if a module import is allowed.

        Args:
            module_name: Full module name (e.g., 'urllib.parse', 'requests')

        Returns:
            True if the module is allowed, False otherwise.
        """
        base_module = self._get_module_base(module_name)

        # Always block system access modules
        if module_name in SYSTEM_ACCESS_MODULES or base_module in SYSTEM_ACCESS_MODULES:
            return False

        # Allow user-specified dependencies (check before network modules since
        # packages like 'requests' are both a dependency and a network module)
        if base_module in self.allowed_dependencies:
            return True

        # Check if module is a submodule of an allowed dependency
        for dep in self.allowed_dependencies:
            if module_name.startswith(f"{dep}."):
                return True

        # Check network modules (after dependencies)
        is_network_module = (
            module_name in NETWORK_MODULES or base_module in NETWORK_MODULES
        )
        if is_network_module:
            # Allow network modules only if explicitly enabled
            return self.allow_network

        # Allow safe stdlib modules
        if module_name in SAFE_STDLIB_MODULES or base_module in SAFE_STDLIB_MODULES:
            return True

        return False

    def visit_Import(self, node: ast.Import) -> None:
        """Validate import statements (e.g., 'import os')."""
        for alias in node.names:
            if not self._is_module_allowed(alias.name):
                base = self._get_module_base(alias.name)
                if base in SYSTEM_ACCESS_MODULES or alias.name in SYSTEM_ACCESS_MODULES:
                    self.errors.append(
                        f"Import of system module '{alias.name}' is not allowed. "
                        f"System modules like os, sys, subprocess are blocked for security."
                    )
                elif base in NETWORK_MODULES or alias.name in NETWORK_MODULES:
                    self.errors.append(
                        f"Import of network module '{alias.name}' is not allowed. "
                        f"Network access is disabled in safe execution mode."
                    )
                else:
                    self.errors.append(
                        f"Import of module '{alias.name}' is not allowed. "
                        f"Only safe stdlib modules and declared dependencies are permitted."
                    )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Validate from-import statements (e.g., 'from os import environ')."""
        if node.module:
            if not self._is_module_allowed(node.module):
                base = self._get_module_base(node.module)
                if (
                    base in SYSTEM_ACCESS_MODULES
                    or node.module in SYSTEM_ACCESS_MODULES
                ):
                    self.errors.append(
                        f"Import from system module '{node.module}' is not allowed. "
                        f"System modules like os, sys, subprocess are blocked for security."
                    )
                elif base in NETWORK_MODULES or node.module in NETWORK_MODULES:
                    self.errors.append(
                        f"Import from network module '{node.module}' is not allowed. "
                        f"Network access is disabled in safe execution mode."
                    )
                else:
                    self.errors.append(
                        f"Import from module '{node.module}' is not allowed. "
                        f"Only safe stdlib modules and declared dependencies are permitted."
                    )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Block access to os.environ via attribute access."""
        if isinstance(node.value, ast.Name) and node.value.id == "os":
            if node.attr == "environ":
                self.errors.append(
                    "Access to 'os.environ' is not allowed. "
                    "Use the env_vars parameter to inject environment variables."
                )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        """Block access to os.environ["KEY"] via subscript."""
        if isinstance(node.value, ast.Attribute):
            if isinstance(node.value.value, ast.Name) and node.value.value.id == "os":
                if node.value.attr == "environ":
                    self.errors.append(
                        "Access to 'os.environ' is not allowed. "
                        "Use the env_vars parameter to inject environment variables."
                    )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Block calls to __import__ which bypass import statement validation.

        The AST validator only checks ast.Import and ast.ImportFrom nodes,
        but __import__('os') is an ast.Call node that would bypass validation.
        This method blocks both direct __import__ calls and builtins.__import__.
        """
        # Block __import__('module')
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            self.errors.append(
                "Direct calls to __import__ are not allowed. "
                "Use regular import statements instead."
            )
        # Block builtins.__import__('module') and similar attribute access
        if isinstance(node.func, ast.Attribute) and node.func.attr == "__import__":
            self.errors.append(
                "Direct calls to __import__ are not allowed. "
                "Use regular import statements instead."
            )
        self.generic_visit(node)

    def validate(self, script: str) -> list[str]:
        """Validate a script and return list of errors.

        Args:
            script: Python source code to validate.

        Returns:
            List of error messages. Empty list if script is valid.
        """
        self.errors = []
        try:
            tree = ast.parse(script)
            self.visit(tree)
        except SyntaxError as e:
            self.errors.append(f"Syntax error in script: {e}")
        return self.errors


# =============================================================================
# Safe Lambda Building and Sandboxed Execution
# =============================================================================


def sandbox_lambda(func: Callable[[Any], Any]) -> Callable[[Any], Any]:
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
    return sandbox_lambda(lambda_func)


# =============================================================================
# Safe Python Executor Templates and Implementation
# =============================================================================


# Template for the import hook that restricts module access at runtime
# This provides defense-in-depth against dynamic imports not caught by AST validation
# NOTE: We only block direct user imports, not transitive imports from stdlib modules
IMPORT_HOOK_TEMPLATE = '''
import sys
import builtins

_ALLOWED_MODULES = {allowed_modules!r}
_BLOCKED_MODULES = {blocked_modules!r}
_SAFE_STDLIB = {safe_stdlib!r}
_INTERNAL_MODULES = frozenset({{
    # Python internals that are needed transitively (underscore-prefixed)
    "_ast", "_bootstrap", "_bootstrap_external", "_collections_abc",
    "_frozen_importlib", "_frozen_importlib_external", "_imp", "_io",
    "_opcode", "_opcode_metadata", "_tokenize", "_weakref", "_weakrefset",
    "_codecs", "_locale", "_stat", "_posixsubprocess", "_signal", "_thread",
    "_sre", "_functools", "_operator", "_abc", "_struct", "_warnings",
    "_string", "_random", "_sha256", "_hashlib", "_blake2", "_sha512",
    "_sha1", "_md5", "_sha3", "_decimal", "_datetime", "_bisect", "_heapq",
    "_pickle", "_json", "_lsprof", "_contextvars", "_asyncio", "_queue",
    "_multibytecodec", "_codecs_cn", "_codecs_hk", "_codecs_iso2022",
    "_codecs_jp", "_codecs_kr", "_codecs_tw",
    # Non-underscore modules that are safe and needed for path operations
    # NOTE: os, sys, and other dangerous modules are NOT included here.
    # They are imported during wrapper init (before _wrapper_initialized=True)
    # and cached in sys.modules. User code cannot re-import them because
    # they are in _BLOCKED_MODULES which is checked first.
    "encodings", "posixpath", "genericpath", "ntpath", "stat",
    "linecache", "tokenize", "token", "keyword", "abc",
}})

_original_import = builtins.__import__
_wrapper_initialized = False

def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Import hook that validates user imports.

    This hook only blocks imports that appear to be direct user imports,
    not transitive imports from allowed stdlib modules.
    """
    global _wrapper_initialized
    base_module = name.split(".")[0]

    # Allow all imports during wrapper initialization (before user script runs)
    if not _wrapper_initialized:
        return _original_import(name, globals, locals, fromlist, level)

    # Always allow Python internals (underscore-prefixed)
    if name.startswith("_") or base_module.startswith("_"):
        return _original_import(name, globals, locals, fromlist, level)

    # CHECK BLOCKED MODULES FIRST - before any allowlists
    # This ensures os, sys, etc. are blocked even if they appear in other sets
    if name in _BLOCKED_MODULES or base_module in _BLOCKED_MODULES:
        raise ImportError(
            f"Import of module '{{name}}' is blocked for security reasons."
        )

    # Allow internal modules (now safe since dangerous ones blocked above)
    if name in _INTERNAL_MODULES or base_module in _INTERNAL_MODULES:
        return _original_import(name, globals, locals, fromlist, level)

    # Allow safe stdlib modules and their transitive dependencies
    if name in _SAFE_STDLIB or base_module in _SAFE_STDLIB:
        return _original_import(name, globals, locals, fromlist, level)

    # Allow explicitly permitted modules (dependencies)
    if name in _ALLOWED_MODULES or base_module in _ALLOWED_MODULES:
        return _original_import(name, globals, locals, fromlist, level)

    # Allow submodules of allowed modules
    for allowed in _ALLOWED_MODULES:
        if name.startswith(f"{{allowed}}."):
            return _original_import(name, globals, locals, fromlist, level)

    # Block unknown modules - anything not in allowlists is rejected
    raise ImportError(
        f"Import of module '{{name}}' is not allowed. "
        f"Only safe stdlib modules and declared dependencies are permitted."
    )

builtins.__import__ = _restricted_import
'''

# Wrapper script for safe execution
SAFE_WRAPPER_SCRIPT = '''
import inspect
import json
import sys
import traceback
from pathlib import Path

def main():
    """Execute user script and capture results."""
    work_dir = "{work_dir}"

    # Read inputs from file
    inputs_path = Path(work_dir) / "inputs.json"
    if inputs_path.exists():
        inputs = json.loads(inputs_path.read_text())
    else:
        inputs = {{}}

    result = {{
        "success": False,
        "output": None,
        "error": None,
        "traceback": None,
        "stdout": "",
        "stderr": "",
    }}

    # Capture stdout/stderr
    import io
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        # Read and execute the user script
        script_path = Path(work_dir) / "script.py"
        script_code = script_path.read_text()

        # Enable import restrictions for user code
        global _wrapper_initialized
        _wrapper_initialized = True

        script_globals = {{"__name__": "__main__"}}
        exec(script_code, script_globals)

        # Find the callable function
        main_func = script_globals.get("main")
        if main_func is None:
            for name, obj in script_globals.items():
                if inspect.isfunction(obj) and not name.startswith("_"):
                    main_func = obj
                    break

        if main_func is None:
            raise ValueError("No callable function found in script")

        # Call the function with inputs
        if inputs:
            output = main_func(**inputs)
        else:
            output = main_func()

        result["success"] = True
        result["output"] = output

    except Exception as e:
        result["error"] = f"{{type(e).__name__}}: {{e}}"
        result["traceback"] = traceback.format_exc()

    finally:
        result["stdout"] = sys.stdout.getvalue()
        result["stderr"] = sys.stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # Write result to file
    result_path = Path(work_dir) / "result.json"
    try:
        result_path.write_text(json.dumps(result))
    except (TypeError, ValueError):
        # Output not JSON-serializable, convert to repr
        result["output"] = repr(result["output"])
        try:
            result_path.write_text(json.dumps(result))
        except Exception as e:
            result["output"] = None
            result["error"] = f"Output not JSON-serializable: {{type(e).__name__}}: {{e}}"
            result["success"] = False
            result_path.write_text(json.dumps(result))

    sys.exit(0 if result["success"] else 1)

if __name__ == "__main__":
    main()
'''


def _extract_package_name(dependency: str) -> str:
    """Extract base package name from dependency spec.

    Examples:
        "requests==2.28.0" -> "requests"
        "py-ocsf-models>=0.8.0" -> "py_ocsf_models" (normalized)
        "openpyxl[lxml]" -> "openpyxl"
    """
    # Remove version specifiers
    for sep in ("==", ">=", "<=", ">", "<", "~=", "!=", "["):
        if sep in dependency:
            dependency = dependency.split(sep)[0]

    # Normalize package name (replace - with _)
    return dependency.strip().replace("-", "_")


class SafePythonExecutor:
    """Executor for Python scripts without nsjail, using subprocess isolation.

    Security is provided through:
    1. AST-based validation before execution (deny-by-default imports)
    2. Runtime import hooks to block unauthorized modules
    3. Isolated virtual environment per dependency set
    4. No access to os.environ from scripts (blocked at import and runtime level)

    Limitations compared to nsjail:
    - No OS-level network isolation
    - No filesystem isolation (scripts can read files)
    - No resource limits (memory, CPU)
    - No process isolation
    """

    def __init__(
        self,
        cache_dir: str = TRACECAT__SANDBOX_CACHE_DIR,
    ):
        self.cache_dir = Path(cache_dir)
        self.package_cache = self.cache_dir / "safe-packages"
        self.uv_cache = self.cache_dir / "uv-cache"

        # Ensure cache directories exist
        self.package_cache.mkdir(parents=True, exist_ok=True)
        self.uv_cache.mkdir(parents=True, exist_ok=True)

    def _compute_cache_key(
        self,
        dependencies: list[str],
        workspace_id: str | None = None,
    ) -> str:
        """Compute a cache key for the virtual environment.

        Args:
            dependencies: List of package specifications.
            workspace_id: Optional workspace ID for multi-tenant isolation.

        Returns:
            16-character hexadecimal cache key.
        """
        normalized = sorted(dep.lower().strip() for dep in dependencies)
        if workspace_id:
            hash_input = f"{workspace_id}\n" + "\n".join(normalized)
        else:
            hash_input = "\n".join(normalized)
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    def _get_allowed_modules(self, dependencies: list[str]) -> set[str]:
        """Get the set of allowed modules for import hook.

        Args:
            dependencies: List of package specifications.

        Returns:
            Set of module names allowed to be imported.
        """
        allowed: set[str] = set()

        # Add dependency base names
        for dep in dependencies:
            dep_name = _extract_package_name(dep)
            allowed.add(dep_name)
            # Also add original name (with hyphens) in case import differs
            original = (
                dep.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].strip()
            )
            allowed.add(original)

        return allowed

    def _validate_script(
        self,
        script: str,
        dependencies: list[str] | None,
        allow_network: bool,
    ) -> None:
        """Validate script using AST analysis.

        Args:
            script: Python source code to validate.
            dependencies: List of allowed dependencies.
            allow_network: Whether network modules are allowed.

        Raises:
            SandboxValidationError: If script fails validation.
        """
        dep_set: set[str] = set()
        if dependencies:
            for dep in dependencies:
                dep_name = _extract_package_name(dep)
                dep_set.add(dep_name)
                # Also add original hyphenated name
                original = (
                    dep.split("==")[0]
                    .split(">=")[0]
                    .split("<=")[0]
                    .split("[")[0]
                    .strip()
                )
                dep_set.add(original)

        validator = ScriptValidator(
            allowed_dependencies=dep_set,
            allow_network=allow_network,
        )
        errors = validator.validate(script)

        if errors:
            raise SandboxValidationError(
                "Script validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )

    async def _create_venv(self, venv_path: Path) -> None:
        """Create a virtual environment using uv.

        Args:
            venv_path: Path where the venv should be created.

        Raises:
            PackageInstallError: If venv creation fails.
        """
        create_cmd = ["uv", "venv", str(venv_path), "--python", "3.12"]

        logger.debug("Creating virtual environment", venv_path=str(venv_path))

        process = await asyncio.create_subprocess_exec(
            *create_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", "/tmp"),
                "UV_CACHE_DIR": str(self.uv_cache),
            },
        )

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=60,
            )
        except TimeoutError as e:
            process.kill()
            raise PackageInstallError("Virtual environment creation timed out") from e

        if process.returncode != 0:
            raise PackageInstallError(
                f"Failed to create virtual environment: {stderr.decode()}"
            )

    async def _install_packages(
        self,
        venv_path: Path,
        dependencies: list[str],
        timeout_seconds: int = 300,
    ) -> None:
        """Install packages in an isolated virtual environment.

        Args:
            venv_path: Path to the virtual environment.
            dependencies: List of packages to install.
            timeout_seconds: Maximum installation time.

        Raises:
            PackageInstallError: If package installation fails.
        """
        # Build pip install command
        pip_cmd = [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv_path / "bin" / "python"),
        ]

        # Add index URLs
        if TRACECAT__SANDBOX_PYPI_INDEX_URL:
            pip_cmd.extend(["--index-url", TRACECAT__SANDBOX_PYPI_INDEX_URL])

        for extra_url in TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS:
            pip_cmd.extend(["--extra-index-url", extra_url])

        pip_cmd.extend(dependencies)

        logger.info(
            "Installing packages",
            dependencies=dependencies,
            venv_path=str(venv_path),
        )

        process = await asyncio.create_subprocess_exec(
            *pip_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", "/tmp"),
                "UV_CACHE_DIR": str(self.uv_cache),
            },
        )

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError as e:
            process.kill()
            raise PackageInstallError(
                f"Package installation timed out after {timeout_seconds}s"
            ) from e

        if process.returncode != 0:
            raise PackageInstallError(f"Failed to install packages: {stderr.decode()}")

        logger.info("Packages installed successfully", venv_path=str(venv_path))

    async def execute(
        self,
        script: str,
        inputs: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,
        timeout_seconds: int | None = None,
        allow_network: bool = False,
        env_vars: dict[str, str] | None = None,
        workspace_id: str | None = None,
    ) -> SandboxResult:
        """Execute a Python script with subprocess isolation.

        Args:
            script: Python script content.
            inputs: Input data for the main function.
            dependencies: Pip packages to install.
            timeout_seconds: Maximum execution time.
            allow_network: Whether network modules are allowed at import level.
                NOTE: This only controls import validation. Without OS-level
                isolation, network access cannot be truly blocked at runtime.
            env_vars: Environment variables to inject into the script.
            workspace_id: Workspace ID for cache isolation.

        Returns:
            SandboxResult with execution outcome.
        """
        if timeout_seconds is None:
            timeout_seconds = TRACECAT__SANDBOX_DEFAULT_TIMEOUT

        start_time = time.time()

        # Log warning about allow_network limitation
        if allow_network:
            logger.warning(
                "allow_network=True has limited effect without nsjail. "
                "Network modules are allowed at import level, but there is "
                "no OS-level network isolation. For full network isolation, "
                "enable nsjail by setting TRACECAT__DISABLE_NSJAIL=false."
            )

        # Validate script before execution
        self._validate_script(script, dependencies, allow_network)

        # Create temporary working directory
        work_dir = Path(tempfile.mkdtemp(prefix="safe-sandbox-"))

        try:
            # Prepare virtual environment with dependencies
            python_path = shutil.which("python3") or "python3"

            if dependencies:
                cache_key = self._compute_cache_key(dependencies, workspace_id)
                cached_venv = self.package_cache / cache_key

                # Fast path: venv already exists and is valid
                if cached_venv.exists() and (cached_venv / "bin" / "python").exists():
                    logger.debug("Using cached venv", cache_key=cache_key)
                else:
                    # Slow path: create venv atomically using temp directory
                    # This prevents race conditions when multiple concurrent requests
                    # try to create the same venv.
                    logger.info(
                        "Cache miss, creating venv with packages",
                        cache_key=cache_key,
                        dependencies=dependencies,
                    )
                    temp_venv = self.package_cache / f"{cache_key}.{os.getpid()}.tmp"

                    try:
                        # Clean up any stale temp dir from previous failed attempt
                        if temp_venv.exists():
                            shutil.rmtree(temp_venv, ignore_errors=True)

                        await self._create_venv(temp_venv)
                        await self._install_packages(
                            temp_venv,
                            dependencies,
                            timeout_seconds=timeout_seconds,
                        )

                        # Atomic rename into place. os.rename is atomic on the same
                        # filesystem. If another process beat us, this will fail.
                        try:
                            os.rename(temp_venv, cached_venv)
                            logger.info("Venv cached", cache_key=cache_key)
                        except OSError:
                            # Another process won the race - use theirs
                            logger.debug(
                                "Venv cache race: using existing venv",
                                cache_key=cache_key,
                            )
                    finally:
                        # Clean up temp dir if it still exists
                        if temp_venv.exists():
                            shutil.rmtree(temp_venv, ignore_errors=True)

                python_path = str(cached_venv / "bin" / "python")

            # Write script files
            (work_dir / "script.py").write_text(script)
            (work_dir / "inputs.json").write_text(json.dumps(inputs or {}))

            # Generate import hook with allowed modules
            allowed_modules = self._get_allowed_modules(dependencies or [])
            blocked_modules = set(SYSTEM_ACCESS_MODULES)
            if not allow_network:
                blocked_modules.update(NETWORK_MODULES)

            import_hook = IMPORT_HOOK_TEMPLATE.format(
                allowed_modules=allowed_modules,
                blocked_modules=blocked_modules,
                safe_stdlib=set(SAFE_STDLIB_MODULES),
            )

            # Generate wrapper with import hook prepended
            wrapper = (
                import_hook
                + "\n"
                + SAFE_WRAPPER_SCRIPT.format(
                    work_dir=str(work_dir),
                )
            )
            wrapper_path = work_dir / "wrapper.py"
            wrapper_path.write_text(wrapper)

            # Build execution environment
            exec_env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", "/tmp"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            }

            # Add user-provided env vars (these are intentionally allowed)
            if env_vars:
                exec_env.update(env_vars)

            # Execute script
            cmd = [python_path, str(wrapper_path)]

            logger.debug(
                "Executing safe sandbox",
                cmd=cmd,
                work_dir=str(work_dir),
            )

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
                env=exec_env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_seconds,
                )
            except TimeoutError as e:
                process.kill()
                await process.wait()
                raise SandboxTimeoutError(
                    f"Script execution timed out after {timeout_seconds}s"
                ) from e

            execution_time_ms = (time.time() - start_time) * 1000
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            # Parse result
            result_path = work_dir / "result.json"
            if result_path.exists():
                try:
                    result_data = json.loads(result_path.read_text())
                    return SandboxResult(
                        success=result_data.get("success", False),
                        output=result_data.get("output"),
                        stdout=result_data.get("stdout", stdout),
                        stderr=result_data.get("stderr", stderr),
                        error=result_data.get("error"),
                        exit_code=process.returncode,
                        execution_time_ms=execution_time_ms,
                    )
                except json.JSONDecodeError:
                    logger.warning("Failed to parse result.json")

            # No result.json - execution failed before writing result
            return SandboxResult(
                success=False,
                error=f"Execution failed: {stderr[:500] if stderr else 'Unknown error'}",
                stdout=stdout,
                stderr=stderr[:500] if stderr else "",
                exit_code=process.returncode,
                execution_time_ms=execution_time_ms,
            )

        except (SandboxValidationError, SandboxTimeoutError, PackageInstallError):
            # Re-raise sandbox-specific exceptions
            raise
        except Exception as e:
            # Wrap unexpected errors
            logger.exception("Unexpected error in safe executor")
            raise SandboxExecutionError(
                f"Unexpected error: {type(e).__name__}: {e}"
            ) from e
        finally:
            # Clean up work directory
            shutil.rmtree(work_dir, ignore_errors=True)
