import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml
from tracecat_registry._internal.safe_lambda import build_safe_lambda

from tracecat.registry.actions.schemas import TemplateAction

GITHUB_ROOT = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/github"
)
# The assertions intentionally inspect the raw YAML mapping alongside the parsed
# TemplateAction because they pin expression strings and transport details.
type RawTemplate = dict[str, Any]
EXPECTED_ENDPOINTS = {
    "get_authenticated_user": (
        "GET",
        "/user",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_repository": (
        "GET",
        "/repos/{owner}/{repo}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_repository": (
        "PATCH",
        "/repos/{owner}/{repo}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_organization_repositories": ("GET", "/orgs/{org}/repos", "read:org", "json"),
    "get_repository_content": (
        "GET",
        "/repos/{owner}/{repo}/contents/{path}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_or_update_repository_content": (
        "PUT",
        "/repos/{owner}/{repo}/contents/{path}",
        "repo (default; omit only for public "
        "repository data); workflow when modifying "
        ".github/workflows",
        "json",
    ),
    "delete_repository_content": (
        "DELETE",
        "/repos/{owner}/{repo}/contents/{path}",
        "repo (default; omit only for public repository data); "
        "workflow when modifying .github/workflows",
        "json",
    ),
    "get_git_tree": (
        "GET",
        "/repos/{owner}/{repo}/git/trees/{tree_sha}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_commits": (
        "GET",
        "/repos/{owner}/{repo}/commits",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_commit": (
        "GET",
        "/repos/{owner}/{repo}/commits/{ref}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "compare_commits": (
        "GET",
        "/repos/{owner}/{repo}/compare/{basehead}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_branches": (
        "GET",
        "/repos/{owner}/{repo}/branches",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_branch": (
        "GET",
        "/repos/{owner}/{repo}/branches/{branch}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "search_repositories": (
        "GET",
        "/search/repositories",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "search_code": (
        "GET",
        "/search/code",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "search_commits": (
        "GET",
        "/search/commits",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "search_issues": (
        "GET",
        "/search/issues",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_repository_issues": (
        "GET",
        "/repos/{owner}/{repo}/issues",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_issue": (
        "POST",
        "/repos/{owner}/{repo}/issues",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_issue": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_issue": (
        "PATCH",
        "/repos/{owner}/{repo}/issues/{issue_number}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_issue_comments": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}/comments",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_issue_comment": (
        "POST",
        "/repos/{owner}/{repo}/issues/{issue_number}/comments",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_issue_comment": (
        "PATCH",
        "/repos/{owner}/{repo}/issues/comments/{comment_id}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_issue_timeline_events": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}/timeline",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_repository_labels": (
        "GET",
        "/repos/{owner}/{repo}/labels",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "add_issue_labels": (
        "POST",
        "/repos/{owner}/{repo}/issues/{issue_number}/labels",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "replace_issue_labels": (
        "PUT",
        "/repos/{owner}/{repo}/issues/{issue_number}/labels",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "remove_issue_label": (
        "DELETE",
        "/repos/{owner}/{repo}/issues/{issue_number}/labels/{name}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_repository_issue_types": (
        "GET",
        "/repos/{owner}/{repo}/issue-types",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_sub_issues": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}/sub_issues",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "add_sub_issue": (
        "POST",
        "/repos/{owner}/{repo}/issues/{issue_number}/sub_issues",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "remove_sub_issue": (
        "DELETE",
        "/repos/{owner}/{repo}/issues/{issue_number}/sub_issue",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "reprioritize_sub_issue": (
        "PATCH",
        "/repos/{owner}/{repo}/issues/{issue_number}/sub_issues/priority",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_blocked_by_dependencies": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocked_by",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_blocking_dependencies": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocking",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "add_blocked_by_dependency": (
        "POST",
        "/repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocked_by",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "remove_blocked_by_dependency": (
        "DELETE",
        "/repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocked_by/{issue_id}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_private_vulnerability_reporting": (
        "GET",
        "/repos/{owner}/{repo}/private-vulnerability-reporting",
        "repo (default)",
        "json",
    ),
    "enable_private_vulnerability_reporting": (
        "PUT",
        "/repos/{owner}/{repo}/private-vulnerability-reporting",
        "repo (default)",
        "json",
    ),
    "disable_private_vulnerability_reporting": (
        "DELETE",
        "/repos/{owner}/{repo}/private-vulnerability-reporting",
        "repo (default)",
        "json",
    ),
    "submit_private_vulnerability_report": (
        "POST",
        "/repos/{owner}/{repo}/security-advisories/reports",
        "repo (default)",
        "json",
    ),
    "list_repo_security_advisories": (
        "GET",
        "/repos/{owner}/{repo}/security-advisories",
        "repo (default)",
        "json",
    ),
    "create_repository_security_advisory": (
        "POST",
        "/repos/{owner}/{repo}/security-advisories",
        "repo (default)",
        "json",
    ),
    "get_repo_security_advisory": (
        "GET",
        "/repos/{owner}/{repo}/security-advisories/{ghsa_id}",
        "repo (default)",
        "json",
    ),
    "update_repo_security_advisory": (
        "PATCH",
        "/repos/{owner}/{repo}/security-advisories/{ghsa_id}",
        "repo (default)",
        "json",
    ),
    "request_security_advisory_cve": (
        "POST",
        "/repos/{owner}/{repo}/security-advisories/{ghsa_id}/cve",
        "repo (default)",
        "json",
    ),
    "create_security_advisory_fork": (
        "POST",
        "/repos/{owner}/{repo}/security-advisories/{ghsa_id}/forks",
        "repo (default)",
        "json",
    ),
    "list_organization_security_advisories": (
        "GET",
        "/orgs/{org}/security-advisories",
        "repo (default)",
        "json",
    ),
    "list_global_security_advisories": (
        "GET",
        "/advisories",
        "repo (default)",
        "json",
    ),
    "get_global_security_advisory": (
        "GET",
        "/advisories/{ghsa_id}",
        "repo (default)",
        "json",
    ),
    "list_pull_requests": (
        "GET",
        "/repos/{owner}/{repo}/pulls",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_pull_request": (
        "POST",
        "/repos/{owner}/{repo}/pulls",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_pull_request": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_pull_request": (
        "PATCH",
        "/repos/{owner}/{repo}/pulls/{pull_number}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_pull_request_files": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/files",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_pull_request_commits": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/commits",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_pull_request_merge_state": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/merge",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "merge_pull_request": (
        "PUT",
        "/repos/{owner}/{repo}/pulls/{pull_number}/merge",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_pull_request_branch": (
        "PUT",
        "/repos/{owner}/{repo}/pulls/{pull_number}/update-branch",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_requested_reviewers": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "request_pull_request_reviewers": (
        "POST",
        "/repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "remove_requested_reviewers": (
        "DELETE",
        "/repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_pull_request_reviews": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_pull_request_review": (
        "POST",
        "/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "submit_pull_request_review": (
        "POST",
        "/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}/events",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "dismiss_pull_request_review": (
        "PUT",
        "/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}/dismissals",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_pull_request_review_comments": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/comments",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_pull_request_review_comment": (
        "POST",
        "/repos/{owner}/{repo}/pulls/{pull_number}/comments",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "reply_to_pull_request_review_comment": (
        "POST",
        "/repos/{owner}/{repo}/pulls/{pull_number}/comments/{comment_id}/replies",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_org_code_scanning_alerts": (
        "GET",
        "/orgs/{org}/code-scanning/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_repo_code_scanning_alerts": (
        "GET",
        "/repos/{owner}/{repo}/code-scanning/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_org_secret_scanning_alerts": (
        "GET",
        "/orgs/{org}/secret-scanning/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_repo_secret_scanning_alerts": (
        "GET",
        "/repos/{owner}/{repo}/secret-scanning/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_org_dependabot_alerts": (
        "GET",
        "/orgs/{org}/dependabot/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_repo_dependabot_alerts": (
        "GET",
        "/repos/{owner}/{repo}/dependabot/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_code_scanning_alert": (
        "GET",
        "/repos/{owner}/{repo}/code-scanning/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "update_code_scanning_alert": (
        "PATCH",
        "/repos/{owner}/{repo}/code-scanning/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_code_scanning_alert_instances": (
        "GET",
        "/repos/{owner}/{repo}/code-scanning/alerts/{alert_number}/instances",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_secret_scanning_alert": (
        "GET",
        "/repos/{owner}/{repo}/secret-scanning/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "update_secret_scanning_alert": (
        "PATCH",
        "/repos/{owner}/{repo}/secret-scanning/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_secret_scanning_alert_locations": (
        "GET",
        "/repos/{owner}/{repo}/secret-scanning/alerts/{alert_number}/locations",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_dependabot_alert": (
        "GET",
        "/repos/{owner}/{repo}/dependabot/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "update_dependabot_alert": (
        "PATCH",
        "/repos/{owner}/{repo}/dependabot/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_code_scanning_default_setup": (
        "GET",
        "/repos/{owner}/{repo}/code-scanning/default-setup",
        "security_events plus repo for private repository access",
        "json",
    ),
    "update_code_scanning_default_setup": (
        "PATCH",
        "/repos/{owner}/{repo}/code-scanning/default-setup",
        "security_events plus repo for private repository access",
        "json",
    ),
    "upload_sarif_and_wait": (
        "POST",
        "/repos/{owner}/{repo}/code-scanning/sarifs",
        "security_events plus repo for private repository access",
        "poll_sarif",
    ),
    "create_code_scanning_autofix_and_wait": (
        "POST",
        "/repos/{owner}/{repo}/code-scanning/alerts/{alert_number}/autofix",
        "security_events plus repo for private repository access",
        "poll_autofix",
    ),
    "commit_code_scanning_autofix": (
        "POST",
        "/repos/{owner}/{repo}/code-scanning/alerts/{alert_number}/autofix/commits",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_repository_sbom": (
        "GET",
        "/repos/{owner}/{repo}/dependency-graph/sbom",
        "security_events plus repo for private repository access",
        "json",
    ),
    "submit_dependency_snapshot": (
        "POST",
        "/repos/{owner}/{repo}/dependency-graph/snapshots",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_vulnerability_alert_status": (
        "GET",
        "/repos/{owner}/{repo}/vulnerability-alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "enable_vulnerability_alerts": (
        "PUT",
        "/repos/{owner}/{repo}/vulnerability-alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "disable_vulnerability_alerts": (
        "DELETE",
        "/repos/{owner}/{repo}/vulnerability-alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_automated_security_fixes_status": (
        "GET",
        "/repos/{owner}/{repo}/automated-security-fixes",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_organization_code_security_configurations": (
        "GET",
        "/orgs/{org}/code-security/configurations",
        "admin:org",
        "json",
    ),
    "list_default_code_security_configurations": (
        "GET",
        "/orgs/{org}/code-security/configurations/defaults",
        "admin:org",
        "json",
    ),
    "get_code_security_configuration": (
        "GET",
        "/orgs/{org}/code-security/configurations/{configuration_id}",
        "admin:org",
        "json",
    ),
    "attach_code_security_configuration": (
        "POST",
        "/orgs/{org}/code-security/configurations/{configuration_id}/attach",
        "admin:org",
        "json",
    ),
    "detach_code_security_configuration": (
        "DELETE",
        "/orgs/{org}/code-security/configurations/detach",
        "admin:org",
        "json",
    ),
    "set_default_code_security_configuration": (
        "PUT",
        "/orgs/{org}/code-security/configurations/{configuration_id}/defaults",
        "admin:org",
        "json",
    ),
    "list_code_security_configuration_repositories": (
        "GET",
        "/orgs/{org}/code-security/configurations/{configuration_id}/repositories",
        "admin:org",
        "json",
    ),
    "get_dependabot_repository_access_policy": (
        "GET",
        "/orgs/{org}/dependabot/repository-access",
        "admin:org",
        "json",
    ),
    "update_dependabot_repository_access_policy": (
        "PATCH",
        "/orgs/{org}/dependabot/repository-access",
        "admin:org",
        "json",
    ),
    "set_dependabot_repository_access_default": (
        "PUT",
        "/orgs/{org}/dependabot/repository-access/default-level",
        "admin:org",
        "json",
    ),
    "list_repository_dependabot_secrets": (
        "GET",
        "/repos/{owner}/{repo}/dependabot/secrets",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_repository_dependabot_public_key": (
        "GET",
        "/repos/{owner}/{repo}/dependabot/secrets/public-key",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_repository_dependabot_secret": (
        "GET",
        "/repos/{owner}/{repo}/dependabot/secrets/{secret_name}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "upsert_repository_dependabot_secret": (
        "PUT",
        "/repos/{owner}/{repo}/dependabot/secrets/{secret_name}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "delete_repository_dependabot_secret": (
        "DELETE",
        "/repos/{owner}/{repo}/dependabot/secrets/{secret_name}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_organization_dependabot_secrets": (
        "GET",
        "/orgs/{org}/dependabot/secrets",
        "admin:org",
        "json",
    ),
    "get_organization_dependabot_public_key": (
        "GET",
        "/orgs/{org}/dependabot/secrets/public-key",
        "admin:org",
        "json",
    ),
    "get_organization_dependabot_secret": (
        "GET",
        "/orgs/{org}/dependabot/secrets/{secret_name}",
        "admin:org",
        "json",
    ),
    "upsert_organization_dependabot_secret": (
        "PUT",
        "/orgs/{org}/dependabot/secrets/{secret_name}",
        "admin:org",
        "json",
    ),
    "delete_organization_dependabot_secret": (
        "DELETE",
        "/orgs/{org}/dependabot/secrets/{secret_name}",
        "admin:org",
        "json",
    ),
    "list_organization_dependabot_secret_repositories": (
        "GET",
        "/orgs/{org}/dependabot/secrets/{secret_name}/repositories",
        "admin:org",
        "json",
    ),
    "set_organization_dependabot_secret_repositories": (
        "PUT",
        "/orgs/{org}/dependabot/secrets/{secret_name}/repositories",
        "admin:org",
        "json",
    ),
    "list_repository_workflows": (
        "GET",
        "/repos/{owner}/{repo}/actions/workflows",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_workflow": (
        "GET",
        "/repos/{owner}/{repo}/actions/workflows/{workflow_id}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "dispatch_workflow": (
        "POST",
        "/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_repository_workflow_runs": (
        "GET",
        "/repos/{owner}/{repo}/actions/runs",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_workflow_run": (
        "GET",
        "/repos/{owner}/{repo}/actions/runs/{run_id}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_workflow_run_jobs": (
        "GET",
        "/repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "download_workflow_job_logs": (
        "GET",
        "/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
        "repo (default; omit only for public repository data)",
        "binary",
    ),
    "cancel_workflow_run": (
        "POST",
        "/repos/{owner}/{repo}/actions/runs/{run_id}/cancel",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "rerun_workflow": (
        "POST",
        "/repos/{owner}/{repo}/actions/runs/{run_id}/rerun",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "rerun_failed_workflow_jobs": (
        "POST",
        "/repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_repository_artifacts": (
        "GET",
        "/repos/{owner}/{repo}/actions/artifacts",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "download_artifact": (
        "GET",
        "/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/{archive_format}",
        "repo (default; omit only for public repository data)",
        "binary",
    ),
    "get_combined_commit_status": (
        "GET",
        "/repos/{owner}/{repo}/commits/{ref}/status",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_commit_statuses": (
        "GET",
        "/repos/{owner}/{repo}/commits/{ref}/statuses",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_commit_status": (
        "POST",
        "/repos/{owner}/{repo}/statuses/{sha}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_releases": (
        "GET",
        "/repos/{owner}/{repo}/releases",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_release": (
        "POST",
        "/repos/{owner}/{repo}/releases",
        "repo (default; omit only for public repository data); workflow "
        "when the resolved target commit modifies .github/workflows",
        "json",
    ),
    "update_release": (
        "PATCH",
        "/repos/{owner}/{repo}/releases/{release_id}",
        "repo (default; omit only for public repository data); workflow "
        "when the resolved target commit modifies .github/workflows",
        "json",
    ),
    "get_release_by_tag": (
        "GET",
        "/repos/{owner}/{repo}/releases/tags/{tag}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "generate_release_notes": (
        "POST",
        "/repos/{owner}/{repo}/releases/generate-notes",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_organization": ("GET", "/orgs/{org}", "read:org", "json"),
    "get_organization_audit_log": (
        "GET",
        "/orgs/{org}/audit-log",
        "read:audit_log",
        "json",
    ),
    "list_organization_github_app_installations": (
        "GET",
        "/orgs/{org}/installations",
        "admin:read",
        "json",
    ),
    "list_organization_members": ("GET", "/orgs/{org}/members", "read:org", "json"),
    "get_organization_membership": (
        "GET",
        "/orgs/{org}/memberships/{username}",
        "read:org",
        "json",
    ),
    "list_outside_collaborators": (
        "GET",
        "/orgs/{org}/outside_collaborators",
        "read:org",
        "json",
    ),
    "list_repository_collaborators": (
        "GET",
        "/repos/{owner}/{repo}/collaborators",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_repository_collaborator_permission": (
        "GET",
        "/repos/{owner}/{repo}/collaborators/{username}/permission",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_branch_protection": (
        "GET",
        "/repos/{owner}/{repo}/branches/{branch}/protection",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_branch_protection": (
        "PUT",
        "/repos/{owner}/{repo}/branches/{branch}/protection",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_required_commit_signature_protection": (
        "GET",
        "/repos/{owner}/{repo}/branches/{branch}/protection/required_signatures",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_repository_rulesets": (
        "GET",
        "/repos/{owner}/{repo}/rulesets",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_organization_rulesets": ("GET", "/orgs/{org}/rulesets", "admin:org", "json"),
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
    url = url.removeprefix("${{ VARS.github.base_url || inputs.base_url }}")
    pattern = re.compile(
        r"\$\{\{ FN\.(?:url_encode_component|url_encode)\(inputs\.([a-zA-Z_][a-zA-Z0-9_]*)\) \}\}"
    )
    return pattern.sub(lambda match: "{" + match.group(1) + "}", url)


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
        assert "GHES version" in definition["description"]
        assert definition["secrets"] == [
            {
                "type": "oauth",
                "provider_id": "github",
                "grant_type": "authorization_code",
            }
        ]
        assert definition["expects"]["base_url"] == {
            "type": "str",
            "description": (
                "GitHub REST API base URL. `VARS.github.base_url` overrides this value."
            ),
            "default": "https://api.github.com",
        }
        request = definition["steps"][0]
        assert request["action"] == "core.http_request"
        assert request["args"]["method"] == method
        request_url = request["args"]["url"]
        assert request_url.startswith("${{ VARS.github.base_url || inputs.base_url }}")
        assert normalized_path(request_url) == path
        for encoder, input_name in re.findall(
            r"FN\.(url_encode_component|url_encode)\(inputs\.([a-zA-Z_][a-zA-Z0-9_]*)\)",
            request_url,
        ):
            expected_encoder = (
                "url_encode" if input_name == "path" else "url_encode_component"
            )
            assert encoder == expected_encoder
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
            assert definition["steps"][1]["action"] == "core.http_poll"
            assert definition["returns"] == "${{ steps.poll.result }}"

        for step in definition["steps"]:
            if step["action"] not in {"core.http_request", "core.http_poll"}:
                continue
            assert step["args"]["url"].startswith(
                "${{ VARS.github.base_url || inputs.base_url }}"
            )


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
        "get_dependabot_repository_access_policy",
        "get_organization_dependabot_public_key",
        "get_organization_dependabot_secret",
        "get_repository_dependabot_public_key",
        "get_repository_dependabot_secret",
        "list_org_dependabot_alerts",
        "list_organization_dependabot_secret_repositories",
        "list_organization_dependabot_secrets",
        "list_repo_dependabot_alerts",
        "list_repository_dependabot_secrets",
        "set_dependabot_repository_access_default",
        "set_organization_dependabot_secret_repositories",
        "update_dependabot_alert",
        "update_dependabot_repository_access_policy",
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


def test_bug_issue_payload_and_ghes_fallback_contract() -> None:
    definition = load_templates()["create_issue"]["definition"]
    request = definition["steps"][0]["args"]
    assert normalized_path(request["url"]) == "/repos/{owner}/{repo}/issues"
    assert request["payload"] == "${{ inputs.payload }}"
    assert "type: Bug" in definition["description"]
    assert "labels: [bug]" in definition["description"]
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
    assert "FN.url_encode(inputs.path)" in url
    assert "FN.url_encode_component(inputs.owner)" in url


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
    assert not sarif_condition({"status_code": 200, "data": {}})
    assert not sarif_condition(
        {"status_code": 503, "data": {"processing_status": "complete"}}
    )
    assert sarif["expects"]["poll_max_attempts"]["default"] > 0

    autofix = templates["create_code_scanning_autofix_and_wait"]["definition"]
    assert autofix["steps"][0]["args"]["method"] == "POST"
    assert autofix["steps"][1]["args"]["method"] == "GET"
    autofix_condition = build_safe_lambda(autofix["steps"][1]["args"]["poll_condition"])
    assert not autofix_condition({"status_code": 200, "data": {"status": "pending"}})
    for status in ("success", "error", "outdated", "new_terminal_state"):
        assert autofix_condition({"status_code": 200, "data": {"status": status}})
    assert not autofix_condition({"status_code": 200, "data": {}})
    assert not autofix_condition({"status_code": 503, "data": {"status": "success"}})
    assert autofix["expects"]["poll_max_attempts"]["default"] > 0
