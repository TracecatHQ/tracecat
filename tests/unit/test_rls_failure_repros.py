from __future__ import annotations

import ast
import inspect
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from tracecat.api.app import info, lifespan
from tracecat.auth.credentials import _role_dependency
from tracecat.auth.discovery import AuthDiscoveryService
from tracecat.auth.types import Role
from tracecat.auth.users import UserManager
from tracecat.cases.triggers.consumer import CaseTriggerConsumer
from tracecat.contexts import ctx_role
from tracecat.db import rls as rls_module
from tracecat.db.models import Base, OrganizationModel, WorkspaceModel
from tracecat.db.rls import set_rls_context, set_rls_context_from_role
from tracecat.executor.registry_resolver import _get_manifest_entry
from tracecat.executor.service import get_registry_artifacts_for_lock
from tracecat.organization.router import (
    accept_invitation,
    get_invitation_by_token,
    list_my_pending_invitations,
)
from tracecat.registry.sync.jobs import sync_platform_registry_on_startup
from tracecat.service import BaseOrgService
from tracecat.settings.service import get_setting
from tracecat.webhooks.dependencies import validate_incoming_webhook

RLS_MIGRATION_PATH = Path("alembic/versions/c76f9b01fad7_add_rls_policies.py")


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit repro tests."""
    yield


def _extract_list_constant(name: str) -> list[str]:
    module = ast.parse(RLS_MIGRATION_PATH.read_text())
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                value = ast.literal_eval(node.value)
                if isinstance(value, list) and all(
                    isinstance(item, str) for item in value
                ):
                    return value
                raise AssertionError(f"{name} must be a list[str]")
    raise AssertionError(f"{name} not found in {RLS_MIGRATION_PATH}")


def _extract_modeled_tables(
    scope_model: type[Any],
    *,
    excluded: set[str] | None = None,
) -> set[str]:
    excluded = excluded or set()
    table_names: set[str] = set()
    for mapper in Base.registry.mappers:
        mapped_cls = cast(type[Any], mapper.class_)
        if not issubclass(mapped_cls, scope_model):
            continue
        table_name = getattr(mapped_cls, "__tablename__", None)
        if isinstance(table_name, str) and table_name not in excluded:
            table_names.add(table_name)
    return table_names


def _extract_tables_with_column(
    column_name: str,
    *,
    excluded: set[str] | None = None,
) -> set[str]:
    excluded = excluded or set()
    table_names: set[str] = set()
    for mapper in Base.registry.mappers:
        local_table = mapper.local_table
        table_name = getattr(local_table, "name", None)
        if (
            isinstance(table_name, str)
            and column_name in local_table.columns
            and table_name not in excluded
        ):
            table_names.add(table_name)
    return table_names


@pytest.mark.anyio
async def test_authenticate_user_membership_check_is_not_done_under_deny_default() -> (
    None
):
    """User auth should not run under deny-default request session context."""
    workspace_id = uuid.uuid4()
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.auth_cache = None
    session = AsyncMock()
    session.sync_session = MagicMock()
    session.sync_session.info = {
        rls_module._RLS_CONTEXT_INFO_KEY: rls_module._RLSContext(  # pyright: ignore[reportPrivateUsage]
            org_id=None,
            workspace_id=None,
            user_id=None,
            bypass=False,
        )
    }
    user = MagicMock()
    user.id = user_id
    user.is_superuser = False

    role = Role(
        type="user",
        workspace_id=workspace_id,
        organization_id=org_id,
        user_id=user_id,
        service_id="tracecat-api",
    )
    validated_role = role.model_copy(update={"scopes": frozenset({"tests:read"})})

    async def _assert_authenticate_user_context(**kwargs) -> Role:  # noqa: ANN003
        auth_session = kwargs["session"]
        context = auth_session.sync_session.info.get(
            rls_module._RLS_CONTEXT_INFO_KEY  # pyright: ignore[reportPrivateUsage]
        )
        assert isinstance(
            context,
            rls_module._RLSContext,  # pyright: ignore[reportPrivateUsage]
        )
        # Auth should not run under deny-default (no tenant + bypass off).
        assert (
            context.bypass
            or context.workspace_id is not None
            or context.org_id is not None
        )
        return role

    with (
        patch(
            "tracecat.auth.credentials._authenticate_user",
            new=AsyncMock(side_effect=_assert_authenticate_user_context),
        ),
        patch(
            "tracecat.auth.credentials._validate_role",
            new=AsyncMock(return_value=validated_role),
        ),
    ):
        resolved_role = await _role_dependency(
            request=request,
            session=session,
            workspace_id=workspace_id,
            user=user,
            api_key=None,
            allow_user=True,
            allow_service=False,
            allow_executor=False,
            require_workspace="yes",
        )

    assert resolved_role.organization_id == org_id
    assert resolved_role.workspace_id == workspace_id
    assert resolved_role.user_id == user_id


@pytest.mark.anyio
async def test_set_rls_context_from_role_applies_gucs_even_when_feature_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Role-based context setup must still write GUCs when policies are installed."""
    session = AsyncMock()
    session.sync_session = MagicMock()
    session.sync_session.info = {}

    monkeypatch.setattr("tracecat.db.rls.config.TRACECAT__FEATURE_FLAGS", set())

    role = Role(
        type="service",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        service_id="tracecat-service",
        scopes=frozenset({"*"}),
    )

    await set_rls_context_from_role(session, role)

    session.execute.assert_called_once()


