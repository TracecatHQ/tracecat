"""The managed write guard across resource types (ENG-2288 to ENG-2293).

Each test seeds a workspace from the expanded sync fixture, marks one
resource managed, and checks that an org owner is refused the locked change
but can still do what customers are allowed to do.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tests.support.fake_vcs import FakeVcsServer
from tests.support.managed_resources import (
    local_id_of,
    mark_managed,
    no_registry_lock,
    seed_workspace,
)
from tracecat.agent.preset.schemas import AgentPresetSkillBindingBase, AgentPresetUpdate
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.preset.types import SkillBindingSpec
from tracecat.agent.skill.schemas import SkillCreate
from tracecat.agent.skill.service import SkillService
from tracecat.auth.types import Role
from tracecat.cases.dropdowns.schemas import (
    CaseDropdownDefinitionUpdate,
    CaseDropdownOptionCreate,
)
from tracecat.cases.dropdowns.service import CaseDropdownDefinitionsService
from tracecat.cases.durations.schemas import CaseDurationDefinitionUpdate
from tracecat.cases.durations.service import CaseDurationDefinitionService
from tracecat.cases.schemas import CaseFieldUpdate
from tracecat.cases.service import CaseFieldsService
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import (
    AgentPreset,
    AgentPresetSkill,
    CaseDropdownDefinition,
    CaseDurationDefinition,
    CaseFields,
    CaseTag,
    ManagedPresetSkillBinding,
    Secret,
    Skill,
    Table,
    Workflow,
    Workspace,
    WorkspaceSyncResourceMapping,
    WorkspaceVariable,
)
from tracecat.forms.schemas import FormCreate
from tracecat.forms.service import FormsService
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.managed_resources.guard import (
    LOCKED_MESSAGE,
    ManagedResourceLockedError,
    hold_ownership_lock,
    managed_write_bypass,
)
from tracecat.managed_resources.presets import (
    preserve_customer_skill_bindings,
    with_customer_skill_bindings,
)
from tracecat.managed_resources.rollout import ManagedRolloutEngine
from tracecat.managed_resources.router import list_managed_resources
from tracecat.secrets.enums import AwsSecretMappingMode, SecretSource
from tracecat.secrets.schemas import AwsSecretKeyMapping, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableRowInsert
from tracecat.tables.service import TablesService
from tracecat.tags.schemas import TagUpdate
from tracecat.variables.schemas import VariableCreate, VariableUpdate
from tracecat.variables.service import VariablesService
from tracecat.webhooks import service as webhook_service
from tracecat.webhooks.schemas import WebhookUpdate
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import WorkflowUpdate
from tracecat.workspace_sync.adapters.base import MANAGED_MAPPING_NAMESPACE
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    SecretMetadataResourceSpec,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def seeded(session: AsyncSession, svc_role: Role) -> AsyncIterator[Role]:
    await seed_workspace(session, svc_role, fake_vcs=FakeVcsServer())
    with no_registry_lock():
        yield svc_role


async def _managed_workflow(session: AsyncSession, role: Role) -> WorkflowUUID:
    workflow_id = WorkflowUUID.new(
        await local_id_of(session, Workflow, role, alias="qa-root")
    )
    await mark_managed(session, role, SyncResourceType.WORKFLOW, workflow_id)
    return workflow_id


@pytest.mark.anyio
async def test_managed_workflow_refuses_update_publish_and_delete(
    session: AsyncSession, seeded: Role
) -> None:
    workflow_id = await _managed_workflow(session, seeded)
    service = WorkflowsManagementService(session, role=seeded)

    with pytest.raises(ManagedResourceLockedError, match=LOCKED_MESSAGE):
        await service.update_workflow(workflow_id, WorkflowUpdate(title="Renamed"))
    with pytest.raises(ManagedResourceLockedError):
        await service.publish_workflow(workflow_id)
    with pytest.raises(ManagedResourceLockedError):
        await service.delete_workflow(workflow_id)


@pytest.mark.anyio
async def test_managed_workflow_webhook_can_be_toggled_but_not_reconfigured(
    session: AsyncSession, seeded: Role
) -> None:
    workflow_id = await _managed_workflow(session, seeded)

    webhook = await webhook_service.update_webhook(
        role=seeded,
        session=session,
        workflow_id=workflow_id,
        params=WebhookUpdate(status="offline"),
    )
    assert webhook.status == "offline"
    with pytest.raises(ManagedResourceLockedError):
        await webhook_service.update_webhook(
            role=seeded,
            session=session,
            workflow_id=workflow_id,
            params=WebhookUpdate(methods=["GET"]),
        )


@pytest.mark.anyio
async def test_unmanaged_workflow_updates_as_before(
    session: AsyncSession, seeded: Role
) -> None:
    workflow_id = WorkflowUUID.new(
        await local_id_of(session, Workflow, seeded, alias="qa-orphan")
    )

    updated = await WorkflowsManagementService(session, role=seeded).update_workflow(
        workflow_id, WorkflowUpdate(title="Still editable")
    )

    assert updated.title == "Still editable"


@pytest.mark.anyio
async def test_bypass_lets_the_rollout_write(
    session: AsyncSession, seeded: Role
) -> None:
    workflow_id = await _managed_workflow(session, seeded)

    with managed_write_bypass():
        updated = await WorkflowsManagementService(
            session, role=seeded
        ).update_workflow(workflow_id, WorkflowUpdate(title="From rollout"))

    assert updated.title == "From rollout"


async def _managed_preset(
    session: AsyncSession, role: Role
) -> tuple[AgentPreset, set[uuid.UUID]]:
    preset = await session.scalar(
        select(AgentPreset).where(
            AgentPreset.workspace_id == role.workspace_id,
            AgentPreset.slug == "qa-triage-parent",
        )
    )
    assert preset is not None
    await mark_managed(session, role, SyncResourceType.AGENT_PRESET, preset.id)
    skill_ids = set(
        await session.scalars(
            select(AgentPresetSkill.skill_id).where(
                AgentPresetSkill.preset_id == preset.id
            )
        )
    )
    for skill_id in skill_ids:
        session.add(
            ManagedPresetSkillBinding(
                workspace_id=role.workspace_id, preset_id=preset.id, skill_id=skill_id
            )
        )
    await session.flush()
    return preset, skill_ids


async def _customer_skill(session: AsyncSession, role: Role) -> uuid.UUID:
    service = SkillService(session, role=role)
    skill = await service.create_skill(SkillCreate(name="customer-notes"))
    await service.publish_skill(skill.id)
    return skill.id


@pytest.mark.anyio
async def test_managed_list_shows_readable_types_and_package_skills(
    session: AsyncSession, seeded: Role
) -> None:
    # Each type needs its own read permission, not workspace:read, and a
    # managed preset reports the skills its package binds.
    preset, package_skills = await _managed_preset(session, seeded)
    await _managed_workflow(session, seeded)
    agent_reader = seeded.model_copy(update={"scopes": frozenset({"agent:read"})})

    page = await list_managed_resources(
        role=agent_reader, session=session, resource_type=None, limit=50, cursor=None
    )

    assert {row.resource_type for row in page.items} == {SyncResourceType.AGENT_PRESET}
    [row] = page.items
    assert row.local_id == preset.id
    assert set(row.package_skill_ids) == package_skills
    workflow_reader = seeded.model_copy(update={"scopes": frozenset({"workflow:read"})})
    workflows = await list_managed_resources(
        role=workflow_reader, session=session, resource_type=None, limit=50, cursor=None
    )
    assert {row.resource_type for row in workflows.items} == {SyncResourceType.WORKFLOW}


@pytest.mark.anyio
async def test_managed_preset_refuses_model_change(
    session: AsyncSession, seeded: Role
) -> None:
    preset, _ = await _managed_preset(session, seeded)

    with pytest.raises(ManagedResourceLockedError):
        await AgentPresetService(session, role=seeded).update_preset(
            preset, AgentPresetUpdate(model_name="other-model")
        )


@pytest.mark.anyio
async def test_managed_preset_accepts_a_customer_skill_but_keeps_package_skills(
    session: AsyncSession, seeded: Role
) -> None:
    preset, package_skills = await _managed_preset(session, seeded)
    customer_skill = await _customer_skill(session, seeded)
    service = AgentPresetService(session, role=seeded)

    await service.update_preset(
        preset,
        AgentPresetUpdate(
            skills=[
                AgentPresetSkillBindingBase(skill_id=skill_id)
                for skill_id in [*package_skills, customer_skill]
            ]
        ),
    )
    bound = set(
        await session.scalars(
            select(AgentPresetSkill.skill_id).where(
                AgentPresetSkill.preset_id == preset.id
            )
        )
    )
    assert bound == {*package_skills, customer_skill}
    with pytest.raises(ManagedResourceLockedError):
        await service.update_preset(
            preset,
            AgentPresetUpdate(
                skills=[AgentPresetSkillBindingBase(skill_id=customer_skill)]
            ),
        )


@pytest.mark.anyio
async def test_managed_preset_save_may_resend_unchanged_fields_with_skills(
    session: AsyncSession, seeded: Role
) -> None:
    # The agent builder sends the whole form back when only skills changed.
    preset, package_skills = await _managed_preset(session, seeded)
    customer_skill = await _customer_skill(session, seeded)

    await AgentPresetService(session, role=seeded).update_preset(
        preset,
        AgentPresetUpdate(
            name=preset.name,
            instructions=preset.instructions,
            model_name=preset.model_name,
            skills=[
                AgentPresetSkillBindingBase(skill_id=skill_id)
                for skill_id in [*package_skills, customer_skill]
            ],
        ),
    )

    bound = set(
        await session.scalars(
            select(AgentPresetSkill.skill_id).where(
                AgentPresetSkill.preset_id == preset.id
            )
        )
    )
    assert customer_skill in bound


@pytest.mark.anyio
async def test_rollout_keeps_the_customer_aws_secret_reference(
    session: AsyncSession, seeded: Role
) -> None:
    assert seeded.workspace_id is not None
    secret = await session.scalar(
        select(Secret).where(
            Secret.workspace_id == seeded.workspace_id,
            Secret.name == "qa_threatintel",
        )
    )
    assert secret is not None
    secret.remote_reference = "customer/threatintel"
    session.add(
        WorkspaceSyncResourceMapping(
            workspace_id=seeded.workspace_id,
            provider=MANAGED_MAPPING_NAMESPACE,
            resource_type=SyncResourceType.SECRET_METADATA.value,
            source_id="threatintel",
            local_id=secret.id,
        )
    )
    await session.flush()

    def aws_spec(
        source_id: str, name: str, reference: str
    ) -> SecretMetadataResourceSpec:
        return SecretMetadataResourceSpec(
            id=source_id,
            name=name,
            environment="default",
            keys=["API_KEY"],
            source=SecretSource.AWS_SECRETS_MANAGER,
            store="org-store",
            remote_reference=reference,
            key_mapping=AwsSecretKeyMapping(
                mode=AwsSecretMappingMode.WHOLE_STRING, keys=["API_KEY"]
            ),
        )

    package_spec = aws_spec("threatintel", "qa_threatintel", "package/intel")
    new_spec = aws_spec("new", "new_secret", "package/new")
    snapshot = WorkspaceRemoteSnapshot(
        commit_sha="",
        files={},
        spec=WorkspaceSpec(
            secret_metadata={"threatintel": package_spec, "new": new_spec}
        ),
    )

    kept = await ManagedRolloutEngine(session)._with_customer_secret_references(
        seeded.workspace_id, snapshot
    )

    assert kept.spec.secret_metadata["threatintel"].remote_reference == (
        "customer/threatintel"
    )
    # A secret the rollout creates takes the package's reference.
    assert kept.spec.secret_metadata["new"].remote_reference == "package/new"

    # A new source id for the same secret is matched by name, as the importer
    # adopts it.
    renamed = await ManagedRolloutEngine(session)._with_customer_secret_references(
        seeded.workspace_id,
        WorkspaceRemoteSnapshot(
            commit_sha="",
            files={},
            spec=WorkspaceSpec(
                secret_metadata={
                    "renamed": aws_spec("renamed", "qa_threatintel", "package/intel")
                }
            ),
        ),
    )
    assert renamed.spec.secret_metadata["renamed"].remote_reference == (
        "customer/threatintel"
    )


@pytest.mark.anyio
async def test_managed_skill_cannot_be_published_or_archived(
    session: AsyncSession, seeded: Role
) -> None:
    skill_id = await local_id_of(session, Skill, seeded, slug="qa-enrichment-skill")
    await mark_managed(session, seeded, SyncResourceType.SKILL, skill_id)
    service = SkillService(session, role=seeded)

    with pytest.raises(ManagedResourceLockedError):
        await service.publish_skill(skill_id)
    with pytest.raises(ManagedResourceLockedError):
        await service.archive_skill(skill_id)


@pytest.mark.anyio
async def test_managed_table_locks_columns_but_keeps_rows_writable(
    session: AsyncSession, seeded: Role
) -> None:
    table = await session.scalar(
        select(Table).where(
            Table.workspace_id == seeded.workspace_id, Table.name == "qa_indicators"
        )
    )
    assert table is not None
    await mark_managed(session, seeded, SyncResourceType.TABLE, table.id)
    service = TablesService(session, role=seeded)

    with pytest.raises(ManagedResourceLockedError):
        await service.create_column(
            table, TableColumnCreate(name="extra", type=SqlType.TEXT)
        )
    row = await service.insert_row(
        table, TableRowInsert(data={"indicator": "198.51.100.7"})
    )
    assert row["indicator"] == "198.51.100.7"


@pytest.mark.anyio
async def test_managed_case_tag_cannot_be_renamed(
    session: AsyncSession, seeded: Role
) -> None:
    tag = await session.scalar(
        select(CaseTag).where(
            CaseTag.workspace_id == seeded.workspace_id, CaseTag.name == "qa-alert"
        )
    )
    assert tag is not None
    await mark_managed(session, seeded, SyncResourceType.CASE_TAG, tag.id)

    with pytest.raises(ManagedResourceLockedError):
        await CaseTagsService(session, role=seeded).update_tag(
            tag, TagUpdate(name="renamed")
        )


@pytest.mark.anyio
async def test_managed_case_dropdown_refuses_edits_and_new_options(
    session: AsyncSession, seeded: Role
) -> None:
    dropdown = await session.scalar(
        select(CaseDropdownDefinition).where(
            CaseDropdownDefinition.workspace_id == seeded.workspace_id
        )
    )
    assert dropdown is not None
    await mark_managed(session, seeded, SyncResourceType.CASE_DROPDOWN, dropdown.id)
    service = CaseDropdownDefinitionsService(session, role=seeded)

    with pytest.raises(ManagedResourceLockedError):
        await service.update_definition(
            dropdown, CaseDropdownDefinitionUpdate(name="renamed")
        )
    with pytest.raises(ManagedResourceLockedError):
        await service.add_option(
            dropdown.id, CaseDropdownOptionCreate(label="Extra", ref="extra")
        )
    with pytest.raises(ManagedResourceLockedError):
        await service.delete_definition(dropdown)


@pytest.mark.anyio
async def test_managed_case_duration_refuses_edits_and_delete(
    session: AsyncSession, seeded: Role
) -> None:
    duration = await session.scalar(
        select(CaseDurationDefinition).where(
            CaseDurationDefinition.workspace_id == seeded.workspace_id
        )
    )
    assert duration is not None
    await mark_managed(session, seeded, SyncResourceType.CASE_DURATION, duration.id)
    service = CaseDurationDefinitionService(session, role=seeded)

    with pytest.raises(ManagedResourceLockedError):
        await service.update_definition(
            duration.id, CaseDurationDefinitionUpdate(name="renamed")
        )
    with pytest.raises(ManagedResourceLockedError):
        await service.delete_definition(duration.id)


@pytest.mark.anyio
async def test_managed_case_field_refuses_edits_and_delete(
    session: AsyncSession, seeded: Role
) -> None:
    fields = await session.scalar(
        select(CaseFields).where(CaseFields.workspace_id == seeded.workspace_id)
    )
    assert fields is not None and fields.schema
    field_id = next(iter(fields.schema))
    # Case field local ids derive from the field name, as the adapter's do.
    await mark_managed(
        session,
        seeded,
        SyncResourceType.CASE_FIELD,
        uuid.uuid5(fields.id, field_id),
        source_id=field_id,
    )
    service = CaseFieldsService(session, role=seeded)

    with pytest.raises(ManagedResourceLockedError):
        await service.update_field(field_id, CaseFieldUpdate(display_name="Renamed"))
    with pytest.raises(ManagedResourceLockedError):
        await service.delete_field(field_id)


@pytest.mark.anyio
async def test_managed_secret_takes_values_but_not_a_rename(
    session: AsyncSession, seeded: Role
) -> None:
    secret = await session.scalar(
        select(Secret).where(
            Secret.workspace_id == seeded.workspace_id,
            Secret.name == "qa_threatintel",
        )
    )
    assert secret is not None
    await mark_managed(session, seeded, SyncResourceType.SECRET_METADATA, secret.id)
    service = SecretsService(session, role=seeded)
    keys = service.secret_key_names(secret)

    await service.update_secret(
        secret,
        SecretUpdate(
            keys=[SecretKeyValue(key=key, value=SecretStr("filled-in")) for key in keys]
        ),
    )
    with pytest.raises(ManagedResourceLockedError):
        await service.update_secret(secret, SecretUpdate(name="renamed"))
    with pytest.raises(ManagedResourceLockedError):
        await service.update_secret(
            secret,
            SecretUpdate(keys=[SecretKeyValue(key="NEW_KEY", value=SecretStr("x"))]),
        )


@pytest.mark.anyio
async def test_managed_variable_takes_values_for_its_keys_only(
    session: AsyncSession, seeded: Role
) -> None:
    variable = await session.scalar(
        select(WorkspaceVariable).where(
            WorkspaceVariable.workspace_id == seeded.workspace_id,
            WorkspaceVariable.name == "qa_config",
        )
    )
    assert variable is not None
    await mark_managed(session, seeded, SyncResourceType.VARIABLE, variable.id)
    service = VariablesService(session, role=seeded)
    keys = list((variable.values or {}).keys())

    await service.update_variable(
        variable, VariableUpdate(values=dict.fromkeys(keys, "set"))
    )
    with pytest.raises(ManagedResourceLockedError):
        await service.update_variable(variable, VariableUpdate(name="renamed"))


@pytest.mark.anyio
async def test_managed_variable_update_checks_keys_against_the_current_row(
    session: AsyncSession, seeded: Role
) -> None:
    # A rollout replaced the variable's keys after the caller loaded it. The
    # update must compare against the new keys, not the stale object.
    variable = await session.scalar(
        select(WorkspaceVariable).where(
            WorkspaceVariable.workspace_id == seeded.workspace_id,
            WorkspaceVariable.name == "qa_config",
        )
    )
    assert variable is not None
    await mark_managed(session, seeded, SyncResourceType.VARIABLE, variable.id)
    stale_keys = list((variable.values or {}).keys())
    await session.execute(
        update(WorkspaceVariable)
        .where(WorkspaceVariable.id == variable.id)
        .values(values={"package_key": "x"})
        .execution_options(synchronize_session=False)
    )

    with pytest.raises(ManagedResourceLockedError):
        await VariablesService(session, role=seeded).update_variable(
            variable, VariableUpdate(values=dict.fromkeys(stale_keys, "set"))
        )


@pytest.mark.anyio
async def test_a_customer_create_waits_for_a_rollout_holding_the_lock(
    svc_role: Role,
) -> None:
    # A rollout adopts an existing resource by name, so a create must not land
    # between its conflict check and its import. Two real sessions: the
    # "rollout" holds the ownership lock exclusively, the create must wait.
    role = svc_role.model_copy(update={"workspace_id": uuid.uuid4()}, deep=True)
    assert role.workspace_id is not None
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    sessions = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with sessions() as seed:
            seed.add(
                Workspace(
                    id=role.workspace_id,
                    name="ownership-lock-workspace",
                    organization_id=role.organization_id,
                )
            )
            await seed.commit()
        async with sessions() as rollout, sessions() as customer:
            await hold_ownership_lock(rollout, role.workspace_id, shared=False)

            async def create_variable() -> WorkspaceVariable:
                return await VariablesService(customer, role=role).create_variable(
                    VariableCreate(name="raced_var", values={"k": "v"})
                )

            create = asyncio.create_task(create_variable())
            await asyncio.sleep(0.5)
            assert not create.done()
            await rollout.commit()
            variable = await asyncio.wait_for(create, timeout=10)
            assert variable.name == "raced_var"
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(
                delete(Workspace).where(Workspace.id == role.workspace_id)
            )
            await cleanup.commit()
        await engine.dispose()


def test_customer_skill_bindings_are_kept_and_package_wins_duplicates() -> None:
    preset_id = uuid.uuid4()
    a, c, d = (SkillBindingSpec(uuid.uuid4(), uuid.uuid4()) for _ in range(3))
    customer_copy_of_d = SkillBindingSpec(d.skill_id, uuid.uuid4())

    with preserve_customer_skill_bindings({preset_id: [c, customer_copy_of_d]}):
        merged = with_customer_skill_bindings(preset_id, [a, d])

    assert merged == [a, d, c]
    assert with_customer_skill_bindings(preset_id, [a]) == [a]


@pytest.mark.anyio
async def test_a_managed_workflow_cannot_get_a_customer_form(
    session: AsyncSession, seeded: Role
) -> None:
    # The form, or its absence, comes from the package.
    workflow_id = await _managed_workflow(session, seeded)

    with pytest.raises(ManagedResourceLockedError):
        await FormsService(session, seeded).create_form(
            FormCreate(workflow_id=workflow_id)
        )


@pytest.mark.anyio
async def test_edit_forms_may_resend_unchanged_locked_fields(
    session: AsyncSession, seeded: Role
) -> None:
    secret = await session.scalar(
        select(Secret).where(
            Secret.workspace_id == seeded.workspace_id,
            Secret.name == "qa_threatintel",
        )
    )
    variable = await session.scalar(
        select(WorkspaceVariable).where(
            WorkspaceVariable.workspace_id == seeded.workspace_id,
            WorkspaceVariable.name == "qa_config",
        )
    )
    assert secret is not None and variable is not None
    await mark_managed(session, seeded, SyncResourceType.SECRET_METADATA, secret.id)
    await mark_managed(session, seeded, SyncResourceType.VARIABLE, variable.id)
    secrets = SecretsService(session, role=seeded)

    await secrets.update_secret(
        secret,
        SecretUpdate(
            name=secret.name,
            environment=secret.environment,
            keys=[
                SecretKeyValue(key=key, value=SecretStr("rotated"))
                for key in secrets.secret_key_names(secret)
            ],
        ),
    )
    await VariablesService(session, role=seeded).update_variable(
        variable,
        VariableUpdate(
            name=variable.name,
            environment=variable.environment,
            values=dict.fromkeys(variable.values or {}, "rotated"),
        ),
    )
