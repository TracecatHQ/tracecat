from __future__ import annotations

from unittest.mock import ANY, AsyncMock, patch

import pytest
from tracecat_registry.core.python import (
    PythonScriptExecutionError,
    PythonScriptTimeoutError,
    PythonScriptValidationError,
    run_python,
)
from tracecat_registry.sdk.sandbox import (
    SandboxExecutionError,
    SandboxTimeoutError,
    SandboxValidationError,
)


@pytest.mark.anyio
async def test_basic_script_execution_calls_executor(registry_context):
    with patch.object(
        registry_context.sandbox, "run_python", new=AsyncMock(return_value=20)
    ) as mock_run:
        result = await run_python(
            script="""
def main():
    return 20
"""
        )

    assert result == 20
    mock_run.assert_awaited_once()


@pytest.mark.anyio
async def test_passes_inputs_dependencies_and_flags(registry_context):
    with patch.object(
        registry_context.sandbox, "run_python", new=AsyncMock(return_value={"ok": True})
    ) as mock_run:
        result = await run_python(
            script="""
def main(x, y):
    return {"ok": True}
""",
            inputs={"x": 1, "y": 2},
            dependencies=["requests==2.32.0"],
            timeout_seconds=123,
            allow_network=True,
            env_vars={"FOO": "bar"},
        )

    assert result == {"ok": True}
    mock_run.assert_awaited_once_with(
        script=ANY,
        inputs={"x": 1, "y": 2},
        dependencies=["requests==2.32.0"],
        timeout_seconds=123,
        allow_network=True,
        env_vars={"FOO": "bar"},
    )


@pytest.mark.anyio
async def test_script_validation_no_function():
    with pytest.raises(PythonScriptValidationError, match="at least one function"):
        await run_python(
            script="""
x = 10
print(x)
"""
        )


@pytest.mark.anyio
async def test_script_validation_multiple_functions_requires_main():
    with pytest.raises(PythonScriptValidationError, match="one must be named 'main'"):
        await run_python(
            script="""
def func1():
    return "Function 1"

def func2():
    return "Function 2"
"""
        )


@pytest.mark.anyio
async def test_maps_executor_timeout_to_python_timeout(registry_context):
    with patch.object(
        registry_context.sandbox,
        "run_python",
        new=AsyncMock(side_effect=SandboxTimeoutError("timed out")),
    ):
        with pytest.raises(PythonScriptTimeoutError, match="timed out"):
            await run_python(
                script="""
def main():
    return 1
"""
            )


@pytest.mark.anyio
async def test_maps_executor_validation_to_python_validation(registry_context):
    with patch.object(
        registry_context.sandbox,
        "run_python",
        new=AsyncMock(side_effect=SandboxValidationError("bad env var")),
    ):
        with pytest.raises(PythonScriptValidationError, match="bad env var"):
            await run_python(
                script="""
def main():
    return 1
"""
            )


@pytest.mark.anyio
async def test_maps_executor_execution_to_python_execution(registry_context):
    with patch.object(
        registry_context.sandbox,
        "run_python",
        new=AsyncMock(side_effect=SandboxExecutionError("boom")),
    ):
        with pytest.raises(PythonScriptExecutionError, match="boom"):
            await run_python(
                script="""
def main():
    raise ValueError("boom")
"""
            )
