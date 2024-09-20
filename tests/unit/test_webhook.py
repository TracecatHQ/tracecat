import json
import re
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "file_path, trigger_data",
    [
        (
            "tests/data/workflows/integration_webhook_concat",
            {"text": "hello"},
        ),
    ],
)
def test_webhook_runs_successfully(file_path, trigger_data):
    path = Path(file_path)
    # Extract the filename without extension
    # 1. Create an commit workflow
    # Output is a JSON where the workflow ID is stored under the key "id"
    output = subprocess.run(
        [
            "tracecat",
            "workflow",
            "create",
            "--file",
            path.as_posix(),
            "--activate",
            "--webhook",
        ],
        capture_output=True,
        text=True,
    )
    # Use regex to extract the workflow ID
    # Example output: {"id":"wf-60f4b1b1d4b3b00001f3b3b1"}
    assert "Created workflow" in output.stdout
    assert output.returncode == 0
    workflow_id = re.search(r"'id':\s*'(wf-[0-9a-f]+)'", output.stdout).group(1)
    # Run the workflow
    output = subprocess.run(
        [
            "tracecat",
            "workflow",
            "run",
            workflow_id,
            "--data",
            json.dumps(trigger_data),
        ],
        capture_output=True,
        text=True,
    )
    assert output.returncode == 0
