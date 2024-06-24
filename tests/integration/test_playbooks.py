import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

load_dotenv()


# Fixture to create Tracecat secrets
@pytest.fixture(scope="session", autouse=True)
def create_secrets():
    secrets = {
        "abusech": {"ABUSECH_API_KEY": os.getenv("ABUSECH_API_KEY")},
        "abuseipdb": {"ABUSEIPDB_API_KEY": os.getenv("ABUSEIPDB_API_KEY")},
        "aws-guardduty": {
            "AWS_ACCESS_KEY_ID": os.getenv("AWS_GUARDDUTY__ACCESS_KEY_ID"),
            "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_GUARDDUTY__SECRET_ACCESS_KEY"),
        },
        "datadog": {
            "DD_API_KEY": os.getenv("DD_API_KEY"),
            "DD_APP_KEY": os.getenv("DD_APP_KEY"),
        },
        "hybrid-analysis": {
            "HYBRID_ANALYSIS_API_KEY": os.getenv("HYBRID_ANALYSIS_API_KEY")
        },
        "openai": {"OPENAI_API_KEY": os.getenv("OPENAI_API_KEY")},
        "pulsedrive": {"PULSEDRIVE_API_KEY": os.getenv("PULSEDRIVE_API_KEY")},
        "resend": {"RESEND_API_KEY": os.getenv("RESEND_API_KEY")},
        "slack": {
            "SLACK_BOT_TOKEN": os.getenv("SLACK_BOT_TOKEN"),
            "SLACK_CHANNEL": os.getenv("SLACK_CHANNEL"),
            "SLACK_WEBHOOK": os.getenv("SLACK_WEBHOOK"),
        },
        "urlscan": {"URLSCAN_API_KEY": os.getenv("URLSCAN_API_KEY")},
        "virustotal": {"VT_API_KEY": os.getenv("VT_API_KEY")},
    }

    for name, env_vars in secrets.items():
        env_vars_str = " ".join([f"{key}={value}" for key, value in env_vars.items()])
        output = subprocess.run(
            f"tracecat secret create {name} {env_vars_str}",
            shell=True,
            capture_output=True,
            text=True,
        )
        assert "Secret created successfully!" in output.stdout


@pytest.mark.parametrize(
    "path_to_playbook, trigger_data",
    [
        (
            "playbooks/alert_management/aws-guardduty-to-slack.yml",
            {
                "start_time": "2024-05-01T00:00:00Z",
                "end_time": "2024-07-01T12:00:00Z",
            },
        ),
        (
            "playbooks/alert_management/datadog-siem-to-slack.yml",
            {
                "start_time": "2024-05-01T00:00:00Z",
                "end_time": "2024-07-01T12:00:00Z",
            },
        ),
    ],
)
def test_playbook(path_to_playbook: str, trigger_data: dict[str, Any]):
    # Extract the filename without extension
    path = Path(path_to_playbook)
    # 1. Create an commit workflow
    # Output is a JSON where the workflow ID is stored under the key "id"
    output = subprocess.run(
        [
            "tracecat",
            "workflow",
            "create",
            "--title",
            path.name,
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
