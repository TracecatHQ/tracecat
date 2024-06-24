import json
import re
import subprocess
from pathlib import Path

import pytest

DATA_PATH = Path(__file__).parent.parent.joinpath("data/workflows")


@pytest.mark.parametrize(
    "filename, trigger_data",
    [
        (
            "integration_webhook_concat",
            {"text": "hello"},
        ),
    ],
)
def test_webhook_runs_successfully(filename, trigger_data):
    path = DATA_PATH / f"{filename}.yml"
    # Extract the filename without extension
    # 1. Create an commit workflow
    # Output is a JSON where the workflow ID is stored under the key "id"
    output = subprocess.run(
        [
            "tracecat",
            "workflow",
            "create",
            "--title",
            filename,
            "--commit",
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
    assert "Successfully committed to workflow" in output.stdout
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
