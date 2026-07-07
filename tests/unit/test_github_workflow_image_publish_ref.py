from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "build-push-images.yml"
VALIDATION_JOB = "validate-manual-publish-ref"
VALIDATION_STEP = "Validate manual publish tag/ref"


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


def _workflow() -> Mapping[str, Any]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
    assert isinstance(workflow, Mapping)
    return workflow


def _jobs() -> Mapping[str, Any]:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, Mapping)
    return jobs


def _validation_script() -> str:
    job = _jobs().get(VALIDATION_JOB)
    assert isinstance(job, Mapping)

    for step in job.get("steps") or []:
        if isinstance(step, Mapping) and step.get("name") == VALIDATION_STEP:
            script = step.get("run")
            assert isinstance(script, str)
            return script

    pytest.fail(f"Missing {VALIDATION_STEP!r} step in {VALIDATION_JOB!r}")


def _run_validation(
    *,
    event_name: str,
    input_tag: str,
    ref_type: str,
    ref_name: str,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "EVENT_NAME": event_name,
        "INPUT_TAG": input_tag,
        "REF_NAME": ref_name,
        "REF_TYPE": ref_type,
    }
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", _validation_script()],
        check=False,
        env=env,
        text=True,
        capture_output=True,
    )


def test_image_build_jobs_wait_for_manual_publish_ref_validation() -> None:
    jobs = _jobs()

    for job_name in ("build-and-push-api", "build-and-push-ui"):
        job = jobs.get(job_name)
        assert isinstance(job, Mapping)
        assert job.get("needs") == VALIDATION_JOB


def test_manual_publish_validation_job_does_not_get_package_write_scope() -> None:
    job = _jobs().get(VALIDATION_JOB)
    assert isinstance(job, Mapping)

    permissions = job.get("permissions")
    assert isinstance(permissions, Mapping)
    assert permissions.get("contents") == "read"
    assert permissions.get("packages") is None


@pytest.mark.parametrize(
    ("input_tag", "ref_name"),
    [
        ("1.2.3", "1.2.3"),
        ("1.2.3-beta.0", "1.2.3-beta.0"),
        ("1.2.3+build.1", "1.2.3+build.1"),
        ("nightly-20250101", "nightly-20250101"),
        ("nightly-20250101-abcdef0", "nightly-20250101-abcdef0"),
    ],
)
def test_manual_publish_validation_accepts_matching_tag_refs(
    input_tag: str, ref_name: str
) -> None:
    result = _run_validation(
        event_name="workflow_dispatch",
        input_tag=input_tag,
        ref_type="tag",
        ref_name=ref_name,
    )

    assert result.returncode == 0, result.stderr


def test_manual_publish_validation_ignores_non_manual_events() -> None:
    result = _run_validation(
        event_name="push",
        input_tag="",
        ref_type="branch",
        ref_name="staging",
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("input_tag", "ref_type", "ref_name", "expected_error"),
    [
        ("", "tag", "", "requires a tag input"),
        ("latest", "tag", "latest", "must be semver or nightly-YYYYMMDD"),
        ("refs/pull/1/merge", "tag", "refs/pull/1/merge", "must be semver"),
        ("1.2.3", "branch", "main", "must run from a Git tag ref"),
        ("1.2.3", "tag", "1.2.4", "must match workflow ref"),
        ("nightly-main", "tag", "nightly-main", "must be semver"),
    ],
)
def test_manual_publish_validation_rejects_unsafe_tag_ref_pairs(
    input_tag: str,
    ref_type: str,
    ref_name: str,
    expected_error: str,
) -> None:
    result = _run_validation(
        event_name="workflow_dispatch",
        input_tag=input_tag,
        ref_type=ref_type,
        ref_name=ref_name,
    )

    assert result.returncode != 0
    assert expected_error in result.stderr
