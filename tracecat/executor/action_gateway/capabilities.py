"""Bound nested Agent SDK calls to the configured registry toolset.

``core.script.run_python`` runs user-authored code, so its executor token must
not expose every Action Gateway operation available to the caller. For each
nested request this module enforces the two independent authorization bounds::

    caller Role allows action
    AND
    Agent execution grant allows action

The SDK calls lower-level HTTP endpoints rather than dispatching another
registry action, so the server-matched FastAPI endpoint is mapped back to the
registry action(s) that legitimately use it. The lookup is O(1), happens after
routing, and denies unmapped endpoints by default for Agent ``run_python``.

Lower-level SDK surfaces without a registry action are intentionally unmapped.
In particular, granting ``run_python`` authorizes script execution; it is not a
wildcard for workspace variables or every internal API available to the caller.

Most endpoints represent one concrete operation. A few are shared by registry
actions that intentionally use the same primitive (for example an update action
that reads back its result). When request parameters select a stronger operation,
``GATEWAY_ACTION_RESOLVERS_BY_ENDPOINT`` narrows the candidate action before the
dual check. Add a resolver and an attack-shaped test whenever a new shared
endpoint has such a parameter-dependent privilege boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from json import JSONDecodeError
from typing import Any

from fastapi import HTTPException, Request, status

from tracecat.auth.executor_tokens import ExecutorTokenPayload, verify_executor_token
from tracecat.authz.controls import has_scope
from tracecat.dsl.enums import PlatformAction

# Values identify registry actions whose implementation legitimately reaches the
# endpoint. Multiple values are allowed only for a shared underlying primitive;
# parameter-dependent privilege differences belong in the resolver table below.
# Do not map non-registry SDK surfaces (for example workspace variables) to
# ``run_python``: doing so would let script execution bypass the Agent toolset
# upper bound. Their existing resource-scope checks constrain only the caller.
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


GatewayActionResolver = Callable[[Request], Awaitable[frozenset[str]]]


async def _resolve_run_agent_action(request: Request) -> frozenset[str]:
    """Distinguish an ad-hoc Agent configuration from a stored preset run."""
    payload = await _request_json_object(request)
    if payload.get("preset_slug"):
        return frozenset({"ai.preset_agent"})
    return frozenset({"ai.agent"})


async def _resolve_get_case_action(request: Request) -> frozenset[str]:
    """Treat linked rows as a separate capability from base case details."""
    include_rows = request.query_params.get("include_rows")
    if include_rows is None or include_rows.lower() in {"false", "0", "off", "no"}:
        return frozenset({"core.cases.get_case"})
    return frozenset({"core.cases.get_linked_case_rows"})


async def _resolve_table_lookup_action(request: Request) -> frozenset[str]:
    """Prevent a single-row lookup grant from expanding to an arbitrary batch."""
    payload = await _request_json_object(request)
    if payload.get("limit") == 1:
        return frozenset({"core.table.lookup", "core.table.lookup_many"})
    return frozenset({"core.table.lookup_many"})


async def _request_json_object(request: Request) -> dict[str, Any]:
    """Read a JSON object without turning malformed input into a gateway 500."""
    try:
        payload = await request.json()
    except (JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


GATEWAY_ACTION_RESOLVERS_BY_ENDPOINT: dict[str, GatewayActionResolver] = {
    "tracecat.agent.internal_router.run_agent_endpoint": _resolve_run_agent_action,
    "tracecat.cases.internal_router.get_case": _resolve_get_case_action,
    "tracecat.tables.internal_router.lookup_rows": _resolve_table_lookup_action,
}


async def resolve_gateway_actions(
    request: Request,
    endpoint: Callable[..., Any],
) -> frozenset[str] | None:
    """Resolve the concrete registry operation represented by this request."""
    key = endpoint_key(endpoint)
    if resolver := GATEWAY_ACTION_RESOLVERS_BY_ENDPOINT.get(key):
        return await resolver(request)
    return GATEWAY_ACTIONS_BY_ENDPOINT.get(key)


def _agent_gateway_action_allowed(
    claims: ExecutorTokenPayload,
    required_actions: frozenset[str] | None,
) -> bool:
    """Require one concrete action to pass both authorization bounds.

    ``required_actions`` is a set of equivalent candidates for one concrete
    endpoint operation. It is not a set of cumulative permissions: one action
    must be present in the signed Agent grant *and* allowed by the signed caller
    Role scopes.
    """
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
    """Enforce the Agent grant on the server-matched endpoint.

    This is a FastAPI dependency, rather than middleware, because FastAPI has
    already selected ``request.scope["endpoint"]`` when dependencies run. That
    gives us a constant-time lookup without reimplementing route matching.
    """
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        # The normal route authentication dependency owns missing credentials.
        return

    try:
        claims = verify_executor_token(token)
    except ValueError:
        # The normal route authentication dependency owns invalid credentials.
        return

    if (
        claims.action != PlatformAction.RUN_PYTHON
        # ``None`` marks an execution created before the Agent-grant patch. Keep
        # those in-flight Temporal histories on their recorded legacy behavior.
        or claims.allowed_actions is None
        or request.url.path == "/internal/health"
    ):
        return

    endpoint = request.scope.get("endpoint")
    required_actions = (
        await resolve_gateway_actions(request, endpoint) if callable(endpoint) else None
    )
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
