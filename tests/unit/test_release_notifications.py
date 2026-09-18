"""Validate the stable-release notification before acquiring a write token."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("version", "accepted"),
    [
        ("1.2.0", True),
        ("1.2.1", True),
        ("1.3.0-alpha.1", False),
        ("1.3.0-rc.1", False),
        ("nightly-20260901", False),
        ("latest", False),
        ("01.2.3", False),
        ("1.2.3\nother", False),
    ],
)
def test_notification_validates_before_creating_cross_repo_token(
    version: str, accepted: bool
) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/notify-release.yml").read_text()
    )
    job = workflow["jobs"]["trigger-k8s-version-bump"]
    # The validation runs before the first step that creates a write token.
    assert job["steps"][1]["id"] == "cross-repo-token"
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", job["steps"][0]["run"]],
        env={**os.environ, "VERSION": version},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == accepted
