"""Tenant RLS on tables that inherit their tenant from parent rows.

The test database is built with ``create_all``, so each test installs the
production policy SQL inside its own transaction and reads as a non-owner,
non-BYPASSRLS role. Everything, including the role, rolls back afterwards.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.enums import ScopeSource
from tracecat.cases.attachments.schemas import CaseAttachmentCreate
from tracecat.cases.attachments.service import CaseAttachmentService
from tracecat.cases.dropdowns.schemas import (
    CaseDropdownDefinitionCreate,
    CaseDropdownOptionCreate,
    CaseDropdownValueSet,
)
from tracecat.cases.dropdowns.service import (
    CaseDropdownDefinitionsService,
    CaseDropdownValuesService,
)
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import CaseCreate
from tracecat.cases.service import CasesService
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import (
    CaseAttachment,
    CaseDropdownDefinition,
    CaseDropdownOption,
    CaseDropdownValue,
    CaseTag,
    CaseTagLink,
    File,
    Organization,
    Ownership,
    RoleScope,
    Scope,
    Table,
    TableColumn,
    Workflow,
    WorkflowTag,
    WorkflowTagLink,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.db.rls import set_rls_context
from tracecat.db.tenant_rls import (
    PARENT_SCOPED_TABLES,
    enable_org_table_rls,
    enable_parent_scoped_table_rls,
    enable_scope_table_rls,
    enable_workspace_table_rls,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.tables.service import TablesService
from tracecat.workflow.tags.service import WorkflowTagsService

pytestmark = pytest.mark.usefixtures("db")

WORKSPACE_PARENT_TABLES = (
    "case",
    "case_tag",
    "case_dropdown_definition",
    "file",
    "workflow",
    "workflow_tag",
    "tables",
)


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for these tests."""
    yield


@dataclass(frozen=True, slots=True)
class Tenant:
    """One tenant's parent rows, each with one child row in every table."""

    role: Role
    org_id: uuid.UUID
    workspace_id: uuid.UUID
    case_id: uuid.UUID
    case_tag_id: uuid.UUID
    definition_id: uuid.UUID
    option_id: uuid.UUID
    file_id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_tag_id: uuid.UUID
    table_id: uuid.UUID
    db_role_id: uuid.UUID
    scope_id: uuid.UUID


