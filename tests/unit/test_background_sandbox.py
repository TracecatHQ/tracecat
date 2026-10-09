"""Background workflow imports remain valid with process-wide import hooks."""

import subprocess
import sys
import textwrap


def test_background_sandbox_with_fastmcp_import_hook() -> None:
    # Isolate the process-wide hook so this regression does not depend on or
    # change the order in which the rest of the unit suite imports FastMCP.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""\
                import asyncio
                import fastmcp
                from temporalio import workflow
                from tracecat.background.sandbox import new_sandbox_runner
                from tracecat.workspace_sync.operations.workflows import WorkspaceSyncWorkflow

                async def validate():
                    definition = workflow._Definition.must_from_class(WorkspaceSyncWorkflow)
                    new_sandbox_runner().prepare_workflow(definition)

                asyncio.run(validate())
                """),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
