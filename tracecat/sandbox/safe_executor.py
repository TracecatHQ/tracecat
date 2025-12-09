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
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from tracecat.config import (
    TRACECAT__SANDBOX_CACHE_DIR,
    TRACECAT__SANDBOX_DEFAULT_TIMEOUT,
    TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS,
    TRACECAT__SANDBOX_PYPI_INDEX_URL,
)
from tracecat.logger import logger
from tracecat.sandbox.exceptions import (
    PackageInstallError,
    SandboxExecutionError,
    SandboxTimeoutError,
    SandboxValidationError,
)
from tracecat.sandbox.types import SandboxResult

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
        # NOTE: 'inspect' is intentionally NOT included here due to security risks.
        # inspect.currentframe().f_back.f_globals allows sandbox escape via frame
        # introspection. It IS blocked in SafeEvaluator.RESTRICTED_SYMBOLS for lambdas.
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
# Safe Python Executor Templates and Implementation
# =============================================================================


# Template for the import hook that restricts module access at runtime
# This provides defense-in-depth against dynamic imports not caught by AST validation
# NOTE: We only block direct user imports, not transitive imports from allowed packages
IMPORT_HOOK_TEMPLATE = '''
import sys
import builtins
import os.path

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
_user_script_path = None  # Set by wrapper before executing user script

def _is_import_from_user_script(globals_dict):
    """Check if an import originates from the user script (not from a package).

    Returns True if the import is from the user script or unknown origin,
    meaning we should apply strict validation.
    Returns False if the import is from site-packages (a package's internal import),
    meaning we can be more permissive.
    """
    if globals_dict is None:
        return True  # Be conservative if we can't determine origin

    # Check __file__ to see where the import is coming from
    caller_file = globals_dict.get("__file__")
    if caller_file is None:
        # No file info - could be interactive or dynamic, be conservative
        return True

    # If the import comes from site-packages, it's a package's internal import
    # Allow these (except for blocked modules which are checked separately)
    if "site-packages" in caller_file:
        return False

    # If the import comes from the user script, apply strict validation
    if _user_script_path and os.path.samefile(caller_file, _user_script_path):
        return True

    # For any other location (e.g., stdlib), allow the import
    # This handles cases where stdlib modules import other stdlib modules
    return False

def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Import hook that validates user imports.

    This hook distinguishes between:
    1. Direct imports from user script -> strict validation (blocked modules + allowlist)
    2. Transitive imports from packages -> permissive (allow all, packages are trusted)

    Security model:
    - User scripts cannot directly import os, sys, subprocess, etc.
    - But packages like numpy CAN use os internally - they're trusted installed code
    - The user declared the dependency, so we trust the package's internal imports
    """
    global _wrapper_initialized
    base_module = name.split(".")[0]

    # Allow all imports during wrapper initialization (before user script runs)
    if not _wrapper_initialized:
        return _original_import(name, globals, locals, fromlist, level)

    # Allow Python internals (underscore-prefixed) - these are needed for basic operation
    if name.startswith("_") or base_module.startswith("_"):
        return _original_import(name, globals, locals, fromlist, level)

    # Allow internal modules needed for Python operation
    if name in _INTERNAL_MODULES or base_module in _INTERNAL_MODULES:
        return _original_import(name, globals, locals, fromlist, level)

    # CHECK IMPORT ORIGIN FIRST - this determines which validation rules apply
    from_user_script = _is_import_from_user_script(globals)

    if not from_user_script:
        # Import is from an installed package (site-packages)
        # Allow all imports - packages are trusted code that may need os, sys, etc.
        # The user explicitly declared this dependency, so we trust its internals
        return _original_import(name, globals, locals, fromlist, level)

    # === FROM HERE ON, WE'RE VALIDATING DIRECT USER SCRIPT IMPORTS ===
    # Apply strict validation: blocked modules + allowlist

    # Block dangerous modules from user script
    if name in _BLOCKED_MODULES or base_module in _BLOCKED_MODULES:
        raise ImportError(
            f"Import of module '{{name}}' is blocked for security reasons. "
            f"User scripts cannot directly import system modules."
        )

    # Allow safe stdlib modules
    if name in _SAFE_STDLIB or base_module in _SAFE_STDLIB:
        return _original_import(name, globals, locals, fromlist, level)

    # Allow explicitly permitted modules (dependencies)
    if name in _ALLOWED_MODULES or base_module in _ALLOWED_MODULES:
        return _original_import(name, globals, locals, fromlist, level)

    # Allow submodules of allowed modules
    for allowed in _ALLOWED_MODULES:
        if name.startswith(f"{{allowed}}."):
            return _original_import(name, globals, locals, fromlist, level)

    # Block unknown modules from user script
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

        # Set the user script path for the import hook to identify user imports
        global _wrapper_initialized, _user_script_path
        _user_script_path = str(script_path.resolve())
        _wrapper_initialized = True

        script_globals = {{"__name__": "__main__", "__file__": _user_script_path}}
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