async def _seed_tenant(session: AsyncSession, role: Role) -> Tenant:
    assert role.workspace_id is not None
    assert role.organization_id is not None
    workspace_id = role.workspace_id
    org_id = role.organization_id
    suffix = uuid.uuid4().hex[:8]

    case = await CasesService(session=session, role=role).create_case(
        CaseCreate(
            summary="RLS probe",
            description="parent-scoped RLS probe",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )
    case_tag = CaseTag(workspace_id=workspace_id, name="probe", ref="probe")
    definition = CaseDropdownDefinition(
        workspace_id=workspace_id, name="Verdict", ref="verdict"
    )
    file = File(
        workspace_id=workspace_id,
        sha256=uuid.uuid4().hex * 2,
        name="probe.txt",
        content_type="text/plain",
        size=1,
    )
    workflow = Workflow(workspace_id=workspace_id, title="Probe", description="")
    workflow_tag = WorkflowTag(workspace_id=workspace_id, name="probe", ref="probe")
    table = Table(workspace_id=workspace_id, name=f"probe_{suffix}")
    db_role = DBRole(name=f"probe-{suffix}", organization_id=org_id)
    scope = Scope(
        name=f"probe:{suffix}",
        resource="probe",
        action=suffix,
        source=ScopeSource.CUSTOM,
        organization_id=org_id,
    )
    session.add_all(
        [case_tag, definition, file, workflow, workflow_tag, table, db_role, scope]
    )
    await session.flush()

    option = CaseDropdownOption(definition_id=definition.id, label="Yes", ref="yes")
    session.add(option)
    await session.flush()
    session.add_all(
        [
            CaseAttachment(case_id=case.id, file_id=file.id),
            CaseDropdownValue(
                case_id=case.id, definition_id=definition.id, option_id=option.id
            ),
            CaseTagLink(case_id=case.id, tag_id=case_tag.id),
            WorkflowTagLink(workflow_id=workflow.id, tag_id=workflow_tag.id),
            TableColumn(table_id=table.id, name="label", type="TEXT"),
            RoleScope(role_id=db_role.id, scope_id=scope.id),
            Ownership(
                resource_id=str(workspace_id),
                resource_type="workspace",
                owner_id=org_id,
                owner_type="organization",
            ),
        ]
    )
    await session.commit()
    return Tenant(
        role=role,
        org_id=org_id,
        workspace_id=workspace_id,
        case_id=case.id,
        case_tag_id=case_tag.id,
        definition_id=definition.id,
        option_id=option.id,
        file_id=file.id,
        workflow_id=workflow.id,
        workflow_tag_id=workflow_tag.id,
        table_id=table.id,
        db_role_id=db_role.id,
        scope_id=scope.id,
    )


@pytest.fixture
async def tenant_a(session: AsyncSession, svc_role: Role) -> Tenant:
    return await _seed_tenant(session, svc_role)


@pytest.fixture
async def tenant_b(session: AsyncSession, svc_role: Role) -> Tenant:
    """A second tenant in another organization and workspace."""
    org = Organization(
        name="RLS probe B", slug=f"rls-probe-{uuid.uuid4().hex[:8]}", is_active=True
    )
    session.add(org)
    await session.flush()
    workspace = Workspace(name="rls-probe-b", organization_id=org.id)
    session.add(workspace)
    await session.commit()
    role = svc_role.model_copy(
        update={
            "organization_id": org.id,
            "workspace_id": workspace.id,
            "user_id": uuid.uuid4(),
        }
    )
    return await _seed_tenant(session, role)


@pytest.fixture
async def enforced_db_role(
    session: AsyncSession, tenant_a: Tenant, tenant_b: Tenant
) -> str:
    """Install the production policies and return a role they apply to."""
    scripts = [
        *(enable_workspace_table_rls(table) for table in WORKSPACE_PARENT_TABLES),
        enable_org_table_rls("role"),
        enable_scope_table_rls(),
        *(enable_parent_scoped_table_rls(t) for t in sorted(PARENT_SCOPED_TABLES)),
    ]
    # Seeding left deferred FK checks queued; ALTER TABLE refuses to run past them.
    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    for script in scripts:
        # asyncpg runs one statement per call; policy SQL has no inner semicolons.
        for statement in filter(str.strip, script.split(";")):
            await session.execute(text(statement))

    db_role = f"rls_child_{uuid.uuid4().hex[:12]}"
    await session.execute(text(f'CREATE ROLE "{db_role}" NOSUPERUSER NOBYPASSRLS'))
    await session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{db_role}"'))
    await session.execute(
        text(f'GRANT ALL ON ALL TABLES IN SCHEMA public TO "{db_role}"')
    )
    await session.execute(
        text(f'GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO "{db_role}"')
    )
    await session.flush()
    return db_role


@asynccontextmanager
async def _as_tenant(
    session: AsyncSession, db_role: str, tenant: Tenant
) -> AsyncGenerator[None]:
    await session.execute(text(f'SET ROLE "{db_role}"'))
    try:
        await set_rls_context(
            session,
            org_id=tenant.org_id,
            workspace_id=tenant.workspace_id,
            user_id=tenant.role.user_id,
            bypass=False,
        )
        yield
    finally:
        await session.execute(text("RESET ROLE"))


# Each table's tenant marker: the parent column and the tenant's value for it.
TENANT_MARKERS: dict[str, tuple[str, Callable[[Tenant], uuid.UUID]]] = {
    "case_attachment": ("case_id", lambda t: t.case_id),
    "case_dropdown_option": ("definition_id", lambda t: t.definition_id),
    "case_dropdown_value": ("case_id", lambda t: t.case_id),
    "case_tag_link": ("case_id", lambda t: t.case_id),
    "workflow_tag_link": ("workflow_id", lambda t: t.workflow_id),
    "table_column": ("table_id", lambda t: t.table_id),
    "role_scope": ("role_id", lambda t: t.db_role_id),
    "ownership": ("owner_id", lambda t: t.org_id),
}

# Writes as tenant A that reference at least one of tenant B's parent rows.
CROSS_TENANT_INSERTS: dict[
    str, tuple[str, Callable[[Tenant, Tenant], dict[str, uuid.UUID]]]
] = {
    "case_attachment": (
        "INSERT INTO case_attachment (id, case_id, file_id) "
        "VALUES (gen_random_uuid(), :a, :b)",
        lambda a, b: {"a": a.case_id, "b": b.file_id},
    ),
    "case_dropdown_option": (
        "INSERT INTO case_dropdown_option (id, definition_id, label, ref, position) "
        "VALUES (gen_random_uuid(), :b, 'No', 'no', 1)",
        lambda a, b: {"b": b.definition_id},
    ),
    "case_dropdown_value": (
        "UPDATE case_dropdown_value SET option_id = :b WHERE case_id = :a",
        lambda a, b: {"a": a.case_id, "b": b.option_id},
    ),
    "case_tag_link": (
        "INSERT INTO case_tag_link (case_id, tag_id) VALUES (:a, :b)",
        lambda a, b: {"a": a.case_id, "b": b.case_tag_id},
    ),
    "workflow_tag_link": (
        "INSERT INTO workflow_tag_link (workflow_id, tag_id) VALUES (:a, :b)",
        lambda a, b: {"a": a.workflow_id, "b": b.workflow_tag_id},
    ),
    "table_column": (
        "INSERT INTO table_column (id, table_id, name, type, nullable, created_at, "
        "updated_at) VALUES (gen_random_uuid(), :b, 'x', 'TEXT', true, now(), now())",
        lambda a, b: {"b": b.table_id},
    ),
    "role_scope": (
        "INSERT INTO role_scope (role_id, scope_id) VALUES (:a, :b)",
        lambda a, b: {"a": a.db_role_id, "b": b.scope_id},
    ),
    "ownership": (
        "INSERT INTO ownership (resource_id, resource_type, owner_id, owner_type) "
        "VALUES (gen_random_uuid()::text, 'workspace', :b, 'organization')",
        lambda a, b: {"b": b.org_id},
    ),
}


def test_behaviour_tables_cover_every_parent_scoped_table() -> None:
    assert set(TENANT_MARKERS) == PARENT_SCOPED_TABLES
    assert set(CROSS_TENANT_INSERTS) == PARENT_SCOPED_TABLES


@pytest.mark.anyio
class TestParentScopedIsolation:
    @pytest.mark.parametrize("table", sorted(PARENT_SCOPED_TABLES))
    async def test_tenant_reads_only_its_own_rows(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        tenant_b: Tenant,
        enforced_db_role: str,
        table: str,
    ) -> None:
        column, marker = TENANT_MARKERS[table]
        query = text(
            f'SELECT {column} FROM "{table}" WHERE {column} IN (:a, :b)'
        ).bindparams(a=marker(tenant_a), b=marker(tenant_b))

        for reader in (tenant_a, tenant_b):
            async with _as_tenant(session, enforced_db_role, reader):
                rows = (await session.execute(query)).scalars().all()
            assert rows == [marker(reader)]

    @pytest.mark.parametrize("table", sorted(PARENT_SCOPED_TABLES))
    async def test_tenant_cannot_write_rows_pointing_at_another_tenant(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        tenant_b: Tenant,
        enforced_db_role: str,
        table: str,
    ) -> None:
        statement, params = CROSS_TENANT_INSERTS[table]
        async with _as_tenant(session, enforced_db_role, tenant_a):
            with pytest.raises(
                ProgrammingError, match="violates row-level security policy"
            ):
                async with session.begin_nested():
                    await session.execute(text(statement), params(tenant_a, tenant_b))

    async def test_dropdown_value_cannot_use_another_dropdowns_option(
        self, session: AsyncSession, tenant_a: Tenant, enforced_db_role: str
    ) -> None:
        other = CaseDropdownDefinition(
            workspace_id=tenant_a.workspace_id, name="Other", ref="other"
        )
        session.add(other)
        await session.flush()
        other_option = CaseDropdownOption(definition_id=other.id, label="X", ref="x")
        session.add(other_option)
        await session.flush()

        async with _as_tenant(session, enforced_db_role, tenant_a):
            with pytest.raises(
                ProgrammingError, match="violates row-level security policy"
            ):
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "UPDATE case_dropdown_value SET option_id = :option "
                            "WHERE case_id = :case"
                        ),
                        {"option": other_option.id, "case": tenant_a.case_id},
                    )


