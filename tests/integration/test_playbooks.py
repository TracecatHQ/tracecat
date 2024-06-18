import json
import os
import subprocess

import pytest


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
        command = f"tracecat secret create {name} {env_vars_str}"
        subprocess.run(command, shell=True, check=True)


@pytest.mark.parametrize(
    "path_to_playbook",
    [
        # "alert_management/aws-guardduty-to-slack.yml",
        "alert_management/crowdstrike-to-cases.yml",
        "alert_management/datadog-siem-to-slack.yml",
    ],
)
def test_playbook(path_to_playbook):
    # Extract the filename without extension
    filename = os.path.basename(path_to_playbook).replace(".yml", "")
    # 1. Create workflow
    # Output is a JSON where the workflow ID is stored under the key "id"
    output = subprocess.run(
        ["tracecat", "workflow", "create", "--title", filename],
        check=True,
        capture_output=True,
        text=True,
    )
    workflow_id = json.loads(output.stdout)["id"]
    # 2. Commit workflow definition
    subprocess.run(
        ["tracecat", "workflow", "commit", "--file", path_to_playbook, workflow_id],
        check=True,
    )
    # 3. Activate the workflow
    subprocess.run(["tracecat", "workflow", "up", "--webhook", workflow_id], check=True)
    # 4. Run the workflow
    subprocess.run(["tracecat", "workflow", "run", workflow_id], check=True)
