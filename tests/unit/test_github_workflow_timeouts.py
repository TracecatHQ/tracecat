from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


@pytest.fixture(autouse=True, scope="session")
def default_org() -> Iterator[None]:
    """Workflow policy checks do not need seeded organization state."""
    yield


@pytest.fixture(autouse=True, scope="session")
def workflow_bucket() -> Iterator[None]:
    """Workflow policy checks do not need object storage buckets."""
    yield


@pytest.fixture(autouse=True)
def clean_redis_db() -> Iterator[None]:
    """Workflow policy checks do not need Redis isolation."""
    yield


def test_github_actions_jobs_set_timeout_minutes() -> None:
    missing_timeouts: list[str] = []

    for workflow_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        workflow = yaml.safe_load(workflow_path.read_text()) or {}
        jobs = workflow.get("jobs") or {}

        for job_name, job in jobs.items():
            if isinstance(job, dict) and "timeout-minutes" not in job:
                relative_path = workflow_path.relative_to(REPO_ROOT)
                missing_timeouts.append(f"{relative_path}:{job_name}")

    assert not missing_timeouts, (
        "GitHub Actions jobs must set timeout-minutes: " + ", ".join(missing_timeouts)
    )
