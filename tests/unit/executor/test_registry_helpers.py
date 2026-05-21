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
from tracecat.executor.registry_artifacts import bundled_builtin_registry_uri
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
async def test_get_registry_tarball_uris_uses_bundled_current_builtin(
    test_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_version = "1.2.3"
    input_data = cast(
        RunActionInput,
        SimpleNamespace(
            registry_lock=SimpleNamespace(
                origins={"tracecat_registry": current_version}
            )
        ),
    )

    async def fail_lookup(*_args, **_kwargs):
        pytest.fail("current builtin registry should not query artifact storage")

    monkeypatch.setattr(
        "tracecat.executor.backends.registry_helpers.tracecat_registry.__version__",
        current_version,
    )
    monkeypatch.setattr(
        "tracecat.executor.backends.registry_helpers.get_registry_artifacts_for_lock",
        fail_lookup,
    )

    assert await get_registry_tarball_uris(input_data, test_role) == [
        bundled_builtin_registry_uri(current_version)
    ]


@pytest.mark.anyio
async def test_get_registry_tarball_uris_looks_up_only_non_current_origins(
    test_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_version = "1.2.3"
    custom_origin = "git+ssh://github.com/example/custom.git"
    input_data = cast(
        RunActionInput,
        SimpleNamespace(
            registry_lock=SimpleNamespace(
                origins={
                    "tracecat_registry": current_version,
                    custom_origin: "abc123",
                }
            )
        ),
    )

    async def get_artifacts(
        origins: dict[str, str],
        organization_id: uuid.UUID,
    ) -> list[RegistryArtifactsContext]:
        assert origins == {custom_origin: "abc123"}
        assert organization_id == test_role.organization_id
        return [
            RegistryArtifactsContext(
                origin=custom_origin,
                version="abc123",
                tarball_uri="s3://bucket/custom/site-packages.tar.gz",
            )
        ]

    monkeypatch.setattr(
        "tracecat.executor.backends.registry_helpers.tracecat_registry.__version__",
        current_version,
    )
    monkeypatch.setattr(
        "tracecat.executor.backends.registry_helpers.get_registry_artifacts_for_lock",
        get_artifacts,
    )

    assert await get_registry_tarball_uris(input_data, test_role) == [
        bundled_builtin_registry_uri(current_version),
        "s3://bucket/custom/site-packages.tar.gz",
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
        "tracecat.executor.backends.registry_helpers.tracecat_registry.__version__",
        "2.0.0",
    )
    monkeypatch.setattr(
        "tracecat.executor.backends.registry_helpers.get_registry_artifacts_for_lock",
        raise_lookup_error,
    )

    with pytest.raises(RuntimeError, match="artifact lookup failed"):
        await get_registry_tarball_uris(input_data, test_role)