@pytest.mark.anyio
async def test_set_rls_context_applies_gucs_even_when_feature_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RLS context must still be written when DB policies are installed."""
    session = AsyncMock()
    session.sync_session = MagicMock()
    session.sync_session.info = {}

    monkeypatch.setattr("tracecat.db.rls.config.TRACECAT__FEATURE_FLAGS", set())

    await set_rls_context(
        session,
        org_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        bypass=True,
    )

    session.execute.assert_called_once()


def test_rls_after_begin_reapplies_cached_context_when_feature_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transaction begin should still reapply cached context when policies exist."""
    session = MagicMock()
    connection = MagicMock()

    session.info = {
        rls_module._RLS_CONTEXT_INFO_KEY: rls_module._RLSContext(  # pyright: ignore[reportPrivateUsage]
            org_id=str(uuid.uuid4()),
            workspace_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            bypass=False,
        )
    }

    monkeypatch.setattr("tracecat.db.rls.is_rls_enabled", lambda: False)

    rls_module._reapply_rls_context_after_begin(  # pyright: ignore[reportPrivateUsage]
        session=session,
        transaction=MagicMock(),
        connection=connection,
    )

    connection.execute.assert_called_once()


def test_rls_migration_covers_all_workspace_scoped_case_tables() -> None:
    workspace_tables = set(_extract_list_constant("WORKSPACE_SCOPED_TABLES"))
    modeled_workspace_tables = _extract_modeled_tables(WorkspaceModel)

    missing_workspace_tables = modeled_workspace_tables - workspace_tables

    assert not missing_workspace_tables, (
        f"Missing workspace-scoped tables in migration: "
        f"{sorted(missing_workspace_tables)}"
    )


def test_rls_migration_covers_org_registry_tables() -> None:
    org_tables = set(_extract_list_constant("ORG_SCOPED_TABLES"))
    modeled_org_tables = _extract_modeled_tables(
        OrganizationModel, excluded={"workspace"}
    )

    missing_org_tables = modeled_org_tables - org_tables

    assert not missing_org_tables, (
        f"Missing organization-scoped tables in migration: {sorted(missing_org_tables)}"
    )


def test_rls_migration_sanity_all_tenant_keyed_tables_are_covered_or_allowlisted() -> (
    None
):
    """Every tenant-keyed table should be policy-covered or explicitly allowlisted."""
    workspace_policy_tables = set(_extract_list_constant("WORKSPACE_SCOPED_TABLES"))
    org_policy_tables = set(_extract_list_constant("ORG_SCOPED_TABLES"))
    org_optional_workspace_policy_tables = set(
        _extract_list_constant("ORG_OPTIONAL_WORKSPACE_SCOPED_TABLES")
    )

    # Workspace has special policy SQL in migration (outside ORG_SCOPED_TABLES).
    special_org_policy_tables = {"workspace", "scope"}

    # Keep exclusions empty by default and only add with explicit rationale.
    intentional_workspace_exclusions: set[str] = set()
    intentional_org_exclusions: set[str] = set()

    workspace_keyed_tables = _extract_tables_with_column("workspace_id")
    org_keyed_tables = _extract_tables_with_column("organization_id")

    missing_workspace_coverage = workspace_keyed_tables - (
        workspace_policy_tables
        | org_optional_workspace_policy_tables
        | intentional_workspace_exclusions
    )
    missing_org_coverage = org_keyed_tables - (
        org_policy_tables
        | org_optional_workspace_policy_tables
        | special_org_policy_tables
        | intentional_org_exclusions
    )

    stale_workspace_exclusions = (
        intentional_workspace_exclusions - workspace_keyed_tables
    )
    stale_org_exclusions = intentional_org_exclusions - org_keyed_tables

    assert not stale_workspace_exclusions, (
        f"Stale workspace exclusions: {sorted(stale_workspace_exclusions)}"
    )
    assert not stale_org_exclusions, (
        f"Stale organization exclusions: {sorted(stale_org_exclusions)}"
    )
    assert not missing_workspace_coverage, (
        f"Workspace-keyed tables missing RLS coverage: "
        f"{sorted(missing_workspace_coverage)}"
    )
    assert not missing_org_coverage, (
        f"Organization-keyed tables missing RLS coverage: "
        f"{sorted(missing_org_coverage)}"
    )


