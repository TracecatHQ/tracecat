import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlmodel.ext.asyncio.session import AsyncSession
from temporalio.client import Client
from temporalio.worker import Worker

from tests.shared import DSL_UTILITIES, TEST_WF_ID, generate_test_exec_id
from tracecat.db.engine import get_async_session_context_manager
from tracecat.dsl.action import DSLActivities
from tracecat.dsl.common import DSLRunArgs
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.dsl.workflow import DSLWorkflow, retry_policies
from tracecat.expressions.shared import ExprType
from tracecat.logger import logger
from tracecat.registry.repository import Repository
from tracecat.types.auth import Role
from tracecat.validation.service import validate_dsl
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService


@pytest.fixture
async def session(env_sandbox) -> AsyncGenerator[AsyncSession]:
    """Test session that connexts to a live database."""
    logger.info("Creating test session")
    async with get_async_session_context_manager() as session:
        yield session


# Fixture to create Tracecat secrets
@pytest.fixture
async def integration_secrets(session: AsyncSession, test_role: Role):
    if not os.getenv("GITHUB_ACTIONS"):
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            logger.warning("dotenv not installed, skipping loading .env file")
            pass

    from tracecat.secrets.models import SecretCreate, SecretKeyValue
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
        await secrets_service.create_secret(SecretCreate(name=name, keys=keyvalues))

    logger.info("Created integration secrets")


@pytest.mark.parametrize(
    "file_path",
    [
        # Quickstart
        "playbooks/tutorials/quickstart.yml",
    ],
    ids=lambda x: x,
)
@pytest.mark.anyio
@pytest.mark.dbtest
async def test_playbook_validation(
    session: AsyncSession, file_path: str, test_role: Role
):
    repo = Repository()
    repo.init(include_base=True, include_templates=True)
    logger.info("Initializing registry", length=len(repo), keys=repo.keys)
    mgmt_service = WorkflowsManagementService(session, role=test_role)
    with Path(file_path).open() as f:
        playbook_defn_data = yaml.safe_load(f)
    workflow = await mgmt_service.create_workflow_from_external_definition(
        playbook_defn_data
    )
    dsl = await mgmt_service.build_dsl_from_workflow(workflow)

    validation_results = await validate_dsl(
        session=session,
        dsl=dsl,
        validate_secrets=False,
        exclude_exprs={ExprType.SECRET},
    )
    assert len(validation_results) == 0


# @pytest.mark.skipif(not os.getenv("GITHUB_ACTIONS"), reason="Only run in CI")
@pytest.mark.parametrize(
    "file_path, trigger_inputs, expected_actions",
    [
        (
            "playbooks/tutorials/quickstart.yml",
            {
                "url": "https://crowdstrikebluescreen.com",
            },
            [
                "search_url",
                "extract_report",
                "list_comments",
            ],
        ),
    ],
    ids=lambda x: x,
)
@pytest.mark.anyio
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
