from typing import Annotated, Any, TypedDict
from tracecat.logger import logger
from typing_extensions import Doc
import json
import re

from tracecat_registry import registry


class PythonScriptOutput(TypedDict):
    """Internal representation of script execution output."""

    output: Any
    stdout: str
    stderr: str
    success: bool
    error: str | None


class PythonScriptError(Exception):
    """Base exception for Python script execution errors."""

    pass


class PythonScriptTimeoutError(PythonScriptError):
    """Exception raised when a Python script execution times out."""

    pass


class PythonScriptValidationError(PythonScriptError):
    """Exception raised when a Python script fails validation."""

    pass


class PythonScriptExecutionError(PythonScriptError):
    """Exception raised when a Python script fails during execution."""

    pass


class PythonScriptOutputError(PythonScriptError):
    """Exception raised when a Python script output cannot be processed."""

    pass


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
    description="Execute a Python script in a sandboxed WebAssembly environment.",
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
            "The script runs in a sandboxed WebAssembly environment."
        ),
    ],
    inputs: Annotated[
        dict[str, Any] | None,
        Doc(
            "Input data for the script. "
            "Each key-value pair becomes a global variable in the script."
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
    Executes a Python script as a function in a sandboxed WebAssembly environment using Pyodide.

    The script must contain at least one function. If multiple functions are defined, one must be
    named 'main', which will be called. If only one function is defined, it will be called.

    The input 'inputs' dictionary's items are injected as global variables into the script.
    The function's return value is the output of this operation.

    Args:
        script: The Python script content with at least one function definition.
        inputs: A dictionary of input data, made available as global variables to the script.
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
        logger.error(f"Script validation failed: {error_message}")
        raise PythonScriptValidationError(error_message)

    import asyncio
    import sys
    from io import StringIO

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

    # Add function execution code
    execution_code = f"""
# Original script
{script}

# Execute the target function
__result = {target_function}()
__result  # Return the result
"""

    internal_output: PythonScriptOutput

    if in_pyodide_env:
        stdout_capture = StringIO()
        stderr_capture = StringIO()

        script_globals = {"__name__": "__main__"}
        if inputs:
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
        except asyncio.TimeoutError:
            error_msg = f"Script execution timed out after {timeout_seconds} seconds"
            logger.error(error_msg)
            raise PythonScriptTimeoutError(error_msg)
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Script execution failed: {error_msg}")
            raise PythonScriptExecutionError(f"Script execution failed: {error_msg}")
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


async def _run_python_script_subprocess(
    script: str,
    inputs: dict[str, Any] | None,
    dependencies: list[str] | None,
    timeout_seconds: int,
) -> PythonScriptOutput:
    import tempfile
    import asyncio

    node_script_template = """\
const {{ loadPyodide }} = require("pyodide");

async function main() {{
    const pyodide = await loadPyodide();

    const dependencies = {dependencies_json};
    if (dependencies && dependencies.length > 0) {{
        try {{
            await pyodide.loadPackage(dependencies);
        }} catch (pkgError) {{
            console.error(`Error loading dependencies: ${{pkgError.toString()}}`);
        }}
    }}

    const scriptInputs = {inputs_json};
    if (scriptInputs) {{
        for (const [key, value] of Object.entries(scriptInputs)) {{
            pyodide.globals.set(key, value);
        }}
    }}

    let stdout_acc = "";
    let stderr_acc = "";
    pyodide.setStdout({{
        batched: (msg) => {{ stdout_acc += msg + "\n"; }}
    }});
    pyodide.setStderr({{
        batched: (msg) => {{ stderr_acc += msg + "\n"; }}
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
        stderr: `Node.js wrapper error: ${{err.toString()}}`,
        error: `Node.js wrapper error: ${{err.toString()}}`
    }}));
}});\n"""

    escaped_script = (
        script.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    )

    node_script = node_script_template.format(
        dependencies_json=json.dumps(dependencies or []),
        inputs_json=json.dumps(inputs or {}),
        escaped_script=escaped_script,
    )
    temp_file_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
            f.write(node_script)
            temp_file_path = f.name

        process = await asyncio.create_subprocess_exec(
            "node",
            temp_file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds + 5
            )
        except asyncio.TimeoutError:
            error_msg = f"Script execution (subprocess) timed out after {timeout_seconds} seconds"
            logger.error(error_msg)
            raise PythonScriptTimeoutError(error_msg)

        stdout = stdout_bytes.decode().strip()
        stderr = stderr_bytes.decode().strip()

        if process.returncode != 0:
            error_msg = f"Node.js process error. Stderr: {stderr}"
            logger.error(error_msg)
            raise PythonScriptExecutionError(error_msg)

        if stdout:
            try:
                output_data = json.loads(stdout)
                return PythonScriptOutput(
                    output=output_data.get("output"),
                    stdout=output_data.get("stdout", ""),
                    stderr=output_data.get("stderr", ""),
                    success=output_data.get("success", False),
                    error=output_data.get("error"),
                )
            except json.JSONDecodeError as jde:
                error_msg = f"Failed to decode JSON output from Node.js: {jde}. stdout: {stdout}"
                logger.error(error_msg)
                raise PythonScriptOutputError(error_msg)
        else:
            error_msg = stderr or "No output from Pyodide Node.js script."
            logger.error(error_msg)
            raise PythonScriptExecutionError(error_msg)

    except Exception as e:
        if isinstance(
            e,
            (
                PythonScriptTimeoutError,
                PythonScriptExecutionError,
                PythonScriptOutputError,
            ),
        ):
            raise
        error_msg = f"Failed to execute script via subprocess: {str(e)}"
        logger.error(error_msg)
        raise PythonScriptExecutionError(error_msg)
    finally:
        if temp_file_path:
            try:
                import os

                os.unlink(temp_file_path)
            except (OSError, NameError):
                pass
