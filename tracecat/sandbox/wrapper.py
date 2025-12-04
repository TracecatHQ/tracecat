"""Wrapper script for executing user Python code in the sandbox.

The wrapper script is executed inside the nsjail sandbox and handles:
1. Reading inputs from a JSON file
2. Executing the user's script
3. Finding and calling the main function
4. Writing results back to a JSON file
"""

# The wrapper script content is embedded as a string constant
# to be written to the job directory before execution
WRAPPER_SCRIPT = '''
import json
import sys
import traceback
from pathlib import Path

def main():
    """Execute user script and capture results."""
    # Read inputs from file
    inputs_path = Path("/work/inputs.json")
    if inputs_path.exists():
        inputs = json.loads(inputs_path.read_text())
    else:
        inputs = {}

    result = {
        "success": False,
        "output": None,
        "error": None,
        "traceback": None,
        "stdout": "",
        "stderr": "",
    }

    # Capture stdout/stderr
    import io
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        # Read and execute the user script
        script_path = Path("/work/script.py")
        script_code = script_path.read_text()

        script_globals = {"__name__": "__main__"}
        exec(script_code, script_globals)

        # Find the callable function
        main_func = script_globals.get("main")
        if main_func is None:
            # Look for the first non-private callable
            for name, obj in script_globals.items():
                if callable(obj) and not name.startswith("_"):
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
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()

    finally:
        # Capture stdout/stderr
        result["stdout"] = sys.stdout.getvalue()
        result["stderr"] = sys.stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # Write result to file
    result_path = Path("/work/result.json")
    result_path.write_text(json.dumps(result))

    # Exit with appropriate code
    sys.exit(0 if result["success"] else 1)

if __name__ == "__main__":
    main()
'''

# Install script for package installation phase
# SECURITY: Dependencies are read from a JSON file to prevent code injection
# via malicious package names. Never interpolate user input into this script.
INSTALL_SCRIPT = """
import json
import subprocess
import sys
from pathlib import Path

# Read dependencies from secure JSON file (written with 0o600 permissions)
deps_path = Path("/work/dependencies.json")
if not deps_path.exists():
    print("No dependencies.json found", file=sys.stderr)
    sys.exit(1)

deps = json.loads(deps_path.read_text())
if not isinstance(deps, list):
    print("dependencies.json must contain a list", file=sys.stderr)
    sys.exit(1)

if not deps:
    print("No dependencies to install")
    sys.exit(0)

result = subprocess.run(
    ["uv", "pip", "install", "--target", "/cache/site-packages", "--python", sys.executable] + deps,
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    print(result.stderr, file=sys.stderr)
    sys.exit(result.returncode)
print("Packages installed successfully")
"""
