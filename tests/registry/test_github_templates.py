import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml
from github_template_contracts import (
    ACTIONS_ENDPOINTS,
    CODE_ENDPOINTS,
    DEPENDABOT_ENDPOINTS,
    ISSUES_ENDPOINTS,
    ORGANIZATION_ENDPOINTS,
    POSTURE_ENDPOINTS,
    PULL_REQUESTS_ENDPOINTS,
    SEARCH_ENDPOINTS,
    SECURITY_ENDPOINTS,
    SECURITY_REPORTS_ENDPOINTS,
    EndpointContract,
)
from tracecat_registry._internal.safe_lambda import build_safe_lambda

from tracecat.registry.actions.schemas import TemplateAction

GITHUB_ROOT = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/github"
)
# The assertions intentionally inspect the raw YAML mapping alongside the parsed
# TemplateAction because they pin expression strings and transport details.
type RawTemplate = dict[str, Any]

EXPECTED_ENDPOINTS: dict[str, EndpointContract] = {}
for endpoint_group in (
    CODE_ENDPOINTS,
    SEARCH_ENDPOINTS,
    ISSUES_ENDPOINTS,
    PULL_REQUESTS_ENDPOINTS,
    SECURITY_REPORTS_ENDPOINTS,
    SECURITY_ENDPOINTS,
    DEPENDABOT_ENDPOINTS,
    ACTIONS_ENDPOINTS,
    ORGANIZATION_ENDPOINTS,
    POSTURE_ENDPOINTS,
):
    assert not EXPECTED_ENDPOINTS.keys() & endpoint_group.keys()
    EXPECTED_ENDPOINTS.update(endpoint_group)

EXPECTED_POLL_ENDPOINTS = {
    "upload_sarif_and_wait": (
        "GET",
        "/repos/{owner}/{repo}/code-scanning/sarifs/{sarif_id}",
    ),
    "create_code_scanning_autofix_and_wait": (
        "GET",
        "/repos/{owner}/{repo}/code-scanning/alerts/{alert_number}/autofix",
    ),
}

INTEGER_PATH_INPUTS = {
    "alert_number",
    "artifact_id",
    "comment_id",
    "configuration_id",
    "issue_id",
    "issue_number",
    "job_id",
    "pull_number",
    "release_id",
    "review_id",
    "run_id",
}


@cache
def load_templates() -> dict[str, RawTemplate]:
    templates: dict[str, RawTemplate] = {}
    for path in GITHUB_ROOT.rglob("*.yml"):
        parsed = TemplateAction.from_yaml(path)
        assert parsed.type == "action"
        with path.open() as handle:
            template = yaml.safe_load(handle)
        name = template["definition"]["name"]
        assert name not in templates
        templates[name] = template
    return templates


def normalized_path(url: str) -> str:
    url = url.removeprefix("${{ inputs.base_url }}")
    pattern = re.compile(
        r'\$\{\{ FN\.url_encode\(inputs\.([a-zA-Z_][a-zA-Z0-9_]*)(?:,\s*"/")?\) \}\}'
    )
    url = pattern.sub(lambda match: "{" + match.group(1) + "}", url)
    return url.replace(
        "${{ FN.url_encode(steps.request.result.data.id) }}", "{sarif_id}"
    )


