import asyncio
import ast
import json
import os
import re
import shutil
import sys
import tempfile
from io import StringIO
from typing import Annotated, Any, TypedDict

from tracecat.logger import logger
from tracecat_registry import registry
from typing_extensions import Doc


class PythonScriptOutput(TypedDict):
    """Internal representation of script execution output."""

    output: Any
    stdout: str
    stderr: str
    success: bool
    error: str | None


class PythonScriptError(Exception):
    """Base exception for Python script execution errors."""


class PythonScriptTimeoutError(PythonScriptError):
    """Exception raised when a Python script execution times out."""


class PythonScriptValidationError(PythonScriptError):
    """Exception raised when a Python script fails validation."""


class PythonScriptExecutionError(PythonScriptError):
    """Exception raised when a Python script fails during execution."""


class PythonScriptOutputError(PythonScriptError):
    """Exception raised when a Python script output cannot be processed."""


def _extract_user_friendly_error(error_msg: str) -> str:
    """
    Extract a clean, user-friendly error message from Python tracebacks.

    This extracts just the Python error representation (e.g., "ValueError: some_msg")
    without the technical traceback details that are not useful for end users
    in a no-code platform.

    Args:
        error_msg: The raw error message, potentially containing a full traceback.

    Returns:
        A clean error message suitable for end users.
    """
    if "PythonError:" not in error_msg:
        return error_msg

    # Split into lines and process in reverse (exception is usually at the end)
    lines = [line.strip() for line in error_msg.split("\n") if line.strip()]

    for line in reversed(lines):
        # Skip traceback-related lines
        match line:
            case line if line.startswith(("File ", "  ", "Traceback")):
                continue
            case line if ":" not in line:
                continue
            case _:
                # If it has the format "SomethingError: message", it's likely a Python exception
                exception_type = line.split(":", 1)[0].strip()
                if exception_type.endswith(("Error", "Exception")):
                    return line

    # Fallback to original message if no clean exception found
    return error_msg


def _get_function_signature(script: str, function_name: str) -> list[str]:
    """
    Extract function parameter names from a Python function definition.

    Args:
        script: The Python script content
        function_name: Name of the function to analyze

    Returns:
        List of parameter names for the function
    """
    try:
        tree = ast.parse(script)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == function_name:
                return [arg.arg for arg in node.args.args]
    except SyntaxError:
        # If we can't parse the AST, fall back to no parameters
        pass
    return []


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
    description="Execute a Python script in a secure, sandboxed WebAssembly environment using Pyodide.",
    display_group="Run script",
    namespace="core.script",
)
async def run_python(
    script: Annotated[
        str,
        Doc(
            "Python script to execute. Must contain at least one function. "
            "If multiple functions are defined, one must be named 'main'. "
            "The function's return value is the output of this operation. "
            "The script runs in a secure, sandboxed WebAssembly environment "
            "isolated from the host operating system."
        ),
    ],
    inputs: Annotated[
        dict[str, Any] | None,
        Doc(
            "Input data for the script. "
            "Each key-value pair becomes a function argument passed to the target function."
        ),
    ] = None,
    dependencies: Annotated[
        list[str] | None,
        Doc(
            "Optional list of Python package dependencies (e.g., ['numpy', 'pandas']). "
            "Only packages available in Pyodide are supported."
        ),
    ] = None,
    timeout_seconds: Annotated[
        int,
        Doc("Maximum execution time in seconds. Default is 30 seconds."),
    ] = 30,
) -> Any:
    """
    Executes a Python script in a secure, sandboxed WebAssembly environment using Pyodide.

    The code is executed using Pyodide in Deno and is therefore isolated from the rest
    of the operating system. This prevents the script from accessing files or resources
    on the host system.

    The script must contain at least one function. If multiple functions are defined, one must be
    named 'main', which will be called. If only one function is defined, it will be called.

    The input 'inputs' dictionary's items are passed as function arguments to the target function.
    The function's return value is the output of this operation.

    Args:
        script: The Python script content with at least one function definition.
        inputs: A dictionary of input data, passed as function arguments to the target function.
        dependencies: A list of Pyodide-compatible Python package dependencies.
        timeout_seconds: Maximum allowed execution time for the script.

    Returns:
        The result of the function call.

    Raises:
        PythonScriptValidationError: If script doesn't meet the requirements.
        PythonScriptTimeoutError: If script execution times out.
        PythonScriptExecutionError: If script execution fails.
        PythonScriptOutputError: If script output cannot be processed.
    """
    # Validate script
    is_valid, error_message = _validate_script(script)
    if not is_valid:
        assert error_message is not None  # Should never be None when is_valid is False
        logger.error(f"Script validation failed: {error_message}")
        raise PythonScriptValidationError(error_message)

    in_pyodide_env = False
    try:
        from pyodide import eval_code_async  # type: ignore

        in_pyodide_env = True
    except ImportError:
        pass

    # Add wrapper to call the appropriate function
    function_pattern = r"def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
    functions = re.findall(function_pattern, script)

    # Determine which function to call
    target_function = "main" if "main" in functions else functions[0]

    # Get function signature to determine how to call it
    function_params = _get_function_signature(script, target_function)

    # Build function call with appropriate arguments
    if function_params and inputs:
        # Call function with arguments from inputs
        args = []
        for param in function_params:
            if param in inputs:
                args.append(repr(inputs[param]))
            else:
                # If parameter not provided in inputs, pass None
                args.append("None")
        function_call = f"{target_function}({', '.join(args)})"
    else:
        # Call function with no arguments (backward compatibility)
        function_call = f"{target_function}()"

    # Add function execution code
    execution_code = f"""
# Original script
{script}

# Execute the target function
__result = {function_call}
__result  # Return the result
"""

    internal_output: PythonScriptOutput

    if in_pyodide_env:
        stdout_capture = StringIO()
        stderr_capture = StringIO()

        script_globals = {"__name__": "__main__"}
        # For backward compatibility, still add inputs as globals if function has no parameters
        if not function_params and inputs:
            script_globals.update(inputs)

        if dependencies:
            import micropip  # type: ignore

            for package_name in dependencies:
                try:
                    await micropip.install(package_name)
                except Exception as e:
                    logger.warning(
                        f"Failed to install dependency '{package_name}': {e}"
                    )

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture

        try:
            script_result = await asyncio.wait_for(
                eval_code_async(execution_code, globals=script_globals),
                timeout=timeout_seconds,
            )
            if hasattr(script_result, "to_py"):
                script_result = script_result.to_py()

            internal_output = PythonScriptOutput(
                output=script_result,
                stdout=stdout_capture.getvalue(),
                stderr=stderr_capture.getvalue(),
                success=True,
                error=None,
            )
        except asyncio.TimeoutError as timeout_error:
            error_msg = f"Script execution timed out after {timeout_seconds} seconds"
            logger.error(error_msg)
            raise PythonScriptTimeoutError(error_msg) from timeout_error
        except Exception as e:
            error_msg = _extract_user_friendly_error(str(e))
            logger.error(f"Script execution failed: {error_msg}")
            raise PythonScriptExecutionError(
                f"Script execution failed: {error_msg}"
            ) from e
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
    else:
        internal_output = await _run_python_script_subprocess(
            execution_code, inputs, dependencies, timeout_seconds
        )

    if internal_output["stdout"]:
        logger.info(f"Script stdout:\n{internal_output['stdout'].strip()}")
    if internal_output["stderr"]:
        logger.error(f"Script stderr:\n{internal_output['stderr'].strip()}")

    if internal_output["success"]:
        return internal_output["output"]
    else:
        error_message = internal_output["error"] or "Unknown script execution error"
        logger.error(f"Script execution failed: {error_message}")
        raise PythonScriptExecutionError(f"Script execution failed: {error_message}")


