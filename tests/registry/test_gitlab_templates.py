import re
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from tracecat_registry._internal.safe_lambda import build_safe_lambda

from tracecat.registry.actions.schemas import TemplateAction

GITLAB_ROOT = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/gitlab"
)
type RawTemplate = dict[str, Any]

EXPECTED_ENDPOINTS = {
    "get_current_user": ("GET", "/user", "json"),
    "get_project": ("GET", "/projects/{project_id}", "json"),
    "list_repository_tree": ("GET", "/projects/{project_id}/repository/tree", "json"),
    "get_repository_file": (
        "GET",
        "/projects/{project_id}/repository/files/{file_path}",
        "json",
    ),
    "get_repository_file_raw": (
        "GET",
        "/projects/{project_id}/repository/files/{file_path}/raw",
        "binary",
    ),
    "create_repository_file": (
        "POST",
        "/projects/{project_id}/repository/files/{file_path}",
        "json",
    ),
    "update_repository_file": (
        "PUT",
        "/projects/{project_id}/repository/files/{file_path}",
        "json",
    ),
    "delete_repository_file": (
        "DELETE",
        "/projects/{project_id}/repository/files/{file_path}",
        "json",
    ),
    "list_branches": ("GET", "/projects/{project_id}/repository/branches", "json"),
    "get_branch": (
        "GET",
        "/projects/{project_id}/repository/branches/{branch}",
        "json",
    ),
    "list_commits": ("GET", "/projects/{project_id}/repository/commits", "json"),
    "get_commit": ("GET", "/projects/{project_id}/repository/commits/{sha}", "json"),
    "get_commit_diff": (
        "GET",
        "/projects/{project_id}/repository/commits/{sha}/diff",
        "json",
    ),
    "compare_revisions": ("GET", "/projects/{project_id}/repository/compare", "json"),
    "create_commit": ("POST", "/projects/{project_id}/repository/commits", "json"),
    "search_project": ("GET", "/projects/{project_id}/search", "json"),
    "semantic_code_search": ("GET", "/projects/{project_id}/search/semantic", "json"),
    "list_project_wiki_pages": ("GET", "/projects/{project_id}/wikis", "json"),
    "list_project_issues": ("GET", "/projects/{project_id}/issues", "json"),
    "create_issue": ("POST", "/projects/{project_id}/issues", "json"),
    "get_issue": ("GET", "/projects/{project_id}/issues/{issue_iid}", "json"),
    "update_issue": ("PUT", "/projects/{project_id}/issues/{issue_iid}", "json"),
    "list_issue_notes": (
        "GET",
        "/projects/{project_id}/issues/{issue_iid}/notes",
        "json",
    ),
    "create_issue_note": (
        "POST",
        "/projects/{project_id}/issues/{issue_iid}/notes",
        "json",
    ),
    "update_issue_note": (
        "PUT",
        "/projects/{project_id}/issues/{issue_iid}/notes/{note_id}",
        "json",
    ),
    "list_issue_links": (
        "GET",
        "/projects/{project_id}/issues/{issue_iid}/links",
        "json",
    ),
    "create_issue_link": (
        "POST",
        "/projects/{project_id}/issues/{issue_iid}/links",
        "json",
    ),
    "delete_issue_link": (
        "DELETE",
        "/projects/{project_id}/issues/{issue_iid}/links/{issue_link_id}",
        "json",
    ),
    "list_project_labels": ("GET", "/projects/{project_id}/labels", "json"),
    "list_merge_requests": ("GET", "/projects/{project_id}/merge_requests", "json"),
    "create_merge_request": ("POST", "/projects/{project_id}/merge_requests", "json"),
    "get_merge_request": (
        "GET",
        "/projects/{project_id}/merge_requests/{merge_request_iid}",
        "json",
    ),
    "update_merge_request": (
        "PUT",
        "/projects/{project_id}/merge_requests/{merge_request_iid}",
        "json",
    ),
    "list_merge_request_commits": (
        "GET",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/commits",
        "json",
    ),
    "list_merge_request_diffs": (
        "GET",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/diffs",
        "json",
    ),
    "list_merge_request_pipelines": (
        "GET",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/pipelines",
        "json",
    ),
    "merge_merge_request": (
        "PUT",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/merge",
        "json",
    ),
    "rebase_merge_request": (
        "PUT",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/rebase",
        "json",
    ),
    "list_merge_request_notes": (
        "GET",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/notes",
        "json",
    ),
    "create_merge_request_note": (
        "POST",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/notes",
        "json",
    ),
    "list_merge_request_discussions": (
        "GET",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/discussions",
        "json",
    ),
    "create_merge_request_discussion": (
        "POST",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/discussions",
        "json",
    ),
    "create_merge_request_discussion_note": (
        "POST",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/discussions/{discussion_id}/notes",
        "json",
    ),
    "resolve_merge_request_discussion": (
        "PUT",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/discussions/{discussion_id}",
        "json",
    ),
    "get_merge_request_approval_state": (
        "GET",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/approval_state",
        "json",
    ),
    "approve_merge_request": (
        "POST",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/approve",
        "json",
    ),
    "unapprove_merge_request": (
        "POST",
        "/projects/{project_id}/merge_requests/{merge_request_iid}/unapprove",
        "json",
    ),
    "list_pipelines": ("GET", "/projects/{project_id}/pipelines", "json"),
    "create_pipeline": ("POST", "/projects/{project_id}/pipeline", "json"),
    "get_pipeline": ("GET", "/projects/{project_id}/pipelines/{pipeline_id}", "json"),
    "update_pipeline_metadata": (
        "PUT",
        "/projects/{project_id}/pipelines/{pipeline_id}/metadata",
        "json",
    ),
    "cancel_pipeline": (
        "POST",
        "/projects/{project_id}/pipelines/{pipeline_id}/cancel",
        "json",
    ),
    "retry_pipeline": (
        "POST",
        "/projects/{project_id}/pipelines/{pipeline_id}/retry",
        "json",
    ),
    "delete_pipeline": (
        "DELETE",
        "/projects/{project_id}/pipelines/{pipeline_id}",
        "json",
    ),
    "list_pipeline_jobs": (
        "GET",
        "/projects/{project_id}/pipelines/{pipeline_id}/jobs",
        "json",
    ),
    "get_job": ("GET", "/projects/{project_id}/jobs/{job_id}", "json"),
    "get_job_trace": ("GET", "/projects/{project_id}/jobs/{job_id}/trace", "text"),
    "retry_job": ("POST", "/projects/{project_id}/jobs/{job_id}/retry", "json"),
    "cancel_job": ("POST", "/projects/{project_id}/jobs/{job_id}/cancel", "json"),
    "download_job_artifacts": (
        "GET",
        "/projects/{project_id}/jobs/{job_id}/artifacts",
        "binary",
    ),
    "lint_ci_configuration": ("POST", "/projects/{project_id}/ci/lint", "json"),
    "list_releases": ("GET", "/projects/{project_id}/releases", "json"),
    "create_release": ("POST", "/projects/{project_id}/releases", "json"),
    "update_release": ("PUT", "/projects/{project_id}/releases/{tag_name}", "json"),
    "list_project_vulnerability_findings": (
        "GET",
        "/projects/{project_id}/vulnerability_findings",
        "json",
    ),
    "get_vulnerability": ("GET", "/vulnerabilities/{vulnerability_id}", "json"),
    "create_project_vulnerability_export_and_wait": (
        "POST",
        "/security/projects/{project_id}/vulnerability_exports",
        "poll",
    ),
    "get_vulnerability_export": (
        "GET",
        "/security/vulnerability_exports/{export_id}",
        "json",
    ),
    "download_vulnerability_export": (
        "GET",
        "/security/vulnerability_exports/{export_id}/download",
        "binary",
    ),
    "list_project_dependencies": ("GET", "/projects/{project_id}/dependencies", "json"),
    "create_project_dependency_list_export_and_wait": (
        "POST",
        "/projects/{project_id}/dependency_list_exports",
        "poll",
    ),
    "get_dependency_list_export": (
        "GET",
        "/dependency_list_exports/{export_id}",
        "json",
    ),
    "download_dependency_list_export": (
        "GET",
        "/dependency_list_exports/{export_id}/download",
        "binary",
    ),
    "get_project_security_settings": (
        "GET",
        "/projects/{project_id}/security_settings",
        "json",
    ),
    "update_project_security_settings": (
        "PUT",
        "/projects/{project_id}/security_settings",
        "json",
    ),
    "list_project_audit_events": ("GET", "/projects/{project_id}/audit_events", "json"),
    "list_protected_branches": (
        "GET",
        "/projects/{project_id}/protected_branches",
        "json",
    ),
    "get_protected_branch": (
        "GET",
        "/projects/{project_id}/protected_branches/{name}",
        "json",
    ),
    "protect_branch": ("POST", "/projects/{project_id}/protected_branches", "json"),
    "update_protected_branch": (
        "PATCH",
        "/projects/{project_id}/protected_branches/{name}",
        "json",
    ),
    "unprotect_branch": (
        "DELETE",
        "/projects/{project_id}/protected_branches/{name}",
        "json",
    ),
    "list_project_approval_rules": (
        "GET",
        "/projects/{project_id}/approval_rules",
        "json",
    ),
    "create_project_approval_rule": (
        "POST",
        "/projects/{project_id}/approval_rules",
        "json",
    ),
    "update_project_approval_rule": (
        "PUT",
        "/projects/{project_id}/approval_rules/{approval_rule_id}",
        "json",
    ),
    "delete_project_approval_rule": (
        "DELETE",
        "/projects/{project_id}/approval_rules/{approval_rule_id}",
        "json",
    ),
    "list_project_members_with_inherited_access": (
        "GET",
        "/projects/{project_id}/members/all",
        "json",
    ),
    "get_project_member_with_inherited_access": (
        "GET",
        "/projects/{project_id}/members/all/{user_id}",
        "json",
    ),
    "list_project_deploy_keys": ("GET", "/projects/{project_id}/deploy_keys", "json"),
    "create_project_deploy_key": ("POST", "/projects/{project_id}/deploy_keys", "json"),
    "delete_project_deploy_key": (
        "DELETE",
        "/projects/{project_id}/deploy_keys/{key_id}",
        "json",
    ),
    "list_project_variables": ("GET", "/projects/{project_id}/variables", "json"),
    "create_project_variable": ("POST", "/projects/{project_id}/variables", "json"),
    "update_project_variable": (
        "PUT",
        "/projects/{project_id}/variables/{key}",
        "json",
    ),
}