def test_validate_incoming_webhook_uses_bypass_session_manager() -> None:
    source = inspect.getsource(validate_incoming_webhook)
    assert "get_async_session_bypass_rls_context_manager" in source


def test_case_trigger_consumer_uses_bypass_session_manager() -> None:
    source = inspect.getsource(CaseTriggerConsumer._process_message)
    assert "get_async_session_bypass_rls_context_manager" in source


def _source_has_auth_safe_rls_bootstrap(source: str) -> bool:
    return any(
        marker in source
        for marker in (
            "AsyncDBSessionBypass",
            "get_async_session_bypass_rls_context_manager",
            "set_rls_context(",
        )
    )


def test_accept_invitation_bootstraps_rls_for_no_org_context() -> None:
    source = inspect.getsource(accept_invitation)
    assert _source_has_auth_safe_rls_bootstrap(source), (
        "accept_invitation must use a bypass session or explicitly prime RLS context "
        "before invitation reads/writes"
    )


def test_list_my_pending_invitations_uses_bypass_session_dependency() -> None:
    source = inspect.getsource(list_my_pending_invitations)
    assert "session: AsyncDBSessionBypass" in source


def test_get_invitation_by_token_uses_bypass_session_dependency() -> None:
    source = inspect.getsource(get_invitation_by_token)
    assert "session: AsyncDBSessionBypass" in source


def test_api_lifespan_rbac_seeding_uses_bypass_session_manager() -> None:
    source = inspect.getsource(lifespan)
    assert "get_async_session_bypass_rls_context_manager" in source


def test_registry_sync_startup_uses_bypass_session_manager() -> None:
    source = inspect.getsource(sync_platform_registry_on_startup)
    assert "get_async_session_bypass_rls_context_manager" in source


@pytest.mark.anyio
async def test_info_endpoint_handles_missing_saml_setting_without_keyerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSettingsService:
        def __init__(self, session, role) -> None:  # noqa: ANN001, ANN204
            self.session = session
            self.role = role

        async def list_org_settings(self, *, keys: set[str]) -> list[object]:
            return []

        def get_value(self, setting: object) -> object:
            return setting

    session = AsyncMock()
    monkeypatch.setattr(
        "tracecat.api.app.get_default_organization_id",
        AsyncMock(return_value=uuid.uuid4()),
    )
    monkeypatch.setattr("tracecat.api.app.SettingsService", FakeSettingsService)
    monkeypatch.setattr("tracecat.api.app.get_setting_override", lambda key: None)

    response = await info(session)

    assert isinstance(response.saml_enabled, bool)


@pytest.mark.anyio
async def test_base_org_service_with_session_sets_role_before_session_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """with_session(role=...) should open the DB session with that role context."""

    class DummyOrgService(BaseOrgService):
        service_name = "dummy_org"

    role = Role(
        type="service",
        service_id="tracecat-service",
        organization_id=uuid.uuid4(),
        scopes=frozenset({"*"}),
    )
    session = AsyncMock()
    observed: list[Role | None] = []

    @asynccontextmanager
    async def _fake_session_cm() -> AsyncIterator[AsyncMock]:
        observed.append(ctx_role.get())
        yield session

    monkeypatch.setattr(
        "tracecat.service.get_async_session_context_manager", _fake_session_cm
    )

    async with DummyOrgService.with_session(role=role):
        pass

    assert observed == [role]


def test_get_setting_primes_rls_context_when_session_is_provided() -> None:
    source = inspect.getsource(get_setting)
    assert "set_rls_context_from_role" in source


def test_user_manager_saml_check_does_not_reuse_unscoped_request_session() -> None:
    source = inspect.getsource(UserManager._is_org_saml_enforced)
    assert "session=self._user_db.session" not in source


def test_auth_discovery_saml_check_does_not_reuse_unscoped_request_session() -> None:
    source = inspect.getsource(AuthDiscoveryService._org_saml_enabled)
    assert "session=self.session" not in source


def test_registry_artifact_lookup_uses_bypass_session_manager() -> None:
    source = inspect.getsource(get_registry_artifacts_for_lock)
    assert "get_async_session_bypass_rls_context_manager" in source


def test_registry_manifest_lookup_uses_bypass_session_manager() -> None:
    source = inspect.getsource(_get_manifest_entry)
    assert "get_async_session_bypass_rls_context_manager" in source