def _create_deno_script(
    script: str, inputs: dict[str, Any] | None, dependencies: list[str] | None
) -> str:
    """
    Create the Deno TypeScript script that loads Pyodide and executes Python code.

    Args:
        script: The Python script code to execute.
        inputs: Dictionary of variables to inject into the script globals (for backward compatibility).
        dependencies: List of Python packages to install via Pyodide.

    Returns:
        The complete Deno TypeScript script as a string.
    """
    escaped_script = (
        script.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    )

    return f"""
import {{ loadPyodide }} from "npm:pyodide";

async function main() {{
    const pyodide = await loadPyodide();

    const dependencies = {json.dumps(dependencies or [])};
    if (dependencies && dependencies.length > 0) {{
        try {{
            await pyodide.loadPackage(dependencies);
        }} catch (pkgError) {{
            console.error(`Error loading dependencies: ${{pkgError.toString()}}`);
        }}
    }}

    const scriptInputs = {json.dumps(inputs or {})};
    if (scriptInputs) {{
        for (const [key, value] of Object.entries(scriptInputs)) {{
            pyodide.globals.set(key, value);
        }}
    }}

    let stdout_acc = "";
    let stderr_acc = "";
    pyodide.setStdout({{
        batched: (msg) => {{ stdout_acc += msg + "\\n"; }}
    }});
    pyodide.setStderr({{
        batched: (msg) => {{ stderr_acc += msg + "\\n"; }}
    }});

    let scriptResult = null;
    try {{
        scriptResult = await pyodide.runPythonAsync(`{escaped_script}`);
        if (typeof scriptResult?.toJs === 'function') {{
             scriptResult = scriptResult.toJs({{ dict_converter: Object.fromEntries }});
        }}
        console.log(JSON.stringify({{
            success: true,
            output: scriptResult,
            stdout: stdout_acc,
            stderr: stderr_acc,
            error: null
        }}));
    }} catch (error) {{
        console.log(JSON.stringify({{
            success: false,
            output: null,
            stdout: stdout_acc,
            stderr: stderr_acc,
            error: error.toString()
        }}));
    }}
}}

main().catch(err => {{
    console.log(JSON.stringify({{
        success: false,
        output: null,
        stdout: "",
        stderr: `Deno wrapper error: ${{err.toString()}}`,
        error: `Deno wrapper error: ${{err.toString()}}`
    }}));
}});
"""