@pytest.mark.anyio
class TestFlowsUnderEnforcedRls:
    async def test_case_tags_add_list_and_remove(
        self, session: AsyncSession, tenant_a: Tenant, enforced_db_role: str
    ) -> None:
        service = CaseTagsService(session=session, role=tenant_a.role)
        async with _as_tenant(session, enforced_db_role, tenant_a):
            tag = await service.add_case_tag(
                tenant_a.case_id, "escalated", create_if_missing=True
            )
            tags = await service.list_tags_for_case(tenant_a.case_id)
            assert {t.id for t in tags} == {tenant_a.case_tag_id, tag.id}

            await service.remove_case_tag(tenant_a.case_id, str(tag.id))
            tags = await service.list_tags_for_case(tenant_a.case_id)
            assert [t.id for t in tags] == [tenant_a.case_tag_id]

    async def test_case_dropdowns_create_set_and_list(
        self, session: AsyncSession, tenant_a: Tenant, enforced_db_role: str
    ) -> None:
        definitions = CaseDropdownDefinitionsService(
            session=session, role=tenant_a.role
        )
        values = CaseDropdownValuesService(session=session, role=tenant_a.role)
        async with _as_tenant(session, enforced_db_role, tenant_a):
            definition = await definitions.create_definition(
                CaseDropdownDefinitionCreate(
                    name="Severity call",
                    ref="severity_call",
                    options=[CaseDropdownOptionCreate(label="High", ref="high")],
                )
            )
            assert [o.ref for o in definition.options] == ["high"]

            await values.set_value(
                tenant_a.case_id,
                definition.id,
                CaseDropdownValueSet(option_id=definition.options[0].id),
            )
            listed = await values.list_values_for_case(tenant_a.case_id)
            assert {(v.definition_id, v.option_id) for v in listed} == {
                (tenant_a.definition_id, tenant_a.option_id),
                (definition.id, definition.options[0].id),
            }

    async def test_case_attachments_create_and_list(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        enforced_db_role: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            CaseAttachmentService, "_upload_to_attachments_bucket", AsyncMock()
        )
        service = CaseAttachmentService(session=session, role=tenant_a.role)
        content = b"incident notes"
        async with _as_tenant(session, enforced_db_role, tenant_a):
            case = await CasesService(session=session, role=tenant_a.role).get_case(
                tenant_a.case_id
            )
            assert case is not None
            created = await service.create_attachment(
                case,
                CaseAttachmentCreate(
                    file_name="notes.txt",
                    content_type="text/plain",
                    size=len(content),
                    content=content,
                ),
            )
            listed = await service.list_attachments(case)
        assert {a.id for a in listed} >= {created.id}
        assert {a.file_id for a in listed} == {tenant_a.file_id, created.file_id}

    async def test_workflow_tags_add_list_and_remove(
        self, session: AsyncSession, tenant_a: Tenant, enforced_db_role: str
    ) -> None:
        service = WorkflowTagsService(session=session, role=tenant_a.role)
        workflow_id = WorkflowUUID.new(tenant_a.workflow_id)
        async with _as_tenant(session, enforced_db_role, tenant_a):
            tag = WorkflowTag(
                workspace_id=tenant_a.workspace_id, name="triage", ref="triage"
            )
            session.add(tag)
            await session.flush()
            link = await service.add_workflow_tag(workflow_id, tag.id)
            tags = await service.list_tags_for_workflow(workflow_id)
            assert {t.id for t in tags} == {tenant_a.workflow_tag_id, tag.id}

            await service.remove_workflow_tag(link)
            tags = await service.list_tags_for_workflow(workflow_id)
            assert [t.id for t in tags] == [tenant_a.workflow_tag_id]

    async def test_table_columns_load_with_their_table(
        self, session: AsyncSession, tenant_a: Tenant, enforced_db_role: str
    ) -> None:
        service = TablesService(session=session, role=tenant_a.role)
        async with _as_tenant(session, enforced_db_role, tenant_a):
            session.add(TableColumn(table_id=tenant_a.table_id, name="v", type="TEXT"))
            await session.flush()
            table = await service.get_table(tenant_a.table_id, populate_existing=True)
        assert sorted(c.name for c in table.columns) == ["label", "v"]
