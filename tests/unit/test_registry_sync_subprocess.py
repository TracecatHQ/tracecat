"""Tests for the unsandboxed registry sync subprocess wrapper."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from tracecat.exceptions import RegistryError, RegistrySyncContentError
from tracecat.registry.sync.schemas import SyncErrorCode, SyncResultError
from tracecat.registry.sync.subprocess import fetch_actions_from_subprocess


def _mock_subprocess(mocker, *, stdout: str, returncode: int) -> None:
    process = mocker.Mock(returncode=returncode)
    process.communicate = mocker.AsyncMock(return_value=(stdout.encode(), b""))
    mocker.patch(
        "tracecat.registry.sync.subprocess.asyncio.create_subprocess_exec",
        mocker.AsyncMock(return_value=process),
    )


@pytest.mark.anyio
async def test_fetch_actions_raises_typed_error_for_error_code(mocker) -> None:
    _mock_subprocess(
        mocker,
        stdout=SyncResultError(
            error="No module named 'internal_registry'",
            error_code=SyncErrorCode.PACKAGE_NOT_FOUND,
        ).model_dump_json(),
        returncode=1,
    )

    with pytest.raises(RegistrySyncContentError) as exc_info:
        await fetch_actions_from_subprocess(
            origin="tracecat_registry", repository_id=uuid4()
        )

    assert exc_info.value.code == SyncErrorCode.PACKAGE_NOT_FOUND
    assert str(exc_info.value) == "No module named 'internal_registry'"


@pytest.mark.anyio
async def test_fetch_actions_raises_plain_error_without_error_code(mocker) -> None:
    _mock_subprocess(
        mocker,
        stdout=SyncResultError(error="git clone failed").model_dump_json(),
        returncode=1,
    )

    with pytest.raises(RegistryError) as exc_info:
        await fetch_actions_from_subprocess(
            origin="tracecat_registry", repository_id=uuid4()
        )

    assert type(exc_info.value) is RegistryError
    assert str(exc_info.value) == "git clone failed"


@pytest.mark.anyio
async def test_fetch_actions_reports_exit_code_for_unparseable_output(mocker) -> None:
    _mock_subprocess(mocker, stdout="not json", returncode=2)

    with pytest.raises(RegistryError, match="exited with code 2"):
        await fetch_actions_from_subprocess(
            origin="tracecat_registry", repository_id=uuid4()
        )


@pytest.mark.anyio
async def test_fetch_actions_maps_zero_exit_error_result(mocker) -> None:
    _mock_subprocess(
        mocker,
        stdout=json.dumps(
            {
                "error": "Failed to load template action from a.yml: bad",
                "error_code": "template_load_failed",
            }
        ),
        returncode=0,
    )

    with pytest.raises(RegistrySyncContentError) as exc_info:
        await fetch_actions_from_subprocess(
            origin="tracecat_registry", repository_id=uuid4()
        )

    assert exc_info.value.code == SyncErrorCode.TEMPLATE_LOAD_FAILED
