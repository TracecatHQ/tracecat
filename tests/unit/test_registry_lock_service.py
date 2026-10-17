"""Tests for RegistryLockService.

This test suite validates:
1. Lock resolution uses current_version_id, not newest row wins
2. Platform registries are queried and available to orgs
3. Org registries override platform for the same origin
4. Helpful error when no current_version is set
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.dsl.enums import PlatformAction
from tracecat.exceptions import (
    BuiltinRegistryHasNoSelectionError,
    EntitlementRequired,
    RegistryLockAmbiguousActionError,
    RegistryLockInvalidDataError,
)
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.versions.schemas import (
    RegistryVersionManifest,
    registry_manifest_fingerprint,
)

pytestmark = pytest.mark.usefixtures("db")


def _make_manifest(action_names: list[str]) -> dict:
    """Create a test manifest with given action names."""
    actions = {}
    for name in action_names:
        parts = name.rsplit(".", 1)
        namespace = parts[0] if len(parts) > 1 else "test"
        action_name = parts[-1]
        actions[name] = {
            "namespace": namespace,
            "name": action_name,
            "action_type": "udf",
            "description": f"Test action {name}",
            "interface": {"expects": {}, "returns": None},
            "implementation": {
                "type": "udf",
                "url": "test_origin",
                "module": f"test.{namespace}",
                "name": action_name,
            },
        }
    return {"version": "1.0", "actions": actions}


def _make_template_manifest(
    *,
    template_action: str,
    step_action: str,
    include_step_action: bool = False,
) -> dict:
    """Create a manifest with a template action and a single step action."""
    template_namespace, template_name = template_action.rsplit(".", 1)
    actions = {
        template_action: {
            "namespace": template_namespace,
            "name": template_name,
            "action_type": "template",
            "description": f"Template action {template_action}",
            "interface": {"expects": {}, "returns": None},
            "implementation": {
                "type": "template",
                "template_action": {
                    "type": "action",
                    "definition": {
                        "name": template_name,
                        "namespace": template_namespace,
                        "title": "Template action",
                        "display_group": "Testing",
                        "expects": {},
                        "steps": [
                            {
                                "ref": "step1",
                                "action": step_action,
                                "args": {},
                            }
                        ],
                        "returns": "${{ steps.step1.result }}",
                    },
                },
            },
        },
    }

    if include_step_action:
        step_namespace, step_name = step_action.rsplit(".", 1)
        step_module = "tracecat_registry.core.script"
        step_fn = "run_python"
        if step_action == PlatformAction.CHILD_WORKFLOW_EXECUTE:
            step_module = "tracecat_registry.core.workflow"
            step_fn = "execute"
        actions[step_action] = {
            "namespace": step_namespace,
            "name": step_name,
            "action_type": "udf",
            "description": f"Platform action {step_action}",
            "interface": {"expects": {}, "returns": None},
            "implementation": {
                "type": "udf",
                "url": "tracecat_registry",
                "module": step_module,
                "name": step_fn,
            },
        }

    return {
        "version": "1.0",
        "actions": actions,
    }


async def _add_org_version(
    session: AsyncSession,
    role: Role,
    origin: str,
    version: str,
    manifest: dict,
    *,
    current: bool = True,
) -> RegistryVersion:
    """Add an org registry version, creating the repository if needed."""
    repo = await session.scalar(
        select(RegistryRepository).where(
            RegistryRepository.organization_id == role.organization_id,
            RegistryRepository.origin == origin,
        )
    )
    if repo is None:
        repo = RegistryRepository(organization_id=role.organization_id, origin=origin)
        session.add(repo)
        await session.flush()
    registry_version = RegistryVersion(
        organization_id=role.organization_id,
        repository_id=repo.id,
        version=version,
        manifest=manifest,
        tarball_uri=f"s3://org/{version}.tar.gz",
    )
    session.add(registry_version)
    await session.flush()
    if current:
        repo.current_version_id = registry_version.id
        session.add(repo)
    return registry_version


async def _add_platform_version(
    session: AsyncSession,
    origin: str,
    version: str,
    manifest: dict,
) -> PlatformRegistryVersion:
    """Add a current platform registry version, creating the repository if needed."""
    repo = await session.scalar(
        select(PlatformRegistryRepository).where(
            PlatformRegistryRepository.origin == origin,
        )
    )
    if repo is None:
        repo = PlatformRegistryRepository(origin=origin)
        session.add(repo)
        await session.flush()
    platform_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version=version,
        manifest=manifest,
        tarball_uri=f"s3://platform/{version}.tar.gz",
    )
    session.add(platform_version)
    await session.flush()
    repo.current_version_id = platform_version.id
    session.add(repo)
    return platform_version


@pytest.fixture(autouse=True)
async def seed_builtin_platform_registry(session: AsyncSession) -> None:
    """Seed the builtin platform registry unless a test explicitly clears it."""
    platform_repo = await session.scalar(
        select(PlatformRegistryRepository).where(
            PlatformRegistryRepository.origin == DEFAULT_REGISTRY_ORIGIN,
        )
    )
    if platform_repo is None:
        platform_repo = PlatformRegistryRepository(origin=DEFAULT_REGISTRY_ORIGIN)
        session.add(platform_repo)
        await session.flush()

    if platform_repo.current_version_id is None:
        platform_version = PlatformRegistryVersion(
            repository_id=platform_repo.id,
            version="seeded-builtin-1",
            manifest=_make_manifest(["core.transform.reshape"]),
            tarball_uri="s3://platform/seeded-builtin.tar.gz",
        )
        session.add(platform_version)
        await session.flush()
        platform_repo.current_version_id = platform_version.id
        session.add(platform_repo)

    await session.commit()


@pytest.mark.anyio
async def test_resolve_lock_uses_current_version(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Test that lock resolution uses current_version_id, not newest row wins."""
    await _add_org_version(
        session, svc_role, "test_origin", "1.0.0", _make_manifest(["test.action_a"])
    )
    # Version 2 is newer, but not current
    await _add_org_version(
        session,
        svc_role,
        "test_origin",
        "2.0.0",
        _make_manifest(["test.action_b"]),
        current=False,
    )
    await session.commit()

    service = RegistryLockService(session, role=svc_role)
    lock = await service.resolve_lock_with_bindings({"test.action_a"})

    # Should use version 1 (the current version), not version 2
    assert lock.origins["test_origin"] == "1.0.0"
    assert "test.action_a" in lock.actions

    # action_b should NOT be found because version 2 is not current
    with pytest.raises(RegistryLockInvalidDataError, match="not found in any registry"):
        await service.resolve_lock_with_bindings({"test.action_b"})


