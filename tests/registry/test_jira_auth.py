"""Tests for Jira credential selection between Atlassian OAuth and API tokens."""

import base64
from pathlib import Path

import pytest

from tracecat.expressions.eval import eval_templated_object
from tracecat.registry.actions.schemas import TemplateAction

JIRA_TEMPLATES_DIR = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/jira"
)
OAUTH_SECRETS = {"atlassian_oauth": {"ATLASSIAN_USER_TOKEN": "oauth-token"}}
BASIC_SECRETS = {
    "jira": {"JIRA_USEREMAIL": "user@example.com", "JIRA_API_TOKEN": "api-token"}
}
BASIC_HEADER = "Basic " + base64.b64encode(b"user@example.com:api-token").decode()
TEMPLATE_PATHS = sorted(JIRA_TEMPLATES_DIR.glob("*.yml"))


def _authorization_headers(path: Path, secrets: dict[str, dict[str, str]]) -> list:
    action = TemplateAction.from_yaml(path)
    return [
        eval_templated_object(
            step.args["headers"]["Authorization"], operand={"SECRETS": secrets}
        )
        for step in action.definition.steps
        if "Authorization" in step.args.get("headers", {})
    ]


def test_jira_templates_found() -> None:
    assert TEMPLATE_PATHS


@pytest.mark.parametrize("path", TEMPLATE_PATHS, ids=lambda p: p.stem)
@pytest.mark.parametrize(
    ("secrets", "expected"),
    [
        pytest.param(OAUTH_SECRETS, "Bearer oauth-token", id="oauth"),
        pytest.param(BASIC_SECRETS, BASIC_HEADER, id="basic"),
        pytest.param(OAUTH_SECRETS | BASIC_SECRETS, "Bearer oauth-token", id="both"),
    ],
)
def test_jira_template_authorization(
    path: Path, secrets: dict[str, dict[str, str]], expected: str
) -> None:
    headers = _authorization_headers(path, secrets)
    assert headers
    assert all(header == expected for header in headers)
