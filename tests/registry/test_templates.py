import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from tracecat.expressions.policy import ExpressionPolicy, expression_policy
from tracecat.registry.actions.schemas import (
    RegistryActionValidationErrorInfo,
    TemplateAction,
)
from tracecat.registry.actions.service import validate_action_template
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.repository import Repository


def _contains_secret_expression(value: Any) -> bool:
    match value:
        case str():
            return "${{" in value and "SECRETS." in value
        case list():
            return any(_contains_secret_expression(item) for item in value)
        case dict():
            return any(
                _contains_secret_expression(key) or _contains_secret_expression(item)
                for key, item in value.items()
            )
        case _:
            return False


@pytest.mark.anyio
async def test_base_registry_validate_template_actions():
    origin = DEFAULT_REGISTRY_ORIGIN
    repo = Repository(origin=origin)
    await repo.load_from_origin()
    val_errs: dict[str, list[RegistryActionValidationErrorInfo]] = defaultdict(list)
    for action_name in sorted(repo.store.keys()):
        action = repo.store[action_name]
        if not action.is_template:
            continue
        if errs := await validate_action_template(
            action,
            repo,
            check_db=False,
        ):
            val_errs[action.action].extend(errs)
    if val_errs:
        import io

        from rich.console import Console
        from rich.table import Table

        file = io.StringIO()
        console = Console(file=file)

        # Show this in a table
        table = Table(title="Validation Errors", show_lines=True)
        table.add_column("Action", no_wrap=False, overflow="fold")
        table.add_column("Details", no_wrap=False, overflow="fold")
        table.add_column(
            "Location [dim](Details)[/dim]", no_wrap=False, overflow="fold"
        )
        for action, errs in val_errs.items():
            for err in errs:
                table.add_row(
                    action,
                    "\n".join(err.details),
                    f"{err.loc_primary} [dim]({err.loc_secondary})[/dim]"
                    if err.loc_secondary
                    else err.loc_primary,
                )

        # Render table to string
        console.print(table)
        error_output = file.getvalue()
        raise AssertionError(error_output)
    assert len(val_errs) == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "file_path",
    Path("packages/tracecat-registry/tracecat_registry/templates").rglob("*.yml"),
    ids=lambda path: str(path.parts[-2:]),
)
async def test_template_action_validation(file_path):
    # Initialize an empty repository; each parameter registers only its template.
    repo = Repository()
    repo.init(include_base=False, include_templates=False)

    # Test parsing
    action = TemplateAction.from_yaml(file_path)
    assert action.type == "action"
    assert action.definition
    for step in action.definition.steps:
        for parameter, value in step.args.items():
            if _contains_secret_expression(value):
                assert (
                    expression_policy(step.action, parameter)
                    is ExpressionPolicy.RESOLVE
                )

    # Test registration
    repo.register_template_action(action)
    assert action.definition.action in repo


# --- Microsoft Graph product namespaces -----------------------------------
#
# The Microsoft Graph products share one SDK transport but must not share
# credentials: each template pins its product OAuth provider and declares that
# provider ahead of the generic Microsoft Graph fallback.

TEMPLATES_DIR = Path("packages/tracecat-registry/tracecat_registry/templates/tools")

MICROSOFT_GRAPH_FALLBACK_SECRETS = [
    ("microsoft_graph", "client_credentials"),
    ("microsoft_graph", "authorization_code"),
]

EXPECTED_OUTLOOK_ACTIONS = {
    "add_file_attachment",
    "copy_message",
    "create_child_folder",
    "create_draft",
    "create_mail_folder",
    "create_message_rule",
    "delete_mail_folder",
    "delete_message",
    "delete_message_rule",
    "forward_message",
    "get_mail_folder",
    "get_mailbox_settings",
    "get_message",
    "get_message_attachment",
    "get_message_rule",
    "list_child_folders",
    "list_folder_messages",
    "list_mail_folders",
    "list_message_attachments",
    "list_message_delta",
    "list_message_rules",
    "list_messages",
    "move_message",
    "reply_all",
    "reply_message",
    "send_draft",
    "send_mail",
    "update_mail_folder",
    "update_mailbox_settings",
    "update_message",
    "update_message_rule",
}

