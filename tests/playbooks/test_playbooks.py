import asyncio
import os
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from temporalio.worker import Worker

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat.db.schemas import User
from tracecat.dsl.common import DSLRunArgs
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.dsl.workflow import DSLActivities, DSLWorkflow, retry_policies
from tracecat.logging import logger
from tracecat.validation import validate_dsl
from tracecat.workflow.management.definitions import (
    WorkflowDefinitionsService,
    get_workflow_definition_activity,
)
from tracecat.workflow.management.management import WorkflowsManagementService


@pytest.fixture
def playbooks_path() -> Path:
    return Path(__file__).parent.parent.parent / "playbooks"


def test_can_see(test_user):
    logger.info(f"User: {test_user}")


@pytest.mark.asyncio
async def test_can_use_session(session: AsyncSession, test_role):
    stmt = select(User)
    result = await session.exec(stmt)
    users = result.all()
    logger.info(f"Users: {users}")


# Fixture to create Tracecat secrets
@pytest.fixture(scope="session")
def create_integrations_secrets(session, test_role):
    from tracecat.secrets.models import CreateSecretParams, SecretKeyValue
    from tracecat.secrets.service import SecretsService

    secrets_service = SecretsService(session, role=test_role)

    secrets = {
        "abusech": {
            "ABUSECH_API_KEY": os.getenv("ABUSECH_API_KEY"),
        },
        "abuseipdb": {
            "ABUSEIPDB_API_KEY": os.getenv("ABUSEIPDB_API_KEY"),
        },
        "aws-guardduty": {
            "AWS_ACCESS_KEY_ID": os.getenv("AWS_GUARDDUTY__ACCESS_KEY_ID"),
            "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_GUARDDUTY__SECRET_ACCESS_KEY"),
        },
        "datadog": {
            "DD_API_KEY": os.getenv("DD_API_KEY"),
            "DD_APP_KEY": os.getenv("DD_APP_KEY"),
        },
        "hybrid-analysis": {
            "HYBRID_ANALYSIS_API_KEY": os.getenv("HYBRID_ANALYSIS_API_KEY")
        },
        "openai": {
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
        },
        "pulsedrive": {
            "PULSEDRIVE_API_KEY": os.getenv("PULSEDRIVE_API_KEY"),
        },
        "resend": {
            "RESEND_API_KEY": os.getenv("RESEND_API_KEY"),
        },
        "slack": {
            "SLACK_BOT_TOKEN": os.getenv("SLACK_BOT_TOKEN"),
            "SLACK_CHANNEL": os.getenv("SLACK_CHANNEL"),
            "SLACK_WEBHOOK": os.getenv("SLACK_WEBHOOK"),
        },
        "urlscan": {
            "URLSCAN_API_KEY": os.getenv("URLSCAN_API_KEY"),
        },
        "virustotal": {
            "VT_API_KEY": os.getenv("VT_API_KEY"),
        },
    }

    loop = asyncio.get_event_loop()
    for name, env_vars in secrets.items():
        keyvalues = [SecretKeyValue(key=k, value=v) for k, v in env_vars.items()]
        loop.run_until_complete(
            secrets_service.create_secret(CreateSecretParams(name=name, keys=keyvalues))
        )


@pytest.mark.parametrize(
    "filename",
    [
        "alert_management/aws-guardduty-to-cases.yml",
    ],
    ids=lambda x: x[0],
)
@pytest.mark.asyncio
async def test_playbook_validation(session, playbooks_path, filename, test_role):
    filepath = playbooks_path / filename
    mgmt_service = WorkflowsManagementService(session, role=test_role)
    with filepath.open() as f:
        playbook_defn_data = yaml.safe_load(f)
    workflow = await mgmt_service.create_workflow_from_external_definition(
        playbook_defn_data
    )
    dsl = await mgmt_service.build_dsl_from_workflow(workflow)
    validation_results = await validate_dsl(dsl, validate_secrets=False)
    assert len(validation_results) == 0


@pytest.mark.parametrize(
    "filename, trigger_data",
    [
        (
            "aws-guardduty-to-cases.yml",
            {
                "start_time": "2024-05-01T00:00:00Z",
                "end_time": "2024-07-01T12:00:00Z",
            },
        ),
    ],
    ids=[
        "aws-guardduty-to-cases",
    ],
)
@pytest.mark.skip
@pytest.mark.asyncio
async def test_playbook(
    session, playbooks_path, filename, trigger_data, test_role, temporal_client
):
    filepath = playbooks_path / filename
    # Create
    logger.info(f"Creating workflow from {filepath}")
    mgmt_service = WorkflowsManagementService(session, role=test_role)
    with filepath.open() as f:
        playbook_defn_data = yaml.safe_load(f)
    workflow = await mgmt_service.create_workflow_from_external_definition(
        playbook_defn_data
    )

    logger.info("Building dsl from workflow")
    dsl = await mgmt_service.build_dsl_from_workflow(workflow)

    # Commit
    defn_service = WorkflowDefinitionsService(session, role=test_role)

    logger.info("Creating workflow definition")
    await defn_service.create_workflow_definition(workflow_id=workflow.id, dsl=dsl)

    wf_exec_id = generate_test_exec_id(f"{filepath.stem}-{workflow.title}")

    logger.info("Executing")
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    async with Worker(
        temporal_client,
        task_queue=queue,
        activities=DSLActivities.load() + [get_workflow_definition_activity],
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            DSLRunArgs(dsl=dsl, role=test_role, wf_id=TEST_WF_ID),
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=retry_policies["workflow:fail_fast"],
        )
        assert result is not None
