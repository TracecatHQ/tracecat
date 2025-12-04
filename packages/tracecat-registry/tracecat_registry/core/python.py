import re
from typing import Annotated, Any

from tracecat.contexts import ctx_role
from tracecat.logger import logger
from tracecat.registry.fields import Code
from tracecat_registry import registry
from typing_extensions import Doc


class PythonScriptError(Exception):
    """Base exception for Python script execution errors."""


class PythonScriptTimeoutError(PythonScriptError):
    """Exception raised when a Python script execution times out."""


class PythonScriptValidationError(PythonScriptError):
    """Exception raised when a Python script fails validation."""


class PythonScriptExecutionError(PythonScriptError):
    """Exception raised when a Python script fails during execution."""


def _validate_script(script: str) -> tuple[bool, str | None]:
    """
    Validates that the script contains at least one function, and if there are multiple functions,
    one must be named 'main'.

    Returns a tuple of (is_valid, error_message)
    """
    # Simple regex to find function definitions
    function_pattern = r"def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
    functions = re.findall(function_pattern, script)

    if not functions:
        return False, "Script must contain at least one function definition."

    if len(functions) > 1 and "main" not in functions:
        return (
            False,
            "When script contains multiple functions, one must be named 'main'.",
        )

    return True, None


@registry.register(
    default_title="Run Python script",
    description="Execute a Python script in a secure nsjail sandbox with pip package support.",
    display_group="Run script",
    namespace="core.script",
)
async def run_python(
    script: Annotated[
        str,
        Doc(
            "Python script to execute. Must contain at least one function. "
            "If multiple functions are defined, one must be named 'main'. "
            "Returns the output of the function. "
        ),
        Code(lang="python"),
    ],
    inputs: Annotated[
        dict[str, Any] | None,
        Doc(
            "Input data passed as function arguments to the main function. "
            "Keys must match the parameter names in the function signature. "
            "Missing parameters will receive `None`."
        ),
    ] = None,
    dependencies: Annotated[
        list[str] | None,
        Doc(
            "Optional list of Python package dependencies to install via pip. "
            "Packages are cached between executions for performance."
        ),
    ] = None,
    timeout_seconds: Annotated[
        int,
        Doc("Maximum execution time in seconds. Default is 300 seconds (5 minutes)."),
    ] = 300,
    allow_network: Annotated[
        bool,
        Doc(
            "Whether to allow network access during script execution. "
            "Default is False. Note: package installation always has network access."
        ),
    ] = False,
    env_vars: Annotated[
        dict[str, str] | None,
        Doc(
            "Environment variables to set in the sandbox. "
            "Use this to inject secrets or configuration."
        ),
    ] = None,
) -> Any:
    """
    Executes a Python script in a secure nsjail sandbox.

    The code is executed in an isolated Linux namespace with:
    - Configurable network access (disabled by default)
    - Resource limits (memory, CPU, file size)
    - Read-only rootfs with minimal Python 3.12 + uv environment
    - Full subprocess.run support for running external commands

    The script must contain at least one function. If multiple functions are defined,
    one must be named 'main', which will be called. If only one function is defined,
    it will be called.

    The input 'inputs' dictionary's items are passed as function arguments to the main function.
    The function's return value is the output of this operation.

    Args:
        script: The Python script content with at least one function definition.
        inputs: A dictionary of input data, passed as function arguments to the main function.
        dependencies: A list of pip packages to install before execution.
        timeout_seconds: Maximum allowed execution time for the script.
        allow_network: Whether to allow network access during script execution.
        env_vars: Environment variables to set in the sandbox.

    Returns:
        The result of the function call.

    Raises:
        PythonScriptValidationError: If script doesn't meet the requirements.
        PythonScriptTimeoutError: If script execution times out.
        PythonScriptExecutionError: If script execution fails.
    """
    # Validate script
    is_valid, error_message = _validate_script(script)
    if not is_valid:
        assert error_message is not None  # Should never be None when is_valid is False
        logger.error(f"Script validation failed: {error_message}")
        raise PythonScriptValidationError(error_message)

    from tracecat.sandbox import (
        PackageInstallError,
        SandboxExecutionError,
        SandboxService,
        SandboxTimeoutError,
        SandboxValidationError,
    )

    try:
        service = SandboxService()
        # Get workspace_id from execution context for multi-tenant cache isolation
        role = ctx_role.get()
        workspace_id = str(role.workspace_id) if role and role.workspace_id else None

        return await service.run_python(
            script=script,
            inputs=inputs,
            dependencies=dependencies,
            timeout_seconds=timeout_seconds,
            allow_network=allow_network,
            env_vars=env_vars,
            workspace_id=workspace_id,
        )
    except SandboxTimeoutError as e:
        raise PythonScriptTimeoutError(str(e)) from e
    except SandboxValidationError as e:
        # Validation errors (e.g., invalid env var keys) should be reported
        # as validation errors, not execution errors
        raise PythonScriptValidationError(str(e)) from e
    except (SandboxExecutionError, PackageInstallError) as e:
        raise PythonScriptExecutionError(str(e)) from e