def test_catalog_contract() -> None:
    templates = load_templates()
    assert set(templates) == set(EXPECTED_ENDPOINTS)
    for name, (method, path, scope_note, mode) in EXPECTED_ENDPOINTS.items():
        definition = templates[name]["definition"]
        title = definition["title"]
        assert title == title.capitalize()
        assert definition["description"].startswith(f"{title}. Calls ")
        assert definition["namespace"] == "tools.github"
        assert scope_note in definition["description"]
        serialized_template = yaml.safe_dump(templates[name])
        for unsupported_reference in (
            "VARS.github.base_url",
            "GITHUB_API_BASE_URL",
            "GHES",
            "GitHub Enterprise",
        ):
            assert unsupported_reference not in serialized_template
        assert definition["secrets"] == [
            {
                "type": "oauth",
                "provider_id": "github",
                "grant_type": "authorization_code",
            }
        ]
        assert definition["expects"]["base_url"] == {
            "type": "str",
            "description": "GitHub.com REST API base URL.",
            "default": "https://api.github.com",
        }
        request = definition["steps"][0]
        assert request["action"] == "core.http_request"
        assert request["args"]["method"] == method
        request_url = request["args"]["url"]
        assert request_url.startswith("${{ inputs.base_url }}")
        assert normalized_path(request_url) == path
        for input_name, safe_arg in re.findall(
            r'FN\.url_encode\(inputs\.([a-zA-Z_][a-zA-Z0-9_]*)(?:,\s*("/"))?\)',
            request_url,
        ):
            assert safe_arg == ('"/"' if input_name == "path" else "")
            if input_name in INTEGER_PATH_INPUTS:
                assert definition["expects"][input_name]["type"] == "int"
            elif input_name == "workflow_id":
                assert definition["expects"][input_name]["type"] == "str | int"
        assert request["args"]["headers"] == {
            "Authorization": "Bearer ${{ SECRETS.github_oauth.GITHUB_USER_TOKEN }}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        assert definition["expects"]["params"]["type"] == "dict[str, Any] | None"
        assert request["args"]["params"] == "${{ inputs.params }}"
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            assert request["args"]["payload"] == "${{ inputs.payload }}"
        if mode == "json":
            assert definition["returns"] == "${{ steps.request.result }}"
        elif mode == "binary":
            assert request["args"]["follow_redirects"] is True
            assert request["args"]["base64_encode_data"] is True
            assert definition["returns"] == "${{ steps.request.result }}"
        else:
            poll = definition["steps"][1]
            assert poll["action"] == "core.http_poll"
            poll_method, poll_path = EXPECTED_POLL_ENDPOINTS[name]
            assert poll["args"]["method"] == poll_method
            assert normalized_path(poll["args"]["url"]) == poll_path
            assert definition["returns"] == "${{ steps.poll.result }}"

        for step in definition["steps"]:
            if step["action"] not in {"core.http_request", "core.http_poll"}:
                continue
            assert step["args"]["url"].startswith("${{ inputs.base_url }}")


@pytest.mark.parametrize(
    "name",
    [
        "submit_private_vulnerability_report",
        "enable_private_vulnerability_reporting",
        "update_repo_security_advisory",
        "request_security_advisory_cve",
        "create_security_advisory_fork",
    ],
)
def test_private_security_reporting_is_p0(name: str) -> None:
    template = load_templates()[name]["definition"]
    assert "repo (default)" in template["description"]
    assert (
        "/security-advisories" in EXPECTED_ENDPOINTS[name][1]
        or "private-vulnerability-reporting" in EXPECTED_ENDPOINTS[name][1]
    )


def test_security_reporting_and_dependabot_inventories_are_complete() -> None:
    templates = load_templates()
    security_reporting = {
        "create_repository_security_advisory",
        "create_security_advisory_fork",
        "disable_private_vulnerability_reporting",
        "enable_private_vulnerability_reporting",
        "get_global_security_advisory",
        "get_private_vulnerability_reporting",
        "get_repo_security_advisory",
        "list_global_security_advisories",
        "list_organization_security_advisories",
        "list_repo_security_advisories",
        "request_security_advisory_cve",
        "submit_private_vulnerability_report",
        "update_repo_security_advisory",
    }
    dependabot = {
        "delete_organization_dependabot_secret",
        "delete_repository_dependabot_secret",
        "get_dependabot_alert",
        "get_organization_dependabot_public_key",
        "get_organization_dependabot_secret",
        "get_repository_dependabot_public_key",
        "get_repository_dependabot_secret",
        "list_org_dependabot_alerts",
        "list_organization_dependabot_secret_repositories",
        "list_organization_dependabot_secrets",
        "list_repo_dependabot_alerts",
        "list_repository_dependabot_secrets",
        "set_organization_dependabot_secret_repositories",
        "update_dependabot_alert",
        "upsert_organization_dependabot_secret",
        "upsert_repository_dependabot_secret",
    }

    assert security_reporting <= templates.keys()
    assert dependabot <= templates.keys()


def test_private_report_uses_native_payload_and_not_issues() -> None:
    definition = load_templates()["submit_private_vulnerability_report"]["definition"]
    request = definition["steps"][0]["args"]
    assert (
        normalized_path(request["url"])
        == "/repos/{owner}/{repo}/security-advisories/reports"
    )
    assert request["payload"] == "${{ inputs.payload }}"
    assert (
        "summary, description, severity, vulnerabilities" in definition["description"]
    )
    assert "/issues" not in request["url"]


def test_bug_issue_payload_and_label_fallback_contract() -> None:
    definition = load_templates()["create_issue"]["definition"]
    request = definition["steps"][0]["args"]
    assert normalized_path(request["url"]) == "/repos/{owner}/{repo}/issues"
    assert request["payload"] == "${{ inputs.payload }}"
    assert "type: Bug" in definition["description"]
    assert "labels: [bug]" in definition["description"]
    assert "caller-controlled fallback" in definition["description"]
    for field in ("labels", "assignees", "milestone", "issue_field_values"):
        assert field in definition["description"]


def test_sub_issue_and_dependency_tracking_contracts() -> None:
    for name in (
        "list_sub_issues",
        "add_sub_issue",
        "remove_sub_issue",
        "reprioritize_sub_issue",
        "list_blocked_by_dependencies",
        "list_blocking_dependencies",
        "add_blocked_by_dependency",
        "remove_blocked_by_dependency",
    ):
        assert name in load_templates()


def test_nested_content_path_preserves_slashes() -> None:
    definition = load_templates()["get_repository_content"]["definition"]
    url = definition["steps"][0]["args"]["url"]
    assert 'FN.url_encode(inputs.path, "/")' in url
    assert "FN.url_encode(inputs.owner)" in url


def test_polling_contracts() -> None:
    templates = load_templates()
    sarif = templates["upload_sarif_and_wait"]["definition"]
    assert sarif["steps"][0]["args"]["method"] == "POST"
    assert sarif["steps"][1]["args"]["method"] == "GET"
    sarif_condition = build_safe_lambda(sarif["steps"][1]["args"]["poll_condition"])
    assert not sarif_condition(
        {"status_code": 200, "data": {"processing_status": "pending"}}
    )
    assert sarif_condition(
        {"status_code": 200, "data": {"processing_status": "complete"}}
    )
    assert sarif_condition(
        {"status_code": 200, "data": {"processing_status": "new_terminal_state"}}
    )
    assert sarif_condition({"status_code": 200, "data": {}})
    assert sarif_condition(
        {"status_code": 503, "data": {"processing_status": "complete"}}
    )
    assert "poll_max_attempts" not in sarif["expects"]
    assert sarif["steps"][1]["args"]["poll_max_attempts"] == 10

    autofix = templates["create_code_scanning_autofix_and_wait"]["definition"]
    assert autofix["steps"][0]["args"]["method"] == "POST"
    assert autofix["steps"][1]["args"]["method"] == "GET"
    autofix_condition = build_safe_lambda(autofix["steps"][1]["args"]["poll_condition"])
    assert not autofix_condition({"status_code": 200, "data": {"status": "pending"}})
    for status in ("success", "error", "outdated", "new_terminal_state"):
        assert autofix_condition({"status_code": 200, "data": {"status": status}})
    assert autofix_condition({"status_code": 200, "data": {}})
    assert autofix_condition({"status_code": 503, "data": {"status": "success"}})
    assert "poll_max_attempts" not in autofix["expects"]
    assert autofix["steps"][1]["args"]["poll_max_attempts"] == 10
