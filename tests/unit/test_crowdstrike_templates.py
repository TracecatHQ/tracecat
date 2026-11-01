"""Run the CrowdStrike list templates end to end with string timestamps.

Template inputs reach steps JSON-serialized, so `datetime` inputs arrive as
ISO strings. The falconpy UDF is replaced with a recorder so the test checks
the template wiring, not the CrowdStrike API.
"""

import sys
import textwrap
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tests.unit.test_template_actions import (
    create_manifest_for_actions,
    run_action_test,
)
from tracecat.auth.types import Role
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.registry.actions.schemas import TemplateAction
from tracecat.registry.repository import Repository

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "tracecat-registry"
    / "tracecat_registry"
    / "templates"
    / "tools"
    / "crowdstrike"
)

FAKE_PACKAGE = "fake_crowdstrike_falconpy"


@pytest.fixture
def fake_falconpy(tmp_path: Path) -> Iterator[ModuleType]:
    package = ModuleType(FAKE_PACKAGE)
    package.__spec__ = ModuleSpec(FAKE_PACKAGE, None)
    package.__path__ = [str(tmp_path)]
    (tmp_path / "falconpy.py").write_text(
        textwrap.dedent(
            """
            from typing import Any

            from tracecat_registry import registry

            CALLS: list[dict[str, Any]] = []


            @registry.register(
                description="Records FalconPy calls",
                namespace="tools.falconpy",
            )
            def call_command(
                operation_id: str,
                params: dict[str, Any] | None = None,
                member_cid: str | None = None,
            ) -> dict[str, Any]:
                CALLS.append({"operation_id": operation_id, "params": params})
                return {
                    "status_code": 200,
                    "headers": {},
                    "body": {"resources": [f"{operation_id}-1"], "errors": []},
                }
            """
        )
    )
    sys.modules[FAKE_PACKAGE] = package
    try:
        yield package
    finally:
        for name in [m for m in sys.modules if m.startswith(FAKE_PACKAGE)]:
            del sys.modules[name]


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("template", "operations", "field"),
    [
        (
            "list_alerts",
            ["GetQueriesAlertsV2", "PostEntitiesAlertsV2"],
            "created_timestamp",
        ),
        ("list_incidents", ["QueryIncidents", "GetIncidents"], "created_date"),
        ("list_detects", ["QueryDetects", "GetDetectSummaries"], "created_date"),
    ],
)
@pytest.mark.parametrize("query", [None, "status:'new'"], ids=["window", "query"])
async def test_crowdstrike_list_templates_accept_string_timestamps(
    template: str,
    operations: list[str],
    field: str,
    query: str | None,
    test_role: Role,
    db_session_with_repo: tuple[AsyncSession, UUID],
    fake_falconpy: ModuleType,
) -> None:
    session, db_repo_id = db_session_with_repo
    repo = Repository()
    repo.init(include_base=False, include_templates=False)
    repo._register_udfs_from_package(fake_falconpy)
    repo.register_template_action(
        TemplateAction.from_yaml(TEMPLATES / f"{template}.yml")
    )
    action = f"tools.crowdstrike.{template}"
    registry_lock = await create_manifest_for_actions(
        session,
        db_repo_id,
        [repo.get(action), repo.get("tools.falconpy.call_command")],
        test_role.organization_id,
    )

    result = await run_action_test(
        input=RunActionInput(
            task=ActionStatement(
                ref="list",
                action=action,
                args={
                    "start_time": "2026-11-01T00:00:00Z",
                    "end_time": "2026-11-01T03:30:00+02:00",
                    "query": query,
                    "limit": 5,
                },
            ),
            exec_context=create_default_execution_context(),
            run_context=RunContext(
                wf_id=TEST_WF_ID,
                wf_exec_id=generate_test_exec_id(f"test_crowdstrike_{template}"),
                wf_run_id=uuid.uuid4(),
                environment="default",
                logical_time=datetime.now(UTC),
            ),
            registry_lock=registry_lock,
        ),
        role=test_role,
    )

    calls: list[dict[str, Any]] = sys.modules[f"{FAKE_PACKAGE}.falconpy"].CALLS
    assert [call["operation_id"] for call in calls] == operations
    expected_filter = query or (
        f"{field}:>='2026-11-01T00:00:00Z' + {field}:<'2026-11-01T01:30:00Z'"
    )
    assert calls[0]["params"] == {"filter": expected_filter, "limit": 5}
    assert list(calls[1]["params"]["body"].values()) == [[f"{operations[0]}-1"]]
    assert result == {"resources": [f"{operations[1]}-1"], "errors": []}
