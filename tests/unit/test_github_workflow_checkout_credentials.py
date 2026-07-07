from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CHECKOUT_ACTION_PREFIX = "actions/checkout@"
PUSH_REQUIRED_CHECKOUT_JOBS = {
    (Path(".github/workflows/create-release.yml"), "create-release"): (
        "pushes release branches with GITHUB_TOKEN"
    ),
    (Path(".github/workflows/publish-release.yml"), "publish-release"): (
        "pushes release tags with GITHUB_TOKEN"
    ),
}


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


@pytest.mark.parametrize(
    ("permissions", "expected"),
    [
        ("write-all", "write"),
        ("read-all", "read"),
        ({"contents": "write"}, "write"),
        ({"contents": "read"}, "read"),
        ({"contents": "none"}, "none"),
        ({"packages": "write"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_contents_permission(permissions: Any, expected: str | None) -> None:
    assert _contents_permission(permissions) == expected


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        ({}, True),
        ({"with": {}}, True),
        ({"with": "invalid"}, True),
        ({"with": {"persist-credentials": False}}, False),
        ({"with": {"persist-credentials": "false"}}, False),
        ({"with": {"persist-credentials": "FALSE"}}, False),
        ({"with": {"persist-credentials": True}}, True),
        ({"with": {"persist-credentials": "true"}}, True),
    ],
)
def test_checkout_persists_credentials(step: Mapping[str, Any], expected: bool) -> None:
    assert _checkout_persists_credentials(step) is expected


def test_non_exempt_checkout_steps_do_not_persist_credentials() -> None:
    workflow_paths = _workflow_paths()
    assert workflow_paths, "No GitHub Actions workflow files found"

    persisted_credentials: list[str] = []
    checked_checkout_steps = 0
    seen_push_required_checkout_jobs: set[tuple[Path, str]] = set()

    for workflow_path in workflow_paths:
        workflow = yaml.safe_load(workflow_path.read_text()) or {}
        workflow_permissions = workflow.get("permissions")
        relative_path = workflow_path.relative_to(REPO_ROOT)

        for job_name, job in (workflow.get("jobs") or {}).items():
            if not isinstance(job, Mapping):
                continue

            permissions = job.get("permissions", workflow_permissions)
            checkout_steps = [
                (index, step)
                for index, step in enumerate(job.get("steps") or [])
                if isinstance(step, Mapping)
                and str(step.get("uses", "")).startswith(CHECKOUT_ACTION_PREFIX)
            ]

            push_required_job = (relative_path, job_name)
            if push_required_job in PUSH_REQUIRED_CHECKOUT_JOBS:
                assert _contents_permission(permissions) == "write", (
                    f"{relative_path}:{job_name} is listed as a push-required "
                    "checkout exemption but does not have contents: write"
                )
                assert checkout_steps, (
                    f"{relative_path}:{job_name} is listed as a push-required "
                    "checkout exemption but has no checkout step"
                )
                seen_push_required_checkout_jobs.add(push_required_job)
                continue

            for index, step in checkout_steps:
                checked_checkout_steps += 1
                if _checkout_persists_credentials(step):
                    persisted_credentials.append(
                        f"{relative_path}:{job_name}:steps[{index}]"
                    )

    assert seen_push_required_checkout_jobs == set(PUSH_REQUIRED_CHECKOUT_JOBS), (
        "Push-required checkout exemptions must match existing checkout jobs: "
        + ", ".join(
            f"{path}:{job_name}"
            for path, job_name in sorted(
                set(PUSH_REQUIRED_CHECKOUT_JOBS) ^ seen_push_required_checkout_jobs,
                key=lambda item: (str(item[0]), item[1]),
            )
        )
    )
    assert checked_checkout_steps > 0, (
        "No non-exempt actions/checkout steps were evaluated"
    )
    assert not persisted_credentials, (
        "Non-exempt checkout steps must set persist-credentials: false: "
        + ", ".join(persisted_credentials)
    )