EXPECTED_POLL_ENDPOINTS = {
    "create_project_vulnerability_export_and_wait": (
        "GET",
        "/security/vulnerability_exports/{export_id}",
    ),
    "create_project_dependency_list_export_and_wait": (
        "GET",
        "/dependency_list_exports/{export_id}",
    ),
}

MCP_REST_BASELINE = {
    "create_issue",
    "get_issue",
    "create_merge_request",
    "get_merge_request",
    "list_merge_requests",
    "list_merge_request_commits",
    "list_merge_request_diffs",
    "list_merge_request_pipelines",
    "create_merge_request_note",
    "list_merge_request_notes",
    "list_pipeline_jobs",
    "get_job_trace",
    "list_pipelines",
    "create_pipeline",
    "update_pipeline_metadata",
    "retry_pipeline",
    "cancel_pipeline",
    "delete_pipeline",
    "create_issue_note",
    "list_issue_notes",
    "create_issue_link",
    "search_project",
    "list_project_labels",
    "list_project_wiki_pages",
    "semantic_code_search",
}


@cache
def load_templates() -> dict[str, RawTemplate]:
    templates: dict[str, RawTemplate] = {}
    for path in GITLAB_ROOT.rglob("*.yml"):
        parsed = TemplateAction.from_yaml(path)
        assert parsed.type == "action"
        with path.open() as handle:
            template = yaml.safe_load(handle)
        name = template["definition"]["name"]
        assert name not in templates
        templates[name] = template
    return templates