def _parse_subprocess_output(stdout: str) -> PythonScriptOutput:
    """
    Parse the stdout from Deno subprocess to extract the JSON result.

    This handles cases where package installation messages are mixed with JSON output
    by looking for the last valid JSON object in the output.

    Args:
        stdout: The raw stdout from the Deno subprocess.

    Returns:
        Parsed PythonScriptOutput.

    Raises:
        PythonScriptExecutionError: If execution failed with a clean error message.
        PythonScriptOutputError: If JSON parsing fails.
    """
    if not stdout:
        error_msg = "No output from Pyodide Deno script."
        logger.error(error_msg)
        raise PythonScriptExecutionError(error_msg)

    try:
        # Handle case where package installation messages are mixed with JSON output
        # Look for the last line that contains valid JSON
        json_line = None
        for line in reversed(stdout.split("\n")):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    json.loads(line)  # Validate it's valid JSON
                    json_line = line
                    break
                except json.JSONDecodeError:
                    continue

        if not json_line:
            # Fallback: try parsing the entire stdout as JSON
            json_line = stdout

        output_data = json.loads(json_line)

        # Check if execution was successful
        if not output_data.get("success", False):
            # Extract clean error message for users
            raw_error = output_data.get("error", "Unknown error occurred")
            clean_error = _extract_user_friendly_error(raw_error)

            logger.error(f"Script execution failed: {clean_error}")
            raise PythonScriptExecutionError(f"Script execution failed: {clean_error}")

        return PythonScriptOutput(
            output=output_data.get("output"),
            stdout=output_data.get("stdout", ""),
            stderr=output_data.get("stderr", ""),
            success=output_data.get("success", False),
            error=output_data.get("error"),
        )

    except json.JSONDecodeError as jde:
        error_msg = f"Failed to decode JSON output from Deno: {jde}. stdout: {stdout}"
        logger.error(error_msg)
        raise PythonScriptOutputError(error_msg) from jde


async def _run_python_script_subprocess(
    script: str,
    inputs: dict[str, Any] | None,
    dependencies: list[str] | None,
    timeout_seconds: int,
) -> PythonScriptOutput:
    """
    Execute Python script via Deno subprocess using Pyodide.

    This function creates a temporary TypeScript file that loads Pyodide
    and executes the Python script in a secure WebAssembly sandbox.

    Args:
        script: The Python script code to execute.
        inputs: Dictionary of variables to inject into the script globals.
        dependencies: List of Python packages to install via Pyodide.
        timeout_seconds: Maximum execution time before timeout.

    Returns:
        PythonScriptOutput containing the execution results.

    Raises:
        PythonScriptExecutionError: If Deno is not found or execution fails.
        PythonScriptTimeoutError: If execution times out.
        PythonScriptOutputError: If output cannot be parsed.
    """
    # Check if Deno is available
    deno_path = shutil.which("deno")
    if not deno_path:
        error_msg = "Deno executable not found in PATH. Please install Deno to run Python scripts."
        logger.error(error_msg)
        raise PythonScriptExecutionError(error_msg)

    deno_script = _create_deno_script(script, inputs, dependencies)

    temp_file_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False) as f:
            f.write(deno_script)
            temp_file_path = f.name

        # Run Deno with minimal permissions for security
        # Following the pydantic-ai MCP secure implementation pattern
        process = await asyncio.create_subprocess_exec(
            deno_path,
            "run",
            # Network access only for downloading Pyodide and Python packages
            "--allow-net",
            # Read access restricted to node_modules only
            "--allow-read=node_modules",
            # Write access restricted to node_modules only for caching
            "--allow-write=node_modules",
            # Use local node_modules directory for package management
            "--node-modules-dir=auto",
            temp_file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds + 5
            )
        except asyncio.TimeoutError as timeout_error:
            error_msg = f"Script execution (subprocess) timed out after {timeout_seconds} seconds"
            logger.error(error_msg)
            raise PythonScriptTimeoutError(error_msg) from timeout_error

        stdout = stdout_bytes.decode().strip()
        stderr = stderr_bytes.decode().strip()

        if process.returncode != 0:
            error_msg = f"Deno process error. Stderr: {stderr}"
            logger.error(error_msg)
            raise PythonScriptExecutionError(error_msg)

        return _parse_subprocess_output(stdout)

    except (
        PythonScriptTimeoutError,
        PythonScriptExecutionError,
        PythonScriptOutputError,
    ):
        # Re-raise our custom exceptions as-is
        raise
    except Exception as e:
        error_msg = f"Failed to execute script via subprocess: {str(e)}"
        logger.error(error_msg)
        raise PythonScriptExecutionError(error_msg) from e
    finally:
        if temp_file_path:
            try:
                os.unlink(temp_file_path)
            except OSError:
                # File cleanup failed, but this is not critical
                logger.warning(f"Failed to clean up temporary file: {temp_file_path}")
                pass
