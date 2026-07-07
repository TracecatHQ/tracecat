from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CHECKOUT_ACTION_PREFIX = "actions/checkout@"


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


def _workflow_paths() -> list[Path]:
    return sorted(
        path
        for suffix in (".yaml", ".yml")
        for path in WORKFLOWS_DIR.glob(f"*{suffix}")
    )


def _contents_permission(permissions: Any) -> str | None:
    if isinstance(permissions, str):
        if permissions == "write-all":
            return "write"
        if permissions == "read-all":
            return "read"
        return None

    if isinstance(permissions, Mapping):
        contents = permissions.get("contents")
        return contents if isinstance(contents, str) else None

    return None


def _checkout_persists_credentials(step: Mapping[str, Any]) -> bool:
    with_config = step.get("with") or {}
    if not isinstance(with_config, Mapping):
        return True

    persist_credentials = with_config.get("persist-credentials")
    return (
        persist_credentials is not False and str(persist_credentials).lower() != "false"
    )


def test_read_only_checkout_steps_do_not_persist_credentials() -> None:
    persisted_credentials: list[str] = []

    for workflow_path in _workflow_paths():
        workflow = yaml.safe_load(workflow_path.read_text()) or {}
        workflow_permissions = workflow.get("permissions")

        for job_name, job in (workflow.get("jobs") or {}).items():
            if not isinstance(job, Mapping):
                continue

            permissions = job.get("permissions", workflow_permissions)
            if _contents_permission(permissions) == "write":
                continue

            for index, step in enumerate(job.get("steps") or []):
                if not isinstance(step, Mapping):
                    continue
                if not str(step.get("uses", "")).startswith(CHECKOUT_ACTION_PREFIX):
                    continue
                if _checkout_persists_credentials(step):
                    relative_path = workflow_path.relative_to(REPO_ROOT)
                    persisted_credentials.append(
                        f"{relative_path}:{job_name}:steps[{index}]"
                    )

    assert not persisted_credentials, (
        "Read-only checkout steps must set persist-credentials: false: "
        + ", ".join(persisted_credentials)
    )
