"""Tests for registry sync Temporal workflow activity behavior."""

from __future__ import annotations

from uuid import uuid4

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.registry.actions.enums import TemplateActionValidationErrorType
from tracecat.registry.actions.schemas import RegistryActionValidationErrorInfo
from tracecat.registry.sync.runner import RegistrySyncValidationError
from tracecat.registry.sync.schemas import RegistrySyncRequest
from tracecat.registry.sync.workflow import sync_registry_activity
from tracecat.runtime.errors import RuntimeErrorKind
from tracecat.temporal.errors import TemporalErrorDetails


@pytest.mark.anyio
async def test_sync_registry_activity_raises_validation_application_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sync activity should fail fast when validation errors are returned."""

    class _FakeRunner:
        async def run(self, request: RegistrySyncRequest) -> None:
            del request
            raise RegistrySyncValidationError(
                {
                    "tools.example.action": [
                        RegistryActionValidationErrorInfo(
                            type=TemplateActionValidationErrorType.SERIALIZATION_ERROR,
                            details=["Forbidden access to os.environ"],
                            is_template=False,
                            loc_primary="tools.example.action",
                            loc_secondary=None,
                        )
                    ]
                }
            )

    monkeypatch.setattr(
        "tracecat.registry.sync.workflow.RegistrySyncRunner", _FakeRunner
    )

    request = RegistrySyncRequest(
        repository_id=uuid4(),
        origin="tracecat_registry",
        origin_type="builtin",
    )

    with pytest.raises(
        ApplicationError, match="Registry sync validation failed"
    ) as exc_info:
        await sync_registry_activity(request)

    envelope = TemporalErrorDetails.runtime_error_from_details(exc_info.value.details)
    assert envelope is not None
    assert envelope.kind == RuntimeErrorKind.USER
    assert envelope.code == "registry.sync.validation_failed"