def normalized_path(url: str) -> str:
    url = url.removeprefix("${{ VARS.gitlab.base_url || inputs.base_url }}")
    pattern = re.compile(
        r"\$\{\{ FN\.url_encode\(inputs\.([a-zA-Z_][a-zA-Z0-9_]*)\) \}\}"
    )
    url = pattern.sub(lambda match: "{" + match.group(1) + "}", url)
    return url.replace(
        "${{ FN.url_encode(steps.request.result.data.id) }}", "{export_id}"
    )


def test_catalog_contract() -> None:
    templates = load_templates()
    assert len(templates) == 93
    assert set(templates) == set(EXPECTED_ENDPOINTS)

    for name, (method, path, mode) in EXPECTED_ENDPOINTS.items():
        definition = templates[name]["definition"]
        expected_title = name.replace("_", " ").capitalize()
        assert definition["title"] == expected_title
        if ". Calls " in definition["description"]:
            assert definition["description"].startswith(f"{expected_title}. Calls ")
        assert definition["namespace"] == "tools.gitlab"
        assert definition["display_group"] == "GitLab"
        assert "api-scoped GitLab project access token" in definition["description"]
        assert "enforced by GitLab" in definition["description"]
        assert definition["secrets"] == [
            {
                "name": "gitlab",
                "keys": ["GITLAB_API_TOKEN"],
            }
        ]
        assert definition["expects"]["base_url"] == {
            "type": "str",
            "description": (
                "GitLab API v4 base URL. `VARS.gitlab.base_url` overrides this value."
            ),
            "default": "https://gitlab.com/api/v4",
        }

        request = definition["steps"][0]
        assert request["action"] == "core.http_request"
        assert request["args"]["method"] == method
        assert normalized_path(request["args"]["url"]) == path
        assert request["args"]["url"].startswith(
            "${{ VARS.gitlab.base_url || inputs.base_url }}"
        )
        assert request["args"]["headers"]["PRIVATE-TOKEN"] == (
            "${{ SECRETS.gitlab.GITLAB_API_TOKEN }}"
        )
        expected_accept = {
            "binary": "application/octet-stream",
            "text": "text/plain",
        }.get(mode, "application/json")
        assert request["args"]["headers"]["Accept"] == expected_accept
        assert definition["expects"]["params"]["type"] == "dict[str, Any] | None"
        assert request["args"]["params"] == "${{ inputs.params }}"

        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            assert definition["expects"]["payload"]["type"] == ("dict[str, Any] | None")
            assert request["args"]["payload"] == "${{ inputs.payload }}"

        for input_name in re.findall(
            r"FN\.url_encode\(inputs\.([a-zA-Z_][a-zA-Z0-9_]*)\)",
            request["args"]["url"],
        ):
            assert input_name in definition["expects"]

        if mode == "binary":
            assert request["args"]["follow_redirects"] is True
            assert request["args"]["base64_encode_data"] is True
            assert definition["returns"] == "${{ steps.request.result }}"
        elif mode == "text":
            assert request["args"]["follow_redirects"] is True
            assert definition["returns"] == "${{ steps.request.result }}"
        elif mode == "poll":
            poll = definition["steps"][1]
            assert poll["action"] == "core.http_poll"
            poll_method, poll_path = EXPECTED_POLL_ENDPOINTS[name]
            assert poll["args"]["method"] == poll_method
            assert normalized_path(poll["args"]["url"]) == poll_path
            assert definition["returns"] == "${{ steps.poll.result }}"
        else:
            assert definition["returns"] == "${{ steps.request.result }}"

        for step in definition["steps"]:
            if step["action"] not in {"core.http_request", "core.http_poll"}:
                continue
            assert step["args"]["url"].startswith(
                "${{ VARS.gitlab.base_url || inputs.base_url }}"
            )
            assert step["args"]["headers"]["PRIVATE-TOKEN"] == (
                "${{ SECRETS.gitlab.GITLAB_API_TOKEN }}"
            )


