"""Registry-action capabilities exposed by Action Gateway endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, status

from tracecat.auth.executor_tokens import ExecutorTokenPayload, verify_executor_token
from tracecat.authz.controls import has_scope
from tracecat.dsl.enums import PlatformAction

GATEWAY_ACTIONS_BY_ENDPOINT: dict[str, frozenset[str]] = {
    # Agent execution and ranking
    "tracecat.agent.internal_router.run_agent_endpoint": frozenset(
        {"ai.agent", "ai.preset_agent"}
    ),
    "tracecat.agent.internal_router.rank_items_endpoint": frozenset(
        {"ai.rank_documents", "ai.select_field", "ai.select_fields"}
    ),
    "tracecat.agent.internal_router.rank_items_pairwise_endpoint": frozenset(
        {"ai.rank_documents", "ai.select_field", "ai.select_fields"}
    ),
    # Agent presets
    "tracecat.agent.preset.internal_router.list_presets": frozenset(
        {"ai.agent.list_presets"}
    ),
    "tracecat.agent.preset.internal_router.create_preset": frozenset(
        {"ai.agent.create_preset"}
    ),
    "tracecat.agent.preset.internal_router.get_preset_by_slug": frozenset(
        {"ai.agent.get_preset"}
    ),
    "tracecat.agent.preset.internal_router.update_preset_by_slug": frozenset(
        {"ai.agent.update_preset"}
    ),
    "tracecat.agent.preset.internal_router.delete_preset_by_slug": frozenset(
        {"ai.agent.delete_preset"}
    ),
    # Agent skills
    "tracecat.agent.skill.internal_router.list_skills": frozenset(
        {"ai.skill.list_skills"}
    ),
    "tracecat.agent.skill.internal_router.create_skill": frozenset(
        {"ai.skill.create_skill"}
    ),
    "tracecat.agent.skill.internal_router.get_skill": frozenset({"ai.skill.get_skill"}),
    "tracecat.agent.skill.internal_router.publish_skill_version": frozenset(
        {"ai.skill.publish_skill_version"}
    ),
    "tracecat.agent.skill.internal_router.list_skill_versions": frozenset(
        {"ai.skill.list_skill_versions"}
    ),
    "tracecat.agent.skill.internal_router.get_skill_version": frozenset(
        {"ai.skill.get_skill_version"}
    ),
    "tracecat.agent.skill.internal_router.restore_skill_version": frozenset(
        {"ai.skill.restore_skill_version"}
    ),
    "tracecat.agent.skill.internal_router.archive_skill": frozenset(
        {"ai.skill.archive_skill"}
    ),
    # Cases
    "tracecat.cases.internal_router.list_cases": frozenset({"core.cases.list_cases"}),
    "tracecat.cases.internal_router.search_cases": frozenset(
        {"core.cases.search_cases"}
    ),
    "tracecat.cases.internal_router.get_case": frozenset(
        {"core.cases.get_case", "core.cases.get_linked_case_rows"}
    ),
    "tracecat.cases.internal_router.create_case": frozenset({"core.cases.create_case"}),
    "tracecat.cases.internal_router.create_case_simple": frozenset(
        {"core.cases.create_case"}
    ),
    "tracecat.cases.internal_router.update_case": frozenset({"core.cases.update_case"}),
    "tracecat.cases.internal_router.update_case_simple": frozenset(
        {"core.cases.update_case"}
    ),
    "tracecat.cases.internal_router.delete_case": frozenset({"core.cases.delete_case"}),
    "tracecat.cases.internal_router.list_events_with_users": frozenset(
        {"core.cases.list_case_events"}
    ),
    "tracecat.cases.internal_router.list_comments": frozenset(
        {"core.cases.list_comments"}
    ),
    "tracecat.cases.internal_router.list_comment_threads": frozenset(
        {"core.cases.list_comment_threads"}
    ),
    "tracecat.cases.internal_router.create_comment": frozenset(
        {"core.cases.create_comment", "core.cases.reply_to_comment"}
    ),
    "tracecat.cases.internal_router.create_comment_simple": frozenset(
        {"core.cases.create_comment", "core.cases.reply_to_comment"}
    ),
    "tracecat.cases.internal_router.update_comment": frozenset(
        {"core.cases.update_comment"}
    ),
    "tracecat.cases.internal_router.update_comment_by_id": frozenset(
        {"core.cases.update_comment"}
    ),
    "tracecat.cases.internal_router.update_comment_simple": frozenset(
        {"core.cases.update_comment"}
    ),
    "tracecat.cases.internal_router.get_comment_thread": frozenset(
        {"core.cases.get_comment_thread"}
    ),
    "tracecat.cases.internal_router.assign_user_to_case": frozenset(
        {"core.cases.assign_user"}
    ),
    "tracecat.cases.internal_router.assign_user_by_email_to_case": frozenset(
        {"core.cases.assign_user_by_email"}
    ),
    # Case tags
    "tracecat.cases.tags.internal_router.add_tag": frozenset(
        {"core.cases.add_case_tag"}
    ),
    "tracecat.cases.tags.internal_router.remove_tag": frozenset(
        {"core.cases.remove_case_tag"}
    ),
    # Case attachments
    "tracecat.cases.attachments.internal_router.list_attachments": frozenset(
        {"core.cases.list_attachments"}
    ),
    "tracecat.cases.attachments.internal_router.create_attachment": frozenset(
        {"core.cases.upload_attachment", "core.cases.upload_attachment_from_url"}
    ),
    "tracecat.cases.attachments.internal_router.get_attachment_download_info": frozenset(
        {"core.cases.get_attachment"}
    ),
    "tracecat.cases.attachments.internal_router.get_attachment_metadata": frozenset(
        {"core.cases.get_attachment"}
    ),
    "tracecat.cases.attachments.internal_router.download_attachment_content": frozenset(
        {"core.cases.download_attachment"}
    ),
    "tracecat.cases.attachments.internal_router.get_attachment_url": frozenset(
        {"core.cases.get_attachment_download_url"}
    ),
    "tracecat.cases.attachments.internal_router.delete_attachment": frozenset(
        {"core.cases.delete_attachment"}
    ),
    # Case rows
    "tracecat.cases.rows.internal_router.list_case_rows": frozenset(
        {"core.cases.get_linked_case_rows"}
    ),
    "tracecat.cases.rows.internal_router.link_case_row": frozenset(
        {"core.cases.link_row"}
    ),
    "tracecat.cases.rows.internal_router.insert_case_row": frozenset(
        {"core.cases.insert_row"}
    ),
    "tracecat.cases.rows.internal_router.unlink_case_row": frozenset(
        {"core.cases.unlink_row"}
    ),
    # Case tasks and metrics
    "tracecat.cases.internal_router.list_tasks": frozenset({"core.cases.list_tasks"}),
    "tracecat.cases.internal_router.create_task": frozenset({"core.cases.create_task"}),
    "tracecat.cases.internal_router.update_task": frozenset({"core.cases.update_task"}),
    "tracecat.cases.internal_router.delete_task": frozenset({"core.cases.delete_task"}),
    "tracecat.cases.internal_router.get_task_by_id": frozenset(
        {"core.cases.get_task", "core.cases.update_task"}
    ),
    "tracecat.cases.internal_router.update_task_by_id": frozenset(
        {"core.cases.update_task"}
    ),
    "tracecat.cases.internal_router.delete_task_by_id": frozenset(
        {"core.cases.delete_task"}
    ),
    "tracecat.cases.internal_router.get_case_metrics": frozenset(
        {"core.cases.get_case_metrics"}
    ),
    # Persistent transform deduplication
    "tracecat.deduplicate.internal_router.create_digests": frozenset(
        {"core.transform.deduplicate", "core.transform.is_duplicate"}
    ),
    # Tables
    "tracecat.tables.internal_router.list_tables": frozenset(
        {"core.table.list_tables"}
    ),
    "tracecat.tables.internal_router.create_table": frozenset(
        {"core.table.create_table"}
    ),
    "tracecat.tables.internal_router.update_table": frozenset(
        {"core.table.update_table"}
    ),
    "tracecat.tables.internal_router.get_table_metadata": frozenset(
        {"core.table.get_table_metadata"}
    ),
    "tracecat.tables.internal_router.create_column": frozenset(
        {"core.table.create_column"}
    ),
    "tracecat.tables.internal_router.update_column": frozenset(
        {"core.table.update_column"}
    ),
    "tracecat.tables.internal_router.delete_column": frozenset(
        {"core.table.delete_column"}
    ),
    "tracecat.tables.internal_router.lookup_rows": frozenset(
        {"core.table.lookup", "core.table.lookup_many"}
    ),
    "tracecat.tables.internal_router.exists_rows": frozenset({"core.table.is_in"}),
    "tracecat.tables.internal_router.search_rows": frozenset(
        {"core.table.search_rows"}
    ),
    "tracecat.tables.internal_router.insert_row": frozenset({"core.table.insert_row"}),
    "tracecat.tables.internal_router.insert_rows_batch": frozenset(
        {"core.table.insert_rows"}
    ),
    "tracecat.tables.internal_router.update_row": frozenset({"core.table.update_row"}),
    "tracecat.tables.internal_router.delete_row": frozenset({"core.table.delete_row"}),
    "tracecat.tables.internal_router.download_table": frozenset(
        {"core.table.download"}
    ),
    # Workflows
    "tracecat.workflow.executions.internal_router.create_workflow": frozenset(
        {"core.workflow.create_workflow"}
    ),
    "tracecat.workflow.executions.internal_router.get_workflow_edit_document": frozenset(
        {"core.workflow.get_workflow"}
    ),
    "tracecat.workflow.executions.internal_router.edit_workflow_document": frozenset(
        {"core.workflow.edit_workflow"}
    ),
    "tracecat.workflow.executions.internal_router.publish_workflow": frozenset(
        {"core.workflow.publish"}
    ),
    "tracecat.workflow.executions.internal_router.get_webhook": frozenset(
        {"core.workflow.get_webhook", "core.workflow.update_webhook"}
    ),
    "tracecat.workflow.executions.internal_router.update_webhook": frozenset(
        {"core.workflow.update_webhook"}
    ),
    "tracecat.workflow.executions.internal_router.get_case_trigger": frozenset(
        {"core.workflow.get_case_trigger", "core.workflow.update_case_trigger"}
    ),
    "tracecat.workflow.executions.internal_router.update_case_trigger": frozenset(
        {"core.workflow.update_case_trigger"}
    ),
    "tracecat.workflow.executions.internal_router.get_authoring_context": frozenset(
        {"core.workflow.get_authoring_context"}
    ),
    "tracecat.workflow.executions.internal_router.execute_workflow": frozenset(
        {"core.workflow.execute"}
    ),
    "tracecat.workflow.executions.internal_router.run_workflow": frozenset(
        {"core.workflow.run"}
    ),
    "tracecat.workflow.executions.internal_router.get_execution_status": frozenset(
        {"core.workflow.execute", "core.workflow.get_status"}
    ),
}


def endpoint_key(endpoint: Callable[..., Any]) -> str:
    """Return the stable module-qualified key for a matched endpoint."""
    return f"{endpoint.__module__}.{endpoint.__name__}"


def get_gateway_actions(endpoint: Callable[..., Any]) -> frozenset[str] | None:
    """Return registry actions represented by a matched gateway endpoint."""
    return GATEWAY_ACTIONS_BY_ENDPOINT.get(endpoint_key(endpoint))


def _agent_gateway_action_allowed(
    claims: ExecutorTokenPayload,
    required_actions: frozenset[str] | None,
) -> bool:
    """Check both the caller Role and Agent grant for a gateway operation."""
    if (
        claims.scopes is None
        or claims.allowed_actions is None
        or required_actions is None
    ):
        return False

    return any(
        action in claims.allowed_actions
        and has_scope(claims.scopes, f"action:{action}:execute")
        for action in required_actions
    )


async def enforce_agent_action_capability(request: Request) -> None:
    """Enforce an Agent run_python grant on the server-matched endpoint."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return

    try:
        claims = verify_executor_token(token)
    except ValueError:
        return

    if (
        claims.action != PlatformAction.RUN_PYTHON
        or claims.allowed_actions is None
        or request.url.path == "/internal/health"
    ):
        return

    endpoint = request.scope.get("endpoint")
    required_actions = get_gateway_actions(endpoint) if callable(endpoint) else None
    if _agent_gateway_action_allowed(claims, required_actions):
        return

    required_scopes = [
        f"action:{action}:execute" for action in sorted(required_actions or ())
    ]
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": {
                "code": "action_not_allowed",
                "message": "Action Gateway operation is not in the Agent toolset",
                "required_scopes": required_scopes,
                "missing_scopes": required_scopes,
            }
        },
    )
