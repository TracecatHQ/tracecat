from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast

import pytest

from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor.backends.registry_helpers import (
    get_registry_tarball_uris,
    sort_registry_tarball_uris,
)
from tracecat.executor.service import RegistryArtifactsContext


@pytest.fixture
def test_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def test_sort_registry_tarball_uris_orders_builtin_first() -> None:
    artifacts = [
        RegistryArtifactsContext(
            origin="git+ssh://github.com/example/b.git",
            version="v1",
            tarball_uri="s3://bucket/b.tar.gz",
        ),
        RegistryArtifactsContext(
            origin="tracecat_registry",
            version="v1",
            tarball_uri="s3://bucket/builtin.tar.gz",
        ),
        RegistryArtifactsContext(
            origin="git+ssh://github.com/example/a.git",
            version="v1",
            tarball_uri="s3://bucket/a.tar.gz",
        ),
    ]

    assert sort_registry_tarball_uris(artifacts) == [
        "s3://bucket/builtin.tar.gz",
        "s3://bucket/a.tar.gz",
        "s3://bucket/b.tar.gz",
    ]


@pytest.mark.anyio
async def test_get_registry_tarball_uris_propagates_lookup_errors(
    test_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_data = cast(
        RunActionInput,
        SimpleNamespace(
            registry_lock=SimpleNamespace(origins={"tracecat_registry": "1.0.0"})
        ),
    )

    async def raise_lookup_error(*_args, **_kwargs):
        raise RuntimeError("artifact lookup failed")

    monkeypatch.setattr(
        "tracecat.executor.backends.registry_helpers.get_registry_artifacts_for_lock",
        raise_lookup_error,
    )

    with pytest.raises(RuntimeError, match="artifact lookup failed"):
        await get_registry_tarball_uris(input_data, test_role)
