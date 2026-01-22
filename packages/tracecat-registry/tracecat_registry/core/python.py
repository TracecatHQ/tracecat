from typing import Annotated, Any

from typing_extensions import Doc

from tracecat_registry import ActionIsInterfaceError, registry
from tracecat_registry.fields import Code


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
    """Execute a Python script in a secure nsjail sandbox.

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
        ActionIsInterfaceError: This action is handled at the platform level.
    """
    raise ActionIsInterfaceError()