# The only Outlook actions allowed an API-native `payload` dictionary: three
# PATCH bodies where an omitted property and an explicit null differ, and the
# open-ended Outlook rule predicates.
OUTLOOK_GENERIC_PAYLOAD_ACTIONS = {
    "create_message_rule",
    "update_mailbox_settings",
    "update_message",
    "update_message_rule",
}

_EXPRESSION = re.compile(r"\$\{\{(.+?)\}\}")


def _load_templates(namespace_dir: str) -> list[TemplateAction]:
    paths = sorted((TEMPLATES_DIR / namespace_dir).rglob("*.yml"))
    return [TemplateAction.from_yaml(path) for path in paths]


def _declared_secrets(action: TemplateAction) -> list[tuple[str, str]]:
    return [
        (secret.provider_id, secret.grant_type)
        for secret in action.definition.secrets or []
        if secret.type == "oauth"
    ]


def test_graph_security_templates_call_the_generic_graph_sdk():
    actions = _load_templates("microsoft_graph_security")
    assert len(actions) == 19

    for action in actions:
        definition = action.definition
        assert definition.namespace == "tools.microsoft_graph_security"
        assert [step.action for step in definition.steps] == [
            "tools.microsoft_graph_sdk.call_method"
        ]
        assert definition.steps[0].args["oauth_provider"] == (
            "microsoft_graph_security"
        )
        assert _declared_secrets(action) == [
            ("microsoft_graph_security", "client_credentials"),
            ("microsoft_graph_security", "authorization_code"),
            *MICROSOFT_GRAPH_FALLBACK_SECRETS,
        ]
        assert all(secret.optional for secret in definition.secrets or [])


def test_outlook_templates_call_the_generic_graph_sdk():
    actions = _load_templates("microsoft_outlook")
    assert len(actions) == 31
    assert {action.definition.name for action in actions} == EXPECTED_OUTLOOK_ACTIONS

    for action in actions:
        definition = action.definition
        assert definition.namespace == "tools.microsoft_outlook"
        assert [step.action for step in definition.steps] == [
            "tools.microsoft_graph_sdk.call_method"
        ]
        assert definition.steps[0].args["oauth_provider"] == "microsoft_outlook"
        assert _declared_secrets(action) == [
            ("microsoft_outlook", "client_credentials"),
            ("microsoft_outlook", "authorization_code"),
            *MICROSOFT_GRAPH_FALLBACK_SECRETS,
        ]
        assert all(secret.optional for secret in definition.secrets or [])
        assert definition.returns == "${{ steps.call_graph.result }}"


def test_outlook_delta_preserves_opaque_continuation_urls():
    action = next(
        action
        for action in _load_templates("microsoft_outlook")
        if action.definition.name == "list_message_delta"
    )
    definition = action.definition

    assert "deltatoken" not in definition.expects
    assert "skiptoken" not in definition.expects
    assert "tools.microsoft_graph_sdk.call_continuation_method" in (
        definition.description or ""
    )
    assert "microsoft_outlook" in (definition.description or "")
    orderby_description = definition.expects["orderby"].description or ""
    assert "receivedDateTime desc" in orderby_description
    assert "receivedDateTime+desc" not in orderby_description


def test_outlook_templates_encode_every_path_identifier():
    for action in _load_templates("microsoft_outlook"):
        definition = action.definition
        assert "mailbox_user_id" in definition.expects
        graph_step = definition.steps[-1]
        path = graph_step.args["path"]
        assert isinstance(path, str)
        assert path.startswith("/users/")
        assert "/users/me" not in path
        expressions = _EXPRESSION.findall(path)
        assert expressions, f"{definition.action} interpolates no identifier"
        for expression in expressions:
            assert "FN.url_encode(inputs." in expression, (
                f"{definition.action} interpolates {expression.strip()!r} unencoded"
            )


def test_outlook_generic_payloads_are_restricted_to_the_justified_actions():
    with_generic_payload = {
        action.definition.name
        for action in _load_templates("microsoft_outlook")
        if action.definition.expects.get("payload") is not None
    }
    assert with_generic_payload == OUTLOOK_GENERIC_PAYLOAD_ACTIONS