def test_project_and_nested_file_paths_are_component_encoded() -> None:
    definition = load_templates()["get_repository_file"]["definition"]
    url = definition["steps"][0]["args"]["url"]
    assert "FN.url_encode(inputs.project_id)" in url
    assert "FN.url_encode(inputs.file_path)" in url


def test_bug_and_security_issue_payload_is_native() -> None:
    definition = load_templates()["create_issue"]["definition"]
    request = definition["steps"][0]["args"]
    assert normalized_path(request["url"]) == "/projects/{project_id}/issues"
    assert request["payload"] == "${{ inputs.payload }}"
    for field in ("issue_type", "confidential", "labels", "assignee_ids", "severity"):
        assert field in definition["description"]


def test_security_inventory_is_p0() -> None:
    required = {
        "list_project_vulnerability_findings",
        "get_vulnerability",
        "create_project_vulnerability_export_and_wait",
        "get_vulnerability_export",
        "download_vulnerability_export",
        "list_project_dependencies",
        "create_project_dependency_list_export_and_wait",
        "get_dependency_list_export",
        "download_dependency_list_export",
        "get_project_security_settings",
        "update_project_security_settings",
        "list_project_audit_events",
    }
    assert required <= load_templates().keys()


def test_mcp_rest_baseline_is_present() -> None:
    assert MCP_REST_BASELINE <= load_templates().keys()


def test_export_polling_is_bounded_and_status_code_driven() -> None:
    for name in (
        "create_project_vulnerability_export_and_wait",
        "create_project_dependency_list_export_and_wait",
    ):
        definition = load_templates()[name]["definition"]
        poll = definition["steps"][1]
        condition = build_safe_lambda(poll["args"]["poll_condition"])
        assert not condition({"status_code": 202, "headers": {}, "data": {}})
        assert condition({"status_code": 200, "headers": {}, "data": {}})
        assert condition({"status_code": 500, "headers": {}, "data": {}})
        assert "poll_max_attempts" not in definition["expects"]
        assert poll["args"]["poll_max_attempts"] == 10
        assert poll["args"]["method"] == "GET"


def test_gitlab_templates_do_not_declare_oauth() -> None:
    for template in load_templates().values():
        secrets = template["definition"]["secrets"]
        assert all("type" not in secret for secret in secrets)


def test_deprecated_security_endpoints_are_disclosed() -> None:
    for name in ("list_project_vulnerability_findings", "get_vulnerability"):
        description = load_templates()[name]["definition"]["description"]
        assert "deprecated and unstable" in description
        assert "preserved unchanged" in description