@pytest.mark.anyio
async def test_resolve_lock_queries_platform_registry(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Test that platform registries are available to organizations."""
    platform_version = await _add_platform_version(
        session,
        "platform_registry",
        "1.0.0",
        _make_manifest(["platform.shared_action"]),
    )
    await session.commit()

    service = RegistryLockService(session, role=svc_role)
    lock = await service.resolve_lock_with_bindings({"platform.shared_action"})

    assert "platform_registry" in lock.origins
    assert lock.origins["platform_registry"] == "1.0.0"
    assert lock.actions["platform.shared_action"] == "platform_registry"
    assert lock.origin_fingerprints["platform_registry"] == (
        registry_manifest_fingerprint(
            RegistryVersionManifest.model_validate(platform_version.manifest)
        )
    )


@pytest.mark.anyio
async def test_resolve_lock_raises_ambiguous_error_for_shadowed_builtin_action(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """A custom registry redefining a builtin action name is a typed collision."""
    custom_origin = "git+ssh://git@example.com/acme/custom-registry.git"
    await _add_org_version(
        session,
        svc_role,
        custom_origin,
        "1.0.0",
        _make_manifest(["core.transform.reshape"]),
    )
    await session.commit()

    service = RegistryLockService(session, role=svc_role)
    with pytest.raises(RegistryLockAmbiguousActionError) as exc_info:
        await service.resolve_lock_with_bindings({"core.transform.reshape"})

    assert exc_info.value.action_name == "core.transform.reshape"
    assert set(exc_info.value.origins) == {custom_origin, DEFAULT_REGISTRY_ORIGIN}
    assert "defined in multiple registries" in str(exc_info.value)
    assert isinstance(exc_info.value, RegistryLockInvalidDataError)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("custom_registry_enabled", "expected_version"),
    [
        pytest.param(True, "org-2.0", id="org_overrides_platform"),
        pytest.param(False, "platform-1.0", id="platform_when_custom_disabled"),
    ],
)
async def test_resolve_lock_shared_origin_precedence(
    svc_role: Role,
    session: AsyncSession,
    custom_registry_enabled: bool,
    expected_version: str,
) -> None:
    """Org registries override platform for the same origin unless custom
    registries are disabled."""
    origin = "shared_origin"
    action_name = "shared.action"
    await _add_platform_version(
        session, origin, "platform-1.0", _make_manifest([action_name])
    )
    await _add_org_version(
        session, svc_role, origin, "org-2.0", _make_manifest([action_name])
    )
    await session.commit()

    service = RegistryLockService(session, role=svc_role)
    if not custom_registry_enabled:
        service.has_entitlement = AsyncMock(return_value=False)  # pyright: ignore[reportAttributeAccessIssue]

    lock = await service.resolve_lock_with_bindings({action_name})

    assert lock.origins[origin] == expected_version
    assert lock.actions[action_name] == origin


@pytest.mark.anyio
async def test_resolve_lock_requires_entitlement_for_custom_only_action(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Custom-only actions should require custom registry entitlement."""
    await _add_org_version(
        session,
        svc_role,
        "git+ssh://git@github.com/acme/custom.git",
        "1.0.0",
        _make_manifest(["tools.custom.only_action"]),
    )
    await session.commit()

    service = RegistryLockService(session, role=svc_role)
    service.has_entitlement = AsyncMock(return_value=False)  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(EntitlementRequired, match="custom_registry") as exc_info:
        await service.resolve_lock_with_bindings({"tools.custom.only_action"})

    assert exc_info.value.detail is not None
    assert exc_info.value.detail["entitlement"] == "custom_registry"
    assert exc_info.value.detail["unavailable_actions"] == ["tools.custom.only_action"]
    assert "tools.custom.only_action" in str(exc_info.value)
    assert "\n\nUnavailable actions on your current plan:\n-" in str(exc_info.value)
    assert "git+ssh://git@github.com/acme/custom.git" not in str(exc_info.value)


@pytest.mark.anyio
async def test_resolve_lock_fails_without_current_version(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Test that lock resolution fails with helpful error when no current_version is set."""
    await _add_org_version(
        session,
        svc_role,
        "test_origin",
        "1.0.0",
        _make_manifest(["test.action"]),
        current=False,
    )
    await session.commit()

    service = RegistryLockService(session, role=svc_role)
    with pytest.raises(RegistryLockInvalidDataError, match="not found in any registry"):
        await service.resolve_lock_with_bindings({"test.action"})


@pytest.mark.anyio
async def test_resolve_lock_combines_platform_and_org(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Test that lock can contain actions from both platform and org registries."""
    await _add_platform_version(
        session, "platform_origin", "1.0.0", _make_manifest(["platform.action"])
    )
    await _add_org_version(
        session, svc_role, "org_origin", "2.0.0", _make_manifest(["org.action"])
    )
    await session.commit()

    service = RegistryLockService(session, role=svc_role)
    lock = await service.resolve_lock_with_bindings({"platform.action", "org.action"})

    assert "platform_origin" in lock.origins
    assert "org_origin" in lock.origins
    assert lock.actions["platform.action"] == "platform_origin"
    assert lock.actions["org.action"] == "org_origin"


@pytest.mark.anyio
async def test_resolve_lock_allows_template_step_with_run_python(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Template steps should allow core.script.run_python platform action."""
    template_action = "tools.testing.template_with_python"
    platform_action = PlatformAction.RUN_PYTHON

    # Use org-scoped origin override to avoid ambiguity with the built-in
    # `tracecat_registry` definition of `core.script.run_python`.
    await _add_org_version(
        session,
        svc_role,
        "tracecat_registry",
        "1.0.0",
        _make_template_manifest(
            template_action=template_action,
            step_action=platform_action,
            include_step_action=True,
        ),
    )
    await session.commit()

    service = RegistryLockService(session, role=svc_role)
    lock = await service.resolve_lock_with_bindings({template_action})

    assert template_action in lock.actions
    assert platform_action in lock.actions


@pytest.mark.anyio
async def test_resolve_lock_always_includes_builtin_registry(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Builtin tracecat_registry must be in the lock as a runtime dependency.

    Custom actions import decorators/secrets/context from tracecat_registry,
    so the ephemeral nsjail sandbox must always mount the builtin tarball —
    even when no workflow action directly resolves to it.
    """
    await _add_platform_version(
        session,
        "tracecat_registry",
        "1.0.0-beta.39",
        _make_manifest(["core.transform"]),
    )
    # Org registry with a custom action that the workflow uses
    await _add_org_version(
        session,
        svc_role,
        "git+ssh://git@github.com/acme/internal.git",
        "abc123",
        _make_manifest(["integrations.greetings.say_hello"]),
    )
    await session.commit()

    # Workflow uses only the custom action — does NOT map to tracecat_registry
    service = RegistryLockService(session, role=svc_role)
    lock = await service.resolve_lock_with_bindings(
        {"integrations.greetings.say_hello"}
    )

    # Custom origin is present because its action is used
    assert "git+ssh://git@github.com/acme/internal.git" in lock.origins
    assert lock.actions["integrations.greetings.say_hello"] == (
        "git+ssh://git@github.com/acme/internal.git"
    )

    # Builtin registry is present as a runtime dependency, even though no
    # workflow action resolves to it.
    assert "tracecat_registry" in lock.origins
    assert lock.origins["tracecat_registry"] == "1.0.0-beta.39"
    # But no action is bound to it
    assert "tracecat_registry" not in lock.actions.values()


@pytest.mark.anyio
async def test_resolve_lock_overwrites_builtin_origin_with_platform_version(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Builtin lock origin should always resolve to the platform selected version."""
    platform_version = await _add_platform_version(
        session, "tracecat_registry", "2.0.0-beta.1", _make_manifest(["core.transform"])
    )
    await _add_org_version(
        session,
        svc_role,
        "tracecat_registry",
        "org-override-1",
        _make_manifest(["tools.custom.same_origin"]),
    )
    await session.commit()

    service = RegistryLockService(session, role=svc_role)
    lock = await service.resolve_lock_with_bindings({"tools.custom.same_origin"})

    assert lock.actions["tools.custom.same_origin"] == "tracecat_registry"
    assert lock.origins["tracecat_registry"] == "2.0.0-beta.1"
    assert lock.origin_fingerprints[
        "tracecat_registry"
    ] == registry_manifest_fingerprint(
        RegistryVersionManifest.model_validate(platform_version.manifest)
    )


@pytest.mark.anyio
async def test_resolve_lock_raises_when_platform_builtin_not_synced(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Unsynced builtin registry should block lock freezing until sync completes."""

    # Simulate an unsynced platform by clearing any seeded builtin version.
    seeded_platform_repo = await session.scalar(
        select(PlatformRegistryRepository).where(
            PlatformRegistryRepository.origin == "tracecat_registry",
        )
    )
    if seeded_platform_repo is not None:
        seeded_platform_repo.current_version_id = None
        session.add(seeded_platform_repo)
        await session.flush()

    await _add_org_version(
        session,
        svc_role,
        "git+ssh://git@github.com/acme/internal.git",
        "abc123",
        _make_manifest(["integrations.greetings.say_hello"]),
    )
    await session.commit()

    service = RegistryLockService(session, role=svc_role)
    with pytest.raises(BuiltinRegistryHasNoSelectionError, match="retry shortly"):
        await service.resolve_lock_with_bindings({"integrations.greetings.say_hello"})


@pytest.mark.anyio
async def test_resolve_lock_rejects_template_step_with_other_platform_action(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Template steps should still reject unsupported platform actions."""
    template_action = "tools.testing.template_with_subflow"
    platform_action = PlatformAction.CHILD_WORKFLOW_EXECUTE

    await _add_platform_version(
        session,
        "platform_registry",
        "1.0.0",
        _make_template_manifest(
            template_action=template_action,
            step_action=platform_action,
        ),
    )
    await session.commit()

    service = RegistryLockService(session, role=svc_role)
    with pytest.raises(
        RegistryLockInvalidDataError,
        match="Platform actions cannot be used inside templates",
    ):
        await service.resolve_lock_with_bindings({template_action})
