from typing import Annotated, Any, TypedDict
from tracecat.logger import logger
from typing_extensions import Doc
import json

from tracecat_registry import registry


class PythonScriptOutput(TypedDict):
    """Internal representation of script execution output."""

    output: Any
    stdout: str
    stderr: str
    success: bool
    error: str | None


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
            "Python script to execute. The script runs in a sandboxed WebAssembly environment. "
            "Input data is available via global variables from the 'inputs' parameter. "
            "The script's last evaluated expression is returned."
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
    Executes a Python script in a sandboxed WebAssembly environment using Pyodide.

    The input 'inputs' dictionary's items are injected as global variables into the script.
    The script's standard output and standard error are logged.
    The value of the last evaluated expression in the script is returned.

    If the script encounters an error or times out, a RuntimeError is raised.

    Args:
        script: The Python script content.
        inputs: A dictionary of input data, made available as global variables to the script.
        dependencies: A list of Pyodide-compatible Python package dependencies.
        timeout_seconds: Maximum allowed execution time for the script.

    Returns:
        The result of the last evaluated expression in the script.

    Raises:
        RuntimeError: If script execution fails, times out, or if the Pyodide environment
                      fails to initialize.
    """
    import asyncio
    import sys
    from io import StringIO

    in_pyodide_env = False
    try:
        from pyodide import eval_code_async  # type: ignore

        in_pyodide_env = True
    except ImportError:
        pass

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
                eval_code_async(script, globals=script_globals),
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
            internal_output = PythonScriptOutput(
                output=None,
                stdout=stdout_capture.getvalue(),
                stderr=stderr_capture.getvalue(),
                success=False,
                error=f"Script execution timed out after {timeout_seconds} seconds",
            )
        except Exception as e:
            internal_output = PythonScriptOutput(
                output=None,
                stdout=stdout_capture.getvalue(),
                stderr=stderr_capture.getvalue(),
                success=False,
                error=str(e),
            )
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
    else:
        internal_output = await _run_python_script_subprocess(
            script, inputs, dependencies, timeout_seconds
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
        raise RuntimeError(f"Script execution failed: {error_message}")


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

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds + 5
        )

        stdout = stdout_bytes.decode().strip()
        stderr = stderr_bytes.decode().strip()

        if process.returncode != 0:
            return PythonScriptOutput(
                output=None,
                stdout="",
                stderr=f"Node.js process exited with code {process.returncode}. Stderr: {stderr}",
                success=False,
                error=f"Node.js process error. Stderr: {stderr}",
            )

        if stdout:
            output_data = json.loads(stdout)
            return PythonScriptOutput(
                output=output_data.get("output"),
                stdout=output_data.get("stdout", ""),
                stderr=output_data.get("stderr", ""),
                success=output_data.get("success", False),
                error=output_data.get("error"),
            )
        else:
            return PythonScriptOutput(
                output=None,
                stdout="",
                stderr=stderr or "No output from Pyodide execution via Node.js.",
                success=False,
                error=stderr or "No output from Pyodide Node.js script.",
            )
    except asyncio.TimeoutError:
        return PythonScriptOutput(
            output=None,
            stdout="",
            stderr="",
            success=False,
            error=f"Script execution (subprocess) timed out after {timeout_seconds} seconds",
        )
    except json.JSONDecodeError as jde:
        return PythonScriptOutput(
            output=None,
            stdout=stdout,
            stderr=stderr,
            success=False,
            error=f"Failed to decode JSON output from Node.js: {jde}. stdout: {stdout}",
        )
    except Exception as e:
        return PythonScriptOutput(
            output=None,
            stdout="",
            stderr=str(e),
            success=False,
            error=f"Failed to execute script via subprocess: {str(e)}",
        )
    finally:
        if temp_file_path:
            try:
                import os

                os.unlink(temp_file_path)
            except (OSError, NameError):
                pass
