import os
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client
from temporalio.worker import Worker

from tests.shared import DSL_UTILITIES, TEST_WF_ID, generate_test_exec_id
from tracecat.dsl.common import DSLRunArgs
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.dsl.workflow import DSLActivities, DSLWorkflow, retry_policies
from tracecat.expressions.shared import ExprType
from tracecat.logger import logger
from tracecat.registry import _Registry
from tracecat.types.auth import Role
from tracecat.validation import validate_dsl
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService


# Fixture to create Tracecat secrets
@pytest_asyncio.fixture
async def integration_secrets(session: AsyncSession, test_role: Role):
    if not os.getenv("GITHUB_ACTIONS"):
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            logger.warning("dotenv not installed, skipping loading .env file")
            pass

    from tracecat.secrets.models import CreateSecretParams, SecretKeyValue
    from tracecat.secrets.service import SecretsService

    secrets_service = SecretsService(session, role=test_role)

    secrets = {
        "virustotal": {
            "VIRUSTOTAL_API_KEY": os.getenv("VIRUSTOTAL_API_KEY"),
        },
    }

    # loop = asyncio.get_event_loop()
    for name, env_vars in secrets.items():
        keyvalues = [SecretKeyValue(key=k, value=v) for k, v in env_vars.items()]
        await secrets_service.create_secret(
            CreateSecretParams(name=name, keys=keyvalues)
        )

    logger.info("Created integration secrets")


@pytest.mark.parametrize(
    "file_path",
    [
        # Detect
        "playbooks/detect/webhook_alerts/elastic.yml",
        "playbooks/detect/webhook_alerts/panther.yml",
        "playbooks/detect/extract_iocs.yml",
        # Respond
        "playbooks/respond/notify_users/slack.yml",
        # Quickstart
        "playbooks/tutorials/virustotal_quickstart.yml",
        "playbooks/tutorials/limacharlie/list_tags.yml",
        "playbooks/tutorials/limacharlie/run_investigation.yml",
        "playbooks/tutorials/limacharlie/run_tests.yml",
    ],
    ids=lambda x: x,
)
@pytest.mark.asyncio
@pytest.mark.dbtest
async def test_playbook_validation(
    session: AsyncSession,
    file_path: str,
    test_role: Role,
    base_registry: _Registry,
):
    logger.info(
        "Initializing registry", length=len(base_registry), keys=base_registry.keys
    )
    mgmt_service = WorkflowsManagementService(session, role=test_role)
    with Path(file_path).open() as f:
        playbook_defn_data = yaml.safe_load(f)
    workflow = await mgmt_service.create_workflow_from_external_definition(
        playbook_defn_data
    )
    dsl = await mgmt_service.build_dsl_from_workflow(workflow)
    validation_results = await validate_dsl(
        dsl, validate_secrets=False, exclude_exprs={ExprType.SECRET}
    )
    assert len(validation_results) == 0


@pytest.mark.skip
@pytest.mark.parametrize(
    "file_path, trigger_inputs, expected_actions",
    [
        (
            "playbooks/tutorials/virustotal_quickstart.yml",
            {
                "url_input": "crowdstrikebluescreen.com",
            },
            ["analyze_url", "open_case"],
        ),
    ],
    ids=lambda x: x,
)
@pytest.mark.asyncio
@pytest.mark.webtest
@pytest.mark.dbtest
async def test_playbook_live_run(
    session: AsyncSession,
    file_path: str,
    trigger_inputs: dict[str, Any],
    test_role: Role,
    temporal_client: Client,
    integration_secrets,
    expected_actions: list[str],
) -> None:
    # Create
    file_path = Path(file_path)
    logger.info(f"Creating workflow from {file_path}")
    mgmt_service = WorkflowsManagementService(session, role=test_role)
    with file_path.open() as f:
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

    wf_exec_id = generate_test_exec_id(f"{file_path.stem}-{workflow.title}")
    run_args = DSLRunArgs(
        role=test_role,
        dsl=dsl,
        wf_id=TEST_WF_ID,
        trigger_inputs=trigger_inputs,
    )

    logger.info("Executing")
    queue = os.environ["TEMPORAL__CLUSTER_QUEUE"]
    async with Worker(
        temporal_client,
        task_queue=queue,
        activities=DSLActivities.load() + DSL_UTILITIES,
        workflows=[DSLWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await temporal_client.execute_workflow(
            DSLWorkflow.run,
            run_args,
            id=wf_exec_id,
            task_queue=queue,
            retry_policy=retry_policies["workflow:fail_fast"],
        )
        assert result is not None
        for action in expected_actions:
            actions_context = result["ACTIONS"]
            assert action in actions_context
            assert actions_context[action]["result"] is not None
