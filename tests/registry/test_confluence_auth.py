"""Tests for Confluence credential selection between Atlassian OAuth and API tokens."""

import base64
from pathlib import Path

import pytest

from tracecat.expressions.eval import eval_templated_object
from tracecat.integrations.providers.atlassian.oauth import ATLASSIAN_SCOPES
from tracecat.registry.actions.schemas import TemplateAction

CONFLUENCE_TEMPLATES_DIR = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/confluence"
)
OAUTH_SECRETS = {"atlassian_oauth": {"ATLASSIAN_USER_TOKEN": "oauth-token"}}
BASIC_SECRETS = {
    "confluence": {
        "CONFLUENCE_USERNAME": "user@example.com",
        "CONFLUENCE_API_TOKEN": "api-token",
    }
}
BASIC_HEADER = "Basic " + base64.b64encode(b"user@example.com:api-token").decode()
TEMPLATE_PATHS = sorted(CONFLUENCE_TEMPLATES_DIR.glob("*.yml"))


def _http_steps(path: Path) -> list:
    action = TemplateAction.from_yaml(path)
    return [
        step for step in action.definition.steps if step.action == "core.http_request"
    ]


def test_confluence_templates_found() -> None:
    assert TEMPLATE_PATHS


@pytest.mark.parametrize("path", TEMPLATE_PATHS, ids=lambda p: p.stem)
def test_confluence_template_accepts_either_credential(path: Path) -> None:
    secrets = TemplateAction.from_yaml(path).definition.secrets or []
    by_kind = {getattr(s, "provider_id", None) or s.name: s for s in secrets}
    assert set(by_kind) == {"atlassian", "confluence"}
    assert all(s.optional for s in secrets)
    for step in _http_steps(path):
        assert "auth" not in step.args


@pytest.mark.parametrize("path", TEMPLATE_PATHS, ids=lambda p: p.stem)
@pytest.mark.parametrize(
    ("secrets", "expected"),
    [
        pytest.param(OAUTH_SECRETS, "Bearer oauth-token", id="oauth"),
        pytest.param(BASIC_SECRETS, BASIC_HEADER, id="basic"),
        pytest.param(OAUTH_SECRETS | BASIC_SECRETS, "Bearer oauth-token", id="both"),
    ],
)
def test_confluence_template_authorization(
    path: Path, secrets: dict[str, dict[str, str]], expected: str
) -> None:
    steps = _http_steps(path)
    assert steps
    for step in steps:
        header = eval_templated_object(
            step.args["headers"]["Authorization"], operand={"SECRETS": secrets}
        )
        assert header == expected


@pytest.mark.parametrize(
    "scope",
    [
        "read:confluence-content.all",
        "read:confluence-content.summary",
        "read:confluence-space.summary",
        "write:confluence-content",
        "search:confluence",
    ],
)
def test_atlassian_default_scopes_cover_confluence(scope: str) -> None:
    assert scope in ATLASSIAN_SCOPES
