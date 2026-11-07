"""Tests for Jira credential selection between Atlassian OAuth and API tokens."""

from pathlib import Path
from typing import Any

import pytest

from tracecat.expressions.eval import eval_templated_object
from tracecat.registry.actions.schemas import TemplateAction
from tracecat.registry.repository import Repository

TEMPLATE_PATH = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/jira/resolve_auth_type.yml"
)
OAUTH_SECRETS = {"atlassian_oauth": {"ATLASSIAN_USER_TOKEN": "oauth-token"}}
BASIC_SECRETS = {
    "jira": {"JIRA_USEREMAIL": "user@example.com", "JIRA_API_TOKEN": "api-token"}
}


def _resolve_auth_type(secrets: dict[str, dict[str, str]]) -> Any:
    """Evaluate the template's step inputs against secrets, then run its script."""
    action = TemplateAction.from_yaml(TEMPLATE_PATH)
    (step,) = action.definition.steps
    inputs = eval_templated_object(step.args["inputs"], operand={"SECRETS": secrets})
    namespace: dict[str, Any] = {}
    exec(step.args["script"], namespace)
    return namespace["main"](**inputs)


def test_resolve_auth_type_selects_oauth() -> None:
    assert _resolve_auth_type(OAUTH_SECRETS) == "oauth"


def test_resolve_auth_type_selects_basic() -> None:
    assert _resolve_auth_type(BASIC_SECRETS) == "basic"


def test_resolve_auth_type_rejects_both() -> None:
    with pytest.raises(ValueError, match="Both the Atlassian OAuth integration"):
        _resolve_auth_type(OAUTH_SECRETS | BASIC_SECRETS)


@pytest.mark.parametrize(
    "secrets",
    [{}, {"jira": {"JIRA_USEREMAIL": "user@example.com"}}],
    ids=["empty", "partial_basic"],
)
def test_resolve_auth_type_rejects_neither(secrets: dict[str, dict[str, str]]) -> None:
    with pytest.raises(ValueError, match="No Jira credentials are configured"):
        _resolve_auth_type(secrets)


def test_resolve_auth_type_validates_as_template_step() -> None:
    """A template without inputs must validate when called from another template."""
    repo = Repository()
    repo.init(include_base=False, include_templates=False)
    action = TemplateAction.from_yaml(TEMPLATE_PATH)
    repo.register_template_action(action)

    bound = repo.get(action.definition.action)
    assert bound.validate_args(args={}) == {}
